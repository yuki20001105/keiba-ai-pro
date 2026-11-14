"""User-authorized trial of conditioned speed targets over unchanged raw46."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import FunctionType

import numpy as np
import pandas as pd

from training import db_shared_fit as legacy
from training import db_shared_optimized as optimized
from training import grouped_speed_baseline as grouping

FEATURE_PIPELINE = optimized.FEATURE_PIPELINE
FEATURE_COLUMNS = optimized.FEATURE_COLUMNS
CATEGORICAL_COLUMNS = optimized.CATEGORICAL_COLUMNS
PROFILE_ID = optimized.PROFILE_ID
SCHEMA = "local-db-shared-grouped-model-v1"
INPUT_CONTRACT_VERSION = "local-db-conditioned-speed-v1"
ARM = "course_going_weather"
_train_booster = optimized._train_booster
_resource_limits = optimized._resource_limits


def _file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


_IMPORTED_SOURCES = {name: _file_hash(path) for name, path in (
    ("training.db_shared_grouped", __file__),
    ("training.grouped_speed_baseline", grouping.__file__),
)}


def feature_source_hashes():
    return optimized.feature_source_hashes()


def source_hashes():
    current = {"training.db_shared_grouped": _file_hash(__file__),
               "training.grouped_speed_baseline": _file_hash(grouping.__file__)}
    if current != _IMPORTED_SOURCES:
        raise ValueError("conditioned-normalization-source-changed-restart-required")
    return {**optimized.source_hashes(), **current}


def _bind(function, replacements):
    # Private globals reuse frozen orchestration without patching old modules
    # or duplicating their chronological split, preset and calibration logic.
    bound = FunctionType(function.__code__, {**function.__globals__, **replacements},
                         function.__name__, function.__defaults__, function.__closure__)
    bound.__kwdefaults__ = function.__kwdefaults__
    return bound


def _conditions(frame):
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("conditioned-normalization-frame-required")
    result = frame.copy()
    for name in ("venue", "field_condition", "weather"):
        if name not in result:
            result[name] = pd.NA
    return result


def _inverse(scores, frame, baseline):
    scores = np.asarray(scores, dtype=float)
    if scores.shape != (len(frame),) or not np.isfinite(scores).all():
        raise ValueError("conditioned-normalization-score-invalid")
    mean, std = grouping.normalization(_conditions(frame), baseline)
    values = scores * std.to_numpy() + mean.to_numpy()
    if not np.isfinite(values).all():
        raise ValueError("conditioned-normalization-physical-speed-invalid")
    return values


def _baseline_hash(baseline):
    canonical = {**baseline, "levels": [
        [[list(key), entry] for key, entry in sorted(level.items())]
        for level in baseline["levels"]]}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, allow_nan=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def fit_shared_model(target_frame, features, *, test_size=.2, n_threads=4, progress=None):
    def baseline(frame):
        return grouping.fit_group_baseline(_conditions(frame), ARM)

    def teacher(frame, fitted):
        return grouping.teacher(_conditions(frame), fitted)

    accuracy = _bind(legacy.accuracy_metrics, {
        "_inverse_speed": _inverse, "apply_speed_deviation_baseline": teacher})
    fit = _bind(optimized.fit_shared_model, {
        "fit_speed_deviation_baseline": baseline, "apply_speed_deviation_baseline": teacher,
        "accuracy_metrics": accuracy, "source_hashes": source_hashes,
        "INPUT_CONTRACT_VERSION": INPUT_CONTRACT_VERSION,
        "_train_booster": _train_booster, "_resource_limits": _resource_limits})
    bundle, result = fit(target_frame, features, test_size=test_size,
                         n_threads=n_threads, progress=progress)
    normalization = {"version": INPUT_CONTRACT_VERSION, "arm": ARM, "training_only": True,
        "baseline_sha256": _baseline_hash(bundle["baseline"]),
        "label": "基準④：芝ダ・距離・競馬場・馬場2分類・天候2分類",
        "adoption": "user-authorized-positive-point-estimate-trial",
        "group_counts": [len(level) for level in bundle["baseline"]["levels"]]}
    report = bundle["probability_validation"]
    report.update(validated=False, eligible_for_ev=False, status="unverified", reference_only=True,
        reason_codes=[*report.get("reason_codes", []), "conditioned_baseline_trial_live_validation_pending"])
    bundle.update(schema=SCHEMA, reference_only=True, target_normalization=normalization,
        feature_source_hashes=feature_source_hashes(), fit_source_sha256=_file_hash(__file__))
    bundle["model_input_contract"]["target_normalization"] = dict(normalization)
    bundle["evaluation"].update(target_normalization=dict(normalization),
        evaluation_label="基準④の試験採用 · 日付分割TEST · 将来の改善・購入判断は未承認")
    result.update(schema="local-db-shared-grouped-fit-result-v1", target_normalization=dict(normalization))
    return bundle, result


def _validate_prediction_bundle(bundle):
    contract = bundle.get("model_input_contract", {})
    normalizer = bundle.get("target_normalization", {})
    baseline = bundle.get("baseline", {})
    if (bundle.get("schema") != SCHEMA or bundle.get("reference_only") is not True
            or normalizer.get("version") != INPUT_CONTRACT_VERSION or normalizer.get("arm") != ARM
            or normalizer.get("training_only") is not True
            or baseline.get("schema") != "coarse-speed-baseline-v1" or baseline.get("arm") != ARM
            or not isinstance(baseline.get("levels"), list) or len(baseline["levels"]) != 3
            or normalizer.get("baseline_sha256") != _baseline_hash(baseline)
            or contract.get("target_normalization") != normalizer
            or contract.get("version") != INPUT_CONTRACT_VERSION
            or contract.get("source_hashes") != source_hashes()
            or bundle.get("source_hashes") != source_hashes()
            or bundle.get("feature_source_hashes") != feature_source_hashes()
            or bundle.get("fit_source_sha256") != _file_hash(__file__)):
        raise ValueError("conditioned-normalization-contract-mismatch")
    # The input profile is still exact46. Validate that unchanged contract
    # independently; never send this projection to old scoring/inverse code.
    parent_sources = optimized.source_hashes()
    optimized._validate_prediction_bundle({
        **bundle, "schema": "local-db-shared-optimized-model-v1",
        "source_hashes": parent_sources,
        "model_input_contract": {**contract, "version": optimized.INPUT_CONTRACT_VERSION,
                                 "source_hashes": parent_sources},
    })


def predict_encoded_shared_scores(bundle, encoded, race_ids=None, *, n_threads=2):
    _validate_prediction_bundle(bundle)
    if list(encoded.columns) != list(FEATURE_COLUMNS) or encoded.empty:
        raise ValueError("conditioned-normalization-fixed46-required")
    if type(n_threads) is not int or not 1 <= n_threads <= 4:
        raise ValueError("conditioned-normalization-thread-budget-invalid")
    scores = legacy._finite_predictions(bundle["model"], encoded, n_threads)
    calibrator = bundle.get("calibrator")
    if calibrator is None:
        return scores, None
    if (not isinstance(calibrator, legacy.RaceTemperatureCalibrator)
            or calibrator.schema != legacy.CALIBRATOR_SCHEMA):
        raise ValueError("conditioned-normalization-calibrator-invalid")
    return scores, calibrator.predict_proba(scores, race_ids)


def predict_shared_scores(bundle, features, race_ids=None, *, n_threads=2):
    _validate_prediction_bundle(bundle)
    if list(features.columns) != list(FEATURE_COLUMNS):
        raise ValueError("conditioned-normalization-fixed46-required")
    return predict_encoded_shared_scores(bundle, bundle["encoder"].transform(features),
                                         race_ids, n_threads=n_threads)


def shared_speed_mps(bundle, scores, current):
    _validate_prediction_bundle(bundle)
    return _inverse(scores, current, bundle["baseline"])


def accelerated_fit_shared_model(target, features, **kwargs):
    bundle, result = _bind(optimized.accelerated_fit_shared_model, {
        "__name__": __name__, "source_hashes": source_hashes,
        "fit_shared_model": fit_shared_model})(target, features, **kwargs)
    bundle["training_execution"]["target_normalization"] = dict(bundle["target_normalization"])
    return bundle, result
