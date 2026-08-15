"""Run the Phase3N OOF probability/value layer without activating a model.

This stage intentionally consumes base speed-model predictions rather than
training that base model itself.  The input contract makes the two leakage
boundaries auditable: inner rows must be base-model OOF predictions and all
odds must carry a valid pre-race observation timestamp.
"""

from __future__ import annotations

import argparse
import json
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
REPORT_SCHEMA = "phase3n-oof-value-evaluation-v1"


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
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError("prediction frame is missing: " + ", ".join(sorted(missing)))
    if frame[["race_id", "horse_id"]].duplicated().any():
        raise ValueError("prediction frame contains duplicate race_id/horse_id rows")
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
        raise ValueError("one or more races lack complete fresh point-in-time odds")
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
    odds_policy: PointInTimeOddsPolicy,
    minimum_meta_training_years: int = 2,
) -> tuple[dict[str, object], WinnerProbabilityMetaModel]:
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

    meta_oof, meta_folds = _meta_oof_probabilities(
        inner,
        minimum_training_years=minimum_meta_training_years,
    )
    selection = select_nested_strategy(
        meta_oof,
        search_space=search_space,
        minimum_bets=int(search_space["minimum_bets"]),
        maximum_drawdown_percent=float(search_space["maximum_drawdown_percent"]),
    )
    final_meta = WinnerProbabilityMetaModel().fit(inner)
    outer = outer.copy()
    outer["probability"] = final_meta.predict(outer)
    outer_metrics = None
    selected_condition = None
    if selection["selected"] is not None:
        selected_condition = StrategyCondition(**selection["selected"]["condition"])
        outer_metrics = evaluate_strategy(outer, selected_condition)

    report: dict[str, object] = {
        "schema": REPORT_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "candidate_only": True,
        "approved": False,
        "activated": False,
        "deployed": False,
        "deployment_eligible": False,
        "target": "speed_deviation-to-winner-probability",
        "odds_policy": asdict(odds_policy),
        "inner_base_oof_period": {
            "start": inner_dates.min().date().isoformat(),
            "end": inner_dates.max().date().isoformat(),
        },
        "outer_period": {
            "start": outer_dates.min().date().isoformat(),
            "end": outer_dates.max().date().isoformat(),
        },
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
    report, model = evaluate_oof_value_layer(
        inner_base_oof=_read_frame(args.inner_base_oof),
        outer_base_predictions=_read_frame(args.outer_base_predictions),
        search_space=search_space,
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
