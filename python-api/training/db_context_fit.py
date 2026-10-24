"""Isolated context candidate; never mutates fitted legacy/forward formulas.

TRAIN chooses fixed presets, CAL fits temperature, TEST reports accuracy and
probability separately. A retrospective candidate is not purchase authority.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import math
from pathlib import Path
import sys
import threading
import time

import numpy as np
import pandas as pd

from training import db_shared_fit as legacy
from training import db_context_features as source

FEATURE_PIPELINE = "local-db-context-v1"
SCHEMA = "local-db-context-model-v1"
FEATURE_COLUMNS = tuple(source.FEATURE_COLUMNS)
CATEGORICAL_COLUMNS = tuple(source.CATEGORICAL_COLUMNS)
PRESETS = legacy.PRESETS
FoldEncoder = legacy.FoldEncoder
RaceTemperatureCalibrator = legacy.RaceTemperatureCalibrator
CALIBRATOR_SCHEMA = legacy.CALIBRATOR_SCHEMA
_resource_limits = legacy._resource_limits
_train_booster = legacy._train_booster


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


_IMPORTED_SELF = file_hash(__file__)
_HELPERS = tuple(Path(__file__).with_name(name) for name in ("db_context_registry.py", "db_context_cache.py"))
_IMPORTED_HELPERS = {path.stem: file_hash(path) for path in _HELPERS}


def feature_source_hashes():
    return source.feature_source_hashes()


def source_hashes():
    own = file_hash(__file__)
    helpers = {path.stem: file_hash(path) for path in _HELPERS}
    if own != _IMPORTED_SELF or helpers != _IMPORTED_HELPERS:
        raise ValueError("context-fit-source-changed-restart-required")
    return {**legacy.source_hashes(), **feature_source_hashes(), "training.db_context_fit": own,
            **helpers}


def prepare_inputs(snapshot_path, date_from, date_to, cache_root, *, progress=None,
                   expected_source_hashes=None, reuse_features=True):
    from training.db_context_cache import prepare_context_inputs
    return prepare_context_inputs(snapshot_path, date_from, date_to, cache_root,
        progress=progress, expected_source_hashes=expected_source_hashes, reuse_features=reuse_features)


def prepare(target, features):
    if (not isinstance(target, pd.DataFrame) or not isinstance(features, pd.DataFrame)
            or features.columns.duplicated().any() or list(features.columns) != list(FEATURE_COLUMNS)
            or len(target) != len(features) or not target.index.equals(features.index)):
        raise ValueError("context-canonical-aligned-features-required")
    if target.empty or target.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("context-complete-unique-target-required")
    # Let the frozen canonical validator check eligibility/order using raw47.
    # Extra columns must not enter its exact47 validation. The removed column
    # is only reconstructed for validation, never fed into the context model.
    raw47 = pd.DataFrame(index=features.index)
    for name in legacy.FEATURE_COLUMNS:
        raw47[name] = features[name] if name in features else np.nan
    checked, _ = legacy._prepare(target, raw47)
    keys = pd.MultiIndex.from_frame(target[["race_id", "horse_number"]])
    desired = pd.MultiIndex.from_frame(checked[["race_id", "horse_number"]])
    positions = keys.get_indexer(desired)
    if (positions < 0).any():
        raise ValueError("context-canonical-order-conflict")
    inputs = features.iloc[positions].reset_index(drop=True).copy()
    inputs.attrs = dict(features.attrs)
    return checked, inputs


def _encoder(frame, columns):
    categories = [name for name in CATEGORICAL_COLUMNS if name in columns]
    encoder = FoldEncoder(list(columns), categories)
    encoder.fit_transform(frame)
    diagnostics = dict(encoder.dropped_columns)
    encoder.feature_columns = list(columns)
    return encoder, encoder.transform(frame), diagnostics


def fit_on_partitions(target, features, splits, *, columns=None, n_threads=4, progress=None, preset_parallel=True):
    """Fixed-group research and normal fitting share this same implementation."""
    started = time.perf_counter()
    sources = source_hashes()
    columns = tuple(columns or FEATURE_COLUMNS)
    if not columns or len(set(columns)) != len(columns) or set(columns) - set(FEATURE_COLUMNS):
        raise ValueError("context-explicit-known-column-group-required")
    original = (legacy._frame_digest(target), legacy._frame_digest(features))
    target, features = prepare(target, features)
    if original != (legacy._frame_digest(target), legacy._frame_digest(features)):
        raise ValueError("context-partitions-require-canonical-position-order")
    if not isinstance(splits, dict) or set(splits) != {"TRAIN", "CAL", "TEST"}:
        raise ValueError("context-three-explicit-partitions-required")
    occupied = set()
    all_counts = target.groupby("race_date").size()
    for positions in splits.values():
        index = np.asarray(positions)
        if (index.ndim != 1 or index.dtype.kind not in "iu" or not len(index)
                or len(np.unique(index)) != len(index) or index.min() < 0 or index.max() >= len(target)
                or occupied.intersection(index.tolist())):
            raise ValueError("context-partition-position-invalid-or-overlapping")
        occupied.update(index.tolist())
        counts = target.iloc[index].groupby("race_date").size()
        if not counts.eq(all_counts.reindex(counts.index)).all():
            raise ValueError("context-partition-whole-dates-required")
    parts = {name: target.iloc[index].reset_index(drop=True) for name, index in splits.items()}
    xs = {name: features.iloc[index].loc[:, list(columns)].reset_index(drop=True) for name, index in splits.items()}
    if set(parts) != {"TRAIN", "CAL", "TEST"} or any(frame.empty for frame in parts.values()):
        raise ValueError("context-three-nonempty-partitions-required")
    if not (parts["TRAIN"].race_date.max() < parts["CAL"].race_date.min()
            and parts["CAL"].race_date.max() < parts["TEST"].race_date.min()):
        raise ValueError("context-chronological-partition-overlap")
    lock = threading.Lock()
    def notify(message, percent):
        if progress:
            with lock:
                progress(message, percent)
    dates = np.sort(parts["TRAIN"].race_date.astype(str).unique())
    if len(dates) < 4:
        raise ValueError("context-insufficient-training-dates")
    boundary = max(2, min(len(dates)-1, int(math.floor(.8 * len(dates)))))
    inner = parts["TRAIN"].race_date.astype(str).isin(dates[:boundary]).to_numpy()
    encoder, x, _ = _encoder(xs["TRAIN"].loc[inner].reset_index(drop=True), columns)
    inner_test_x = encoder.transform(xs["TRAIN"].loc[~inner].reset_index(drop=True))
    inner_train = parts["TRAIN"].loc[inner].reset_index(drop=True)
    inner_test = parts["TRAIN"].loc[~inner].reset_index(drop=True)
    baseline = legacy.fit_speed_deviation_baseline(inner_train)
    y = legacy.apply_speed_deviation_baseline(inner_train, baseline).to_numpy()
    jobs, native = _resource_limits(n_threads, x)
    if not preset_parallel:
        jobs, native = 1, min(n_threads, native)
    def trial(preset):
        clock = time.perf_counter()
        model = _train_booster(x, y, preset, n_threads=native, progress=notify, progress_start=10)
        scores = legacy._finite_predictions(model, inner_test_x, native)
        return {"preset": dict(preset), "metrics": legacy.accuracy_metrics(inner_test, scores, baseline),
                "elapsed_seconds": time.perf_counter()-clock}
    notify(f"固定2候補を比較（CPU {jobs}並列・各{native}スレッド）", 10)
    if jobs == 2:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="context-preset") as pool:
            trials = list(pool.map(trial, PRESETS))
    else:
        trials = [trial(preset) for preset in PRESETS]
    best = max(item["metrics"]["rank_correlation"] for item in trials)
    selected = min((item for item in trials if best-item["metrics"]["rank_correlation"] <= .002),
        key=lambda item: (item["metrics"]["rmse_speed_mps"], item["preset"]["num_boost_round"], item["preset"]["id"]))
    notify("TRAIN内で確定した候補を学習", 35)
    encoder, train_x, encoder_audit = _encoder(xs["TRAIN"], columns)
    baseline = legacy.fit_speed_deviation_baseline(parts["TRAIN"])
    y = legacy.apply_speed_deviation_baseline(parts["TRAIN"], baseline).to_numpy()
    final_native = max(1, min(4, n_threads, native*jobs))
    model = _train_booster(train_x, y, selected["preset"], n_threads=final_native,
        progress=notify, progress_start=35)
    notify("独立CALで勝率校正", 55)
    cal_scores = legacy._finite_predictions(model, encoder.transform(xs["CAL"]), final_native)
    calibrator, cal_audit = legacy.fit_temperature(parts["CAL"], cal_scores)
    notify("固定TESTで精度と校正を別評価", 70)
    scores = legacy._finite_predictions(model, encoder.transform(xs["TEST"]), final_native)
    accuracy = legacy.accuracy_metrics(parts["TEST"], scores, baseline)
    probabilities = calibrator.predict_proba(scores, parts["TEST"].race_id) if calibrator is not None else None
    probability = legacy.probability_metrics(parts["TEST"], probabilities) if probabilities is not None else None
    validation = legacy.validate_probabilities(cal_audit, probability, parts["CAL"], parts["TEST"])
    receipts = {name: legacy._partition_receipt(frame) for name, frame in parts.items()}
    evaluation = {"schema": "model-evaluation-v1", "target": "speed_deviation", "rmse_unit": "m/s",
        "primary": {"rank_correlation": accuracy["rank_correlation"], "rmse": accuracy["rmse_speed_mps"],
            "top_pick_win_rate": accuracy["top_pick_win_rate"], "favorite_win_rate": None,
            "top_pick_win_rate_delta": None, "win_roi": None},
        "details": {"mae": accuracy["mae_speed_mps"], "r2": accuracy["r2_speed_mps"],
            "rmse_speed_deviation": accuracy["rmse_speed_deviation"],
            "evaluation_date_from": receipts["TEST"]["date_from"], "evaluation_date_to": receipts["TEST"]["date_to"],
            "evaluation_race_count": accuracy["race_count"], "evaluation_runner_count": accuracy["runner_count"]},
        "probability_validation": validation, "time_slices": [],
        "evaluation_label": "条件・展開の比較候補 · 既知データの時系列検証 · 回収率は別評価"}
    for year, positions in parts["TEST"].groupby(parts["TEST"].race_date.astype(str).str[:4], sort=True).indices.items():
        metric = legacy.accuracy_metrics(parts["TEST"].iloc[positions], scores[positions], baseline)
        evaluation["time_slices"].append({"period": str(year), "race_count": metric["race_count"],
            "rank_correlation": metric["rank_correlation"], "rmse": metric["rmse_speed_mps"],
            "top_pick_win_rate": metric["top_pick_win_rate"], "win_roi": None})
    if source_hashes() != sources:
        raise ValueError("context-source-changed-during-fit")
    contract = {"version": FEATURE_PIPELINE, "feature_pipeline": FEATURE_PIPELINE,
        "feature_columns": list(columns), "categorical_columns": encoder.categorical_columns,
        "source_hashes": sources, "history_policy": "source_race_date < target_race_date; no same-day outcomes",
        "current_outcomes_consumed": False, "live_capture_timing_verified": False,
        "market_features": False, "fit_feature_selection": False,
        "input_feature_sha256": legacy._frame_digest(features[list(columns)])}
    bundle = {"schema": SCHEMA, "feature_pipeline": FEATURE_PIPELINE, "target": "speed_deviation",
        "model_type": "lightgbm", "model": model, "encoder": encoder, "baseline": baseline,
        "feature_columns": list(columns), "feature_cols_num": [c for c in columns if c not in encoder.categorical_columns],
        "feature_cols_cat": list(encoder.categorical_columns), "calibrator": calibrator, "optimizer": None,
        "model_input_contract": contract, "source_hashes": sources, "feature_source_hashes": feature_source_hashes(),
        "fit_source_sha256": file_hash(__file__), "probability_validation": validation,
        "evaluation": evaluation, "ultimate_mode": True, "reference_only": True,
        "training_date_from": receipts["TRAIN"]["date_from"], "training_date_to": receipts["TRAIN"]["date_to"],
        "data_count": len(parts["TRAIN"]), "race_count": receipts["TRAIN"]["race_count"]}
    result = {"schema": "local-db-context-fit-result-v1", "feature_pipeline": FEATURE_PIPELINE,
        "feature_count": len(columns), "feature_columns": list(columns), "partitions": receipts,
        "inner_training": legacy._partition_receipt(inner_train), "inner_validation": legacy._partition_receipt(inner_test),
        "preset_trials": trials, "selected_preset": selected["preset"]["id"],
        "selection_rule": "TRAIN-only Spearman first; within 0.002 physical RMSE then complexity",
        "cv_rank_correlation": selected["metrics"]["rank_correlation"],
        "test_used_for_selection_or_fit": False, "cal_used_for_preset_selection": False,
        "training_parallel_workers": jobs, "training_native_threads": native,
        "data_count": len(parts["TRAIN"]), "race_count": receipts["TRAIN"]["race_count"],
        "encoder_audit": encoder_audit, "calibration_audit": cal_audit, "probability_metrics": probability,
        "probability_validation": validation, "evaluation": evaluation, "source_hashes": sources,
        "elapsed_seconds": time.perf_counter()-started, "roi_evaluated": False,
        "active_model_changed": False, "artifact_published": False, "future_validation_required": True}
    return bundle, result, {"target": parts["TEST"], "scores": scores, "probabilities": probabilities,
                           "physical_speed": legacy._inverse_speed(scores, parts["TEST"], baseline)}


def fit_shared_model(target, features, *, test_size=.2, n_threads=4, progress=None):
    target, features = prepare(target, features)
    bundle, result, _ = fit_on_partitions(target, features, legacy.date_partitions(target, test_size=test_size),
        n_threads=n_threads, progress=progress)
    return bundle, result


def _validate_prediction_bundle(bundle):
    contract = bundle.get("model_input_contract", {})
    if (bundle.get("schema") != SCHEMA or bundle.get("feature_pipeline") != FEATURE_PIPELINE
            or bundle.get("target") != "speed_deviation" or bundle.get("reference_only") is not True
            or bundle.get("feature_columns") != list(FEATURE_COLUMNS)
            or contract.get("version") != FEATURE_PIPELINE or contract.get("feature_pipeline") != FEATURE_PIPELINE
            or contract.get("categorical_columns") != list(CATEGORICAL_COLUMNS)
            or contract.get("history_policy") != "source_race_date < target_race_date; no same-day outcomes"
            or contract.get("current_outcomes_consumed") is not False or contract.get("market_features") is not False
            or contract.get("feature_columns") != list(FEATURE_COLUMNS)
            or contract.get("source_hashes") != source_hashes() or bundle.get("source_hashes") != source_hashes()
            or bundle.get("feature_source_hashes") != feature_source_hashes()
            or bundle.get("fit_source_sha256") != file_hash(__file__)
            or not isinstance(bundle.get("encoder"), FoldEncoder)
            or bundle["encoder"].feature_columns != list(FEATURE_COLUMNS)
            or bundle["encoder"].categorical_columns != list(CATEGORICAL_COLUMNS)):
        raise ValueError("context-published-input-contract-mismatch")


def predict_encoded_shared_scores(bundle, encoded, race_ids=None, *, n_threads=2):
    _validate_prediction_bundle(bundle)
    if list(encoded.columns) != list(FEATURE_COLUMNS) or encoded.empty:
        raise ValueError("context-fixed-feature-matrix-required")
    if type(n_threads) is not int or not 1 <= n_threads <= 4:
        raise ValueError("context-prediction-native-thread-budget-invalid")
    scores = legacy._finite_predictions(bundle["model"], encoded, n_threads)
    calibrator = bundle.get("calibrator")
    if calibrator is None:
        return scores, None
    if not isinstance(calibrator, RaceTemperatureCalibrator) or calibrator.schema != CALIBRATOR_SCHEMA:
        raise ValueError("context-calibrator-contract-invalid")
    return scores, calibrator.predict_proba(scores, race_ids)


def shared_speed_mps(bundle, scores, current):
    _validate_prediction_bundle(bundle)
    scores = np.asarray(scores, dtype=float)
    if scores.shape != (len(current),) or not np.isfinite(scores).all():
        raise ValueError("context-physical-score-invalid")
    values = legacy._inverse_speed(scores, current, bundle["baseline"])
    if not np.isfinite(values).all():
        raise ValueError("context-physical-speed-invalid")
    return values


def accelerated_fit_shared_model(target, features, *, compute_device="auto", execution_receipt=None,
                                 test_size=.2, n_threads=4, progress=None):
    from training import db_shared_acceleration as acceleration
    receipt = execution_receipt or acceleration.current_execution_receipt()
    if receipt is None or compute_device not in {"auto", "cpu", "gpu"}:
        raise ValueError("context-bound-worker-execution-required")
    acceleration._validate_receipt_structure(receipt)
    bound = acceleration.current_execution_receipt()
    if bound is None or bound["receipt_sha256"] != receipt["receipt_sha256"]:
        raise ValueError("context-worker-execution-receipt-mismatch")
    device = receipt["resolved_device"]
    if compute_device != "auto" and compute_device != device:
        raise ValueError("context-worker-device-mismatch")
    records = []
    with acceleration._FIT_LOCK:
        with acceleration._fit_device(sys.modules[__name__], device, records):
            bundle, result = fit_shared_model(target, features, test_size=test_size, n_threads=n_threads, progress=progress)
    if device == "gpu" and (len(records) != 3 or not all(row["gpu_device_verified"] for row in records)):
        raise ValueError("context-gpu-execution-evidence-incomplete")
    execution = {"schema": acceleration.SCHEMA, "device": device, "requested_device": receipt["requested_device"],
        "fallback_reason": receipt["fallback_reason"], "receipt": receipt, "gpu_fit_records": records,
        "gpu_device_verified": device == "gpu", "feature_formula_changed": False,
        "partial_fit_cpu_fallback": False, "roi_evaluated": False}
    bundle["training_execution"] = execution
    result["training_execution"] = execution
    return bundle, result
