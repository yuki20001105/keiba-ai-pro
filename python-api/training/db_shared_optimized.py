"""Versioned 46-column profile over the unchanged, point-in-time raw47 inputs.

The omission was fixed by a prior TRAIN-only subset experiment, not selected
again from this job's CAL/TEST outcomes. This is a candidate profile, not a claim
of a stable accuracy or profitability advantage. Existing fixed47 artifacts,
formula sources, caches, calibrator classes and forward collection stay intact.
This module performs no publication, activation, DB access or external fetch.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import math
from pathlib import Path
import sys
import threading
import time
from typing import Callable

import numpy as np
import pandas as pd

from training import db_shared_fit as legacy
from training import db_shared_features as raw_features
from training import db_shared_profiles as profiles


FEATURE_PIPELINE = profiles.OPTIMIZED_PIPELINE
FEATURE_VERSION = FEATURE_PIPELINE
INPUT_CONTRACT_VERSION = FEATURE_PIPELINE
EXCLUDED_FEATURES = ("safe_past5_top3_rate",)
FEATURE_COLUMNS = tuple(name for name in legacy.FEATURE_COLUMNS if name not in EXCLUDED_FEATURES)
CATEGORICAL_COLUMNS = tuple(name for name in legacy.CATEGORICAL_COLUMNS if name in FEATURE_COLUMNS)
PROFILE_ID = "db-shared-46-without-past5-top3-v1"
PROFILE_SELECTION_SCOPE = "prior TRAIN-only finite subset study; retrospective improvement not established"
PRESETS = legacy.PRESETS
FoldEncoder = legacy.FoldEncoder
RaceTemperatureCalibrator = legacy.RaceTemperatureCalibrator
CALIBRATOR_SCHEMA = legacy.CALIBRATOR_SCHEMA
PROBABILITY_VALIDATION_SCHEMA = legacy.PROBABILITY_VALIDATION_SCHEMA

# Reuse only helpers which do not assume a feature-column schema. The encoding
# and whole fit orchestration below are explicit46, not mutations of legacy47.
date_partitions = legacy.date_partitions
accuracy_metrics = legacy.accuracy_metrics
fit_temperature = legacy.fit_temperature
probability_metrics = legacy.probability_metrics
validate_probabilities = legacy.validate_probabilities
fit_speed_deviation_baseline = legacy.fit_speed_deviation_baseline
apply_speed_deviation_baseline = legacy.apply_speed_deviation_baseline
_finite_predictions = legacy._finite_predictions
_partition_receipt = legacy._partition_receipt
_frame_digest = legacy._frame_digest
_inverse_speed = legacy._inverse_speed
_resource_limits = legacy._resource_limits
_train_booster = legacy._train_booster

if (len(legacy.FEATURE_COLUMNS) != 47 or len(FEATURE_COLUMNS) != 46
        or set(EXCLUDED_FEATURES) - set(legacy.FEATURE_COLUMNS)
        or profiles.BASELINE_PIPELINE != legacy.FEATURE_PIPELINE
        or profiles.PROFILE_FEATURE_COUNTS.get(FEATURE_PIPELINE) != len(FEATURE_COLUMNS)):
    raise ValueError("db-shared-optimized-parent-profile-changed")


def _own_hash() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


_IMPORTED_SELF_HASH = _own_hash()
_IMPORTED_PROFILE_HASH = hashlib.sha256(Path(profiles.__file__).read_bytes()).hexdigest()


def feature_source_hashes() -> dict[str, str]:
    """The raw formula and exact ordered-subset implementation identities."""
    own_hash = _own_hash()
    profile_hash = hashlib.sha256(Path(profiles.__file__).read_bytes()).hexdigest()
    if own_hash != _IMPORTED_SELF_HASH or profile_hash != _IMPORTED_PROFILE_HASH:
        raise ValueError("db-shared-optimized-source-changed-since-import-restart-required")
    return {**raw_features.source_hashes(), "training.db_shared_optimized": own_hash,
            "training.db_shared_profiles": profile_hash}


def source_hashes() -> dict[str, str]:
    return {**legacy.source_hashes(), **feature_source_hashes()}


def select_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Subset the canonical raw matrix without imputing, reordering or fitting."""
    if (not isinstance(frame, pd.DataFrame) or frame.columns.duplicated().any()
            or list(frame.columns) != list(legacy.FEATURE_COLUMNS)):
        raise ValueError("db-shared-optimized-canonical-raw47-required")
    return frame.loc[:, list(FEATURE_COLUMNS)].copy()


def _fitted_fixed_encoder(frame: pd.DataFrame) -> tuple[FoldEncoder, pd.DataFrame, dict]:
    if list(frame.columns) != list(FEATURE_COLUMNS):
        raise ValueError("db-shared-optimized-fixed46-encoder-required")
    encoder = FoldEncoder(list(FEATURE_COLUMNS), list(CATEGORICAL_COLUMNS))
    encoder.fit_transform(frame)
    diagnosed = dict(encoder.dropped_columns)
    # Retain schema-level constants/missing values; there is no new search here.
    encoder.feature_columns = list(FEATURE_COLUMNS)
    encoded = encoder.transform(frame)
    return encoder, encoded, {"training_column_diagnostics": diagnosed,
                              "retain_fixed46_constant_or_missing_columns": True}


def fit_shared_model(target_frame: pd.DataFrame, features: pd.DataFrame, *, test_size: float = .2,
                     n_threads: int = 4, progress: Callable[[str, int], None] | None = None) -> tuple[dict, dict]:
    """Select two fixed presets within TRAIN; CAL and TEST are never used to tune."""
    started = time.perf_counter()
    before_sources = source_hashes()
    target, raw_inputs = legacy._prepare(target_frame, features)
    inputs = select_features(raw_inputs)
    splits = date_partitions(target, test_size=test_size)
    parts = {name: target.iloc[positions].reset_index(drop=True) for name, positions in splits.items()}
    xs = {name: inputs.iloc[positions].reset_index(drop=True) for name, positions in splits.items()}
    callback_lock = threading.Lock()

    def notify(stage: str, percent: int):
        if progress:
            with callback_lock:
                progress(stage, percent)

    notify("46特徴量の学習・校正・検証を日付で分割", 5)
    train_dates = np.sort(parts["TRAIN"].race_date.astype(str).unique())
    inner_boundary = max(2, min(len(train_dates) - 1, int(math.floor(len(train_dates) * .8))))
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
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="db-shared-optimized-fit") as executor:
            trials = list(executor.map(trial, PRESETS))
    else:
        trials = [trial(preset) for preset in PRESETS]
    best_rho = max(row["metrics"]["rank_correlation"] for row in trials)
    shortlist = [row for row in trials if best_rho - row["metrics"]["rank_correlation"] <= .002]
    selected = min(shortlist, key=lambda row: (row["metrics"]["rmse_speed_mps"],
                                             row["preset"]["num_boost_round"], row["preset"]["id"]))
    notify("TRAIN内の順位相関・RMSEで候補を確定", 30)
    encoder, x_train, encoder_audit = _fitted_fixed_encoder(xs["TRAIN"])
    baseline = fit_speed_deviation_baseline(parts["TRAIN"])
    y_train = apply_speed_deviation_baseline(parts["TRAIN"], baseline).to_numpy()
    final_native = min(4, n_threads, native * jobs)
    model = _train_booster(x_train, y_train, selected["preset"], n_threads=final_native,
                           progress=notify, progress_start=35)
    notify("独立CAL期間で勝率の温度を校正", 55)
    cal_raw = _finite_predictions(model, encoder.transform(xs["CAL"]), final_native)
    calibrator, calibration_audit = fit_temperature(parts["CAL"], cal_raw)
    notify("固定TEST期間で順位・速度誤差・校正を評価", 70)
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
        raise ValueError("db-shared-optimized-source-changed-during-fit")
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
        "time_slices": time_slices,
        "evaluation_label": "日付分割TEST（既知データの事後検証）· 特徴量の改善未確定 · 回収率は別評価",
        "probability_validation": probability_validation,
        "definitions": {"rank_correlation": "macro per-race Spearman(predicted speed score, -finish); constant vectors count as zero",
                        "rmse": "horse-weighted physical-speed RMSE m/s using TRAIN-only speed baseline inverse",
                        "selection": "preset selection uses TRAIN-internal forward validation only; CAL fits temperature; TEST never changes fitting"},
    }
    input_contract = {
        "version": INPUT_CONTRACT_VERSION, "feature_pipeline": FEATURE_PIPELINE,
        "feature_profile": PROFILE_ID, "feature_columns": list(FEATURE_COLUMNS),
        "requested_feature_columns": list(FEATURE_COLUMNS), "categorical_columns": list(CATEGORICAL_COLUMNS),
        "source_hashes": before_sources, "input_feature_sha256": _frame_digest(inputs),
        "raw_feature_pipeline": legacy.FEATURE_PIPELINE,
        "raw_feature_columns": list(legacy.FEATURE_COLUMNS), "raw_feature_sha256": _frame_digest(raw_inputs),
        "excluded_features": list(EXCLUDED_FEATURES), "profile_selection_scope": PROFILE_SELECTION_SCOPE,
        "target_cohort_sha256": _frame_digest(target[["race_id", "race_date", "horse_id", "horse_number", "finish", "time_seconds", "distance"]]),
        "history_policy": "strict source_race_date < target_race_date; same-day outcomes excluded",
        "fit_feature_selection": False, "market_features": False,
        "missing_value_policy": "retain NaN; TRAIN-only categorical dictionary", "live_capture_timing_verified": False,
    }
    bundle = {
        "schema": "local-db-shared-optimized-model-v1", "target": "speed_deviation", "model_type": "lightgbm",
        "model": model, "encoder": encoder, "baseline": baseline, "feature_columns": list(FEATURE_COLUMNS),
        "feature_cols_num": [name for name in FEATURE_COLUMNS if name not in CATEGORICAL_COLUMNS],
        "feature_cols_cat": list(CATEGORICAL_COLUMNS), "feature_pipeline": FEATURE_PIPELINE,
        "feature_profile": PROFILE_ID, "model_input_contract": input_contract,
        "calibrator": calibrator, "optimizer": None, "source_hashes": before_sources,
        "probability_validation": probability_validation, "evaluation": evaluation,
        "ultimate_mode": True, "training_date_from": receipts["TRAIN"]["date_from"],
        "training_date_to": receipts["TRAIN"]["date_to"], "data_count": len(parts["TRAIN"]),
        "race_count": receipts["TRAIN"]["race_count"],
    }
    result = {
        "schema": "local-db-shared-optimized-fit-result-v1", "feature_pipeline": FEATURE_PIPELINE,
        "feature_profile": PROFILE_ID, "excluded_features": list(EXCLUDED_FEATURES),
        "profile_selection_scope": PROFILE_SELECTION_SCOPE,
        "partitions": receipts, "inner_training": _partition_receipt(inner_train),
        "inner_validation": _partition_receipt(inner_validation), "preset_trials": trials,
        "selected_preset": selected["preset"]["id"],
        "selection_rule": "TRAIN-internal macro Spearman first; within 0.002 use physical RMSE, then fewer rounds",
        "test_used_for_selection_or_fit": False, "cal_used_for_preset_selection": False,
        "parallel_workers": jobs, "native_threads_per_worker": native, "final_fit_native_threads": final_native,
        "training_parallel_workers": jobs, "training_native_threads": native, "native_threads": native,
        "data_count": len(parts["TRAIN"]), "race_count": receipts["TRAIN"]["race_count"],
        "cv_rank_correlation": selected["metrics"]["rank_correlation"], "encoder_audit": encoder_audit,
        "calibration_audit": calibration_audit, "probability_metrics": test_probability,
        "probability_validation": probability_validation, "evaluation": evaluation,
        "source_hashes": before_sources, "elapsed_seconds": time.perf_counter() - started,
        "roi_evaluated": False, "active_model_changed": False, "artifact_published": False,
        "future_validation_required": True,
    }
    notify("46特徴量の評価完了・保存待ち", 95)
    return bundle, result


def _validate_prediction_bundle(bundle: dict) -> None:
    contract = bundle.get("model_input_contract", {})
    if (bundle.get("schema") != "local-db-shared-optimized-model-v1"
            or bundle.get("feature_pipeline") != FEATURE_PIPELINE
            or bundle.get("feature_profile") != PROFILE_ID
            or bundle.get("feature_columns") != list(FEATURE_COLUMNS)
            or contract.get("version") != INPUT_CONTRACT_VERSION
            or contract.get("feature_pipeline") != FEATURE_PIPELINE
            or contract.get("feature_profile") != PROFILE_ID
            or contract.get("feature_columns") != list(FEATURE_COLUMNS)
            or contract.get("categorical_columns") != list(CATEGORICAL_COLUMNS)
            or contract.get("raw_feature_pipeline") != legacy.FEATURE_PIPELINE
            or contract.get("raw_feature_columns") != list(legacy.FEATURE_COLUMNS)
            or contract.get("excluded_features") != list(EXCLUDED_FEATURES)
            or contract.get("source_hashes") != source_hashes()
            or bundle.get("source_hashes") != source_hashes()
            or not isinstance(bundle.get("encoder"), FoldEncoder)
            or bundle["encoder"].feature_columns != list(FEATURE_COLUMNS)
            or bundle["encoder"].categorical_columns != list(CATEGORICAL_COLUMNS)):
        raise ValueError("db-shared-optimized-model-input-source-contract-mismatch")


def predict_shared_scores(bundle: dict, features: pd.DataFrame, race_ids=None, *, n_threads: int = 2,
                          encoded: bool = False) -> tuple[np.ndarray, np.ndarray | None]:
    _validate_prediction_bundle(bundle)
    if list(features.columns) != list(FEATURE_COLUMNS) or features.empty:
        raise ValueError("db-shared-optimized-prediction-fixed46-required")
    if isinstance(n_threads, bool) or not isinstance(n_threads, int) or not 1 <= n_threads <= 4:
        raise ValueError("db-shared-optimized-prediction-native-threads-invalid")
    matrix = features if encoded else bundle["encoder"].transform(features)
    raw = _finite_predictions(bundle["model"], matrix, n_threads)
    calibrator = bundle.get("calibrator")
    if calibrator is None:
        return raw, None
    if not isinstance(calibrator, RaceTemperatureCalibrator) or calibrator.schema != CALIBRATOR_SCHEMA:
        raise ValueError("db-shared-optimized-calibrator-type-required")
    return raw, calibrator.predict_proba(raw, race_ids)


def predict_encoded_shared_scores(bundle: dict, encoded: pd.DataFrame, race_ids=None, *, n_threads: int = 2):
    return predict_shared_scores(bundle, encoded, race_ids, n_threads=n_threads, encoded=True)


def shared_speed_mps(bundle: dict, raw_scores: np.ndarray, current: pd.DataFrame) -> np.ndarray:
    _validate_prediction_bundle(bundle)
    scores = np.asarray(raw_scores, dtype=float)
    if scores.shape != (len(current),) or not np.isfinite(scores).all():
        raise ValueError("db-shared-optimized-physical-score-invalid")
    result = _inverse_speed(scores, current, bundle["baseline"])
    if not np.isfinite(result).all():
        raise ValueError("db-shared-optimized-physical-speed-invalid")
    return result


def accelerated_fit_shared_model(target, features, *, compute_device="auto", execution_receipt=None,
                                 test_size=.2, n_threads=4, progress=None):
    """Use existing bound CPU/GPU runtime, mutating only this isolated module."""
    from training import db_shared_acceleration as acceleration
    if compute_device not in {"auto", "cpu", "gpu"}:
        raise ValueError("db-shared-optimized-compute-device-invalid")
    receipt = execution_receipt or acceleration.current_execution_receipt()
    if receipt is None:
        raise ValueError("db-shared-optimized-bound-worker-execution-required")
    acceleration._validate_receipt_structure(receipt)
    bound = acceleration.current_execution_receipt()
    if bound is None or bound["receipt_sha256"] != receipt["receipt_sha256"]:
        raise ValueError("db-shared-optimized-unverified-worker-execution")
    device = receipt["resolved_device"]
    if compute_device != "auto" and compute_device != device:
        raise ValueError("db-shared-optimized-requested-bound-device-mismatch")
    before_sources = source_hashes()
    records = []
    clock = time.perf_counter()

    def notify(stage, percent):
        if device == "gpu" and stage.startswith("固定2候補を比較"):
            stage = "固定2候補を比較（GPU・46特徴量・順次実行）"
        if progress:
            progress(stage, percent)

    with acceleration._FIT_LOCK:
        with acceleration._fit_device(sys.modules[__name__], device, records):
            bundle, result = fit_shared_model(target, features, test_size=test_size,
                                             n_threads=n_threads, progress=notify)
    if source_hashes() != before_sources:
        raise ValueError("db-shared-optimized-acceleration-source-contract-changed")
    if device == "gpu" and (len(records) != 3 or not all(row["gpu_device_verified"] for row in records)):
        raise ValueError("db-shared-optimized-gpu-fit-evidence-incomplete")
    execution = {"schema": acceleration.SCHEMA, "device": device,
        "requested_device": receipt["requested_device"], "fallback_reason": receipt["fallback_reason"],
        "receipt": receipt, "fit_pipeline_seconds": time.perf_counter() - clock,
        "gpu_fit_records": records, "gpu_device_verified": device == "gpu" and bool(records),
        "feature_formula_changed": False, "source_contract_changed": False,
        "feature_profile": PROFILE_ID, "partial_fit_cpu_fallback": False, "roi_evaluated": False}
    bundle["training_execution"] = execution
    result["training_execution"] = execution
    return bundle, result
