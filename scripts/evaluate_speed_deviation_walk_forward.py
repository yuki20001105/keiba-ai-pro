from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd
from lightgbm import Booster, Dataset, early_stopping, log_evaluation, train
from scipy.stats import spearmanr
from scipy.optimize import minimize_scalar
from sklearn.metrics import mean_absolute_error, mean_squared_error, roc_auc_score


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "keiba"))

from keiba_ai.constants import FUTURE_FIELDS, ID_COLUMNS  # noqa: E402
from keiba_ai.db_ultimate_loader import load_ultimate_training_frame  # noqa: E402
from keiba_ai.feature_engineering import add_derived_features  # noqa: E402
from keiba_ai.lightgbm_feature_optimizer import (  # noqa: E402
    prepare_for_lightgbm_ultimate,
)
from keiba_ai.speed_deviation import (  # noqa: E402
    apply_speed_deviation_baseline,
    fit_speed_deviation_baseline,
)
from scripts.train_speed_deviation_candidate import (  # noqa: E402
    STAKING_POLICY,
    _date_series,
    _ece,
    _evaluate,
    _softmax_by_race,
    _strategy_metrics,
)


REPORT_SCHEMA = "speed-deviation-walk-forward-evaluation-v1"
ACCEPTANCE_CONTRACT_PATH = ROOT / "config" / "model_acceptance_contract.v1.json"
DEFAULT_FOLDS = (2016, 2017, 2018, 2025)


def _params() -> dict[str, Any]:
    return {
        "objective": "regression",
        "metric": "rmse",
        "learning_rate": 0.03,
        "num_leaves": 63,
        "min_data_in_leaf": 50,
        "feature_fraction": 0.85,
        "bagging_fraction": 0.85,
        "bagging_freq": 1,
        "reg_alpha": 0.1,
        "reg_lambda": 0.2,
        "verbosity": -1,
        "seed": 42,
        "feature_fraction_seed": 42,
        "bagging_seed": 42,
    }


def _prepare_matrices(
    training_features: pd.DataFrame,
    evaluation_features: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    train_optimized, optimizer, categorical = prepare_for_lightgbm_ultimate(
        training_features,
        target_col="speed_deviation",
        is_training=True,
    )
    evaluation_optimized, _, _ = prepare_for_lightgbm_ultimate(
        evaluation_features,
        target_col="speed_deviation",
        is_training=False,
        optimizer=optimizer,
    )
    excluded = set(FUTURE_FIELDS) | set(ID_COLUMNS) | {
        "speed_deviation",
        "_race_date",
        "race_date",
        "date",
    }
    train_x = train_optimized.drop(
        columns=[column for column in excluded if column in train_optimized.columns]
    )
    evaluation_x = evaluation_optimized.drop(
        columns=[column for column in excluded if column in evaluation_optimized.columns]
    )
    object_columns = train_x.select_dtypes(include=["object"]).columns.tolist()
    if object_columns:
        train_x = train_x.drop(columns=object_columns)
    for column in train_x.columns:
        if column not in evaluation_x.columns:
            evaluation_x[column] = np.nan
    evaluation_x = evaluation_x.loc[:, train_x.columns]
    categorical = [column for column in categorical if column in train_x.columns]
    return train_x, evaluation_x, categorical


def _weights(
    dates: pd.Series,
    cutoff: pd.Timestamp,
    recency_half_life_years: float,
) -> np.ndarray:
    age_years = (
        cutoff - dates
    ).dt.days.clip(lower=0).to_numpy(dtype=float) / 365.25
    return np.power(0.5, age_years / recency_half_life_years)


def _select_probability_temperature(evaluated: pd.DataFrame) -> float:
    def objective(log_temperature: float) -> float:
        temperature = math.exp(log_temperature)
        candidate = evaluated[["race_id", "score", "winner"]].copy()
        candidate["scaled_score"] = candidate["score"] / temperature
        probabilities = _softmax_by_race(candidate, "scaled_score")
        winner_probabilities = probabilities[candidate["winner"].eq(1)]
        return float(-np.log(np.clip(winner_probabilities, 1e-12, 1.0)).mean())

    result = minimize_scalar(
        objective,
        bounds=(math.log(0.05), math.log(20.0)),
        method="bounded",
        options={"xatol": 1e-4},
    )
    if not result.success:
        raise RuntimeError("inner probability temperature optimization failed")
    return float(math.exp(float(result.x)))


def _fit_fold(
    *,
    source: pd.DataFrame,
    engineered: pd.DataFrame,
    validation_year: int,
    train_start: pd.Timestamp,
    tuning_fraction: float,
    min_group_size: int,
    recency_half_life_years: float,
    num_boost_round: int,
    early_stopping_rounds: int,
) -> tuple[dict[str, Any], pd.DataFrame]:
    validation_start = pd.Timestamp(f"{validation_year}-01-01")
    validation_end = pd.Timestamp(f"{validation_year}-12-31")
    train_end = validation_start - pd.Timedelta(days=1)
    training_mask = source["_race_date"].between(train_start, train_end)
    validation_mask = source["_race_date"].between(validation_start, validation_end)
    training_source = source.loc[training_mask].copy()
    validation_source = source.loc[validation_mask].copy()
    if len(training_source) < 1_000 or len(validation_source) < 100:
        raise ValueError(f"fold {validation_year} has insufficient observations")

    unique_dates = training_source["_race_date"].dropna().drop_duplicates().sort_values()
    tuning_index = min(
        max(int(len(unique_dates) * (1.0 - tuning_fraction)), 1),
        len(unique_dates) - 1,
    )
    tuning_start = pd.Timestamp(unique_dates.iloc[tuning_index])
    inner_fit_source = training_source[training_source["_race_date"].lt(tuning_start)].copy()
    inner_tuning_source = training_source[
        training_source["_race_date"].ge(tuning_start)
    ].copy()

    inner_baseline = fit_speed_deviation_baseline(
        inner_fit_source,
        min_group_size=min_group_size,
    )
    inner_fit_target = apply_speed_deviation_baseline(inner_fit_source, inner_baseline)
    inner_tuning_target = apply_speed_deviation_baseline(
        inner_tuning_source,
        inner_baseline,
    )
    inner_fit_valid = inner_fit_target.notna()
    inner_tuning_valid = inner_tuning_target.notna()
    inner_fit_features = engineered.loc[inner_fit_source.index].loc[inner_fit_valid].copy()
    inner_tuning_features = (
        engineered.loc[inner_tuning_source.index].loc[inner_tuning_valid].copy()
    )
    inner_fit_target = inner_fit_target.loc[inner_fit_valid].reset_index(drop=True)
    inner_tuning_target = inner_tuning_target.loc[inner_tuning_valid].reset_index(drop=True)
    inner_train_x, inner_tuning_x, inner_categorical = _prepare_matrices(
        inner_fit_features,
        inner_tuning_features,
    )
    inner_train_set = Dataset(
        inner_train_x,
        label=inner_fit_target,
        weight=_weights(
            inner_fit_source.loc[inner_fit_valid, "_race_date"],
            train_end,
            recency_half_life_years,
        ),
        categorical_feature=inner_categorical,
        free_raw_data=False,
    )
    inner_tuning_set = Dataset(
        inner_tuning_x,
        label=inner_tuning_target,
        reference=inner_train_set,
        categorical_feature=inner_categorical,
        free_raw_data=False,
    )
    inner_model: Booster = train(
        _params(),
        inner_train_set,
        num_boost_round=num_boost_round,
        valid_sets=[inner_tuning_set],
        valid_names=["inner_tuning"],
        callbacks=[
            early_stopping(early_stopping_rounds, verbose=False),
            log_evaluation(0),
        ],
    )
    selected_iterations = max(int(inner_model.best_iteration), 1)
    inner_predictions = inner_model.predict(
        inner_tuning_x,
        num_iteration=selected_iterations,
    )
    inner_tuning_evaluation_source = inner_tuning_source.loc[
        inner_tuning_valid
    ].reset_index(drop=True)
    _, inner_evaluated = _evaluate(
        inner_tuning_evaluation_source,
        inner_tuning_target,
        inner_predictions,
    )
    probability_temperature = _select_probability_temperature(inner_evaluated)

    final_baseline = fit_speed_deviation_baseline(
        training_source,
        min_group_size=min_group_size,
    )
    training_target = apply_speed_deviation_baseline(training_source, final_baseline)
    validation_target = apply_speed_deviation_baseline(validation_source, final_baseline)
    training_valid = training_target.notna()
    validation_valid = validation_target.notna()
    training_features = engineered.loc[training_source.index].loc[training_valid].copy()
    validation_features = engineered.loc[validation_source.index].loc[validation_valid].copy()
    training_target = training_target.loc[training_valid].reset_index(drop=True)
    validation_target = validation_target.loc[validation_valid].reset_index(drop=True)
    validation_source = validation_source.loc[validation_valid].reset_index(drop=True)
    train_x, validation_x, categorical = _prepare_matrices(
        training_features,
        validation_features,
    )
    final_training_set = Dataset(
        train_x,
        label=training_target,
        weight=_weights(
            training_source.loc[training_valid, "_race_date"],
            train_end,
            recency_half_life_years,
        ),
        categorical_feature=categorical,
        free_raw_data=False,
    )
    model: Booster = train(
        _params(),
        final_training_set,
        num_boost_round=selected_iterations,
        callbacks=[log_evaluation(0)],
    )
    predictions = model.predict(validation_x, num_iteration=selected_iterations)
    metrics, evaluated = _evaluate(
        validation_source,
        validation_target,
        predictions,
        probability_temperature=probability_temperature,
    )
    fold = {
        "validation_year": validation_year,
        "training_period": {
            "start": train_start.date().isoformat(),
            "end": train_end.date().isoformat(),
        },
        "inner_tuning_period": {
            "start": tuning_start.date().isoformat(),
            "end": train_end.date().isoformat(),
        },
        "training_sample_count": int(len(training_target)),
        "training_race_count": int(
            training_source.loc[training_valid, "race_id"].nunique()
        ),
        "inner_tuning_sample_count": int(len(inner_tuning_target)),
        "selected_iterations": selected_iterations,
        "probability_temperature": probability_temperature,
        "feature_count": int(len(train_x.columns)),
        "future_feature_intersection": sorted(set(train_x.columns) & set(FUTURE_FIELDS)),
        "metrics": metrics,
    }
    evaluated["validation_year"] = validation_year
    return fold, evaluated


def _aggregate(evaluated: pd.DataFrame) -> dict[str, float | int]:
    labels = evaluated["winner"].to_numpy(dtype=int)
    probabilities = evaluated["probability"].to_numpy(dtype=float)
    candidate = _strategy_metrics(
        evaluated,
        selector="candidate",
        minimum_expected_value=float(
            STAKING_POLICY["candidate"]["minimum_expected_value"]
        ),
    )
    baseline = _strategy_metrics(evaluated, selector="baseline")
    correlation = spearmanr(evaluated["target"], evaluated["score"]).statistic
    return {
        "rmse": float(mean_squared_error(evaluated["target"], evaluated["score"]) ** 0.5),
        "mae": float(mean_absolute_error(evaluated["target"], evaluated["score"])),
        "spearman": float(correlation) if math.isfinite(float(correlation)) else 0.0,
        "winner_auc": float(roc_auc_score(labels, probabilities)),
        "brier_score": float(np.mean((probabilities - labels) ** 2)),
        "expected_calibration_error": _ece(labels, probabilities),
        "sample_count": int(len(evaluated)),
        "race_count": int(evaluated["race_id"].nunique()),
        "candidate_bet_count": int(candidate["bet_count"]),
        "candidate_win_count": int(candidate["win_count"]),
        "candidate_roi_percent": float(candidate["roi_percent"]),
        "candidate_max_drawdown_percent": float(candidate["max_drawdown_percent"]),
        "baseline_roi_percent": float(baseline["roi_percent"]),
        "roi_delta_to_baseline_percent": float(
            float(candidate["roi_percent"]) - float(baseline["roi_percent"])
        ),
        "minimum_expected_value": float(
            STAKING_POLICY["candidate"]["minimum_expected_value"]
        ),
    }


def _gate_results(metrics: dict[str, float | int]) -> dict[str, Any]:
    contract = json.loads(ACCEPTANCE_CONTRACT_PATH.read_text(encoding="utf-8"))
    mappings = {
        "auc": "winner_auc",
        "brier_score": "brier_score",
        "expected_calibration_error": "expected_calibration_error",
        "roi_percent": "candidate_roi_percent",
        "max_drawdown_percent": "candidate_max_drawdown_percent",
        "bet_count": "candidate_bet_count",
        "sample_count": "sample_count",
        "baseline_roi_delta_percent": "roi_delta_to_baseline_percent",
    }
    results: dict[str, Any] = {}
    for contract_name, metric_name in mappings.items():
        threshold = contract["thresholds"][contract_name]
        actual = float(metrics[metric_name])
        passed = (
            actual >= float(threshold["value"])
            if threshold["operator"] == "gte"
            else actual <= float(threshold["value"])
        )
        results[contract_name] = {
            "metric": metric_name,
            "actual": actual,
            "operator": threshold["operator"],
            "threshold": float(threshold["value"]),
            "passed": passed,
        }
    for unavailable in ("p95_latency_ms", "data_freshness_minutes", "observation_period_days"):
        results[unavailable] = {
            "passed": None,
            "reason": "not-a-prospective-runtime-metric",
        }
    return results


def evaluate_walk_forward(
    *,
    database: Path,
    output_directory: Path,
    train_start: pd.Timestamp,
    validation_years: Sequence[int],
    tuning_fraction: float,
    min_group_size: int,
    recency_half_life_years: float,
    num_boost_round: int,
    early_stopping_rounds: int,
) -> dict[str, Any]:
    if not 0.05 <= tuning_fraction <= 0.4:
        raise ValueError("tuning_fraction must be between 0.05 and 0.4")
    source = load_ultimate_training_frame(database)
    source = source.loc[:, ~source.columns.duplicated()].copy()
    source["_race_date"] = _date_series(source)
    last_validation_end = pd.Timestamp(f"{max(validation_years)}-12-31")
    source = source[
        source["_race_date"].between(train_start, last_validation_end)
    ].copy()
    source = source.sort_values(
        ["_race_date", "race_id", "horse_number"]
    ).reset_index(drop=True)
    engineered = add_derived_features(source, full_history_df=source)
    engineered = engineered.loc[:, ~engineered.columns.duplicated()]

    folds: list[dict[str, Any]] = []
    evaluated_folds: list[pd.DataFrame] = []
    for year in validation_years:
        fold, evaluated = _fit_fold(
            source=source,
            engineered=engineered,
            validation_year=year,
            train_start=train_start,
            tuning_fraction=tuning_fraction,
            min_group_size=min_group_size,
            recency_half_life_years=recency_half_life_years,
            num_boost_round=num_boost_round,
            early_stopping_rounds=early_stopping_rounds,
        )
        folds.append(fold)
        evaluated_folds.append(evaluated)
        print(
            f"fold={year} auc={fold['metrics']['winner_auc']:.4f} "
            f"spearman={fold['metrics']['spearman']:.4f} "
            f"roi={fold['metrics']['candidate_roi_percent']:.2f}%",
            flush=True,
        )

    evaluated = pd.concat(evaluated_folds, ignore_index=True)
    aggregate = _aggregate(evaluated)
    gates = _gate_results(aggregate)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    coverage = (
        source.assign(year=source["_race_date"].dt.year)
        .groupby("year", sort=True)
        .agg(entries=("race_id", "size"), races=("race_id", "nunique"))
    )
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "created_at": timestamp,
        "candidate_only": True,
        "activated": False,
        "target": "speed_deviation",
        "target_definition": "distance_divided_by_time_zscore_by_distance_surface",
        "walk_forward_policy": {
            "outer_validation_years": list(validation_years),
            "inner_tuning_fraction": tuning_fraction,
            "outer_fold_never_used_for_iteration_selection": True,
            "feature_policy_fixed_before_outer_validation": True,
        },
        "staking_policy_id": STAKING_POLICY["policy_id"],
        "staking_policy_approval_reference": STAKING_POLICY["approval_reference"],
        "folds": folds,
        "aggregate_metrics": aggregate,
        "historical_gate_results": gates,
        "historical_model_business_gate_passed": all(
            result["passed"] is True
            for result in gates.values()
            if result["passed"] is not None
        ),
        "coverage_by_year": {
            str(int(year)): {
                "entries": int(row["entries"]),
                "races": int(row["races"]),
            }
            for year, row in coverage.iterrows()
            if pd.notna(year)
        },
        "excluded_final_unknown_period": "after-2026-07-11",
        "limitations": [
            "historical-development-screen-not-prospective-staging-evidence",
            "2019-through-2024-source-coverage-is-insufficient-for-annual-folds",
            "outer-fold-results-are-now-observed-and-cannot-be-reused-as-final-unknown-data",
            "runtime-latency-freshness-and-90-day-observation-gates-are-not-evaluated",
        ],
    }
    output_directory.mkdir(parents=True, exist_ok=True)
    report_path = output_directory / f"speed_deviation_walk_forward_{timestamp}.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report["report"] = str(report_path.resolve())
    return report


def _timestamp(value: str) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if pd.isna(result):
        raise argparse.ArgumentTypeError("date is invalid")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run strict annual walk-forward evaluation for speed-deviation regression."
    )
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "reports" / "model-candidates",
    )
    parser.add_argument("--train-start", type=_timestamp, default=pd.Timestamp("2013-01-01"))
    parser.add_argument("--validation-years", type=int, nargs="+", default=DEFAULT_FOLDS)
    parser.add_argument("--tuning-fraction", type=float, default=0.15)
    parser.add_argument("--min-group-size", type=int, default=30)
    parser.add_argument("--recency-half-life-years", type=float, default=5.0)
    parser.add_argument("--num-boost-round", type=int, default=1_500)
    parser.add_argument("--early-stopping-rounds", type=int, default=75)
    args = parser.parse_args(argv)
    report = evaluate_walk_forward(
        database=args.db.resolve(),
        output_directory=args.output_dir.resolve(),
        train_start=args.train_start,
        validation_years=args.validation_years,
        tuning_fraction=args.tuning_fraction,
        min_group_size=args.min_group_size,
        recency_half_life_years=args.recency_half_life_years,
        num_boost_round=args.num_boost_round,
        early_stopping_rounds=args.early_stopping_rounds,
    )
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
