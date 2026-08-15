"""Point-in-time win-odds selection shared by training and evaluation.

The selector intentionally fails closed.  A final/result-time quote, a quote
observed after the configured decision cutoff, or a stale quote is never used
to calculate historical expected value.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

REQUIRED_SNAPSHOT_COLUMNS = frozenset(
    {
        "race_id",
        "horse_id",
        "odds",
        "observed_at",
        "source",
        "snapshot_kind",
    }
)
REQUIRED_RACE_COLUMNS = frozenset({"race_id", "post_time"})
FORBIDDEN_SNAPSHOT_KINDS = frozenset({"final", "result", "settled", "payout"})
ALLOWED_SNAPSHOT_KINDS = frozenset({"pre_race", "decision_time"})


@dataclass(frozen=True)
class PointInTimeOddsPolicy:
    """Rules used to choose the quote that was available at decision time."""

    decision_offset_minutes: int = 5
    max_age_minutes: int = 30
    minimum_odds: float = 1.01

    def __post_init__(self) -> None:
        if self.decision_offset_minutes < 0:
            raise ValueError("decision_offset_minutes must be non-negative")
        if self.max_age_minutes <= 0:
            raise ValueError("max_age_minutes must be positive")
        if self.minimum_odds <= 1.0:
            raise ValueError("minimum_odds must be greater than 1.0")


def _utc(values: pd.Series, name: str) -> pd.Series:
    parsed = pd.to_datetime(values, utc=True, errors="coerce")
    if parsed.isna().any():
        raise ValueError(f"{name} contains missing or invalid timestamps")
    return parsed


def select_point_in_time_win_odds(
    snapshots: pd.DataFrame,
    races: pd.DataFrame,
    *,
    policy: PointInTimeOddsPolicy | None = None,
    require_complete_races: bool = True,
) -> pd.DataFrame:
    """Return the latest eligible quote per runner at a fixed pre-race cutoff.

    ``races`` may contain ``expected_runner_count``.  When present and
    ``require_complete_races`` is true, races without exactly that many fresh
    quotes are excluded in their entirety.
    """

    policy = policy or PointInTimeOddsPolicy()
    missing_snapshots = REQUIRED_SNAPSHOT_COLUMNS - set(snapshots.columns)
    missing_races = REQUIRED_RACE_COLUMNS - set(races.columns)
    if missing_snapshots:
        raise ValueError(
            "odds snapshots are missing required columns: "
            + ", ".join(sorted(missing_snapshots))
        )
    if missing_races:
        raise ValueError(
            "race metadata is missing required columns: "
            + ", ".join(sorted(missing_races))
        )
    if snapshots.empty or races.empty:
        return pd.DataFrame(
            columns=[
                "race_id",
                "horse_id",
                "odds",
                "odds_observed_at",
                "odds_cutoff_at",
                "odds_age_minutes",
                "odds_source",
                "odds_snapshot_kind",
            ]
        )

    race_meta_columns = ["race_id", "post_time"]
    if "expected_runner_count" in races.columns:
        race_meta_columns.append("expected_runner_count")
    race_meta = races[race_meta_columns].copy()
    if race_meta["race_id"].astype(str).duplicated().any():
        raise ValueError("race metadata contains duplicate race_id values")
    race_meta["race_id"] = race_meta["race_id"].astype(str)
    race_meta["post_time"] = _utc(race_meta["post_time"], "post_time")
    race_meta["odds_cutoff_at"] = race_meta["post_time"] - pd.to_timedelta(
        policy.decision_offset_minutes, unit="minute"
    )

    work = snapshots.copy()
    work["race_id"] = work["race_id"].astype(str)
    work["horse_id"] = work["horse_id"].astype(str)
    if work[["race_id", "horse_id"]].eq("").any().any():
        raise ValueError("race_id and horse_id must be non-empty")
    work["observed_at"] = _utc(work["observed_at"], "observed_at")
    work["odds"] = pd.to_numeric(work["odds"], errors="coerce")
    work["snapshot_kind"] = work["snapshot_kind"].astype(str).str.strip().str.lower()
    work["source"] = work["source"].astype(str).str.strip()
    if work["source"].eq("").any():
        raise ValueError("odds source must be non-empty")
    if work["snapshot_kind"].eq("").any():
        raise ValueError("snapshot_kind must be non-empty")

    work = work.merge(race_meta, on="race_id", how="inner", validate="many_to_one")
    work["odds_age_minutes"] = (
        work["odds_cutoff_at"] - work["observed_at"]
    ).dt.total_seconds() / 60.0
    eligible = work[
        work["odds"].ge(policy.minimum_odds)
        & np.isfinite(work["odds"])
        & work["odds_age_minutes"].between(0.0, float(policy.max_age_minutes))
        & ~work["snapshot_kind"].isin(FORBIDDEN_SNAPSHOT_KINDS)
        & work["snapshot_kind"].isin(ALLOWED_SNAPSHOT_KINDS)
    ].copy()
    if eligible.empty:
        return select_point_in_time_win_odds(
            snapshots.iloc[0:0], races.iloc[0:0], policy=policy
        )

    eligible = eligible.sort_values(
        ["race_id", "horse_id", "observed_at", "source"],
        kind="stable",
    )
    selected = eligible.groupby(["race_id", "horse_id"], sort=False).tail(1).copy()

    if require_complete_races:
        selected_counts = selected.groupby("race_id")["horse_id"].nunique()
        if "expected_runner_count" in selected.columns:
            expected = (
                selected.groupby("race_id")["expected_runner_count"].first().astype(int)
            )
        else:
            expected = work.groupby("race_id")["horse_id"].nunique()
        complete = selected_counts[
            selected_counts.eq(expected.reindex(selected_counts.index))
        ]
        selected = selected[selected["race_id"].isin(complete.index)].copy()

    selected = selected.rename(
        columns={
            "observed_at": "odds_observed_at",
            "source": "odds_source",
            "snapshot_kind": "odds_snapshot_kind",
        }
    )
    columns = [
        "race_id",
        "horse_id",
        "odds",
        "odds_observed_at",
        "odds_cutoff_at",
        "odds_age_minutes",
        "odds_source",
        "odds_snapshot_kind",
    ]
    return selected[columns].sort_values(["race_id", "horse_id"]).reset_index(drop=True)


def normalized_market_probability(frame: pd.DataFrame) -> pd.Series:
    """Return overround-normalized implied win probability per race."""

    if not {"race_id", "odds"}.issubset(frame.columns):
        raise ValueError("race_id and odds are required")
    odds = pd.to_numeric(frame["odds"], errors="coerce")
    if odds.isna().any() or odds.le(1.0).any() or not np.isfinite(odds).all():
        raise ValueError("odds must be finite and greater than 1.0")
    inverse = 1.0 / odds
    totals = inverse.groupby(frame["race_id"]).transform("sum")
    if totals.le(0).any():
        raise ValueError("race implied-probability total must be positive")
    return inverse / totals
