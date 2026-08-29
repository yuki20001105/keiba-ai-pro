from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import joblib
import numpy as np
import pandas as pd
from lightgbm import Booster, Dataset, early_stopping, log_evaluation, train
from scipy.stats import spearmanr
from sklearn.metrics import mean_absolute_error, mean_squared_error, roc_auc_score


ROOT = Path(__file__).resolve().parents[1]
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


REPORT_SCHEMA = "speed-deviation-candidate-evaluation-v1"
STAKING_POLICY_PATH = ROOT / "config" / "phase3n_staking_payout_policy.v1.json"
STAKING_POLICY = json.loads(STAKING_POLICY_PATH.read_text(encoding="utf-8"))
ABILITY_EXCLUDED_MARKET_FIELDS = frozenset(
    {
        "odds",
        "popularity",
        "odds_observed_at",
        "odds_cutoff_at",
        "odds_age_minutes",
        "odds_source",
        "odds_snapshot_kind",
        "implied_prob_norm",
        "implied_prob",
        "odds_rank_in_race",
        "odds_z_in_race",
        "market_entropy",
        "top3_probability",
        "popularity_normalized",
        "tansho_implied_prob",
        "past5_avg_tansho_log",
    }
)


def _date_series(frame: pd.DataFrame) -> pd.Series:
    if "race_date" in frame.columns:
        raw = frame["race_date"].astype("string").str.strip()
    elif "date" in frame.columns:
        raw = frame["date"].astype("string").str.strip()
    else:
        raw = pd.Series("", index=frame.index, dtype="string")
    fallback = frame["race_id"].astype("string").str.slice(0, 8)
    raw = raw.where(raw.str.fullmatch(r"\d{8}", na=False), fallback)
    return pd.to_datetime(raw, format="%Y%m%d", errors="coerce")


def _finish_series(frame: pd.DataFrame) -> pd.Series:
    return pd.to_numeric(frame.get("finish"), errors="coerce")


def _softmax_by_race(frame: pd.DataFrame, score_column: str) -> pd.Series:
    def normalize(values: pd.Series) -> pd.Series:
        array = values.to_numpy(dtype=float)
        finite = np.isfinite(array)
        floor = float(array[finite].min() - 1.0) if finite.any() else -5.0
        safe = np.where(finite, array, floor)
        exponent = np.exp(np.clip(safe - safe.max(), -50.0, 50.0))
        total = float(exponent.sum())
        if total <= 0:
            return pd.Series(np.zeros(len(values)), index=values.index)
        return pd.Series(exponent / total, index=values.index)

    return frame.groupby("race_id", sort=False, group_keys=False)[score_column].apply(normalize)


def _ece(labels: np.ndarray, probabilities: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = len(labels)
    value = 0.0
    for index in range(bins):
        lower, upper = edges[index], edges[index + 1]
        selected = (probabilities >= lower) & (
            probabilities <= upper if index == bins - 1 else probabilities < upper
        )
        count = int(selected.sum())
        if count:
            value += count / total * abs(
                float(labels[selected].mean()) - float(probabilities[selected].mean())
            )
    return float(value)


def _strategy_metrics(
    evaluation: pd.DataFrame,
    *,
    selector: str,
    minimum_expected_value: float = 1.2,
    initial_bankroll: float = 100_000.0,
    stake: float = 100.0,
) -> dict[str, float | int]:
    if selector == "candidate":
        eligible = evaluation.copy()
        eligible["expected_value"] = eligible["probability"] * eligible["odds"]
        eligible = eligible[eligible["expected_value"].ge(minimum_expected_value)]
        chosen = (
            eligible.loc[eligible.groupby("race_id")["expected_value"].idxmax()].copy()
            if not eligible.empty
            else eligible.copy()
        )
    elif selector == "baseline":
        ranked = evaluation.sort_values(
            ["race_id", "popularity", "odds"],
            ascending=[True, True, True],
            na_position="last",
        )
        chosen = ranked.groupby("race_id", sort=False).head(1).copy()
    else:
        raise ValueError("selector is invalid")

    chosen = chosen.sort_values(["race_date", "race_id"])
    if chosen.empty:
        return {
            "bet_count": 0,
            "win_count": 0,
            "roi_percent": 0.0,
            "max_drawdown_percent": 0.0,
        }
    returns = np.where(chosen["winner"].eq(1), chosen["odds"] * stake, 0.0)
    pnl = returns - stake
    bankroll = initial_bankroll + np.cumsum(pnl)
    running_peak = np.maximum.accumulate(np.r_[initial_bankroll, bankroll])[:-1]
    drawdown = np.maximum(running_peak - bankroll, 0.0) / initial_bankroll * 100.0
    bet_count = len(chosen)
    return {
        "bet_count": int(bet_count),
        "win_count": int(chosen["winner"].sum()),
        "roi_percent": float((returns.sum() / (stake * bet_count) - 1.0) * 100.0),
        "max_drawdown_percent": float(drawdown.max()) if bet_count else 0.0,
    }


def _point_in_time_value_frame(evaluation: pd.DataFrame) -> pd.DataFrame:
    """Fail closed unless every runner has a validated decision-time quote."""

    required = {
        "race_id",
        "odds",
        "popularity",
        "odds_observed_at",
        "odds_cutoff_at",
        "odds_age_minutes",
        "odds_source",
        "odds_snapshot_kind",
    }
    if not required.issubset(evaluation.columns):
        return evaluation.iloc[0:0].copy()
    work = evaluation.copy()
    observed = pd.to_datetime(work["odds_observed_at"], utc=True, errors="coerce")
    cutoff = pd.to_datetime(work["odds_cutoff_at"], utc=True, errors="coerce")
    age = pd.to_numeric(work["odds_age_minutes"], errors="coerce")
    valid = (
        work[["odds", "popularity"]].notna().all(axis=1)
        & work["odds"].gt(1.0)
        & observed.notna()
        & cutoff.notna()
        & observed.le(cutoff)
        & age.between(0.0, 30.0)
        & work["odds_source"].fillna("").astype(str).str.strip().ne("")
        & work["odds_snapshot_kind"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
        .isin({"pre_race", "decision_time"})
    )
    complete_races = valid.groupby(work["race_id"].astype(str)).transform("all")
    return work.loc[complete_races].copy()


def _evaluate(
    validation_frame: pd.DataFrame,
    target: pd.Series,
    predictions: np.ndarray,
    *,
    probability_temperature: float = 1.0,
    require_value_data: bool = True,
    winner_scores: np.ndarray | None = None,
) -> tuple[dict[str, float | int | None], pd.DataFrame]:
    if probability_temperature <= 0:
        raise ValueError("probability_temperature must be positive")
    evaluation = validation_frame[["race_id"]].copy()
    evaluation["odds"] = pd.to_numeric(
        validation_frame["odds"] if "odds" in validation_frame else np.nan,
        errors="coerce",
    )
    evaluation["popularity"] = pd.to_numeric(
        validation_frame["popularity"] if "popularity" in validation_frame else np.nan,
        errors="coerce",
    )
    for column in (
        "odds_observed_at",
        "odds_cutoff_at",
        "odds_age_minutes",
        "odds_source",
        "odds_snapshot_kind",
    ):
        evaluation[column] = (
            validation_frame[column] if column in validation_frame else np.nan
        )
    evaluation["race_date"] = _date_series(validation_frame)
    evaluation["finish"] = _finish_series(validation_frame)
    evaluation["target"] = target.to_numpy(dtype=float)
    evaluation["score"] = np.asarray(predictions, dtype=float)
    if winner_scores is not None:
        winner_scores_array = np.asarray(winner_scores, dtype=float)
        if len(winner_scores_array) != len(evaluation):
            raise ValueError("winner_scores must align with validation_frame")
        evaluation["winner_score"] = winner_scores_array
    evaluation["winner"] = evaluation["finish"].eq(1).astype(int)

    complete_races: list[str] = []
    for race_id, group in evaluation.groupby("race_id", sort=False):
        if (
            len(group) >= 5
            and int(group["winner"].sum()) == 1
            and group[["finish", "target", "score"]].notna().all().all()
        ):
            complete_races.append(str(race_id))
    evaluation = evaluation[evaluation["race_id"].astype(str).isin(complete_races)].copy()
    if evaluation.empty:
        raise ValueError("no complete validation races are available")

    probability_source = "winner_score" if winner_scores is not None else "score"
    evaluation["probability_score"] = (
        evaluation[probability_source] / probability_temperature
    )
    evaluation["probability"] = _softmax_by_race(evaluation, "probability_score")
    labels = evaluation["winner"].to_numpy(dtype=int)
    probabilities = evaluation["probability"].to_numpy(dtype=float)
    minimum_expected_value = float(
        STAKING_POLICY["candidate"]["minimum_expected_value"]
    )
    value_evaluation = _point_in_time_value_frame(evaluation)
    if require_value_data and value_evaluation.empty:
        raise ValueError("no complete point-in-time odds races are available")
    candidate = (
        _strategy_metrics(
            value_evaluation,
            selector="candidate",
            minimum_expected_value=minimum_expected_value,
        )
        if not value_evaluation.empty
        else None
    )
    baseline = (
        _strategy_metrics(value_evaluation, selector="baseline")
        if not value_evaluation.empty
        else None
    )
    correlation = spearmanr(evaluation["target"], evaluation["score"]).statistic
    metrics: dict[str, float | int | None] = {
        "rmse": float(mean_squared_error(evaluation["target"], evaluation["score"]) ** 0.5),
        "mae": float(mean_absolute_error(evaluation["target"], evaluation["score"])),
        "spearman": float(correlation) if math.isfinite(float(correlation)) else 0.0,
        "winner_auc": float(roc_auc_score(labels, probabilities)),
        "brier_score": float(np.mean((probabilities - labels) ** 2)),
        "expected_calibration_error": _ece(labels, probabilities),
        "sample_count": int(len(evaluation)),
        "race_count": int(evaluation["race_id"].nunique()),
        "observation_period_days": int(
            (evaluation["race_date"].max() - evaluation["race_date"].min()).days + 1
        ),
        "point_in_time_value_race_count": int(value_evaluation["race_id"].nunique()),
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
        "minimum_expected_value": minimum_expected_value,
        "probability_temperature": float(probability_temperature),
    }
    return metrics, evaluation


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def train_candidate(
    *,
    database: Path,
    output_directory: Path,
    train_start: pd.Timestamp,
    train_end: pd.Timestamp,
    validation_start: pd.Timestamp,
    validation_end: pd.Timestamp,
    min_group_size: int,
    recency_half_life_years: float,
    num_boost_round: int,
    early_stopping_rounds: int,
) -> dict[str, Any]:
    if validation_start <= train_end:
        raise ValueError("validation_start must be after train_end")
    if recency_half_life_years <= 0:
        raise ValueError("recency_half_life_years must be positive")

    source = load_ultimate_training_frame(database)
    source = source.loc[:, ~source.columns.duplicated()].copy()
    source["_race_date"] = _date_series(source)
    source = source[source["_race_date"].between(train_start, validation_end)].copy()
    source = source.sort_values(["_race_date", "race_id", "horse_number"]).reset_index(drop=True)
    train_mask = source["_race_date"].between(train_start, train_end)
    validation_mask = source["_race_date"].between(validation_start, validation_end)
    training_source = source.loc[train_mask].copy()
    validation_source = source.loc[validation_mask].copy()
    if len(training_source) < 1_000 or len(validation_source) < 100:
        raise ValueError("training or validation observations are insufficient")

    baseline = fit_speed_deviation_baseline(
        training_source,
        min_group_size=min_group_size,
    )
    training_target = apply_speed_deviation_baseline(training_source, baseline)
    validation_target = apply_speed_deviation_baseline(validation_source, baseline)

    engineered = add_derived_features(source, full_history_df=source)
    engineered = engineered.loc[:, ~engineered.columns.duplicated()]
    training_features = engineered.loc[train_mask].copy()
    validation_features = engineered.loc[validation_mask].copy()
    training_valid = training_target.notna()
    validation_valid = validation_target.notna()
    training_features = training_features.loc[training_valid].copy()
    validation_features = validation_features.loc[validation_valid].copy()
    training_target = training_target.loc[training_valid].reset_index(drop=True)
    validation_target = validation_target.loc[validation_valid].reset_index(drop=True)
    validation_source = validation_source.loc[validation_valid].reset_index(drop=True)

    train_optimized, optimizer, categorical = prepare_for_lightgbm_ultimate(
        training_features,
        target_col="speed_deviation",
        is_training=True,
    )
    validation_optimized, _, _ = prepare_for_lightgbm_ultimate(
        validation_features,
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
    validation_x = validation_optimized.drop(
        columns=[column for column in excluded if column in validation_optimized.columns]
    )
    object_columns = train_x.select_dtypes(include=["object"]).columns.tolist()
    if object_columns:
        train_x = train_x.drop(columns=object_columns)
    for column in train_x.columns:
        if column not in validation_x.columns:
            validation_x[column] = np.nan
    validation_x = validation_x.loc[:, train_x.columns]
    categorical = [column for column in categorical if column in train_x.columns]

    age_years = (
        train_end - training_source.loc[training_valid, "_race_date"]
    ).dt.days.clip(lower=0).to_numpy(dtype=float) / 365.25
    weights = np.power(0.5, age_years / recency_half_life_years)
    params = {
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
    training_dataset = Dataset(
        train_x,
        label=training_target,
        weight=weights,
        categorical_feature=categorical,
        free_raw_data=False,
    )
    validation_dataset = Dataset(
        validation_x,
        label=validation_target,
        reference=training_dataset,
        categorical_feature=categorical,
        free_raw_data=False,
    )
    model: Booster = train(
        params,
        training_dataset,
        num_boost_round=num_boost_round,
        valid_sets=[validation_dataset],
        valid_names=["out_of_time"],
        callbacks=[
            early_stopping(early_stopping_rounds, verbose=True),
            log_evaluation(100),
        ],
    )
    predictions = model.predict(validation_x, num_iteration=model.best_iteration)
    metrics, evaluated = _evaluate(validation_source, validation_target, predictions)

    output_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = (
        f"model_speed_deviation_lightgbm_{train_start:%Y%m%d}_{train_end:%Y%m%d}_"
        f"{timestamp}_candidate"
    )
    artifact = output_directory / f"{stem}.joblib"
    bundle = {
        "model": model,
        "optimizer": optimizer,
        "calibrator": None,
        "categorical_features": categorical,
        "feature_columns": train_x.columns.tolist(),
        "target": "speed_deviation",
        "target_definition": "distance_divided_by_time_zscore_by_distance_surface",
        "speed_deviation_baseline": baseline,
        "model_type": "lightgbm_regression",
        "ultimate_mode": True,
        "use_optimizer": True,
        "pipeline_config": {
            "use_feature_engineering": True,
            "use_optimizer": True,
            "requires_full_history": True,
            "point_in_time": True,
        },
        "metrics": metrics,
        "data_count": int(len(training_target)),
        "race_count": int(training_source.loc[training_valid, "race_id"].nunique()),
        "created_at": timestamp,
        "training_date_from": train_start.date().isoformat(),
        "training_date_to": train_end.date().isoformat(),
        "validation_date_from": validation_start.date().isoformat(),
        "validation_date_to": validation_end.date().isoformat(),
        "recency_half_life_years": float(recency_half_life_years),
        "candidate_only": True,
    }
    joblib.dump(bundle, artifact)

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
        "database_sha256": _sha256(database),
        "artifact": str(artifact.resolve()),
        "artifact_sha256": _sha256(artifact),
        "target": bundle["target"],
        "target_definition": bundle["target_definition"],
        "staking_policy_id": STAKING_POLICY["policy_id"],
        "staking_policy_approval_reference": STAKING_POLICY["approval_reference"],
        "training_period": {
            "start": train_start.date().isoformat(),
            "end": train_end.date().isoformat(),
        },
        "validation_period": {
            "start": validation_start.date().isoformat(),
            "end": validation_end.date().isoformat(),
        },
        "training_sample_count": int(len(training_target)),
        "training_race_count": int(training_source.loc[training_valid, "race_id"].nunique()),
        "validation_complete_sample_count": int(len(evaluated)),
        "validation_complete_race_count": int(evaluated["race_id"].nunique()),
        "feature_count": int(len(train_x.columns)),
        "best_iteration": int(model.best_iteration),
        "recency_half_life_years": float(recency_half_life_years),
        "coverage_by_year": {
            str(int(year)): {
                "entries": int(row["entries"]),
                "races": int(row["races"]),
            }
            for year, row in coverage.iterrows()
            if pd.notna(year)
        },
        "metrics": metrics,
        "limitations": [
            "historical-screening-not-prospective-staging-evidence",
            "validation-window-has-been-observed-and-is-not-a-final-untouched-holdout",
            "source-coverage-is-sparse-for-2019-through-2024",
        ],
    }
    report_path = output_directory / f"{stem}.json"
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
        description="Train a non-activating speed-deviation LightGBM candidate with an OOT split."
    )
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports" / "model-candidates")
    parser.add_argument("--train-start", type=_timestamp, default=pd.Timestamp("2016-01-01"))
    parser.add_argument("--train-end", type=_timestamp, default=pd.Timestamp("2026-02-01"))
    parser.add_argument("--validation-start", type=_timestamp, default=pd.Timestamp("2026-02-02"))
    parser.add_argument("--validation-end", type=_timestamp, default=pd.Timestamp("2026-07-11"))
    parser.add_argument("--min-group-size", type=int, default=30)
    parser.add_argument("--recency-half-life-years", type=float, default=5.0)
    parser.add_argument("--num-boost-round", type=int, default=2_000)
    parser.add_argument("--early-stopping-rounds", type=int, default=100)
    args = parser.parse_args(argv)
    report = train_candidate(
        database=args.db.resolve(),
        output_directory=args.output_dir.resolve(),
        train_start=args.train_start,
        train_end=args.train_end,
        validation_start=args.validation_start,
        validation_end=args.validation_end,
        min_group_size=args.min_group_size,
        recency_half_life_years=args.recency_half_life_years,
        num_boost_round=args.num_boost_round,
        early_stopping_rounds=args.early_stopping_rounds,
    )
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
