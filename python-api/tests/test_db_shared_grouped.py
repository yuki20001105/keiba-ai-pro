"""Trial adoption must not retrofit old teachers, models or frozen globals."""
from copy import deepcopy
import importlib
import importlib.util

import joblib
import numpy as np
import pandas as pd
import pytest

from training import db_shared_fit as legacy
from training import db_shared_optimized as optimized
from training.grouped_speed_baseline import fit_group_baseline, normalization, teacher
from test_db_shared_optimized import fixture_data, receipt


def module():
    assert importlib.util.find_spec("training.db_shared_grouped"), "conditioned target adapter is missing"
    return importlib.import_module("training.db_shared_grouped")


def conditions():
    target, raw = fixture_data()
    day = pd.factorize(target.race_date)[0]
    target["venue"] = np.where(day % 2, "Tokyo", "Kyoto")
    target["field_condition"] = np.where(day % 3, "良", "稍重")
    target["weather"] = np.where(day % 4, "晴", "雨")
    target["time_seconds"] += np.where(day % 2, 0, 5)
    return target, raw


def test_new_fit_uses_grouped_inner_and_full_train_without_mutating_legacy(monkeypatch):
    grouped = module()
    target, raw = conditions()
    old_fit, old_teacher = optimized.fit_speed_deviation_baseline, legacy.apply_speed_deviation_baseline
    calls = []
    real_train = grouped._train_booster

    def train(matrix, labels, preset, **kwargs):
        calls.append(np.asarray(labels).copy())
        return real_train(matrix, labels, preset, **kwargs)

    monkeypatch.setattr(grouped, "_train_booster", train)
    bundle, result = grouped.fit_shared_model(target, raw, n_threads=1)
    splits = optimized.date_partitions(target)
    full = target.iloc[splits["TRAIN"]].reset_index(drop=True)
    dates = np.sort(full.race_date.unique())
    inner = full.loc[full.race_date.isin(dates[:int(.8 * len(dates))])].reset_index(drop=True)
    np.testing.assert_allclose(calls[0], teacher(inner, fit_group_baseline(inner, "course_going_weather")))
    np.testing.assert_allclose(calls[-1], teacher(full, fit_group_baseline(full, "course_going_weather")))
    assert bundle["baseline"]["base"]["training_sample_count"] == len(full)
    assert len(bundle["baseline"]["levels"]) == 3
    assert bundle["baseline"]["levels"][0]
    assert optimized.fit_speed_deviation_baseline is old_fit
    assert legacy.apply_speed_deviation_baseline is old_teacher
    assert bundle["feature_columns"] == list(optimized.FEATURE_COLUMNS)
    assert result["test_used_for_selection_or_fit"] is False
    assert result["cal_used_for_preset_selection"] is False
    assert bundle["reference_only"] is True
    assert bundle["probability_validation"]["eligible_for_ev"] is False


def test_cal_and_test_speeds_cannot_change_train_baseline_or_booster():
    grouped = module()
    target, raw = conditions()
    first, result = grouped.fit_shared_model(target, raw, n_threads=1)
    changed = target.copy()
    splits = optimized.date_partitions(target)
    changed.loc[np.concatenate([splits["CAL"], splits["TEST"]]), "time_seconds"] += 20
    second, _ = grouped.fit_shared_model(changed, raw, n_threads=1)
    assert first["baseline"] == second["baseline"]
    assert first["model"].model_to_string() == second["model"].model_to_string()
    assert first["calibrator"] == second["calibrator"]
    assert result["evaluation"]["target_normalization"]["arm"] == "course_going_weather"


def test_saved_bundle_scores_and_physical_inverse_use_the_same_grouped_baseline(tmp_path):
    grouped = module()
    target, raw = conditions()
    bundle, _ = grouped.fit_shared_model(target, raw, n_threads=1)
    path = tmp_path / "grouped.joblib"
    joblib.dump(bundle, path)
    restored = joblib.load(path)
    current = target.iloc[-6:].reset_index(drop=True)
    inputs = optimized.select_features(raw.iloc[-6:].reset_index(drop=True))
    scores, probability = grouped.predict_shared_scores(restored, inputs, current.race_id, n_threads=1)
    mean, std = normalization(current, restored["baseline"])
    np.testing.assert_allclose(grouped.shared_speed_mps(restored, scores, current), scores * std + mean)
    np.testing.assert_allclose(pd.Series(probability).groupby(current.race_id).sum(), 1)
    unknown = current.assign(venue="unseen", field_condition=None, weather=None)
    mean, std = normalization(unknown, restored["baseline"])
    np.testing.assert_allclose(grouped.shared_speed_mps(restored, scores, unknown), scores * std + mean)
    encoded = restored["encoder"].transform(inputs)
    repeated, repeated_probability = grouped.predict_encoded_shared_scores(restored, encoded, current.race_id, n_threads=1)
    np.testing.assert_array_equal(repeated, scores)
    np.testing.assert_array_equal(repeated_probability, probability)


def test_changed_normalizer_and_borrowed_old_schema_are_rejected():
    grouped = module()
    target, raw = conditions()
    bundle, _ = grouped.fit_shared_model(target, raw, n_threads=1)
    grouped._validate_prediction_bundle(bundle)
    tampered = deepcopy(bundle)
    tampered["baseline"]["base"]["global_mean"] += 1
    with pytest.raises(ValueError, match="normalization"):
        grouped._validate_prediction_bundle(tampered)
    tampered = {**bundle, "schema": "local-db-shared-optimized-model-v1"}
    with pytest.raises(ValueError, match="normalization"):
        grouped._validate_prediction_bundle(tampered)


def test_new_training_dispatch_is_separate_from_old_saved_model_dispatch():
    grouped = module()
    registry = importlib.import_module("training.db_normalized_registry")
    assert registry.fit_module(optimized.FEATURE_PIPELINE) is grouped
    assert registry.prediction_module({"feature_pipeline": optimized.FEATURE_PIPELINE,
                                      "schema": "local-db-shared-optimized-model-v1"}) is optimized
    assert registry.prediction_module({"feature_pipeline": optimized.FEATURE_PIPELINE,
                                      "schema": grouped.SCHEMA}) is grouped
    assert registry.fit_module(legacy.FEATURE_PIPELINE) is legacy


def test_input_admission_and_purchase_report_do_not_borrow_old_validation():
    grouped = module()
    from services.db_shared_input import embedded_probability_report, verify_current_bundle_sources
    target, raw = conditions()
    bundle, _ = grouped.fit_shared_model(target, raw, n_threads=1)
    verify_current_bundle_sources(bundle)
    bundle["probability_validation"].update(validated=True, eligible_for_ev=True, status="validated")
    report = embedded_probability_report(bundle, None)
    assert report["reference_only"] is True
    assert report["eligible_for_ev"] is False
    assert report["eligible_bet_types"] == []


def test_serving_keeps_raw46_and_propagates_trial_status(monkeypatch, tmp_path):
    grouped = module()
    from services import db_shared_input as serving
    from training import db_shared_features, db_context_features
    target, raw = conditions()
    bundle, _ = grouped.fit_shared_model(target, raw, n_threads=1)
    current_raw = raw.iloc[-3:].reset_index(drop=True)
    monkeypatch.setattr(db_shared_features, "build_serving_features",
        lambda *_: (current_raw, {"issue_codes": []}))
    monkeypatch.setattr(db_context_features, "build_serving_features",
        lambda *_: (pd.DataFrame(index=current_raw.index), {"issue_codes": []}))
    monkeypatch.setattr(serving, "record_pre_race_input",
        lambda **_: {"verified": False, "reason": "fixture-not-prestart"})
    matrix, diagnostics = serving.build_shared_input(bundle, target.iloc[-3:],
        db_path=tmp_path / "not-used.db", race_info={"date": "20220301"},
        model_path=tmp_path / "not-published.joblib")
    assert list(matrix) == list(optimized.FEATURE_COLUMNS)
    assert diagnostics["reference_only"] is True
    assert diagnostics["target_normalization"]["arm"] == "course_going_weather"


def test_accelerated_worker_records_normalization_and_keeps_old_modules_unchanged():
    grouped = module()
    from training import db_shared_acceleration as acceleration
    target, raw = conditions()
    original = (legacy._train_booster, optimized._train_booster, optimized._resource_limits)
    value = receipt()
    token = acceleration._RECEIPT.set(value)
    try:
        bundle, result = grouped.accelerated_fit_shared_model(target, raw,
            compute_device="cpu", execution_receipt=value, n_threads=1)
    finally:
        acceleration._RECEIPT.reset(token)
    assert (legacy._train_booster, optimized._train_booster, optimized._resource_limits) == original
    assert bundle["training_execution"]["target_normalization"]["arm"] == "course_going_weather"
    assert result["training_execution"]["target_normalization"] == bundle["target_normalization"]
