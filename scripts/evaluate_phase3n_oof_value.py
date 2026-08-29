"""Run the Phase3N OOF probability/value layer without activating a model.

This stage intentionally consumes base speed-model predictions rather than
training that base model itself.  The input contract makes the two leakage
boundaries auditable: inner rows must be base-model OOF predictions and all
odds must carry a valid pre-race observation timestamp.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import joblib
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "keiba"))

from keiba_ai.nested_strategy import (  # noqa: E402
    StrategyCondition,
    evaluate_baseline_strategy,
    evaluate_strategy,
    select_nested_strategy,
)
from keiba_ai.point_in_time_odds import (  # noqa: E402
    PointInTimeOddsPolicy,
    select_point_in_time_win_odds,
)
from keiba_ai.winner_meta_model import (  # noqa: E402
    WinnerProbabilityMetaModel,
    expanding_year_splits,
)

SEARCH_SPACE_PATH = ROOT / "config" / "phase3n_strategy_search_space.v1.json"
STAKING_POLICY_PATH = ROOT / "config" / "phase3n_staking_payout_policy.v1.json"
REPORT_SCHEMA = "phase3n-oof-value-evaluation-v2"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalize_input_aliases(frame: pd.DataFrame) -> pd.DataFrame:
    """Accept explicit evidence names while retaining the existing file contract."""

    work = frame.copy()
    aliases = {
        "quote_at": "odds_observed_at",
        "race_start_at": "post_time",
        "exact_commit_sha": "candidate_commit_sha",
    }
    for source, target in aliases.items():
        if source not in work:
            continue
        if target in work:
            left = work[source].astype(str)
            right = work[target].astype(str)
            if not left.eq(right).all():
                raise ValueError(f"{source} and {target} contain conflicting values")
            work = work.drop(columns=[source])
        else:
            work = work.rename(columns={source: target})
    return work


def _validate_staking_policy(policy: dict[str, object]) -> None:
    if policy.get("status") != "approved":
        raise ValueError("staking/payout policy must be approved")
    if not str(policy.get("approval_reference") or "").startswith("https://"):
        raise ValueError("staking/payout policy approval reference is required")
    candidate = policy.get("candidate")
    baseline = policy.get("baseline")
    if not isinstance(candidate, dict) or not isinstance(baseline, dict):
        raise ValueError("staking/payout policy candidate and baseline are required")
    if candidate.get("maximum_wagers_per_race") != 1:
        raise ValueError("candidate policy must allow exactly one wager per race")
    if baseline.get("maximum_wagers_per_race") != 1:
        raise ValueError("baseline policy must allow exactly one wager per race")
    if candidate.get("selection") != "highest_model_expected_value":
        raise ValueError("candidate selection policy is unsupported")
    if baseline.get("selection") != "lowest_valid_win_odds":
        raise ValueError("baseline selection policy is unsupported")


def _input_evidence(frame: pd.DataFrame) -> dict[str, object]:
    columns = [
        "race_id",
        "horse_id",
        "race_date",
        "post_time",
        "base_score",
        "winner",
        "odds",
        "odds_observed_at",
        "odds_source",
        "odds_snapshot_kind",
        "odds_status",
        "candidate_commit_sha",
    ]
    canonical = frame[columns].copy().sort_values(
        ["race_date", "race_id", "horse_id"], kind="stable"
    )
    input_digest = hashlib.sha256(
        canonical.to_json(
            orient="records",
            date_format="iso",
            date_unit="us",
            double_precision=15,
        ).encode("utf-8")
    ).hexdigest()
    quotes = pd.to_datetime(frame["odds_observed_at"], utc=True, errors="raise")
    starts = pd.to_datetime(frame["post_time"], utc=True, errors="raise")
    return {
        "row_count": int(len(frame)),
        "race_count": int(frame["race_id"].nunique()),
        "source_values": sorted(frame["odds_source"].astype(str).unique().tolist()),
        "odds_status_values": sorted(
            frame["odds_status"].astype(str).str.lower().unique().tolist()
        ),
        "quote_at_min": quotes.min().isoformat(),
        "quote_at_max": quotes.max().isoformat(),
        "race_start_at_min": starts.min().isoformat(),
        "race_start_at_max": starts.max().isoformat(),
        "maximum_odds_age_minutes": float(frame["odds_age_minutes"].max()),
        "candidate_commit_sha": str(frame["candidate_commit_sha"].iloc[0]),
        "input_rows_sha256": input_digest,
    }


def _read_frame(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix in {".jsonl", ".ndjson"}:
        return pd.read_json(path, lines=True)
    raise ValueError("prediction input must be parquet, jsonl, or ndjson")


def _strict_point_in_time_frame(
    frame: pd.DataFrame,
    *,
    policy: PointInTimeOddsPolicy,
) -> pd.DataFrame:
    frame = _normalize_input_aliases(frame)
    required = {
        "race_id",
        "horse_id",
        "race_date",
        "post_time",
        "base_score",
        "winner",
        "odds",
        "odds_observed_at",
        "odds_source",
        "odds_snapshot_kind",
        "odds_status",
        "candidate_commit_sha",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError("prediction frame is missing: " + ", ".join(sorted(missing)))
    if frame.empty:
        raise ValueError("prediction frame contains no point-in-time odds rows")
    if frame[["race_id", "horse_id"]].duplicated().any():
        raise ValueError("prediction frame contains duplicate race_id/horse_id rows")
    odds_status = frame["odds_status"].astype(str).str.strip().str.lower()
    invalid_statuses = sorted(set(odds_status) - {"middle"})
    if invalid_statuses:
        raise ValueError(
            "Phase3N value evaluation requires odds_status=middle; rejected: "
            + ", ".join(invalid_statuses)
        )
    snapshot_kinds = (
        frame["odds_snapshot_kind"].astype(str).str.strip().str.lower()
    )
    if snapshot_kinds.isin({"final", "result", "settled", "payout"}).any():
        raise ValueError("final/result-time odds are forbidden for value evaluation")
    commits = frame["candidate_commit_sha"].astype(str).str.strip().str.lower()
    if commits.nunique() != 1 or COMMIT_RE.fullmatch(commits.iloc[0]) is None:
        raise ValueError("prediction frame requires one valid exact candidate commit SHA")
    if commits.iloc[0] == "0" * 40:
        raise ValueError("zero candidate commit SHA is forbidden")
    frame = frame.copy()
    frame["candidate_commit_sha"] = commits
    if policy.max_age_minutes > 30:
        raise ValueError("Phase3N odds freshness must not exceed 30 minutes")
    races = frame[["race_id", "post_time"]].drop_duplicates()
    race_counts = (
        frame.groupby("race_id")["horse_id"].nunique().rename("expected_runner_count")
    )
    races = races.merge(race_counts, on="race_id", how="left", validate="one_to_one")
    snapshots = frame[
        [
            "race_id",
            "horse_id",
            "odds",
            "odds_observed_at",
            "odds_source",
            "odds_snapshot_kind",
        ]
    ].rename(
        columns={
            "odds_observed_at": "observed_at",
            "odds_source": "source",
            "odds_snapshot_kind": "snapshot_kind",
        }
    )
    selected = select_point_in_time_win_odds(snapshots, races, policy=policy)
    if len(selected) != len(frame):
        raise ValueError(
            "one or more races lack complete fresh middle point-in-time odds"
        )
    base = frame.drop(
        columns=[
            "odds",
            "odds_observed_at",
            "odds_source",
            "odds_snapshot_kind",
            "odds_cutoff_at",
            "odds_age_minutes",
        ],
        errors="ignore",
    )
    return base.merge(selected, on=["race_id", "horse_id"], validate="one_to_one")


def _meta_oof_probabilities(
    base_oof: pd.DataFrame,
    *,
    minimum_training_years: int,
) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    outputs: list[pd.DataFrame] = []
    fold_reports: list[dict[str, object]] = []
    dates = pd.to_datetime(base_oof["race_date"], errors="raise")
    for training_indices, validation_indices, validation_year in expanding_year_splits(
        dates,
        minimum_training_years=minimum_training_years,
    ):
        training = base_oof.iloc[training_indices].copy()
        validation = base_oof.iloc[validation_indices].copy()
        model = WinnerProbabilityMetaModel().fit(training)
        validation["probability"] = model.predict(validation)
        validation["base_prediction_is_oof"] = True
        validation["probability_prediction_is_oof"] = True
        outputs.append(validation)
        fold_reports.append(
            {
                "validation_year": validation_year,
                "meta_training_end": model.fitted_through.date().isoformat(),
                "training_race_count": int(training["race_id"].nunique()),
                "validation_race_count": int(validation["race_id"].nunique()),
            }
        )
    if not outputs:
        raise ValueError(
            "insufficient annual base OOF folds to produce meta-level OOF predictions"
        )
    return pd.concat(outputs, ignore_index=True), fold_reports


def evaluate_oof_value_layer(
    *,
    inner_base_oof: pd.DataFrame,
    outer_base_predictions: pd.DataFrame,
    search_space: dict[str, object],
    staking_policy: dict[str, object] | None = None,
    odds_policy: PointInTimeOddsPolicy,
    minimum_meta_training_years: int = 2,
) -> tuple[dict[str, object], WinnerProbabilityMetaModel]:
    if staking_policy is None:
        staking_policy = json.loads(STAKING_POLICY_PATH.read_text(encoding="utf-8"))
    _validate_staking_policy(staking_policy)
    if int(search_space.get("maximum_wagers_per_race", 1)) != 1:
        raise ValueError("strategy search must allow exactly one wager per race")
    minimum_bet_rate = float(search_space.get("minimum_bet_rate", 0.05))
    maximum_bet_rate = float(search_space.get("maximum_bet_rate", 0.15))
    target_rates = [float(value) for value in search_space["target_bet_rates"]]
    if any(
        value < minimum_bet_rate or value > maximum_bet_rate
        for value in target_rates
    ):
        raise ValueError("target bet rates must stay inside the approved search bounds")
    inner = _strict_point_in_time_frame(inner_base_oof, policy=odds_policy)
    outer = _strict_point_in_time_frame(outer_base_predictions, policy=odds_policy)
    if (
        "base_prediction_is_oof" not in inner
        or not inner["base_prediction_is_oof"].eq(True).all()
    ):  # noqa: E712
        raise ValueError("inner base predictions must all be marked OOF")
    inner_dates = pd.to_datetime(inner["race_date"], errors="raise")
    outer_dates = pd.to_datetime(outer["race_date"], errors="raise")
    if outer_dates.min() <= inner_dates.max():
        raise ValueError("outer evaluation must be strictly after all inner OOF rows")
    inner_commit = str(inner["candidate_commit_sha"].iloc[0])
    outer_commit = str(outer["candidate_commit_sha"].iloc[0])
    if inner_commit != outer_commit:
        raise ValueError("inner and outer rows must bind to the same exact commit SHA")

    meta_oof, meta_folds = _meta_oof_probabilities(
        inner,
        minimum_training_years=minimum_meta_training_years,
    )
    selection = select_nested_strategy(
        meta_oof,
        search_space=search_space,
        minimum_bets=int(search_space["minimum_bets"]),
        maximum_drawdown_percent=float(search_space["maximum_drawdown_percent"]),
        minimum_bet_rate=minimum_bet_rate,
        maximum_bet_rate=maximum_bet_rate,
        minimum_baseline_roi_delta_percent=float(
            search_space.get("minimum_baseline_roi_delta_percent", 1.0)
        ),
        minimum_years_with_bets=int(search_space.get("minimum_years_with_bets", 1)),
        minimum_profitable_year_rate=float(
            search_space.get("minimum_profitable_year_rate", 0.0)
        ),
    )
    final_meta = WinnerProbabilityMetaModel().fit(inner)
    outer = outer.copy()
    outer["probability"] = final_meta.predict(outer)
    outer_metrics = None
    selected_condition = None
    if selection["selected"] is not None:
        selected_condition = StrategyCondition(**selection["selected"]["condition"])
        outer_metrics = evaluate_strategy(outer, selected_condition)
        outer_baseline = evaluate_baseline_strategy(outer)
        outer_metrics["baseline_metrics"] = outer_baseline
        outer_metrics["baseline_roi_delta_percent"] = float(
            outer_metrics["roi_percent"] - outer_baseline["roi_percent"]
        )

    staking_policy_digest = _canonical_digest(staking_policy)
    search_space_digest = _canonical_digest(search_space)
    decision_binding = {
        "candidate_commit_sha": inner_commit,
        "staking_policy_digest": staking_policy_digest,
        "strategy_search_space_digest": search_space_digest,
        "odds_policy": asdict(odds_policy),
        "selected_condition": (
            asdict(selected_condition) if selected_condition is not None else None
        ),
    }

    report: dict[str, object] = {
        "schema": REPORT_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "candidate_only": True,
        "approved": False,
        "activated": False,
        "deployed": False,
        "deployment_eligible": False,
        "target": "speed_deviation-to-winner-probability",
        "winner_meta_feature_policy": "market-free-oof-ability-only",
        "candidate_commit_sha": inner_commit,
        "odds_policy": asdict(odds_policy),
        "odds_evidence_contract": {
            "required_status": "middle",
            "quote_must_precede_race_start": True,
            "maximum_age_minutes": min(30, odds_policy.max_age_minutes),
            "source_required": True,
            "exact_commit_sha_required": True,
            "final_result_odds_forbidden": True,
        },
        "staking_policy": {
            "policy_id": staking_policy.get("policy_id"),
            "approval_reference": staking_policy.get("approval_reference"),
            "sha256": staking_policy_digest,
        },
        "strategy_search_space_sha256": search_space_digest,
        "policy_digest": staking_policy_digest,
        "evaluation_decision_digest": _canonical_digest(decision_binding),
        "inner_base_oof_period": {
            "start": inner_dates.min().date().isoformat(),
            "end": inner_dates.max().date().isoformat(),
        },
        "inner_input_evidence": _input_evidence(inner),
        "outer_period": {
            "start": outer_dates.min().date().isoformat(),
            "end": outer_dates.max().date().isoformat(),
        },
        "outer_input_evidence": _input_evidence(outer),
        "outer_rows_visible_during_strategy_selection": False,
        "meta_oof_folds": meta_folds,
        "nested_strategy_selection": selection,
        "selected_condition_fixed_before_outer_evaluation": (
            asdict(selected_condition) if selected_condition is not None else None
        ),
        "outer_metrics": outer_metrics,
        "limitations": [
            "research-only-unapproved-wagering-policy",
            "historical-screen-not-prospective-staging-evidence",
            "no-model-activation-or-deployment",
        ],
    }
    return report, final_meta


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate the OOF winner-probability and nested value layer."
    )
    parser.add_argument("--inner-base-oof", type=Path, required=True)
    parser.add_argument("--outer-base-predictions", type=Path, required=True)
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "reports" / "model-candidates"
    )
    parser.add_argument("--minimum-meta-training-years", type=int, default=2)
    parser.add_argument("--decision-offset-minutes", type=int, default=5)
    parser.add_argument("--max-odds-age-minutes", type=int, default=30)
    args = parser.parse_args(argv)
    search_space = json.loads(SEARCH_SPACE_PATH.read_text(encoding="utf-8"))
    if search_space.get("status") != "research-only-unapproved":
        raise ValueError("strategy search space must remain research-only-unapproved")
    staking_policy = json.loads(STAKING_POLICY_PATH.read_text(encoding="utf-8"))
    report, model = evaluate_oof_value_layer(
        inner_base_oof=_read_frame(args.inner_base_oof),
        outer_base_predictions=_read_frame(args.outer_base_predictions),
        search_space=search_space,
        staking_policy=staking_policy,
        odds_policy=PointInTimeOddsPolicy(
            decision_offset_minutes=args.decision_offset_minutes,
            max_age_minutes=args.max_odds_age_minutes,
        ),
        minimum_meta_training_years=args.minimum_meta_training_years,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = args.output_dir / f"phase3n_oof_value_{timestamp}_candidate.json"
    artifact_path = (
        args.output_dir / f"phase3n_oof_winner_meta_{timestamp}_candidate.joblib"
    )
    joblib.dump(
        {
            "model": model,
            "candidate_only": True,
            "approved": False,
            "activated": False,
            "deployed": False,
            "candidate_commit_sha": report["candidate_commit_sha"],
            "policy_digest": report["policy_digest"],
            "evaluation_decision_digest": report["evaluation_decision_digest"],
        },
        artifact_path,
    )
    report["candidate_artifact"] = str(artifact_path.resolve())
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"report": str(report_path.resolve()), **report},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
