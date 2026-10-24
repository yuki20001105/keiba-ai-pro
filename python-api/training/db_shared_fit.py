"""DB-only, fixed-feature speed training with honest chronological calibration.

This module receives already prepared point-in-time features. It never opens a
database, fetches data, publishes an artifact, changes an active model, or trains
H04. Preset selection uses an inner TRAIN forward split; CAL fits one scalar
race-softmax temperature; untouched TEST determines probability validation.
Accuracy and probability validation are separate from executable betting ROI.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import threading
import time
from typing import Callable

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from scipy.special import logsumexp

from experiments import features as feature_source
from experiments.training import FoldEncoder, _inverse_speed
from keiba_ai.speed_deviation import apply_speed_deviation_baseline, fit_speed_deviation_baseline, raw_speed_mps


FEATURE_PIPELINE = "local-db-shared-v1"
INPUT_CONTRACT_VERSION = "local-db-shared-v1"
PROBABILITY_VALIDATION_SCHEMA = "local-db-probability-validation-v1"
CALIBRATOR_SCHEMA = "local-db-race-temperature-v1"
FEATURE_COLUMNS = tuple(feature_source.FEATURE_GROUPS["base"] + feature_source.HISTORY_COLUMNS)
CATEGORICAL_COLUMNS = tuple(feature_source.BASE_CATEGORICAL)
CALIBRATION_FRACTION = 0.20
PRESETS = (
    {"id": "lean", "num_boost_round": 120, "num_leaves": 15, "min_data_in_leaf": 60, "lambda_l2": 1.0},
    {"id": "standard", "num_boost_round": 200, "num_leaves": 31, "min_data_in_leaf": 30, "lambda_l2": 1.0},
)
# Fixed before any new fit/results. These are safety/accuracy checks, not evidence
# of profitability, market advantage, complete historical coverage or live odds.
PROBABILITY_GATES = {
    "minimum_calibration_races": 100,
    "minimum_calibration_dates": 10,
    "minimum_validation_races": 200,
    "minimum_validation_dates": 20,
    "maximum_selection_ece": 0.08,
    "maximum_selection_absolute_bias": 0.05,
    "selection_bins": 10,
    "maximum_nll_delta_vs_uniform": 0.0,
    "maximum_brier_delta_vs_uniform": 0.0,
    "temperature_bounds": [0.02, 10.0],
    "temperature_log_boundary_tolerance": 0.01,
}
_HELPER_PINS = {
    "experiments.training": "d4b2cc042425951aa65706ba382d38084f06f97a28701be7776574a1d645a828",
    "experiments.features": "6ae59cc7643e353d3229430acc30e99bedd6c03c12af47d90e4854a6fdd67423",
    "keiba_ai.speed_deviation": "c36c29a145855aa9caadead996970d81f644f99a2c534f932ae1e70aa5f4f3e6",
}


def source_hashes() -> dict[str, str]:
    """Actual shared-feature, fitted helper and fit-module byte identities."""
    from training.db_shared_features import source_hashes as feature_hashes
    import experiments.training as training_module
    import keiba_ai.speed_deviation as speed_module
    values = dict(feature_hashes())
    for module in (training_module, feature_source, speed_module):
        digest = hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
        if digest != _HELPER_PINS[module.__name__]:
            raise ValueError("db-shared-frozen-training-helper-changed")
        values[module.__name__] = digest
    values["training.db_shared_fit"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return values


def _prepare(target_frame: pd.DataFrame, features: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    required = {"race_id", "race_date", "horse_id", "horse_number", "finish", "time_seconds",
                "distance", "surface", "venue", "num_horses"}
    if not isinstance(target_frame, pd.DataFrame) or not isinstance(features, pd.DataFrame):
        raise ValueError("db-shared-dataframes-required")
    if (target_frame.empty or not required.issubset(target_frame.columns)
            or target_frame.columns.duplicated().any() or features.columns.duplicated().any()
            or list(features.columns) != list(FEATURE_COLUMNS)
            or not target_frame.index.is_unique or not target_frame.index.equals(features.index)):
        raise ValueError("db-shared-aligned-fixed47-input-required")
    target = target_frame.copy()
    target["race_date"] = target["race_date"].astype("string")
    dates = pd.to_datetime(target.race_date, format="%Y%m%d", errors="coerce")
    if dates.isna().any() or not target.race_date.str.fullmatch(r"\d{8}").fillna(False).all():
        raise ValueError("db-shared-valid-whole-dates-required")
    target["race_id"] = target.race_id.astype("string").str.strip()
    target["horse_id"] = target.horse_id.astype("string").fillna("").str.strip()
    if target.race_id.isna().any() or target.race_id.eq("").any():
        raise ValueError("db-shared-race-identity-required")
    number = pd.to_numeric(target.horse_number, errors="coerce")
    if (not np.isfinite(number).all() or not number.between(1, 18).all()
            or not number.eq(np.floor(number)).all()):
        raise ValueError("db-shared-runner-number-invalid")
    target["horse_number"] = number.astype(int)
    if target.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("db-shared-duplicate-runner")
    known = target.loc[target.horse_id.ne("")]
    if known.duplicated(["race_id", "horse_id"]).any():
        raise ValueError("db-shared-duplicate-horse-identity")
    sizes = target.groupby("race_id").race_id.transform("size")
    if (sizes.lt(2).any() or not pd.to_numeric(target.num_horses, errors="coerce").eq(sizes).all()):
        raise ValueError("db-shared-complete-runner-cohort-required")
    for column in ("race_date", "distance", "surface", "venue"):
        if target.groupby("race_id")[column].nunique(dropna=False).ne(1).any():
            raise ValueError(f"db-shared-inconsistent-race-{column}")
    finish = pd.to_numeric(target.finish, errors="coerce")
    if (not np.isfinite(finish).all() or not finish.between(1, sizes).all()
            or not finish.eq(np.floor(finish)).all()
            or not target.assign(_winner=finish.eq(1)).groupby("race_id")._winner.any().all()
            or not np.isfinite(raw_speed_mps(target)).all()):
        raise ValueError("db-shared-confirmed-complete-targets-required")
    target["finish"] = finish.astype(int)
    order = target.assign(_position=np.arange(len(target))).sort_values(
        ["race_date", "race_id", "horse_number", "horse_id"], kind="stable",
    )["_position"].to_numpy()
    return target.iloc[order].reset_index(drop=True), features.iloc[order].reset_index(drop=True).copy()


def date_partitions(target: pd.DataFrame, *, test_size: float = 0.2) -> dict[str, np.ndarray]:
    if isinstance(test_size, bool) or not isinstance(test_size, (int, float)) or not 0.10 <= test_size <= 0.50:
        raise ValueError("db-shared-test-size-must-be-0.10-to-0.50")
    dates = np.sort(target.race_date.astype(str).unique())
    train_end = int(math.floor(len(dates) * (1 - CALIBRATION_FRACTION - float(test_size))))
    calibration_end = int(math.floor(len(dates) * (1 - float(test_size))))
    if train_end < 5 or calibration_end <= train_end or calibration_end >= len(dates):
        raise ValueError("db-shared-insufficient-dates-for-train-calibration-test")
    result = {
        "TRAIN": np.flatnonzero(target.race_date.astype(str).isin(dates[:train_end]).to_numpy()),
        "CAL": np.flatnonzero(target.race_date.astype(str).isin(dates[train_end:calibration_end]).to_numpy()),
        "TEST": np.flatnonzero(target.race_date.astype(str).isin(dates[calibration_end:]).to_numpy()),
    }
    return result


def _fitted_fixed_encoder(frame: pd.DataFrame) -> tuple[FoldEncoder, pd.DataFrame, dict]:
    encoder = FoldEncoder(list(FEATURE_COLUMNS), list(CATEGORICAL_COLUMNS))
    encoder.fit_transform(frame)
    diagnosed = dict(encoder.dropped_columns)
    # This new pipeline fixes all 47 schema columns. Train-only category fitting
    # remains unchanged; constant/all-missing columns remain constant/NaN, not 0.
    encoder.feature_columns = list(FEATURE_COLUMNS)
    encoded = encoder.transform(frame)
    return encoder, encoded, {"training_column_diagnostics": diagnosed,
                              "retain_fixed47_constant_or_missing_columns": True}


def _resource_limits(n_threads: int, features: pd.DataFrame) -> tuple[int, int]:
    if isinstance(n_threads, bool) or not isinstance(n_threads, int) or not 1 <= n_threads <= 8:
        raise ValueError("db-shared-native-thread-budget-must-be-1-to-8")
    try:
        import psutil
    except ImportError:
        # Without a memory probe, do not infer spare RAM or start two fits.
        # The stdlib CPU count only adds a conservative native-thread cap.
        return 1, max(1, min(4, n_threads, os.cpu_count() or 1))
    physical = max(1, psutil.cpu_count(logical=False) or 1)
    budget = min(8, physical, n_threads * 2)
    estimated_copy_bytes = max(512 * 1024**2, int(features.memory_usage(deep=True).sum()) * 6)
    available = psutil.virtual_memory().available
    parallel = 2 if budget >= 2 and available >= 2 * estimated_copy_bytes + 2 * 1024**3 else 1
    return parallel, max(1, min(4, n_threads, budget // parallel))


def _train_booster(encoded: pd.DataFrame, target: np.ndarray, preset: dict, *, n_threads: int,
                   progress: Callable[[str, int], None] | None = None, progress_start: int = 0):
    import lightgbm as lgb
    params = {
        "objective": "regression", "metric": "rmse", "learning_rate": 0.05,
        "num_leaves": preset["num_leaves"], "min_data_in_leaf": preset["min_data_in_leaf"],
        "lambda_l2": preset["lambda_l2"], "max_bin": 63, "feature_fraction": 1.0,
        "bagging_fraction": 1.0, "seed": 42, "data_random_seed": 42,
        "feature_fraction_seed": 42, "bagging_seed": 42, "deterministic": True,
        "force_col_wise": True, "device_type": "cpu", "num_threads": n_threads, "verbosity": -1,
    }
    dataset = lgb.Dataset(encoded, label=target, categorical_feature=list(CATEGORICAL_COLUMNS), free_raw_data=True)
    def callback(environment):
        if progress and environment.iteration % 20 == 0:
            progress(f"{preset['id']}: 学習 {environment.iteration + 1}/{preset['num_boost_round']}",
                     min(progress_start + 15, progress_start + int(15 * (environment.iteration + 1) / preset['num_boost_round'])))
    return lgb.train(params, dataset, num_boost_round=preset["num_boost_round"], callbacks=[callback])


def _finite_predictions(model, encoded: pd.DataFrame, n_threads: int) -> np.ndarray:
    values = np.asarray(model.predict(encoded, num_threads=n_threads), dtype=float)
    if values.shape != (len(encoded),) or not np.isfinite(values).all():
        raise ValueError("db-shared-invalid-model-predictions")
    return values


def accuracy_metrics(target: pd.DataFrame, raw_scores: np.ndarray, baseline: dict) -> dict:
    raw_scores = np.asarray(raw_scores, dtype=float)
    if raw_scores.shape != (len(target),) or not np.isfinite(raw_scores).all() or target.empty:
        raise ValueError("db-shared-accuracy-scores-invalid")
    frame = target[["race_id", "horse_number", "finish"]].copy().reset_index(drop=True)
    frame["score"] = raw_scores
    frame["truth_rank"] = -pd.to_numeric(frame.finish)
    first = frame.groupby("race_id").score.rank(method="average")
    second = frame.groupby("race_id").truth_rank.rank(method="average")
    first -= first.groupby(frame.race_id).transform("mean")
    second -= second.groupby(frame.race_id).transform("mean")
    numerator = (first * second).groupby(frame.race_id).sum()
    denominator = np.sqrt((first ** 2).groupby(frame.race_id).sum() * (second ** 2).groupby(frame.race_id).sum())
    correlations = (numerator / denominator.where(denominator.gt(0))).fillna(0)
    physical = _inverse_speed(raw_scores, target, baseline)
    actual_speed = raw_speed_mps(target).to_numpy()
    actual_target = apply_speed_deviation_baseline(target, baseline).to_numpy()
    physical_error = physical - actual_speed
    top = frame.sort_values(["race_id", "score", "horse_number"], ascending=[True, False, True], kind="stable").groupby("race_id", sort=False).head(1)
    variation = float(np.sum((actual_speed - np.mean(actual_speed)) ** 2))
    return {
        "race_count": int(frame.race_id.nunique()), "runner_count": len(frame),
        "rank_correlation": float(correlations.mean()), "constant_score_or_result_races": int(denominator.eq(0).sum()),
        "rmse_speed_mps": float(np.sqrt(np.mean(physical_error ** 2))),
        "mae_speed_mps": float(np.mean(np.abs(physical_error))),
        "r2_speed_mps": float(1 - np.sum(physical_error ** 2) / variation) if variation > 0 else None,
        "rmse_speed_deviation": float(np.sqrt(np.mean((raw_scores - actual_target) ** 2))),
        "top_pick_win_rate": float(top.finish.eq(1).mean()),
    }


def race_softmax(raw_scores: np.ndarray, race_ids, temperature: float) -> np.ndarray:
    values = np.asarray(raw_scores, dtype=float)
    raw_ids = np.asarray(race_ids, dtype=object)
    if raw_ids.shape != values.shape or pd.isna(raw_ids).any():
        raise ValueError("db-shared-race-softmax-input-invalid")
    ids = raw_ids.astype(str)
    if (values.ndim != 1 or not len(values) or ids.shape != values.shape or not np.isfinite(values).all()
            or not np.isfinite(temperature) or temperature <= 0 or any(not value.strip() for value in ids)):
        raise ValueError("db-shared-race-softmax-input-invalid")
    result = np.empty_like(values)
    for race_id in np.unique(ids):
        positions = np.flatnonzero(ids == race_id)
        if len(positions) < 2:
            raise ValueError("db-shared-softmax-complete-race-required")
        logits = values[positions] / float(temperature)
        result[positions] = np.exp(logits - logsumexp(logits))
    return result


@dataclass(frozen=True)
class RaceTemperatureCalibrator:
    temperature: float
    schema: str = CALIBRATOR_SCHEMA

    def predict_proba(self, raw_scores: np.ndarray, race_ids=None) -> np.ndarray:
        values = np.asarray(raw_scores, dtype=float)
        return race_softmax(values, np.zeros(len(values), dtype=int) if race_ids is None else race_ids, self.temperature)

    def transform(self, raw_scores: np.ndarray, race_ids=None) -> np.ndarray:
        return self.predict_proba(raw_scores, race_ids)

    def predict_logits(self, raw_scores: np.ndarray, race_ids=None) -> np.ndarray:
        """Compatibility name: inputs are regression logits, output race probs."""
        return self.predict_proba(raw_scores, race_ids)


def _single_winner_mask(target: pd.DataFrame) -> np.ndarray:
    winners = target.finish.eq(1).groupby(target.race_id).transform("sum")
    return winners.eq(1).to_numpy()


def fit_temperature(target: pd.DataFrame, raw_scores: np.ndarray) -> tuple[RaceTemperatureCalibrator | None, dict]:
    if np.asarray(raw_scores).shape != (len(target),) or not np.isfinite(raw_scores).all():
        raise ValueError("db-shared-calibration-scores-invalid")
    mask = _single_winner_mask(target)
    cohort = target.loc[mask].reset_index(drop=True)
    scores = np.asarray(raw_scores, dtype=float)[mask]
    audit = {"total_races": int(target.race_id.nunique()), "eligible_races": int(cohort.race_id.nunique()),
             "excluded_dead_heat_races": int(target.race_id.nunique() - cohort.race_id.nunique()),
             "eligible_dates": int(cohort.race_date.nunique()), "success": False, "boundary_reached": False,
             "temperature": None, "calibration_parameter_count": 1, "fit_uses_test": False}
    if cohort.race_id.nunique() < 2:
        audit["reason_codes"] = ["calibration_too_few_single_winner_races"]
        return None, audit
    race_groups = list(cohort.groupby("race_id", sort=True).indices.values())
    logits = [scores[positions] for positions in race_groups]
    winner_positions = [int(np.flatnonzero(cohort.iloc[positions].finish.eq(1).to_numpy())[0]) for positions in race_groups]
    def nll(log_temperature):
        temperature = math.exp(float(log_temperature))
        return float(np.mean([logsumexp(values / temperature) - values[winner] / temperature
                              for values, winner in zip(logits, winner_positions)]))
    bounds = np.log(PROBABILITY_GATES["temperature_bounds"])
    fitted = minimize_scalar(nll, bounds=(float(bounds[0]), float(bounds[1])), method="bounded",
                             options={"xatol": 1e-6, "maxiter": 100})
    if not fitted.success or not np.isfinite(fitted.x) or not np.isfinite(fitted.fun):
        audit["reason_codes"] = ["calibration_optimization_failed"]
        return None, audit
    temperature = float(math.exp(float(fitted.x)))
    near_boundary = min(float(fitted.x) - bounds[0], bounds[1] - float(fitted.x)) <= PROBABILITY_GATES["temperature_log_boundary_tolerance"]
    audit.update(success=True, boundary_reached=bool(near_boundary), temperature=temperature,
                 calibration_nll=float(fitted.fun), optimizer="bounded_scalar_multinomial_nll",
                 reason_codes=["calibration_temperature_boundary"] if near_boundary else [])
    return RaceTemperatureCalibrator(temperature), audit


def probability_metrics(target: pd.DataFrame, probabilities: np.ndarray) -> dict:
    probabilities = np.asarray(probabilities, dtype=float)
    if (probabilities.shape != (len(target),) or not np.isfinite(probabilities).all()
            or (probabilities < 0).any() or (probabilities > 1).any()):
        raise ValueError("db-shared-probability-values-invalid")
    all_sums = pd.Series(probabilities).groupby(target.race_id.reset_index(drop=True)).sum()
    if not np.allclose(all_sums.to_numpy(), 1.0, rtol=0, atol=1e-10):
        raise ValueError("db-shared-probabilities-not-race-normalized")
    mask = _single_winner_mask(target)
    cohort = target.loc[mask, ["race_id", "race_date", "horse_number", "finish"]].reset_index(drop=True)
    cohort["probability"] = probabilities[mask]
    nll, brier, uniform_nll, uniform_brier, picked = [], [], [], [], []
    for _race_id, race in cohort.groupby("race_id", sort=True):
        p = race.probability.to_numpy()
        outcome = race.finish.eq(1).to_numpy(dtype=float)
        nll.append(float(-np.log(max(float(p[outcome.astype(bool)][0]), 1e-15))))
        brier.append(float(np.sum((p - outcome) ** 2)))
        uniform_nll.append(float(np.log(len(race))))
        uniform_brier.append(float(1 - 1 / len(race)))
        top = race.sort_values(["probability", "horse_number"], ascending=[False, True], kind="stable").iloc[0]
        picked.append((float(top.probability), float(top.finish == 1)))
    bins = []
    ece = 0.0
    for number in range(PROBABILITY_GATES["selection_bins"]):
        lower, upper = number / 10, (number + 1) / 10
        entries = [(p, y) for p, y in picked if p >= lower and (p <= upper if number == 9 else p < upper)]
        if entries:
            predicted, outcomes = np.asarray(entries).T
            confidence, observed = float(predicted.mean()), float(outcomes.mean())
            ece += len(entries) / len(picked) * abs(confidence - observed)
        else:
            confidence = observed = None
        bins.append({"lower": lower, "upper": upper, "count": len(entries),
                     "mean_predicted": confidence, "observed_win_rate": observed})
    selection_bias = float(np.mean([p - y for p, y in picked])) if picked else None
    return {
        "race_count": len(nll), "runner_count": len(cohort), "date_count": int(cohort.race_date.nunique()),
        "total_races": int(target.race_id.nunique()),
        "excluded_dead_heat_races": int(target.race_id.nunique() - cohort.race_id.nunique()),
        "nll": float(np.mean(nll)) if nll else None, "brier": float(np.mean(brier)) if brier else None,
        "uniform_nll": float(np.mean(uniform_nll)) if nll else None,
        "uniform_brier": float(np.mean(uniform_brier)) if brier else None,
        "selection_ece": float(ece) if picked else None,
        "selection_bias": selection_bias,
        "selection_absolute_bias": abs(selection_bias) if selection_bias is not None else None,
        "selection_calibration_bins": bins,
    }


def validate_probabilities(calibration_audit: dict, test_metrics: dict | None, cal: pd.DataFrame, test: pd.DataFrame) -> dict:
    reasons = list(calibration_audit.get("reason_codes", []))
    checks = {
        "temperature_fit_succeeded": calibration_audit.get("success") is True,
        "temperature_not_at_boundary": calibration_audit.get("success") is True and calibration_audit.get("boundary_reached") is False,
        "calibration_races_sufficient": calibration_audit.get("eligible_races", 0) >= PROBABILITY_GATES["minimum_calibration_races"],
        "calibration_dates_sufficient": calibration_audit.get("eligible_dates", 0) >= PROBABILITY_GATES["minimum_calibration_dates"],
        "validation_races_sufficient": bool(test_metrics and test_metrics["race_count"] >= PROBABILITY_GATES["minimum_validation_races"]),
        "validation_dates_sufficient": bool(test_metrics and test_metrics["date_count"] >= PROBABILITY_GATES["minimum_validation_dates"]),
        "nll_not_worse_than_uniform": bool(test_metrics and test_metrics["nll"] is not None and test_metrics["nll"] - test_metrics["uniform_nll"] <= PROBABILITY_GATES["maximum_nll_delta_vs_uniform"]),
        "brier_not_worse_than_uniform": bool(test_metrics and test_metrics["brier"] is not None and test_metrics["brier"] - test_metrics["uniform_brier"] <= PROBABILITY_GATES["maximum_brier_delta_vs_uniform"]),
        "selection_ece_within_bound": bool(test_metrics and test_metrics["selection_ece"] is not None and test_metrics["selection_ece"] <= PROBABILITY_GATES["maximum_selection_ece"]),
        "selection_bias_within_bound": bool(test_metrics and test_metrics["selection_absolute_bias"] is not None and test_metrics["selection_absolute_bias"] <= PROBABILITY_GATES["maximum_selection_absolute_bias"]),
    }
    reasons.extend(name for name, passed in checks.items() if not passed)
    passed = all(checks.values())
    iso_date = lambda value: pd.to_datetime(str(value), format="%Y%m%d").strftime("%Y-%m-%d")
    return {
        "schema": PROBABILITY_VALIDATION_SCHEMA, "status": "validated" if passed else "unverified",
        "calibrated": calibration_audit.get("success") is True, "validated": passed, "eligible_for_ev": passed,
        "metrics": {key: test_metrics.get(key) if test_metrics else None for key in ("nll", "brier", "selection_ece")},
        "reason_codes": sorted(set(reasons)), "race_count": test_metrics["race_count"] if test_metrics else 0,
        "calibration_period_start": iso_date(cal.race_date.min()), "calibration_period_end": iso_date(cal.race_date.max()),
        "validation_period_start": iso_date(test.race_date.min()), "validation_period_end": iso_date(test.race_date.max()),
        "calibration_date_count": int(calibration_audit.get("eligible_dates", 0)),
        "validation_date_count": int(test_metrics["date_count"]) if test_metrics else 0,
        "gates": dict(PROBABILITY_GATES), "gate_results": checks,
        "score_is_win_probability": passed, "market_edge_verified": False, "profitability_verified": False,
        "definitions": {"nll": "macro single-winner race multinomial negative log likelihood",
                        "brier": "macro race sum_i (p_i - winner_i)^2",
                        "selection_ece": "weighted absolute top1 confidence minus hit rate in fixed ten bins",
                        "cohort": "complete runner races; dead heats excluded as whole races only from calibration/probability metrics"},
    }


def _partition_receipt(frame: pd.DataFrame) -> dict:
    return {"date_from": str(frame.race_date.min()), "date_to": str(frame.race_date.max()),
            "date_count": int(frame.race_date.nunique()), "race_count": int(frame.race_id.nunique()), "runner_count": len(frame)}


def _frame_digest(frame: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    digest.update("\0".join(str(column) for column in frame.columns).encode())
    digest.update(pd.util.hash_pandas_object(frame, index=False).to_numpy(dtype=np.uint64).tobytes())
    return digest.hexdigest()


def fit_shared_model(target_frame: pd.DataFrame, features: pd.DataFrame, *, test_size: float = 0.2,
                     n_threads: int = 4, progress: Callable[[str, int], None] | None = None) -> tuple[dict, dict]:
    """Fit in memory only; the caller owns snapshot/lease and atomic publication."""
    started = time.perf_counter()
    before_sources = source_hashes()
    target, inputs = _prepare(target_frame, features)
    splits = date_partitions(target, test_size=test_size)
    parts = {name: target.iloc[positions].reset_index(drop=True) for name, positions in splits.items()}
    xs = {name: inputs.iloc[positions].reset_index(drop=True) for name, positions in splits.items()}
    callback_lock = threading.Lock()
    def notify(stage: str, percent: int):
        if progress:
            with callback_lock:
                progress(stage, percent)
    notify("学習・校正・検証を日付で分割", 5)
    train_dates = np.sort(parts["TRAIN"].race_date.astype(str).unique())
    inner_boundary = max(2, min(len(train_dates) - 1, int(math.floor(len(train_dates) * 0.8))))
    inner_train_mask = parts["TRAIN"].race_date.astype(str).isin(train_dates[:inner_boundary]).to_numpy()
    inner_validation_mask = ~inner_train_mask
    inner_train = parts["TRAIN"].loc[inner_train_mask].reset_index(drop=True)
    inner_validation = parts["TRAIN"].loc[inner_validation_mask].reset_index(drop=True)
    inner_encoder, inner_x, _ = _fitted_fixed_encoder(xs["TRAIN"].loc[inner_train_mask].reset_index(drop=True))
    inner_validation_x = inner_encoder.transform(xs["TRAIN"].loc[inner_validation_mask].reset_index(drop=True))
    inner_baseline = fit_speed_deviation_baseline(inner_train)
    inner_y = apply_speed_deviation_baseline(inner_train, inner_baseline).to_numpy()
    jobs, native = _resource_limits(n_threads, inner_x)
    def trial(preset):
        clock = time.perf_counter()
        model = _train_booster(inner_x, inner_y, preset, n_threads=native, progress=notify, progress_start=10)
        raw = _finite_predictions(model, inner_validation_x, native)
        metrics = accuracy_metrics(inner_validation, raw, inner_baseline)
        return {"preset": dict(preset), "metrics": metrics, "elapsed_seconds": time.perf_counter() - clock}
    notify(f"固定2候補を比較（CPU {jobs}並列・各{native}スレッド）", 10)
    if jobs == 2:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="db-shared-fit") as executor:
            trials = list(executor.map(trial, PRESETS))
    else:
        trials = [trial(preset) for preset in PRESETS]
    best_rho = max(row["metrics"]["rank_correlation"] for row in trials)
    shortlist = [row for row in trials if best_rho - row["metrics"]["rank_correlation"] <= 0.002]
    selected = min(shortlist, key=lambda row: (row["metrics"]["rmse_speed_mps"], row["preset"]["num_boost_round"], row["preset"]["id"]))
    notify("TRAIN内の順位相関・RMSEで候補を確定", 30)
    encoder, x_train, encoder_audit = _fitted_fixed_encoder(xs["TRAIN"])
    baseline = fit_speed_deviation_baseline(parts["TRAIN"])
    y_train = apply_speed_deviation_baseline(parts["TRAIN"], baseline).to_numpy()
    final_native = min(4, n_threads, native * jobs)
    model = _train_booster(x_train, y_train, selected["preset"], n_threads=final_native, progress=notify, progress_start=35)
    notify("独立CAL期間で勝率の温度を校正", 55)
    cal_raw = _finite_predictions(model, encoder.transform(xs["CAL"]), final_native)
    calibrator, calibration_audit = fit_temperature(parts["CAL"], cal_raw)
    notify("未使用TEST期間で順位・速度誤差・校正を評価", 70)
    test_raw = _finite_predictions(model, encoder.transform(xs["TEST"]), final_native)
    test_accuracy = accuracy_metrics(parts["TEST"], test_raw, baseline)
    test_probability = (probability_metrics(parts["TEST"], calibrator.predict_proba(test_raw, parts["TEST"].race_id))
                        if calibrator is not None else None)
    probability_validation = validate_probabilities(calibration_audit, test_probability, parts["CAL"], parts["TEST"])
    time_slices = []
    for year, positions in parts["TEST"].groupby(parts["TEST"].race_date.astype(str).str[:4], sort=True).indices.items():
        metrics = accuracy_metrics(parts["TEST"].iloc[positions], test_raw[positions], baseline)
        time_slices.append({"period": str(year), "race_count": metrics["race_count"],
                            "rank_correlation": metrics["rank_correlation"], "rmse": metrics["rmse_speed_mps"],
                            "top_pick_win_rate": metrics["top_pick_win_rate"], "win_roi": None})
    if source_hashes() != before_sources:
        raise ValueError("db-shared-source-changed-during-fit")
    receipts = {name: _partition_receipt(frame) for name, frame in parts.items()}
    evaluation = {
        "schema": "model-evaluation-v1", "target": "speed_deviation", "rmse_unit": "m/s",
        "primary": {"rank_correlation": test_accuracy["rank_correlation"], "rmse": test_accuracy["rmse_speed_mps"],
                    "top_pick_win_rate": test_accuracy["top_pick_win_rate"], "favorite_win_rate": None,
                    "top_pick_win_rate_delta": None, "win_roi": None},
        "details": {"mae": test_accuracy["mae_speed_mps"], "r2": test_accuracy["r2_speed_mps"],
                    "rmse_speed_deviation": test_accuracy["rmse_speed_deviation"],
                    "evaluation_date_from": receipts["TEST"]["date_from"], "evaluation_date_to": receipts["TEST"]["date_to"],
                    "evaluation_race_count": test_accuracy["race_count"], "evaluation_runner_count": test_accuracy["runner_count"],
                    "constant_score_or_result_races": test_accuracy["constant_score_or_result_races"]},
        "time_slices": time_slices, "evaluation_label": "日付分割TEST（既知データの事後検証）· 回収率は別評価",
        "probability_validation": probability_validation,
        "definitions": {"rank_correlation": "macro per-race Spearman(predicted speed score, -finish); constant vectors count as zero",
                        "rmse": "horse-weighted physical-speed RMSE m/s using TRAIN-only speed baseline inverse",
                        "selection": "preset selection uses TRAIN-internal forward validation only; CAL fits temperature; TEST never changes fitting"},
    }
    input_contract = {
        "version": INPUT_CONTRACT_VERSION, "feature_pipeline": FEATURE_PIPELINE,
        "feature_columns": list(FEATURE_COLUMNS), "requested_feature_columns": list(FEATURE_COLUMNS),
        "categorical_columns": list(CATEGORICAL_COLUMNS), "source_hashes": before_sources,
        "input_feature_sha256": _frame_digest(inputs),
        "target_cohort_sha256": _frame_digest(target[["race_id", "race_date", "horse_id", "horse_number", "finish", "time_seconds", "distance"]]),
        "history_policy": "strict source_race_date < target_race_date; same-day outcomes excluded",
        "fit_feature_selection": False, "market_features": False, "missing_value_policy": "retain NaN; TRAIN-only categorical dictionary",
        "live_capture_timing_verified": False,
    }
    bundle = {
        "schema": "local-db-shared-model-v1", "target": "speed_deviation", "model_type": "lightgbm",
        "model": model, "encoder": encoder, "baseline": baseline, "feature_columns": list(FEATURE_COLUMNS),
        "feature_cols_num": [name for name in FEATURE_COLUMNS if name not in CATEGORICAL_COLUMNS],
        "feature_cols_cat": list(CATEGORICAL_COLUMNS), "feature_pipeline": FEATURE_PIPELINE,
        "model_input_contract": input_contract, "calibrator": calibrator, "optimizer": None,
        "source_hashes": before_sources,
        "probability_validation": probability_validation, "evaluation": evaluation,
        "ultimate_mode": True, "training_date_from": receipts["TRAIN"]["date_from"],
        "training_date_to": receipts["TRAIN"]["date_to"], "data_count": len(parts["TRAIN"]),
        "race_count": receipts["TRAIN"]["race_count"],
    }
    result = {
        "schema": "local-db-shared-fit-result-v1", "feature_pipeline": FEATURE_PIPELINE,
        "partitions": receipts, "inner_training": _partition_receipt(inner_train),
        "inner_validation": _partition_receipt(inner_validation), "preset_trials": trials,
        "selected_preset": selected["preset"]["id"],
        "selection_rule": "TRAIN-internal macro Spearman first; within 0.002 use physical RMSE, then fewer rounds",
        "test_used_for_selection_or_fit": False, "cal_used_for_preset_selection": False,
        "parallel_workers": jobs, "native_threads_per_worker": native, "final_fit_native_threads": final_native,
        "training_parallel_workers": jobs, "training_native_threads": native,
        "native_threads": native,
        "data_count": len(parts["TRAIN"]), "race_count": receipts["TRAIN"]["race_count"],
        "cv_rank_correlation": selected["metrics"]["rank_correlation"],
        "encoder_audit": encoder_audit, "calibration_audit": calibration_audit,
        "probability_metrics": test_probability, "probability_validation": probability_validation,
        "evaluation": evaluation, "source_hashes": before_sources,
        "elapsed_seconds": time.perf_counter() - started, "roi_evaluated": False,
        "active_model_changed": False, "artifact_published": False,
        "future_validation_required": True,
    }
    notify("評価完了・保存待ち", 95)
    return bundle, result


def _validate_prediction_bundle(bundle: dict) -> None:
    if (bundle.get("feature_pipeline") != FEATURE_PIPELINE
            or bundle.get("model_input_contract", {}).get("version") != INPUT_CONTRACT_VERSION
            or bundle.get("feature_columns") != list(FEATURE_COLUMNS)
            or bundle.get("model_input_contract", {}).get("source_hashes") != source_hashes()
            or not isinstance(bundle.get("encoder"), FoldEncoder)
            or bundle["encoder"].feature_columns != list(FEATURE_COLUMNS)):
        raise ValueError("db-shared-model-input-source-contract-mismatch")


def predict_shared_scores(bundle: dict, features: pd.DataFrame, race_ids=None, *, n_threads: int = 2,
                          encoded: bool = False) -> tuple[np.ndarray, np.ndarray | None]:
    """Return speed scores and CAL-fitted probabilities, not an ROI decision.

    A fitted temperature can exist while TEST validation fails. Consumers must
    consult probability_validation.validated before treating these as verified
    win probabilities or EV inputs. Artifact/hash publication is the caller's
    additional admission gate; a caller-supplied JSON is never authorization.
    """
    _validate_prediction_bundle(bundle)
    if list(features.columns) != list(FEATURE_COLUMNS) or features.empty:
        raise ValueError("db-shared-prediction-fixed47-required")
    if isinstance(n_threads, bool) or not isinstance(n_threads, int) or not 1 <= n_threads <= 4:
        raise ValueError("db-shared-prediction-native-threads-invalid")
    matrix = features if encoded else bundle["encoder"].transform(features)
    raw = _finite_predictions(bundle["model"], matrix, n_threads)
    calibrator = bundle.get("calibrator")
    if calibrator is None:
        return raw, None
    if not isinstance(calibrator, RaceTemperatureCalibrator) or calibrator.schema != CALIBRATOR_SCHEMA:
        raise ValueError("db-shared-calibrator-type-required")
    probabilities = calibrator.predict_proba(raw, race_ids)
    return raw, probabilities


def predict_encoded_shared_scores(bundle: dict, encoded: pd.DataFrame, race_ids=None, *, n_threads: int = 2):
    """Avoid double encoding the shared ModelPredictor's prepared matrix."""
    return predict_shared_scores(bundle, encoded, race_ids, n_threads=n_threads, encoded=True)


def shared_speed_mps(bundle: dict, raw_scores: np.ndarray, current: pd.DataFrame) -> np.ndarray:
    """Invert the fitted TRAIN baseline only; never recalculate it at serving."""
    _validate_prediction_bundle(bundle)
    scores = np.asarray(raw_scores, dtype=float)
    if scores.shape != (len(current),) or not np.isfinite(scores).all():
        raise ValueError("db-shared-physical-score-invalid")
    result = _inverse_speed(scores, current, bundle["baseline"])
    if not np.isfinite(result).all():
        raise ValueError("db-shared-physical-speed-invalid")
    return result
