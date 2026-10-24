"""Deterministic eligibility policy for model-training rows.

The acquisition database is an inventory of observations, not a ready-made
training set.  This module keeps the two concepts separate: race-level quality
problems reject the whole race, while an unavailable target rejects only the
affected runner (for example a scratched horse without a finish time).

The returned manifest deliberately contains hashes instead of large identifier
lists so it is safe to persist in a model bundle and simple to display in the
training UI.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


POLICY_VERSION = "training-eligibility-v1"
SUPPORTED_TARGETS = frozenset({"win", "place3", "win_tie", "speed_deviation", "rank"})

# The order is part of the serialized manifest contract.
RACE_EXCLUSION_REASONS = (
    "invalid_race_id",
    "race_date_invalid",
    "race_date_inconsistent",
    "runner_identity_missing",
    "duplicate_runner",
    "horse_count_inconsistent",
    "distance_invalid",
    "distance_inconsistent",
    "venue_missing",
    "venue_inconsistent",
    "recorded_quality_incomplete",
    "result_not_settled",
    "finish_invalid",
    "unverified_partial_result",
    "odds_coverage_insufficient",
    "popularity_coverage_insufficient",
)
ROW_EXCLUSION_REASONS = ("target_missing",)
EXCLUSION_REASONS = RACE_EXCLUSION_REASONS + ROW_EXCLUSION_REASONS


class TrainingEligibilityError(ValueError):
    """Raised when an eligibility policy cannot be evaluated safely."""


@dataclass(frozen=True)
class TrainingEligibilityManifest:
    """Small, JSON-serializable provenance record for one selected data set."""

    target: str
    requested_date_from: str | None
    requested_date_to: str | None
    observed_date_from: str | None
    observed_date_to: str | None
    months_with_data: tuple[str, ...]
    missing_months: tuple[str, ...]
    quality_ledger_covered_race_count: int
    input_race_count: int
    input_row_count: int
    eligible_race_count: int
    eligible_row_count: int
    excluded_race_count: int
    excluded_row_count: int
    exclusion_reason_counts: tuple[tuple[str, int, int], ...]
    eligible_race_ids_sha256: str
    eligible_row_ids_sha256: str
    eligibility_sha256: str

    def as_dict(self) -> dict[str, Any]:
        """Return a fresh deterministic object accepted by ``json.dumps``."""

        return {
            "schema_version": 1,
            "policy_version": POLICY_VERSION,
            "target": self.target,
            "requested_period": {
                "from": self.requested_date_from,
                "to": self.requested_date_to,
            },
            "observed_eligible_date_range": {
                "from": self.observed_date_from,
                "to": self.observed_date_to,
            },
            "months_with_data": list(self.months_with_data),
            "missing_months": list(self.missing_months),
            "quality_ledger": {
                "covered_races": self.quality_ledger_covered_race_count,
                "uncovered_races": (
                    self.input_race_count - self.quality_ledger_covered_race_count
                ),
            },
            "counts": {
                "input": {
                    "races": self.input_race_count,
                    "rows": self.input_row_count,
                },
                "eligible": {
                    "races": self.eligible_race_count,
                    "rows": self.eligible_row_count,
                },
                "excluded": {
                    "races": self.excluded_race_count,
                    "rows": self.excluded_row_count,
                },
            },
            # A row/race can have more than one reason; totals are non-exclusive.
            "exclusion_reason_counts_are_nonexclusive": True,
            "exclusion_reason_counts": {
                reason: {"races": races, "rows": rows}
                for reason, races, rows in self.exclusion_reason_counts
            },
            "eligible_identifiers": {
                "race_ids_sha256": self.eligible_race_ids_sha256,
                "row_ids_sha256": self.eligible_row_ids_sha256,
            },
            "eligibility_sha256": self.eligibility_sha256,
        }


@dataclass(frozen=True)
class TrainingEligibilitySelection:
    """Eligible frame plus the exact policy result that produced it."""

    frame: Any
    manifest: TrainingEligibilityManifest


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _clean_identifier(series: Any) -> Any:
    value = series.astype("string").fillna("").str.strip()
    return value.mask(value.str.lower().isin({"none", "nan", "<na>"}), "")


def _month(value: str | None, *, label: str) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    match = re.fullmatch(r"(\d{4})-?(\d{2})", text)
    if match is None:
        raise TrainingEligibilityError(f"{label}-invalid")
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        raise TrainingEligibilityError(f"{label}-invalid")
    return f"{year:04d}-{month:02d}"


def _month_range(start: str | None, end: str | None) -> tuple[str, ...]:
    if start is None or end is None:
        return ()
    start_year, start_month = (int(value) for value in start.split("-"))
    end_year, end_month = (int(value) for value in end.split("-"))
    if (start_year, start_month) > (end_year, end_month):
        raise TrainingEligibilityError("requested-period-invalid")
    months: list[str] = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return tuple(months)


def _observed_dates(frame: Any) -> Any:
    """Return normalized YYYYMMDD strings from the authoritative race date."""

    import pandas as pd

    if "race_date" in frame.columns:
        raw = frame["race_date"].astype("string").fillna("").str.strip()
        supported_syntax = raw.str.fullmatch(
            r"(?:\d{8}|\d{4}-\d{2}-\d{2}|\d{4}/\d{2}/\d{2})"
        )
        compact = raw.str.replace(r"\D", "", regex=True)
        valid = supported_syntax & compact.str.fullmatch(r"\d{8}")
        observed = compact.where(valid, "")
    else:
        observed = pd.Series("", index=frame.index, dtype="string")
    return observed


def _iso_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    except (TypeError, ValueError):
        return None
    return parsed.isoformat()


def _target_valid_mask(frame: Any, target: str) -> Any:
    import pandas as pd

    finish_column = next(
        (name for name in ("finish", "finish_position") if name in frame.columns),
        None,
    )
    finish_valid = (
        pd.to_numeric(frame[finish_column], errors="coerce").gt(0)
        if finish_column is not None
        else pd.Series(False, index=frame.index)
    )
    if target == "speed_deviation":
        # ``raw_speed_mps`` consumes the canonical loader output only.  Do not
        # claim an alias is eligible when target construction cannot use it.
        if "time_seconds" not in frame.columns:
            return pd.Series(False, index=frame.index)
        return finish_valid & pd.to_numeric(
            frame["time_seconds"], errors="coerce"
        ).gt(0)

    return finish_valid


def load_recorded_quality_states(
    database_path: str | Path,
) -> dict[str, tuple[str, str, bool]]:
    """Load the optional acquisition ledger without mutating the source DB."""

    path = Path(database_path).resolve(strict=True)
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='scrape_race_acquisition_state'"
        ).fetchone()
        if exists is None:
            return {}
        rows = connection.execute(
            "SELECT race_id, lifecycle, quality_status, excluded_from_standard "
            "FROM scrape_race_acquisition_state"
        ).fetchall()
    finally:
        connection.close()
    return {
        str(race_id).strip(): (
            str(lifecycle or "").strip(),
            str(quality_status or "").strip(),
            bool(excluded),
        )
        for race_id, lifecycle, quality_status, excluded in rows
        if str(race_id or "").strip()
    }


def select_training_eligible_rows(
    frame: Any,
    *,
    target: str,
    training_date_from: str | None = None,
    training_date_to: str | None = None,
    recorded_quality_states: Mapping[str, tuple[str, str, bool]] | None = None,
) -> TrainingEligibilitySelection:
    """Select rows that are safe enough to enter feature engineering.

    The caller should apply the requested period first.  Structural, market,
    consistency, duplicate and unsettled-result findings reject a whole race.
    ``target_missing`` rejects only the affected row, which preserves usable
    finishers when a scratched/non-finishing runner has no numeric outcome.
    """

    import pandas as pd

    if target not in SUPPORTED_TARGETS:
        raise TrainingEligibilityError("target-invalid")
    if not isinstance(frame, pd.DataFrame):
        raise TrainingEligibilityError("frame-invalid")
    if "race_id" not in frame.columns:
        raise TrainingEligibilityError("race-id-column-missing")

    requested_from = _month(training_date_from, label="training-date-from")
    requested_to = _month(training_date_to, label="training-date-to")
    requested_months = _month_range(requested_from, requested_to)

    working = frame.copy(deep=False).reset_index(drop=True)
    race_ids = _clean_identifier(working["race_id"])
    # Missing race IDs are separate logical observations for accurate counts.
    race_keys = race_ids.copy()
    missing_race_ids = race_keys.eq("")
    if missing_race_ids.any():
        positions = pd.Series(range(len(working)), index=working.index).astype(str)
        race_keys = race_keys.mask(missing_race_ids, "__missing_race__" + positions)

    reasons = {
        reason: pd.Series(False, index=working.index, dtype=bool)
        for reason in EXCLUSION_REASONS
    }

    def reject_races(reason: str, per_row_condition: Any) -> None:
        failed_keys = set(race_keys.loc[per_row_condition].tolist())
        if failed_keys:
            reasons[reason] = race_keys.isin(failed_keys)

    invalid_race = ~race_ids.str.fullmatch(r"\d{12}")
    reject_races("invalid_race_id", invalid_race)

    observed_race_dates = _observed_dates(working)
    parsed_race_dates = pd.to_datetime(
        observed_race_dates,
        format="%Y%m%d",
        errors="coerce",
    )
    reject_races(
        "race_date_invalid",
        observed_race_dates.eq("") | parsed_race_dates.isna(),
    )
    reject_races(
        "race_date_inconsistent",
        observed_race_dates.groupby(race_keys).transform("nunique").gt(1),
    )

    identity_columns = [
        column for column in ("horse_id", "horse_number") if column in working.columns
    ]
    if identity_columns:
        identities = {column: _clean_identifier(working[column]) for column in identity_columns}
        identity_missing = pd.Series(True, index=working.index)
        duplicate = pd.Series(False, index=working.index)
        for identity in identities.values():
            present = identity.ne("")
            identity_missing &= ~present
            duplicate |= present & pd.DataFrame(
                {"race": race_keys, "identity": identity}
            ).duplicated(["race", "identity"], keep=False)
        reject_races("runner_identity_missing", identity_missing)
        reject_races("duplicate_runner", duplicate)
    else:
        reasons["runner_identity_missing"] = pd.Series(True, index=working.index)

    observed_count = race_keys.groupby(race_keys).transform("size")
    if "num_horses" in working.columns:
        declared_count = pd.to_numeric(working["num_horses"], errors="coerce")
        declared_invalid = (
            declared_count.isna()
            | declared_count.le(0)
            | declared_count.ne(declared_count.round())
            | declared_count.ne(observed_count)
        )
        declared_varies = declared_count.groupby(race_keys).transform("nunique").gt(1)
        reject_races("horse_count_inconsistent", declared_invalid | declared_varies)
    else:
        reasons["horse_count_inconsistent"] = pd.Series(True, index=working.index)

    if "distance" in working.columns:
        distance = pd.to_numeric(working["distance"], errors="coerce")
        reject_races("distance_invalid", distance.isna() | distance.le(0))
        reject_races(
            "distance_inconsistent",
            distance.groupby(race_keys).transform("nunique").gt(1),
        )
    else:
        reasons["distance_invalid"] = pd.Series(True, index=working.index)

    if "venue" in working.columns:
        venue = _clean_identifier(working["venue"])
        reject_races("venue_missing", venue.eq(""))
        reject_races(
            "venue_inconsistent",
            venue.groupby(race_keys).transform("nunique").gt(1),
        )
    else:
        reasons["venue_missing"] = pd.Series(True, index=working.index)

    quality_states = recorded_quality_states or {}
    quality_keys = {
        str(race_id).strip()
        for race_id in quality_states
        if str(race_id or "").strip()
    }
    recorded_state = race_ids.map(quality_states)
    quality_covered = race_ids.isin(quality_keys)
    quality_incomplete = quality_covered & recorded_state.map(
        lambda value: not (
            isinstance(value, tuple)
            and len(value) == 3
            and value == ("settled", "complete", False)
        )
    )
    reject_races("recorded_quality_incomplete", quality_incomplete)

    finish_column = next(
        (name for name in ("finish", "finish_position") if name in working.columns),
        None,
    )
    if finish_column is None:
        reasons["result_not_settled"] = pd.Series(True, index=working.index)
        reasons["unverified_partial_result"] = ~quality_covered
    else:
        finish = pd.to_numeric(working[finish_column], errors="coerce")
        has_winner = finish.eq(1).groupby(race_keys).transform("any")
        reject_races("result_not_settled", ~has_winner)
        if "num_horses" in working.columns:
            declared_finish_limit = pd.to_numeric(
                working["num_horses"],
                errors="coerce",
            )
            reject_races(
                "finish_invalid",
                finish.notna()
                & (
                    finish.le(0)
                    | finish.ne(finish.round())
                    | finish.gt(declared_finish_limit)
                ),
            )
        # A complete acquisition ledger can distinguish legitimate scratches
        # and non-finishers from an interrupted result scrape.  For legacy
        # races without that evidence, any missing finish makes the whole race
        # unverifiable and therefore ineligible.
        incomplete_finish = (~finish.gt(0)).groupby(race_keys).transform("any")
        reject_races(
            "unverified_partial_result",
            ~quality_covered & incomplete_finish,
        )

    for column, reason in (
        ("odds", "odds_coverage_insufficient"),
        ("popularity", "popularity_coverage_insufficient"),
    ):
        if column not in working.columns:
            reasons[reason] = pd.Series(True, index=working.index)
            continue
        valid_market_value = pd.to_numeric(working[column], errors="coerce").gt(0)
        missing_fraction = (~valid_market_value).groupby(race_keys).transform("mean")
        reject_races(reason, missing_fraction.ge(0.8))

    reasons["target_missing"] = ~_target_valid_mask(working, target)

    rejected = pd.Series(False, index=working.index)
    for mask in reasons.values():
        rejected |= mask
    eligible_mask = ~rejected
    eligible = working.loc[eligible_mask].copy().reset_index(drop=True)

    eligible_race_ids = tuple(sorted(set(race_ids.loc[eligible_mask].tolist())))
    if "horse_id" in working.columns:
        primary_runner = _clean_identifier(working["horse_id"])
    else:
        primary_runner = pd.Series("", index=working.index, dtype="string")
    if "horse_number" in working.columns:
        fallback_runner = _clean_identifier(working["horse_number"])
        primary_runner = primary_runner.where(primary_runner.ne(""), fallback_runner)
    eligible_row_ids = tuple(
        sorted(
            f"{race_id}|{runner_id}"
            for race_id, runner_id in zip(
                race_ids.loc[eligible_mask].tolist(),
                primary_runner.loc[eligible_mask].tolist(),
            )
        )
    )

    observed_dates = observed_race_dates.loc[eligible_mask]
    valid_dates = sorted(
        {
            value
            for value in observed_dates.tolist()
            if isinstance(value, str) and re.fullmatch(r"\d{8}", value)
        }
    )
    observed_months = tuple(
        sorted({f"{value[:4]}-{value[4:6]}" for value in valid_dates})
    )
    missing_months = tuple(
        month for month in requested_months if month not in set(observed_months)
    )

    reason_counts: list[tuple[str, int, int]] = []
    for reason in EXCLUSION_REASONS:
        mask = reasons[reason]
        reason_counts.append(
            (
                reason,
                int(race_keys.loc[mask].nunique()),
                int(mask.sum()),
            )
        )

    input_races = int(race_keys.nunique())
    eligible_races = len(eligible_race_ids)
    manifest_core = {
        "schema_version": 1,
        "policy_version": POLICY_VERSION,
        "target": target,
        "requested_period": {"from": requested_from, "to": requested_to},
        "observed_eligible_date_range": {
            "from": _iso_date(valid_dates[0]) if valid_dates else None,
            "to": _iso_date(valid_dates[-1]) if valid_dates else None,
        },
        "months_with_data": list(observed_months),
        "missing_months": list(missing_months),
        "quality_ledger": {
            "covered_races": int(race_keys.loc[quality_covered].nunique()),
            "uncovered_races": (
                input_races - int(race_keys.loc[quality_covered].nunique())
            ),
        },
        "counts": {
            "input": {"races": input_races, "rows": len(working)},
            "eligible": {"races": eligible_races, "rows": len(eligible)},
            "excluded": {
                "races": input_races - eligible_races,
                "rows": len(working) - len(eligible),
            },
        },
        "exclusion_reason_counts_are_nonexclusive": True,
        "exclusion_reason_counts": {
            reason: {"races": races, "rows": rows}
            for reason, races, rows in reason_counts
        },
        "eligible_identifiers": {
            "race_ids_sha256": _canonical_sha256(eligible_race_ids),
            "row_ids_sha256": _canonical_sha256(eligible_row_ids),
        },
    }
    manifest = TrainingEligibilityManifest(
        target=target,
        requested_date_from=requested_from,
        requested_date_to=requested_to,
        observed_date_from=manifest_core["observed_eligible_date_range"]["from"],
        observed_date_to=manifest_core["observed_eligible_date_range"]["to"],
        months_with_data=observed_months,
        missing_months=missing_months,
        quality_ledger_covered_race_count=manifest_core["quality_ledger"][
            "covered_races"
        ],
        input_race_count=input_races,
        input_row_count=len(working),
        eligible_race_count=eligible_races,
        eligible_row_count=len(eligible),
        excluded_race_count=input_races - eligible_races,
        excluded_row_count=len(working) - len(eligible),
        exclusion_reason_counts=tuple(reason_counts),
        eligible_race_ids_sha256=manifest_core["eligible_identifiers"]["race_ids_sha256"],
        eligible_row_ids_sha256=manifest_core["eligible_identifiers"]["row_ids_sha256"],
        eligibility_sha256=_canonical_sha256(manifest_core),
    )
    return TrainingEligibilitySelection(frame=eligible, manifest=manifest)
