"""Pure46-profile regressions: immutable legacy47, honest splits and GPU fences."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import date, timedelta
import hashlib
from pathlib import Path
import sys

import joblib
import numpy as np
import pandas as pd
import pytest

from training import db_shared_acceleration as acceleration
from training import db_shared_fit as legacy
from training import db_shared_optimized as optimized


def fixture_data(days=60, races_per_day=4):
    targets, rows = [], []
    for day in range(days):
        current = (date(2021, 1, 1) + timedelta(days=day)).strftime("%Y%m%d")
        for race in range(races_per_day):
            for horse in range(1, 4):
                targets.append({"race_id": f"{current}{race:04d}", "race_date": current,
                    "horse_id": f"horse{horse}", "horse_number": horse, "finish": horse,
                    "time_seconds": 95.0 + horse + day % 4 * .1, "distance": 1600,
                    "surface": "turf", "venue": "Tokyo", "num_horses": 3})
                row = {name: float(horse + day % 4) for name in legacy.FEATURE_COLUMNS}
                for name in legacy.CATEGORICAL_COLUMNS:
                    row[name] = "known"
                row["safe_horse_number"] = float(horse)
                row["safe_weight_change"] = np.nan
                row["safe_weather"] = "sunny" if day < int(days * .6) else "later_unknown"
                rows.append(row)
    return pd.DataFrame(targets), pd.DataFrame(rows, columns=legacy.FEATURE_COLUMNS)


class FixtureBooster:
    def __init__(self, scale=1.):
        self.scale = scale

    def predict(self, matrix, *, num_threads=1):
        return -matrix["safe_horse_number"].to_numpy() * self.scale


@pytest.fixture
def pure_fit(monkeypatch):
    calls = []
    monkeypatch.setattr(optimized, "source_hashes", lambda: {"fixture-feature-formula": "verified"})
    monkeypatch.setattr(optimized, "_resource_limits", lambda *_: (2, 2))

    def train(matrix, target, preset, *, n_threads, progress=None, progress_start=0):
        calls.append({"matrix": matrix.copy(), "target": target.copy(), "preset": preset["id"],
                      "threads": n_threads})
        return FixtureBooster(scale=1 if preset["id"] == "lean" else .5)

    monkeypatch.setattr(optimized, "_train_booster", train)
    return calls


def test_profile_removes_exactly_prior_fixed_candidate_and_preserves_order():
    assert len(optimized.FEATURE_COLUMNS) == 46
    assert optimized.EXCLUDED_FEATURES == ("safe_past5_top3_rate",)
    assert optimized.FEATURE_COLUMNS == tuple(name for name in legacy.FEATURE_COLUMNS
                                              if name != "safe_past5_top3_rate")
    assert len(legacy.FEATURE_COLUMNS) == 47
    assert optimized.FEATURE_PIPELINE != legacy.FEATURE_PIPELINE
    assert optimized.CATEGORICAL_COLUMNS == legacy.CATEGORICAL_COLUMNS
    assert optimized.RaceTemperatureCalibrator is legacy.RaceTemperatureCalibrator


def test_select_features_preserves_missing_values_and_source_matrix():
    _, raw = fixture_data(days=10)
    before = raw.copy()
    selected = optimized.select_features(raw)
    assert selected.shape == (len(raw), 46)
    pd.testing.assert_frame_equal(selected, raw.drop(columns="safe_past5_top3_rate"))
    pd.testing.assert_frame_equal(raw, before)
    assert selected.safe_weight_change.isna().all()


@pytest.mark.parametrize("mutation", ["selected_only", "extra", "reversed", "duplicate", "not_frame"])
def test_profile_selection_rejects_noncanonical_inputs(mutation):
    _, raw = fixture_data(days=10)
    if mutation == "selected_only":
        raw = optimized.select_features(raw)
    elif mutation == "extra":
        raw["finish"] = 1
    elif mutation == "reversed":
        raw = raw.loc[:, list(reversed(raw.columns))]
    elif mutation == "duplicate":
        raw.columns = [raw.columns[0], *list(raw.columns[:-1])]
    else:
        raw = []
    with pytest.raises(ValueError, match="canonical-raw47-required"):
        optimized.select_features(raw)


def test_fit_uses_raw47_parent_validation_but_trains_only46(pure_fit):
    target, features = fixture_data()
    bundle, result = optimized.fit_shared_model(target, features)
    assert len(pure_fit) == 3
    assert {row["preset"] for row in pure_fit[:2]} == {"lean", "standard"}
    assert pure_fit[-1]["matrix"].shape == (432, 46)
    assert all(list(row["matrix"]) == list(optimized.FEATURE_COLUMNS) for row in pure_fit)
    assert result["partitions"]["TRAIN"]["date_to"] < result["partitions"]["CAL"]["date_from"]
    assert result["partitions"]["CAL"]["date_to"] < result["partitions"]["TEST"]["date_from"]
    assert max(result["inner_training"]["date_to"], result["inner_validation"]["date_to"]) <= result["partitions"]["TRAIN"]["date_to"]
    assert bundle["encoder"].categories["safe_weather"] == ["sunny"]
    assert bundle["baseline"]["training_sample_count"] == 432
    assert pure_fit[-1]["matrix"].safe_weight_change.isna().all()
    assert bundle["encoder"].transform(optimized.select_features(features.iloc[-3:])).safe_weather.isna().all()
    assert bundle["feature_columns"] == list(optimized.FEATURE_COLUMNS)
    contract = bundle["model_input_contract"]
    assert contract["feature_columns"] == list(optimized.FEATURE_COLUMNS)
    assert contract["raw_feature_columns"] == list(legacy.FEATURE_COLUMNS)
    assert contract["raw_feature_pipeline"] == legacy.FEATURE_PIPELINE
    assert contract["excluded_features"] == list(optimized.EXCLUDED_FEATURES)
    assert contract["fit_feature_selection"] is False
    assert result["roi_evaluated"] is result["active_model_changed"] is result["artifact_published"] is False
    assert result["evaluation"]["primary"]["win_roi"] is None
    assert result["test_used_for_selection_or_fit"] is result["cal_used_for_preset_selection"] is False
    assert result["data_count"] == 432
    assert "既知データ" in result["evaluation"]["evaluation_label"]


@pytest.mark.parametrize("mutation", ["misaligned", "partial_race", "target_missing", "duplicate_runner", "already46"])
def test_parent_quality_rules_are_not_relaxed(pure_fit, mutation):
    target, raw = fixture_data()
    if mutation == "misaligned":
        raw.index += 1
    elif mutation == "partial_race":
        target.loc[0, "num_horses"] = 2
    elif mutation == "target_missing":
        target.loc[0, "time_seconds"] = np.nan
    elif mutation == "duplicate_runner":
        target.loc[1, "horse_number"] = 1
    else:
        raw = optimized.select_features(raw)
    with pytest.raises(ValueError):
        optimized.fit_shared_model(target, raw)
    assert pure_fit == []


def test_excluded_values_cannot_change_fit_or_prediction(pure_fit):
    target, raw = fixture_data()
    first, first_result = optimized.fit_shared_model(target, raw)
    first_calls = deepcopy(pure_fit)
    pure_fit.clear()
    changed = raw.copy()
    changed["safe_past5_top3_rate"] = np.nan
    second, second_result = optimized.fit_shared_model(target, changed)
    assert first_result["selected_preset"] == second_result["selected_preset"]
    assert first["model_input_contract"]["input_feature_sha256"] == second["model_input_contract"]["input_feature_sha256"]
    assert first["model_input_contract"]["raw_feature_sha256"] != second["model_input_contract"]["raw_feature_sha256"]
    for left, right in zip(first_calls, pure_fit):
        pd.testing.assert_frame_equal(left["matrix"], right["matrix"])
        np.testing.assert_array_equal(left["target"], right["target"])
    assert first["baseline"] == second["baseline"]
    assert first["calibrator"] == second["calibrator"]


def test_test_labels_never_change_train_preset_or_calibrator(pure_fit):
    target, raw = fixture_data()
    first, first_result = optimized.fit_shared_model(target, raw)
    first_calls = deepcopy(pure_fit)
    pure_fit.clear()
    altered = target.copy()
    positions = optimized.date_partitions(target)["TEST"]
    altered.loc[positions, "time_seconds"] += 20
    altered.loc[positions, "finish"] = np.tile([3, 2, 1], len(positions) // 3)
    second, second_result = optimized.fit_shared_model(altered, raw)
    assert first_result["selected_preset"] == second_result["selected_preset"]
    assert first["baseline"] == second["baseline"]
    assert first["calibrator"] == second["calibrator"]
    for left, right in zip(first_calls, pure_fit):
        pd.testing.assert_frame_equal(left["matrix"], right["matrix"])
        np.testing.assert_array_equal(left["target"], right["target"])
    assert first_result["evaluation"]["primary"]["rank_correlation"] != second_result["evaluation"]["primary"]["rank_correlation"]


def test_calibration_labels_do_not_change_train_or_preset(pure_fit):
    target, raw = fixture_data()
    first, first_result = optimized.fit_shared_model(target, raw)
    first_calls = deepcopy(pure_fit)
    pure_fit.clear()
    altered = target.copy()
    positions = optimized.date_partitions(target)["CAL"]
    altered.loc[positions, "finish"] = np.tile([3, 2, 1], len(positions) // 3)
    altered.loc[positions, "time_seconds"] += 20
    second, second_result = optimized.fit_shared_model(altered, raw)
    assert first_result["selected_preset"] == second_result["selected_preset"]
    assert first["baseline"] == second["baseline"]
    for left, right in zip(first_calls, pure_fit):
        pd.testing.assert_frame_equal(left["matrix"], right["matrix"])
        np.testing.assert_array_equal(left["target"], right["target"])
    assert first["calibrator"] != second["calibrator"]


def test_small_cohort_cannot_grant_win_probability_or_ev_authority(pure_fit):
    target, raw = fixture_data()
    bundle, result = optimized.fit_shared_model(target, raw)
    report = result["probability_validation"]
    assert bundle["calibrator"] is not None
    assert report["schema"] == legacy.PROBABILITY_VALIDATION_SCHEMA
    assert report["validated"] is report["eligible_for_ev"] is False
    assert report["market_edge_verified"] is report["profitability_verified"] is False
    assert "validation_races_sufficient" in report["reason_codes"]


def test_encoded_scoring_matches_single_encoding_and_roundtrip(pure_fit, tmp_path):
    target, raw = fixture_data()
    bundle, _ = optimized.fit_shared_model(target, raw)
    path = tmp_path / "fixture.joblib"
    joblib.dump(bundle, path)
    restored = joblib.load(path)
    current = optimized.select_features(raw.iloc[-6:])
    ids = target.iloc[-6:].race_id
    raw_scores, probabilities = optimized.predict_shared_scores(restored, current, ids)
    encoded = restored["encoder"].transform(current)
    repeated_scores, repeated_probabilities = optimized.predict_encoded_shared_scores(restored, encoded, ids)
    np.testing.assert_array_equal(raw_scores, repeated_scores)
    np.testing.assert_array_equal(probabilities, repeated_probabilities)
    assert np.allclose(pd.Series(probabilities).groupby(ids.reset_index(drop=True)).sum(), 1)
    assert np.isfinite(optimized.shared_speed_mps(restored, raw_scores, target.iloc[-6:])).all()
    assert len(pure_fit) == 3


@pytest.mark.parametrize("where", ["bundle_version", "columns", "contract_columns", "parent_columns", "exclusion", "profile", "encoder"])
def test_contract_or_schema_mutation_blocks_predict(pure_fit, where):
    target, raw = fixture_data()
    bundle, _ = optimized.fit_shared_model(target, raw)
    if where == "bundle_version":
        bundle["feature_pipeline"] = legacy.FEATURE_PIPELINE
    elif where == "columns":
        bundle["feature_columns"] = list(legacy.FEATURE_COLUMNS)
    elif where == "contract_columns":
        bundle["model_input_contract"]["feature_columns"] = list(reversed(optimized.FEATURE_COLUMNS))
    elif where == "parent_columns":
        bundle["model_input_contract"]["raw_feature_columns"] = []
    elif where == "exclusion":
        bundle["model_input_contract"]["excluded_features"] = []
    elif where == "profile":
        bundle["feature_profile"] = "arbitrary46"
    else:
        bundle["encoder"].feature_columns = list(legacy.FEATURE_COLUMNS)
    with pytest.raises(ValueError, match="input-source-contract-mismatch"):
        optimized.predict_shared_scores(bundle, optimized.select_features(raw.iloc[-3:]))


@pytest.mark.parametrize("matrix", ["raw47", "reversed46", "empty"])
def test_prediction_requires_exact46_input(pure_fit, matrix):
    target, raw = fixture_data()
    bundle, _ = optimized.fit_shared_model(target, raw)
    current = raw.iloc[-3:] if matrix == "raw47" else optimized.select_features(raw.iloc[-3:])
    if matrix == "reversed46":
        current = current.loc[:, list(reversed(current.columns))]
    elif matrix == "empty":
        current = current.iloc[:0]
    with pytest.raises(ValueError, match="prediction-fixed46-required"):
        optimized.predict_shared_scores(bundle, current)


def test_source_drift_and_dictionary_calibrator_fail_closed(pure_fit, monkeypatch):
    target, raw = fixture_data()
    bundle, _ = optimized.fit_shared_model(target, raw)
    current = optimized.select_features(raw.iloc[-3:])
    bundle["calibrator"] = {"temperature": 1, "validated": True}
    with pytest.raises(ValueError, match="calibrator-type-required"):
        optimized.predict_shared_scores(bundle, current)
    monkeypatch.setattr(optimized, "source_hashes", lambda: {"fixture-feature-formula": "changed"})
    with pytest.raises(ValueError, match="input-source-contract-mismatch"):
        optimized.predict_shared_scores(bundle, current)


@pytest.mark.parametrize("n_threads", [True, 0, 5, "2", 2.5])
def test_prediction_native_threads_remain_bounded(pure_fit, n_threads):
    target, raw = fixture_data()
    bundle, _ = optimized.fit_shared_model(target, raw)
    with pytest.raises(ValueError, match="native-threads-invalid"):
        optimized.predict_shared_scores(bundle, optimized.select_features(raw.iloc[-3:]), n_threads=n_threads)


def test_profile_and_frozen_source_hashes_are_all_bound():
    hashes = optimized.source_hashes()
    assert hashes["training.db_shared_optimized"] == hashlib.sha256(Path(optimized.__file__).read_bytes()).hexdigest()
    assert "training.db_shared_profiles" in hashes
    assert {key: hashes[key] for key in legacy.source_hashes()} == legacy.source_hashes()
    assert optimized.feature_source_hashes()["training.db_shared_optimized"] == hashes["training.db_shared_optimized"]


def test_long_lived_import_cannot_attest_edited_profile_source(monkeypatch):
    monkeypatch.setattr(optimized, "_own_hash", lambda: "changed")
    with pytest.raises(ValueError, match="source-changed-since-import"):
        optimized.source_hashes()


def receipt(requested="cpu", resolved="cpu", fallback=None):
    record = {"schema": acceleration.SCHEMA, "requested_device": requested,
        "resolved_device": resolved, "fallback_reason": fallback,
        "fallback_before_fit_only": True, "maximum_cpu_native_threads": 8,
        "maximum_concurrent_gpu_fits": 1,
        "probe": {"python": str(acceleration.APP_PYTHON), "success": True,
                  "gpu_device_verified": resolved == "gpu"}}
    record["receipt_sha256"] = acceleration._canonical_sha(record)
    return record


def test_cpu_acceleration_context_never_mutates_legacy47(pure_fit):
    target, raw = fixture_data()
    original_legacy = (legacy._resource_limits, legacy._train_booster, legacy.FEATURE_COLUMNS,
                       legacy.FEATURE_PIPELINE)
    original_optimized = (optimized._resource_limits, optimized._train_booster)
    value = receipt()
    token = acceleration._RECEIPT.set(value)
    try:
        bundle, result = optimized.accelerated_fit_shared_model(target, raw, compute_device="cpu")
    finally:
        acceleration._RECEIPT.reset(token)
    assert (legacy._resource_limits, legacy._train_booster, legacy.FEATURE_COLUMNS,
            legacy.FEATURE_PIPELINE) == original_legacy
    assert (optimized._resource_limits, optimized._train_booster) == original_optimized
    assert bundle["training_execution"]["device"] == "cpu"
    assert result["training_execution"]["partial_fit_cpu_fallback"] is False


@pytest.mark.parametrize("mode", ["no_receipt", "unbound", "different_hash", "requested_device", "bad_hash", "unknown_device"])
def test_execution_receipt_fences_before_fit(pure_fit, mode):
    value = receipt()
    current = value
    explicit = value
    device = "cpu"
    if mode == "no_receipt":
        current = explicit = None
    elif mode == "unbound":
        current = None
    elif mode == "different_hash":
        current = receipt("auto", "cpu", "fixture")
    elif mode == "requested_device":
        device = "gpu"
    elif mode == "bad_hash":
        explicit = {**value, "receipt_sha256": "bad"}
    else:
        device = "cuda"
    token = acceleration._RECEIPT.set(current)
    try:
        with pytest.raises(ValueError):
            optimized.accelerated_fit_shared_model(None, None, compute_device=device,
                                                   execution_receipt=explicit)
    finally:
        acceleration._RECEIPT.reset(token)
    assert pure_fit == []


def test_gpu_context_receives_only_new_module_and_requires_three_fit_receipts(pure_fit, monkeypatch):
    target, raw = fixture_data()
    value = receipt("gpu", "gpu")
    original_legacy = (legacy._resource_limits, legacy._train_booster)

    @contextmanager
    def device_context(module, device, records):
        assert module is sys.modules[optimized.__name__]
        assert device == "gpu"
        yield
        records.extend([{"gpu_device_verified": True}] * 3)

    monkeypatch.setattr(acceleration, "_fit_device", device_context)
    token = acceleration._RECEIPT.set(value)
    try:
        bundle, result = optimized.accelerated_fit_shared_model(target, raw, compute_device="gpu")
    finally:
        acceleration._RECEIPT.reset(token)
    assert bundle["training_execution"]["gpu_device_verified"] is True
    assert len(result["training_execution"]["gpu_fit_records"]) == 3
    assert (legacy._resource_limits, legacy._train_booster) == original_legacy


def test_gpu_failure_never_falls_back_to_cpu(pure_fit, monkeypatch):
    target, raw = fixture_data()
    value = receipt("gpu", "gpu")
    attempts = []

    @contextmanager
    def device_context(module, device, records):
        attempts.append(device)
        raise RuntimeError("GPU fit failed")
        yield

    monkeypatch.setattr(acceleration, "_fit_device", device_context)
    token = acceleration._RECEIPT.set(value)
    try:
        with pytest.raises(RuntimeError, match="GPU fit failed"):
            optimized.accelerated_fit_shared_model(target, raw, compute_device="gpu")
    finally:
        acceleration._RECEIPT.reset(token)
    assert attempts == ["gpu"]
    assert pure_fit == []


@pytest.mark.parametrize("count,verified", [(0, True), (2, True), (4, True), (3, False)])
def test_missing_or_failed_gpu_evidence_rejects_finished_bundle(pure_fit, monkeypatch, count, verified):
    target, raw = fixture_data()
    value = receipt("gpu", "gpu")

    @contextmanager
    def device_context(module, device, records):
        yield
        records.extend([{"gpu_device_verified": verified}] * count)

    monkeypatch.setattr(acceleration, "_fit_device", device_context)
    token = acceleration._RECEIPT.set(value)
    try:
        with pytest.raises(ValueError, match="gpu-fit-evidence-incomplete"):
            optimized.accelerated_fit_shared_model(target, raw, compute_device="gpu")
    finally:
        acceleration._RECEIPT.reset(token)


def test_actual_gpu_context_restores_new_globals_on_preparation_failure():
    value = receipt("gpu", "gpu")
    before = (optimized._resource_limits, optimized._train_booster)
    legacy_before = (legacy._resource_limits, legacy._train_booster, legacy.FEATURE_COLUMNS)
    token = acceleration._RECEIPT.set(value)
    try:
        with pytest.raises(ValueError, match="dataframes-required"):
            optimized.accelerated_fit_shared_model(None, None, compute_device="gpu")
    finally:
        acceleration._RECEIPT.reset(token)
    assert (optimized._resource_limits, optimized._train_booster) == before
    assert (legacy._resource_limits, legacy._train_booster, legacy.FEATURE_COLUMNS) == legacy_before


def test_real_small_lightgbm_fixture_uses46_with_frozen_legacy_intact(monkeypatch):
    before = legacy.source_hashes()
    monkeypatch.setattr(optimized, "_resource_limits", lambda *_: (1, 1))
    target, raw = fixture_data(days=30, races_per_day=2)
    bundle, result = optimized.fit_shared_model(target, raw, n_threads=1)
    assert bundle["model"].num_feature() == 46
    assert bundle["model"].feature_name() == list(optimized.FEATURE_COLUMNS)
    raw_scores, probability = optimized.predict_shared_scores(bundle, optimized.select_features(raw.iloc[-3:]), n_threads=1)
    assert np.isfinite(raw_scores).all()
    assert probability is not None and np.isfinite(probability).all()
    assert result["native_threads"] == 1
    assert result["probability_validation"]["validated"] is False
    assert legacy.source_hashes() == before
