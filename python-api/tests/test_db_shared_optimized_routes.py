"""Explicit46 routing tests; learners, publication and live inputs are stubs.

Only test-local input receipts are written. The old raw47 cache/formula and
active/forward state are never migrated or granted new calibration authority.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from models import TrainRequest
from routers import train, predict
from services import db_shared_input as serving
from training import db_shared_contract as contract
from training import db_shared_features as raw
from training import db_shared_fit as legacy
from training import db_shared_optimized as optimized
from training.db_shared_profiles import BASELINE_PIPELINE, OPTIMIZED_PIPELINE
from training.local_retrain import LocalRetrainError

# Reuse the established fully mocked pipeline and reference arithmetic fixtures.
from test_db_shared_acceleration_routes import _stub_shared_pipeline
from test_db_shared_serving import parameters, receipt_args
from test_reference_expectation import build as reference_build, context as reference_context


def _request(**changes):
    values = dict(target="speed_deviation", feature_pipeline=OPTIMIZED_PIPELINE,
                  force_sync=False, training_date_from="2016-01", training_date_to="2026-09")
    return TrainRequest(**dict(values, **changes))


def _contract(columns=None, **changes):
    values = dict(selected_features=tuple(optimized.FEATURE_COLUMNS if columns is None else columns),
                  removed_features=(), target="speed_deviation", model_type="lightgbm",
                  training_parameters=parameters())
    return SimpleNamespace(**dict(values, **changes))


def test_request_default_and_previous47_do_not_silently_change():
    assert TrainRequest().feature_pipeline == "legacy"
    assert _request(feature_pipeline=BASELINE_PIPELINE).feature_pipeline == BASELINE_PIPELINE
    assert _request().feature_pipeline == OPTIMIZED_PIPELINE
    assert tuple(raw.FEATURE_COLUMNS) == tuple(legacy.FEATURE_COLUMNS)
    assert len(raw.FEATURE_COLUMNS) == 47
    assert "safe_past5_top3_rate" in raw.FEATURE_COLUMNS
    assert "safe_past5_top3_rate" not in optimized.FEATURE_COLUMNS


@pytest.mark.parametrize("value", ["optimized46", "unknown", "local-db-shared-v2", 46, None])
def test_unknown_feature_request_profiles_fail_pydantic_validation(value):
    with pytest.raises(ValidationError):
        _request(feature_pipeline=value)


def test_profile46_lease_binds_exact_order_and_cannot_be_relabelled47():
    bound = _contract()
    assert contract.bound_pipeline(bound) == OPTIMIZED_PIPELINE
    bundle = SimpleNamespace(contract=bound, training_request_payload=lambda: parameters())
    assert contract.request_payload(bundle)["feature_pipeline"] == OPTIMIZED_PIPELINE
    contract.verify_pipeline(bound, OPTIMIZED_PIPELINE)
    with pytest.raises(LocalRetrainError, match="binding-mismatch"):
        contract.verify_pipeline(bound, BASELINE_PIPELINE)
    assert contract.bound_pipeline(_contract(raw.FEATURE_COLUMNS)) == BASELINE_PIPELINE
    with pytest.raises(LocalRetrainError, match="binding-mismatch"):
        contract.verify_pipeline(_contract(raw.FEATURE_COLUMNS), OPTIMIZED_PIPELINE)


@pytest.mark.parametrize("columns", [
    tuple(reversed(optimized.FEATURE_COLUMNS)),
    tuple(optimized.FEATURE_COLUMNS[:-1]),
    tuple(optimized.FEATURE_COLUMNS) + (optimized.FEATURE_COLUMNS[0],),
    tuple(name for name in raw.FEATURE_COLUMNS if name != "safe_age"),
])
def test_reordered_duplicate_or_different_subset_has_no46_authority(columns):
    bound = _contract(columns)
    assert contract.bound_pipeline(bound) == "legacy"
    with pytest.raises(LocalRetrainError, match="binding-mismatch"):
        contract.verify_pipeline(bound, OPTIMIZED_PIPELINE)


@pytest.mark.parametrize("changes", [
    {"target": "win"}, {"model_type": "catboost"},
    {"removed_features": ("safe_past5_top3_rate",)},
    {"training_parameters": parameters(use_optuna=True)},
    {"training_parameters": parameters(use_optimizer=False)},
])
def test_recognized46_columns_do_not_bypass_existing_contract_modes(changes):
    with pytest.raises(LocalRetrainError, match="shared-contract-invalid"):
        contract.bound_pipeline(_contract(**changes))


@pytest.mark.parametrize(("requested", "resolved", "reuse", "hit"), [
    ("auto", "gpu", True, True), ("gpu", "gpu", True, False),
    ("cpu", "cpu", False, False), ("auto", "cpu", True, True),
])
def test46_route_reuses_raw47_cache_but_binds_saved_model_to46(
    monkeypatch, tmp_path, requested, resolved, reuse, hit,
):
    seen, execution, runtime, _receipt, raw_sources = _stub_shared_pipeline(
        monkeypatch, tmp_path, requested=requested, resolved=resolved, cache_hit=hit)
    # The established helper asserts that fit receives the exact raw47 matrix.
    from training import db_shared_acceleration as acceleration
    mocked_fit = acceleration.accelerated_fit_shared_model
    profile_sources = {**raw_sources, "training.db_shared_optimized": "8" * 64}
    monkeypatch.setattr(optimized, "accelerated_fit_shared_model", mocked_fit)
    monkeypatch.setattr(optimized, "feature_source_hashes", lambda: profile_sources)
    monkeypatch.setattr(train, "_active_model_binding",
                        lambda: pytest.fail("46 training must not switch or mutate active state"))
    sentinel = tmp_path / "preserved-active-and-forward.json"
    sentinel.write_text('{"active":"unchanged","forward":"unchanged"}', encoding="utf-8")
    before = sentinel.read_bytes()
    response = asyncio.run(train._do_shared_db_train(
        _request(compute_device=requested, reuse_features=reuse), execution,
        lambda message, pct: seen["progress"].append((message, pct))))
    assert tuple(seen["selected"][0][0]) == tuple(optimized.FEATURE_COLUMNS)
    assert seen["prepared"][0][4]["expected_source_hashes"] == raw_sources
    assert seen["prepared"][0][4]["reuse_features"] is reuse
    assert seen["fit"][0]["compute_device"] == requested
    assert seen["fit"][0]["execution_receipt"] is runtime
    assert response.feature_count == 46
    assert response.feature_columns == list(optimized.FEATURE_COLUMNS)
    assert response.feature_pipeline == OPTIMIZED_PIPELINE
    assert response.acceleration["device"] == resolved
    assert response.acceleration["cache_hit"] is hit
    saved, destination = seen["artifacts"][0]
    assert saved["feature_pipeline"] == OPTIMIZED_PIPELINE
    assert saved["feature_columns"] == list(optimized.FEATURE_COLUMNS)
    assert saved["feature_source_hashes"] == profile_sources
    assert saved["pipeline_config"]["shared_db_features"] == OPTIMIZED_PIPELINE
    assert saved["local_execution"]["active_model_id"] == "active-preserved"
    assert saved["fit_source_sha256"] == "c" * 64
    assert destination.parent == tmp_path
    assert sentinel.read_bytes() == before
    assert not list(tmp_path.glob("*.joblib"))  # Writer is a stub; no registry publication.
    assert list(raw.FEATURE_COLUMNS) == list(legacy.FEATURE_COLUMNS)
    assert len(legacy.FEATURE_COLUMNS) == 47


def test46_cache_snapshot_mismatch_never_fits_or_saves(monkeypatch, tmp_path):
    seen, execution, _runtime, receipt, _sources = _stub_shared_pipeline(monkeypatch, tmp_path)
    monkeypatch.setattr(optimized, "accelerated_fit_shared_model",
                        lambda *_args, **_kwargs: pytest.fail("fit after wrong snapshot"))
    receipt["snapshot_sha256"] = "9" * 64
    with pytest.raises(LocalRetrainError, match="cache-snapshot-mismatch"):
        asyncio.run(train._do_shared_db_train(_request(), execution, lambda *_args: None))
    assert not seen["fit"] and not seen["artifacts"]


def _bundle46():
    sources = optimized.source_hashes()
    encoder = optimized.FoldEncoder(list(optimized.FEATURE_COLUMNS), list(optimized.CATEGORICAL_COLUMNS))
    encoder.feature_columns = list(optimized.FEATURE_COLUMNS)
    # No fitted learner or training run; an identity transform for fixture numeric inputs.
    encoder.transform = lambda frame: frame.copy()
    return {
        "schema": "local-db-shared-optimized-model-v1", "feature_pipeline": OPTIMIZED_PIPELINE,
        "feature_profile": optimized.PROFILE_ID, "feature_columns": list(optimized.FEATURE_COLUMNS),
        "feature_source_hashes": optimized.feature_source_hashes(),
        "source_hashes": sources, "fit_source_sha256": serving.file_hash(Path(optimized.__file__)),
        "model": object(), "encoder": encoder, "target": "speed_deviation",
        "dataset_period_end": "20260913",
        "model_input_contract": {
            "version": optimized.INPUT_CONTRACT_VERSION, "feature_pipeline": OPTIMIZED_PIPELINE,
            "feature_profile": optimized.PROFILE_ID, "feature_columns": list(optimized.FEATURE_COLUMNS),
            "categorical_columns": list(optimized.CATEGORICAL_COLUMNS), "source_hashes": sources,
            "raw_feature_pipeline": BASELINE_PIPELINE, "raw_feature_columns": list(raw.FEATURE_COLUMNS),
            "excluded_features": list(optimized.EXCLUDED_FEATURES),
        },
    }


def test46_saved_bundle_has_separate_source_and_ordered_column_contract():
    serving.verify_bundle_sources(_bundle46())


@pytest.mark.parametrize("change", ["wrong_pipeline", "wrong_columns", "wrong_order", "wrong_source",
                                   "wrong_fit_source", "wrong_encoder", "wrong_raw", "wrong_profile"])
def test46_bundle_source_and_column_forgery_fails_closed(change):
    bundle = _bundle46()
    if change == "wrong_pipeline": bundle["feature_pipeline"] = BASELINE_PIPELINE
    if change == "wrong_columns": bundle["feature_columns"] = list(raw.FEATURE_COLUMNS)
    if change == "wrong_order": bundle["feature_columns"].reverse()
    if change == "wrong_source": bundle["feature_source_hashes"] = raw.source_hashes()
    if change == "wrong_fit_source": bundle["fit_source_sha256"] = "0" * 64
    if change == "wrong_encoder": bundle["encoder"].feature_columns = list(raw.FEATURE_COLUMNS)
    if change == "wrong_raw": bundle["model_input_contract"]["raw_feature_columns"] = list(optimized.FEATURE_COLUMNS)
    if change == "wrong_profile": bundle["feature_profile"] = "unknown"
    with pytest.raises(ValueError, match="contract|source"):
        serving.verify_bundle_sources(bundle)


@pytest.mark.parametrize("captured", [True, False])
def test_serving46_subsets_raw47_preserves_nan_and_requires_actual_capture(monkeypatch, tmp_path, captured):
    bundle = _bundle46()
    matrix = pd.DataFrame({name: [1., 2.] for name in raw.FEATURE_COLUMNS})
    matrix.loc[0, "safe_prev2_speed_mps"] = np.nan
    before = matrix.copy(deep=True)
    seen = []
    monkeypatch.setattr(raw, "build_serving_features",
                        lambda *_args: (matrix, {"issue_codes": ["card_capture_provenance_unverified"]}))
    def record(**kwargs):
        seen.append(kwargs)
        return ({"verified": True, "receipt_sha256": "a" * 64} if captured else
                {"verified": False, "reason": "pre_race_db_capture_unverified"})
    monkeypatch.setattr(serving, "record_pre_race_input", record)
    encoded, diagnostics = serving.build_shared_input(bundle,
        pd.DataFrame({"horse_number": [1, 2]}), db_path=tmp_path / "no-db-access.db",
        race_info={"date": "20261011"}, model_path=tmp_path / "no-model-access.joblib",
        trusted_context={"trusted_db_card": False})
    assert list(encoded) == list(optimized.FEATURE_COLUMNS)
    assert pd.isna(encoded.loc[0, "safe_prev2_speed_mps"])
    pd.testing.assert_frame_equal(matrix, before)
    assert list(seen[0]["features"]) == list(optimized.FEATURE_COLUMNS)
    assert diagnostics["feature_pipeline"] == diagnostics["scope"] == OPTIMIZED_PIPELINE
    assert diagnostics["expected_count"] == diagnostics["actual_count"] == 46
    assert diagnostics["status"] == ("aligned" if captured else "unverified")
    assert diagnostics["live_capture_hash_bound"] is captured
    assert diagnostics["issue_codes"] == ([] if captured else ["pre_race_db_capture_unverified"])
    assert not tmp_path.joinpath("no-db-access.db").exists()


def test_serving46_never_accepts_reordered_encoded_output(monkeypatch, tmp_path):
    bundle = _bundle46()
    bundle["encoder"].transform = lambda frame: frame.loc[:, list(reversed(frame.columns))]
    monkeypatch.setattr(raw, "build_serving_features", lambda *_: (
        pd.DataFrame({name: [1., 2.] for name in raw.FEATURE_COLUMNS}), {"issue_codes": []}))
    monkeypatch.setattr(serving, "record_pre_race_input",
                        lambda **_: pytest.fail("capture before encoder schema admission"))
    with pytest.raises(ValueError, match="encoder-column-mismatch"):
        serving.build_shared_input(bundle, pd.DataFrame({"horse_number": [1, 2]}),
            db_path=tmp_path / "unused.db", race_info={"date": "20261011"},
            model_path=tmp_path / "unused.joblib")


@pytest.mark.parametrize("problem", ["client", "result", "post", "stale", "issues", "unknown"])
def test46_actual_capture_gate_remains_fail_closed_without_writes(tmp_path, problem):
    args = receipt_args(tmp_path)
    args["diagnostics"]["feature_pipeline"] = OPTIMIZED_PIPELINE
    if problem == "client": args["trusted_context"] = {"verified": True}
    if problem == "result": args["trusted_context"]["race_has_result"] = True
    if problem == "post": args["info"]["post_time"] = "09:30"
    if problem == "stale": args["info"]["data_observed_at"] = "2026-10-11T00:00:00Z"
    if problem == "issues": args["diagnostics"]["issue_codes"] = ["source_mismatch"]
    if problem == "unknown": args["diagnostics"]["feature_pipeline"] = "unknown"
    assert serving.record_pre_race_input(**args)["verified"] is False
    assert not args["receipt_path"].exists()


def test46_receipt_pins_exact_subset_and_nan_without_rewriting_raw47(tmp_path, monkeypatch):
    args = receipt_args(tmp_path)
    args["diagnostics"]["feature_pipeline"] = OPTIMIZED_PIPELINE
    args["features"] = pd.DataFrame({name: [np.nan, 1.] for name in optimized.FEATURE_COLUMNS})
    monkeypatch.setattr(serving, "validate_published_artifact", lambda _: "a" * 64)
    proof = serving.record_pre_race_input(**args)
    assert proof["verified"] is True
    with sqlite3.connect(args["receipt_path"]) as connection:
        digest, body = connection.execute("SELECT receipt_sha256,payload_json FROM input_receipts").fetchone()
        data = json.loads(body)
        assert digest == hashlib.sha256(body.encode()).hexdigest()
        assert data["feature_pipeline"] == OPTIMIZED_PIPELINE
        assert data["columns"] == list(optimized.FEATURE_COLUMNS)
        assert data["values"][0] == [None] * 46
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM input_receipts")


def test_predictor_dispatches46_to_its_own_inference_not47(monkeypatch):
    bundle = _bundle46()
    bundle["calibrator"] = object()
    bundle["probability_validation"] = {"validated": True}
    inputs = pd.DataFrame({name: [1., 2.] for name in optimized.FEATURE_COLUMNS})
    seen = []
    def inference(actual, encoded, **kwargs):
        seen.append((actual, encoded, kwargs))
        return np.array([1., 2.]), np.array([.4, .6])
    monkeypatch.setattr(optimized, "predict_encoded_shared_scores", inference)
    monkeypatch.setattr(legacy, "predict_encoded_shared_scores",
                        lambda *_args, **_kwargs: pytest.fail("46 cannot use47 model path"))
    predictor = predict.ModelPredictor(bundle, inference_threads=2)
    scores, probabilities = predictor.predict_scores(inputs)
    assert seen == [(bundle, inputs, {"n_threads": 2})]
    assert scores.tolist() == [1., 2.] and probabilities.tolist() == [.4, .6]
    assert predictor.calibrator_applied is True


@pytest.mark.parametrize(("pipeline", "expected", "actual", "accepted"), [
    (OPTIMIZED_PIPELINE, 46, 46, True), (BASELINE_PIPELINE, 47, 47, True),
    (OPTIMIZED_PIPELINE, 47, 47, False), (BASELINE_PIPELINE, 46, 46, False),
    (OPTIMIZED_PIPELINE, 46, 47, False), (OPTIMIZED_PIPELINE, 47, 46, False),
    ("unknown", 46, 46, False), ("legacy", 46, 46, False),
    (OPTIMIZED_PIPELINE, 46., 46, False), (OPTIMIZED_PIPELINE, 46, 46., False),
])
def test_reference_ev_requires_exact_server_profile_and_count(pipeline, expected, actual, accepted):
    context = reference_context()
    context["input_diagnostics"].update(feature_pipeline=pipeline, expected_count=expected, actual_count=actual)
    before = deepcopy(context)
    reference = reference_build(context=context)
    assert (reference is not None) is accepted
    assert context == before
    if accepted:
        assert reference["purchase_eligible"] is False
        assert reference["capture_verified"] is False
        assert reference["odds_freshness_verified"] is False
        assert reference["items"][0]["expected_value"] == pytest.approx(.741)


def test46_reference_requires_its_own_model_calibration_not47_borrowed_validation():
    context = reference_context()
    context["input_diagnostics"].update(feature_pipeline=OPTIMIZED_PIPELINE, expected_count=46, actual_count=46)
    context["probability_validation"]["model_id"] = "previous47-model"
    assert reference_build(context=context) is None


@pytest.mark.parametrize("device", ["auto", "cpu", "gpu"])
@pytest.mark.parametrize("forged47", [False, True])
def test_queued46_restores_profile_and_device_only_from_bound_owner_request(
    tmp_path, monkeypatch, device, forged47,
):
    from training import db_shared_acceleration as acceleration
    request = _request(compute_device=device, reuse_features=False)
    params = train._local_training_parameters(request)
    bound = _contract(training_parameters=params)
    bound.source_tree_sha256, bound.active_model_sha256 = "a" * 64, "b" * 64
    bound.candidate_commit_sha, bound.active_model_id = "c" * 40, "active-preserved"
    bundle = SimpleNamespace(contract=bound, training_request_payload=lambda: {
        "target": bound.target, "model_type": bound.model_type, **params})
    durable = request.model_dump()
    if forged47:
        durable["feature_pipeline"] = BASELINE_PIPELINE
    queued = SimpleNamespace(state="queued", contract=bound, snapshot_path=tmp_path / "unused.db",
                             request_payload=durable, record_version=2)
    seen = {"restored": [], "failed": [], "recorded": []}
    store = SimpleNamespace(get_job=lambda _: queued,
                            fail_queued=lambda **kwargs: seen["failed"].append(kwargs))
    gateway = SimpleNamespace(progress_observer=lambda _: lambda *_: None,
        stage_result=lambda *_args, **_kwargs: None,
        record_result=lambda **kwargs: seen["recorded"].append(kwargs))
    class Coordinator:
        def __init__(self, *_args, **kwargs):
            assert kwargs["active_model_id"] == "active-preserved"
            self.factory = kwargs["request_factory"]
        async def run_job(self, **_kwargs):
            seen["restored"].append(self.factory(bundle))
            return "fixture-only-artifact"
    monkeypatch.setattr(train, "_get_local_retrain_runtime", lambda: (store, gateway))
    monkeypatch.setattr(train, "ApprovedRetrainCoordinator", Coordinator)
    monkeypatch.setattr(acceleration, "current_execution_receipt", lambda: {"requested_device": device})
    asyncio.run(train._execute_queued_local_retrain("fixture46-job"))
    if forged47:
        assert not seen["restored"] and not seen["recorded"]
        assert seen["failed"] == [{"job_id": "fixture46-job", "expected_version": 2,
                                   "failure_code": "claim-runtime-binding-failed"}]
    else:
        assert not seen["failed"] and len(seen["recorded"]) == 1
        restored = seen["restored"][0]
        assert restored.feature_pipeline == OPTIMIZED_PIPELINE
        assert restored.compute_device == device and restored.reuse_features is False
        assert restored.training_date_from == "2016-01" and restored.training_date_to == "2026-09"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("problem", [None, "wrong_artifact", "uncalibrated", "unvalidated"])
def test46_embedded_calibration_requires_its_admitted_artifact(tmp_path, monkeypatch, problem):
    bundle = _bundle46()
    bundle.update(calibrator=object(), created_at="2026-10-11T01:00:00+09:00",
        _shared_artifact_sha256="a" * 64,
        probability_validation={"schema": "local-db-probability-validation-v1", "status": "validated",
            "calibrated": True, "validated": True, "eligible_for_ev": True,
            "validation_period_end": "2026-09-13"})
    if problem == "wrong_artifact": bundle["_shared_artifact_sha256"] = "b" * 64
    if problem == "uncalibrated": bundle["calibrator"] = None
    if problem == "unvalidated": bundle["probability_validation"]["validated"] = False
    monkeypatch.setattr(serving, "validate_published_artifact", lambda _: "a" * 64)
    path = tmp_path / "model_speed_deviation_lightgbm_dbshared_fixture46.joblib"
    report = serving.embedded_probability_report(bundle, path)
    assert report["validated"] is (problem is None)
    if problem is None:
        assert report["model_id"] == path.stem
        assert report["model_sha256"] == "a" * 64
        assert report["eligible_bet_types"] == ["単勝"]
    assert not path.exists()


def test46_reference_does_not_reinstate_official_ev_or_purchase_recommendation():
    from betting.value_policy import recommend
    from test_reference_expectation import NOW, PREDS, RACE
    context = reference_context()
    context["input_diagnostics"].update(feature_pipeline=OPTIMIZED_PIPELINE, expected_count=46, actual_count=46)
    selected = recommend(deepcopy(PREDS), deepcopy(RACE), bankroll=10000, risk_mode="balanced",
        min_ev=1.05, use_kelly=True, dynamic_unit=True, context=context, now=NOW)
    before = deepcopy(selected)
    reference = reference_build(predictions=selected["predictions"],
        recommendation=selected["recommendation"], context=context)
    assert reference["items"][1]["expected_value"] > 1
    assert selected == before
    assert selected["recommendation"]["decision"] == "skip"
    assert selected["recommendation"]["total_cost"] == 0
    assert selected["recommendation"]["tickets"] == []
    assert all(row["expected_value"] is None for row in selected["predictions"])

