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
    raw_speed_mps,
)
from scripts.train_speed_deviation_candidate import (  # noqa: E402
    ABILITY_EXCLUDED_MARKET_FIELDS,
    STAKING_POLICY,
    _date_series,
    _ece,
    _evaluate,
    _point_in_time_value_frame,
    _softmax_by_race,
    _strategy_metrics,
)


REPORT_SCHEMA = "speed-deviation-walk-forward-evaluation-v2"
ACCEPTANCE_CONTRACT_PATH = ROOT / "config" / "model_acceptance_contract.v1.json"
DEFAULT_FOLDS = (2016, 2017, 2018, 2025)

WINNER_META_FEATURES = frozenset(
    {
        "horse_number",
        "frame_number",
        "distance",
        "num_horses",
        "race_number",
        "carried_weight",
        "horse_weight",
        "horse_weight_change",
        "days_since_last_race",
        "prev_race_finish",
        "prev2_race_finish",
        "prev3_race_finish",
        "prev4_race_finish",
        "prev5_race_finish",
        "prev_race_distance",
        "prev2_race_distance",
        "prev_speed_index",
        "prev_speed_zscore",
        "prev2_speed_index",
        "prev2_speed_zscore",
        "recent_form_weighted",
        "form_trend",
        "speed_index_change",
        "prior_speed_mps_last",
        "prior_speed_mps_mean_3",
        "prior_speed_mps_mean_5",
        "prior_speed_mps_std_5",
        "prior_speed_mps_career_mean",
        "prior_speed_mps_surface_mean",
        "prior_speed_mps_distance_band_mean",
        "prior_speed_mps_trend",
        "prior_speed_observation_count",
        "horse_distance_win_rate",
        "horse_surface_win_rate",
        "horse_venue_win_rate",
        "past3_avg_finish",
        "past3_win_rate",
        "past5_win_rate",
        "jockey_course_win_rate",
        "jockey_recent30_win_rate",
        "trainer_recent30_win_rate",
        "jockey_show_rate",
        "trainer_show_rate",
        "sire_win_rate",
        "sire_show_rate",
        "damsire_win_rate",
        "damsire_show_rate",
        "venue_encoded",
        "venue_code_encoded",
        "field_condition_encoded",
        "race_class_encoded",
        "sex_encoded",
        "date_month",
        "date_dayofweek",
    }
)


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
    excluded = set(FUTURE_FIELDS) | set(ID_COLUMNS) | set(ABILITY_EXCLUDED_MARKET_FIELDS) | {
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


def _add_prior_speed_history(source: pd.DataFrame) -> pd.DataFrame:
    """Add strictly shifted realized-speed history; the current result is never used."""
    if "horse_id" not in source.columns:
        return source.copy()
    result = source.copy()
    horse = result["horse_id"].astype("string").str.strip()
    missing_horse = horse.isna() | horse.eq("")
    if missing_horse.any():
        horse = horse.mask(
            missing_horse,
            pd.Series(
                [f"__missing_horse_{index}" for index in result.index],
                index=result.index,
                dtype="string",
            ),
        )
    speed = raw_speed_mps(result)
    prior = speed.groupby(horse, sort=False).shift(1)
    prior_grouped = prior.groupby(horse, sort=False)
    result["prior_speed_mps_last"] = prior
    result["prior_speed_mps_mean_3"] = prior_grouped.transform(
        lambda values: values.rolling(3, min_periods=1).mean()
    )
    result["prior_speed_mps_mean_5"] = prior_grouped.transform(
        lambda values: values.rolling(5, min_periods=1).mean()
    )
    result["prior_speed_mps_std_5"] = prior_grouped.transform(
        lambda values: values.rolling(5, min_periods=2).std()
    )
    result["prior_speed_mps_career_mean"] = prior_grouped.transform(
        lambda values: values.expanding(min_periods=1).mean()
    )
    result["prior_speed_observation_count"] = prior.notna().groupby(
        horse, sort=False
    ).cumsum()

    if "surface" in result.columns:
        surface = result["surface"].astype("string").fillna("unknown")
    elif "track_type" in result.columns:
        surface = result["track_type"].astype("string").fillna("unknown")
    else:
        surface = pd.Series("unknown", index=result.index, dtype="string")
    surface_prior = speed.groupby([horse, surface], sort=False).shift(1)
    result["prior_speed_mps_surface_mean"] = surface_prior.groupby(
        [horse, surface], sort=False
    ).transform(lambda values: values.expanding(min_periods=1).mean())

    distance = pd.to_numeric(result.get("distance"), errors="coerce")
    distance_band = (distance // 400 * 400).astype("Int64").astype("string")
    distance_prior = speed.groupby([horse, distance_band], sort=False).shift(1)
    result["prior_speed_mps_distance_band_mean"] = distance_prior.groupby(
        [horse, distance_band], sort=False
    ).transform(lambda values: values.expanding(min_periods=1).mean())
    result["prior_speed_mps_trend"] = (
        result["prior_speed_mps_last"] - result["prior_speed_mps_mean_3"]
    )
    return result


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


def _winner_meta_matrix(
    base_features: pd.DataFrame,
    race_ids: pd.Series,
    speed_scores: np.ndarray,
) -> pd.DataFrame:
    """Build a market-free winner matrix from pre-race fields and speed OOF scores."""
    if len(base_features) != len(race_ids) or len(base_features) != len(speed_scores):
        raise ValueError("winner meta inputs must have identical lengths")
    selected = [column for column in base_features.columns if column in WINNER_META_FEATURES]
    matrix = base_features.loc[:, selected].reset_index(drop=True).copy()
    race = race_ids.astype("string").reset_index(drop=True)
    score = pd.Series(np.asarray(speed_scores, dtype=float), name="speed_score")
    grouped = score.groupby(race, sort=False)
    group_mean = grouped.transform("mean")
    group_std = grouped.transform("std").replace(0.0, np.nan)
    matrix["speed_score"] = score
    matrix["speed_score_race_z"] = (score - group_mean) / group_std
    matrix["speed_gap_to_best"] = score - grouped.transform("max")
    matrix["speed_rank_percentile"] = grouped.rank(
        method="average", ascending=False, pct=True
    )
    matrix["field_size"] = race.groupby(race, sort=False).transform("size").astype(float)
    return matrix.replace([np.inf, -np.inf], np.nan)


def _fit_winner_meta(
    *,
    inner_features: pd.DataFrame,
    inner_source: pd.DataFrame,
    inner_speed_scores: np.ndarray,
    validation_features: pd.DataFrame,
    validation_source: pd.DataFrame,
    validation_speed_scores: np.ndarray,
) -> tuple[np.ndarray, float, dict[str, Any]]:
    """Fit an inner-period OOF winner model and score the untouched outer year."""
    inner_source = inner_source.reset_index(drop=True)
    validation_source = validation_source.reset_index(drop=True)
    inner_x = _winner_meta_matrix(
        inner_features,
        inner_source["race_id"],
        inner_speed_scores,
    )
    validation_x = _winner_meta_matrix(
        validation_features,
        validation_source["race_id"],
        validation_speed_scores,
    )
    for column in inner_x.columns:
        if column not in validation_x.columns:
            validation_x[column] = np.nan
    validation_x = validation_x.loc[:, inner_x.columns]

    labels = pd.to_numeric(inner_source["finish"], errors="coerce").eq(1).astype(int)
    race_ids = inner_source["race_id"].astype("string")
    race_sizes = race_ids.groupby(race_ids, sort=False).transform("size")
    race_winners = labels.groupby(race_ids, sort=False).transform("sum")
    usable = race_sizes.ge(5) & race_winners.eq(1)
    inner_x = inner_x.loc[usable].reset_index(drop=True)
    labels = labels.loc[usable].reset_index(drop=True)
    race_ids = race_ids.loc[usable].reset_index(drop=True)
    dates = inner_source.loc[usable, "_race_date"].reset_index(drop=True)

    unique_dates = dates.dropna().drop_duplicates().sort_values()
    if len(unique_dates) < 10 or len(inner_x) < 1_000:
        raise ValueError("winner meta inner period is insufficient")
    split_index = min(max(int(len(unique_dates) * 0.7), 1), len(unique_dates) - 1)
    calibration_start = pd.Timestamp(unique_dates.iloc[split_index])
    fit_mask = dates.lt(calibration_start)
    calibration_mask = dates.ge(calibration_start)
    if int(fit_mask.sum()) < 500 or int(calibration_mask.sum()) < 200:
        raise ValueError("winner meta fit/calibration split is insufficient")

    params = {
        "objective": "binary",
        "metric": "binary_logloss",
        "learning_rate": 0.03,
        "num_leaves": 15,
        "min_data_in_leaf": 100,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "reg_alpha": 0.2,
        "reg_lambda": 0.5,
        "verbosity": -1,
        "seed": 42,
        "feature_fraction_seed": 42,
        "bagging_seed": 42,
    }
    fit_set = Dataset(
        inner_x.loc[fit_mask].reset_index(drop=True),
        label=labels.loc[fit_mask].reset_index(drop=True),
        free_raw_data=False,
    )
    calibration_set = Dataset(
        inner_x.loc[calibration_mask].reset_index(drop=True),
        label=labels.loc[calibration_mask].reset_index(drop=True),
        reference=fit_set,
        free_raw_data=False,
    )
    selector: Booster = train(
        params,
        fit_set,
        num_boost_round=800,
        valid_sets=[calibration_set],
        valid_names=["winner_meta_calibration"],
        callbacks=[early_stopping(50, verbose=False), log_evaluation(0)],
    )
    selected_iterations = max(int(selector.best_iteration), 1)
    calibration_scores = selector.predict(
        inner_x.loc[calibration_mask].reset_index(drop=True),
        num_iteration=selected_iterations,
        raw_score=True,
    )
    calibration_evaluation = pd.DataFrame(
        {
            "race_id": race_ids.loc[calibration_mask].reset_index(drop=True),
            "score": calibration_scores,
            "winner": labels.loc[calibration_mask].reset_index(drop=True),
        }
    )
    temperature = _select_probability_temperature(calibration_evaluation)

    full_set = Dataset(inner_x, label=labels, free_raw_data=False)
    model: Booster = train(
        params,
        full_set,
        num_boost_round=selected_iterations,
        callbacks=[log_evaluation(0)],
    )
    validation_scores = model.predict(
        validation_x,
        num_iteration=selected_iterations,
        raw_score=True,
    )
    metadata = {
        "training_sample_count": int(len(inner_x)),
        "training_race_count": int(race_ids.nunique()),
        "calibration_start": calibration_start.date().isoformat(),
        "selected_iterations": selected_iterations,
        "probability_temperature": temperature,
        "feature_count": int(len(inner_x.columns)),
        "features": list(inner_x.columns),
        "market_feature_intersection": sorted(
            set(inner_x.columns) & set(ABILITY_EXCLUDED_MARKET_FIELDS)
        ),
    }
    return np.asarray(validation_scores, dtype=float), temperature, metadata


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
    winner_scores, probability_temperature, winner_meta = _fit_winner_meta(
        inner_features=inner_tuning_x,
        inner_source=inner_tuning_evaluation_source,
        inner_speed_scores=inner_predictions,
        validation_features=validation_x,
        validation_source=validation_source,
        validation_speed_scores=predictions,
    )
    metrics, evaluated = _evaluate(
        validation_source,
        validation_target,
        predictions,
        probability_temperature=probability_temperature,
        require_value_data=False,
        winner_scores=winner_scores,
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
        "winner_meta": winner_meta,
        "feature_count": int(len(train_x.columns)),
        "future_feature_intersection": sorted(set(train_x.columns) & set(FUTURE_FIELDS)),
        "market_feature_intersection": sorted(
            set(train_x.columns) & set(ABILITY_EXCLUDED_MARKET_FIELDS)
        ),
        "metrics": metrics,
    }
    evaluated["validation_year"] = validation_year
    return fold, evaluated


def _aggregate(evaluated: pd.DataFrame) -> dict[str, float | int | None]:
    labels = evaluated["winner"].to_numpy(dtype=int)
    probabilities = evaluated["probability"].to_numpy(dtype=float)
    value_evaluated = _point_in_time_value_frame(evaluated)
    candidate = (
        _strategy_metrics(
            value_evaluated,
            selector="candidate",
            minimum_expected_value=float(
                STAKING_POLICY["candidate"]["minimum_expected_value"]
            ),
        )
        if not value_evaluated.empty
        else None
    )
    baseline = (
        _strategy_metrics(value_evaluated, selector="baseline")
        if not value_evaluated.empty
        else None
    )
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
        "point_in_time_value_race_count": int(value_evaluated["race_id"].nunique()),
        "candidate_bet_count": int(candidate["bet_count"]) if candidate else None,
        "candidate_win_count": int(candidate["win_count"]) if candidate else None,
        "candidate_roi_percent": float(candidate["roi_percent"]) if candidate else None,
        "candidate_max_drawdown_percent": (
            float(candidate["max_drawdown_percent"]) if candidate else None
        ),
        "baseline_roi_percent": float(baseline["roi_percent"]) if baseline else None,
        "roi_delta_to_baseline_percent": (
            float(candidate["roi_percent"]) - float(baseline["roi_percent"])
            if candidate and baseline
            else None
        ),
        "minimum_expected_value": float(
            STAKING_POLICY["candidate"]["minimum_expected_value"]
        ),
    }


def _gate_results(metrics: dict[str, float | int | None]) -> dict[str, Any]:
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
        raw_actual = metrics[metric_name]
        if raw_actual is None:
            results[contract_name] = {
                "metric": metric_name,
                "actual": None,
                "operator": threshold["operator"],
                "threshold": float(threshold["value"]),
                "passed": False,
                "reason": "point-in-time-odds-unavailable",
            }
            continue
        actual = float(raw_actual)
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
    source = _add_prior_speed_history(source)
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
            "roi="
            + (
                f"{fold['metrics']['candidate_roi_percent']:.2f}%"
                if fold["metrics"]["candidate_roi_percent"] is not None
                else "unavailable"
            ),
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
            "prior_speed_features_strictly_shifted": True,
            "winner_probability_source": "inner-period-oof-speed-winner-meta",
            "winner_meta_outer_year_never_seen": True,
            "ability_market_fields_excluded": sorted(ABILITY_EXCLUDED_MARKET_FIELDS),
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
            "official-result-pdfs-have-limited-pre-race-feature-depth",
            "point-in-time-odds-are-unavailable-for-2019-through-2024",
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
