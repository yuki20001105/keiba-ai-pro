"""Opt-in context routing; no learner, network, model activation or live DB.

All artifact writes and source reads in routing tests are test-local stubs.
The old ordered 46/47 contracts retain their existing source identities.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from models import TrainRequest
from routers import predict, train
from services import db_shared_input as serving
from training import db_context_features as context_features
from training import db_context_fit as context_fit
from training import db_context_registry as registry
from training import db_shared_fit as legacy
from training import db_shared_optimized as optimized
from training.local_retrain import LocalRetrainError

from test_db_shared_acceleration_routes import _stub_shared_pipeline
from test_db_shared_serving import parameters


def _contract(columns=None, **changes):
    values = dict(selected_features=tuple(context_features.FEATURE_COLUMNS if columns is None else columns),
        removed_features=(), target="speed_deviation", model_type="lightgbm", training_parameters=parameters())
    return SimpleNamespace(**dict(values, **changes))


def _bundle():
    encoder = legacy.FoldEncoder(list(context_fit.FEATURE_COLUMNS), list(context_fit.CATEGORICAL_COLUMNS))
    encoder.feature_columns = list(context_fit.FEATURE_COLUMNS)
    encoder.transform = lambda frame: frame.copy()
    sources = context_fit.source_hashes()
    return {"schema": context_fit.SCHEMA, "feature_pipeline": registry.CONTEXT_PIPELINE,
        "feature_columns": list(context_fit.FEATURE_COLUMNS), "encoder": encoder,
        "model": object(), "target": "speed_deviation", "reference_only": True, "dataset_period_end": "20260913",
        "feature_source_hashes": context_fit.feature_source_hashes(), "source_hashes": sources,
        "fit_source_sha256": serving.file_hash(Path(context_fit.__file__)),
        "model_input_contract": {"feature_columns": list(context_fit.FEATURE_COLUMNS), "source_hashes": sources,
            "version": registry.CONTEXT_PIPELINE, "feature_pipeline": registry.CONTEXT_PIPELINE,
            "categorical_columns": list(context_fit.CATEGORICAL_COLUMNS),
            "history_policy": "source_race_date < target_race_date; no same-day outcomes",
            "current_outcomes_consumed": False, "market_features": False}}


def test_opt_in_request_does_not_replace_legacy_or_normal_default():
    assert TrainRequest().feature_pipeline == "legacy"
    assert TrainRequest(feature_pipeline=registry.CONTEXT_PIPELINE).feature_pipeline == registry.CONTEXT_PIPELINE
    assert len(context_features.FEATURE_COLUMNS) == 80
    assert context_features.FEATURE_COLUMNS[:46] == optimized.FEATURE_COLUMNS
    assert len(legacy.FEATURE_COLUMNS) == 47
    assert registry.fit_module(registry.CONTEXT_PIPELINE) is context_fit
    assert registry.fit_module(optimized.FEATURE_PIPELINE) is optimized
    assert registry.fit_module(legacy.FEATURE_PIPELINE) is legacy
    with pytest.raises(ValidationError):
        TrainRequest(feature_pipeline="local-db-context-unknown")


def test_registry_binds_exact_order_without_changing_old_contracts():
    bound = _contract()
    assert registry.bound_pipeline(bound) == registry.CONTEXT_PIPELINE
    assert registry.bound_pipeline(_contract(optimized.FEATURE_COLUMNS)) == optimized.FEATURE_PIPELINE
    assert registry.bound_pipeline(_contract(legacy.FEATURE_COLUMNS)) == legacy.FEATURE_PIPELINE
    bundle = SimpleNamespace(contract=bound, training_request_payload=lambda: parameters())
    assert registry.request_payload(bundle)["feature_pipeline"] == registry.CONTEXT_PIPELINE
    registry.verify_pipeline(bound, registry.CONTEXT_PIPELINE)
    with pytest.raises(LocalRetrainError, match="binding-mismatch"):
        registry.verify_pipeline(bound, optimized.FEATURE_PIPELINE)


@pytest.mark.parametrize("columns", [tuple(reversed(context_features.FEATURE_COLUMNS)),
    context_features.FEATURE_COLUMNS[:-1], context_features.FEATURE_COLUMNS + ("unknown",)])
def test_unknown_reordered_and_partial_profiles_do_not_receive_context_authority(columns):
    assert registry.bound_pipeline(_contract(columns)) == "legacy"
    with pytest.raises(LocalRetrainError, match="binding-mismatch"):
        registry.verify_pipeline(_contract(columns), registry.CONTEXT_PIPELINE)


@pytest.mark.parametrize("changes", [{"target": "win"}, {"model_type": "catboost"},
    {"removed_features": ("ctx_gate_rank_delta",)}, {"training_parameters": parameters(use_optuna=True)},
    {"training_parameters": parameters(use_optimizer=False)}])
def test_context_columns_do_not_bypass_bound_fit_mode(changes):
    with pytest.raises(LocalRetrainError, match="context-contract-invalid"):
        registry.bound_pipeline(_contract(**changes))


@pytest.mark.parametrize(("requested", "resolved", "reuse"), [("cpu", "cpu", True),
    ("auto", "gpu", True), ("gpu", "gpu", False)])
def test_context_normal_route_keeps_snapshot_lease_and_explicit_profile(
    monkeypatch, tmp_path, requested, resolved, reuse,
):
    seen, execution, runtime, receipt, _sources = _stub_shared_pipeline(
        monkeypatch, tmp_path, requested=requested, resolved=resolved)
    context_matrix = pd.DataFrame({name: [1., 2.] for name in context_features.FEATURE_COLUMNS})
    context_sources = {"context-test-source": "7" * 64}

    def prepare(path, date_from, date_to, cache_root, **kwargs):
        seen["prepared"].append((path, date_from, date_to, cache_root, kwargs))
        return pd.DataFrame({"race_date": ["20160105", "20260913"]}), context_matrix, {}, receipt

    def fit(target, matrix, **kwargs):
        assert matrix is context_matrix
        seen["fit"].append(kwargs)
        return {}, {"evaluation": {"primary": {"rank_correlation": .4, "rmse": .25}},
            "probability_validation": {"calibrated": True, "validated": False},
            "data_count": 2, "race_count": 2, "training_parallel_workers": 2, "training_native_threads": 2}

    monkeypatch.setattr(context_fit, "prepare_inputs", prepare)
    monkeypatch.setattr(context_fit, "source_hashes", lambda: context_sources)
    monkeypatch.setattr(context_fit, "feature_source_hashes", lambda: context_sources)
    monkeypatch.setattr(context_fit, "accelerated_fit_shared_model", fit)
    monkeypatch.setattr(train, "_active_model_binding", lambda: pytest.fail("context route cannot activate"))
    request = TrainRequest(target="speed_deviation", feature_pipeline=registry.CONTEXT_PIPELINE,
        compute_device=requested, reuse_features=reuse, force_sync=False,
        training_date_from="2016-01", training_date_to="2026-09")
    response = asyncio.run(train._do_shared_db_train(request, execution, lambda *_: None))
    assert seen["selected"][0][0] == list(context_features.FEATURE_COLUMNS)
    assert seen["prepared"][0][3] == tmp_path / "cache"
    assert seen["prepared"][0][4]["expected_source_hashes"] == context_sources
    assert seen["prepared"][0][4]["reuse_features"] is reuse
    assert seen["fit"][0]["execution_receipt"] is runtime
    assert response.feature_count == 80
    assert response.feature_pipeline == registry.CONTEXT_PIPELINE
    saved, destination = seen["artifacts"][0]
    assert saved["local_execution"]["active_model_id"] == "active-preserved"
    assert saved["feature_source_hashes"] == context_sources
    assert destination.name.startswith(serving.PREFIX)
    assert not list(tmp_path.glob("*.joblib"))


@pytest.mark.parametrize("captured", [False, True])
def test_context_serving_dispatch_keeps_nan_and_never_upgrades_purchase_authority(monkeypatch, tmp_path, captured):
    bundle = _bundle()
    matrix = pd.DataFrame({name: [1., 2.] for name in context_features.FEATURE_COLUMNS})
    matrix.loc[0, "ctx_early_position_mean5"] = np.nan
    before = matrix.copy(deep=True)
    monkeypatch.setattr(context_features, "build_serving_features", lambda *_: (
        matrix, {"issue_codes": ["card_capture_provenance_unverified"], "context_caveats": {"pace_is_proxy": True}}))
    monkeypatch.setattr(serving, "record_pre_race_input", lambda **_: {"verified": True} if captured else
        {"verified": False, "reason": "pre_race_db_capture_unverified"})
    encoded, diagnostic = serving.build_shared_input(bundle, pd.DataFrame({"horse_number": [1, 2]}),
        db_path=tmp_path / "never-open.db", race_info={"date": "20261020"}, model_path=tmp_path / "never-load.joblib")
    pd.testing.assert_frame_equal(matrix, before)
    assert list(encoded) == list(context_features.FEATURE_COLUMNS)
    assert pd.isna(encoded.loc[0, "ctx_early_position_mean5"])
    assert diagnostic["expected_count"] == diagnostic["actual_count"] == 80
    assert diagnostic["reference_only"] is True
    assert diagnostic["context_caveats"] == {"pace_is_proxy": True}
    assert diagnostic["live_capture_hash_bound"] is captured
    assert diagnostic["status"] == ("aligned" if captured else "unverified")
    assert serving.embedded_probability_report(bundle, tmp_path / "never-load.joblib")["validated"] is False
    assert not (tmp_path / "never-open.db").exists()


@pytest.mark.parametrize("field", ["feature_columns", "feature_source_hashes", "fit_source_sha256"])
def test_context_bundle_mismatch_cannot_fall_through_to_legacy(field):
    bundle = _bundle()
    bundle[field] = [] if field == "feature_columns" else {} if field == "feature_source_hashes" else "0" * 64
    with pytest.raises(ValueError, match="context-db-feature-contract-source-mismatch"):
        serving.verify_bundle_sources(bundle)


def test_context_inference_uses_its_own_fixed_model_and_columns(monkeypatch):
    bundle = _bundle()
    seen = []
    matrix = pd.DataFrame({name: [1., 2.] for name in context_features.FEATURE_COLUMNS})
    def infer(actual, encoded, **kwargs):
        seen.append((actual, encoded, kwargs))
        return np.array([2., 1.]), np.array([.6, .4])
    monkeypatch.setattr(context_fit, "predict_encoded_shared_scores", infer)
    monkeypatch.setattr(legacy, "predict_encoded_shared_scores", lambda *_a, **_k: pytest.fail("cannot use old47"))
    monkeypatch.setattr(optimized, "predict_encoded_shared_scores", lambda *_a, **_k: pytest.fail("cannot use old46"))
    scores, probability = predict.ModelPredictor(bundle, inference_threads=2).predict_scores(matrix)
    assert seen == [(bundle, matrix, {"n_threads": 2})]
    assert scores.tolist() == [2., 1.] and probability.tolist() == [.6, .4]


@pytest.mark.parametrize("reason", ["context-source-changed-during-preparation-retry",
    "context-source-changed-during-prediction-retry"])
def test_context_updates_during_preparation_request_retry_without_legacy_fallback(monkeypatch, tmp_path, reason):
    def changed(*_):
        raise ValueError(reason)
    monkeypatch.setattr(context_features, "build_serving_features", changed)
    with pytest.raises(HTTPException) as captured:
        serving.build_shared_input(_bundle(), pd.DataFrame({"horse_number": [1, 2]}),
            db_path=tmp_path / "never-open.db", race_info={"date": "20261020"}, model_path=tmp_path / "never-load.joblib")
    assert captured.value.status_code == 409
    assert "取得完了後" in captured.value.detail
