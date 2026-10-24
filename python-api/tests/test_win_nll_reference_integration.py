"""Reference-only publication dispatch stays separate from purchase authority.

Only temporary artifacts and monkey-patched in-memory bundles are used here.
No research publication, active-model write, ledger mutation or network access.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException

import app_config
from betting import value_policy
from routers import models_mgmt, predict
from services import prediction_compute, win_nll_reference_input as reference


def _forbidden(*_args, **_kwargs):
    raise AssertionError("reference inference must not use this legacy path")


@pytest.fixture
def reference_fixture(tmp_path):
    path = tmp_path / reference.MODEL_FILENAME
    path.write_bytes(b"isolated-reference-loader-fixture")
    bundle = {
        "model": object(), "model_id": path.stem, "target": "win",
        "model_type": "lightgbm", "ultimate_mode": True,
        "feature_pipeline": reference.PIPELINE,
        "feature_columns": ["safe_fixture"], "_win_nll_reference": True,
        "reference_only": True, "purchase_authorized": False,
        "probability_validation": reference.probability_validation(),
        "calibrator": None, "created_at": "2026-10-12T00:00:00+00:00",
        "training_date_from": "20130101", "training_date_to": "20241231",
    }
    return path, bundle


@pytest.mark.parametrize("loader", [predict._load_served_model_bundle, prediction_compute._load_bundle,
                                     models_mgmt._load_managed_bundle])
def test_reference_loaders_use_guarded_publisher_before_legacy_unpickle(reference_fixture, monkeypatch, loader):
    path, bundle = reference_fixture
    calls = []
    monkeypatch.setattr(reference, "load_published_bundle", lambda actual: calls.append(Path(actual)) or bundle)
    monkeypatch.setattr(predict, "load_model_bundle", _forbidden)
    monkeypatch.setattr(app_config, "load_model_bundle", _forbidden)
    monkeypatch.setattr(models_mgmt.joblib, "load", _forbidden)
    loaded = loader(path)
    assert loaded == bundle
    assert loaded["model"] is bundle["model"]
    assert calls == [path]


@pytest.mark.parametrize("loader", [predict._load_served_model_bundle, prediction_compute._load_bundle,
                                     models_mgmt._load_managed_bundle])
def test_reference_publisher_failure_is_not_a_legacy_fallback(reference_fixture, monkeypatch, loader):
    path, _bundle = reference_fixture
    def reject(_path):
        raise ValueError("fixture-publication-hash-mismatch")
    monkeypatch.setattr(reference, "load_published_bundle", reject)
    monkeypatch.setattr(app_config, "load_model_bundle", _forbidden)
    monkeypatch.setattr(predict, "load_model_bundle", _forbidden)
    monkeypatch.setattr(models_mgmt.joblib, "load", _forbidden)
    with pytest.raises(ValueError, match="publication-hash-mismatch"):
        loader(path)


def test_reference_response_cache_checks_publication_before_cached_labels(reference_fixture, monkeypatch):
    from models import AnalyzeRaceRequest
    path, _bundle = reference_fixture
    monkeypatch.setattr(predict, "_resolve_model_path", lambda _model_id: path)
    def reject(_path):
        raise ValueError("fixture-publication-hash-mismatch")
    monkeypatch.setattr(reference, "validate_published_artifact", reject)
    monkeypatch.setattr(reference, "load_published_bundle", reject)
    class ForbiddenResponseCache(dict):
        def get(self, *_args, **_kwargs):
            raise AssertionError("unvalidated publication reached cached labels")
    monkeypatch.setattr(predict, "_ANALYZE_CACHE", ForbiddenResponseCache())
    request = AnalyzeRaceRequest(race_id="202608040511")
    with pytest.raises(ValueError, match="publication-hash-mismatch"):
        asyncio.run(predict._analyze_race_impl(request))


def test_predictor_reference_features_use_exact_adapter_and_final_probability_rank(reference_fixture, monkeypatch):
    path, bundle = reference_fixture
    source = pd.DataFrame({"horse_number": [1, 2]})
    features = pd.DataFrame({"safe_fixture": [1., 2.]})
    diagnostics = {"reference_only": True, "semantic_parity_verified": False}
    calls = []
    def build(actual_bundle, actual_source, **kwargs):
        assert actual_bundle is bundle and actual_source is source
        calls.append(kwargs)
        return features, diagnostics
    probabilities = np.array([.7, .3])
    monkeypatch.setattr(reference, "build_input", build)
    monkeypatch.setattr(reference, "predict", lambda *_args, **_kwargs: {
        "probability": probabilities, "raw_score": np.array([-.8, .8]),
    })
    predictor = predict.ModelPredictor(bundle, path, db_path=path.parent / "isolated.db",
                                       race_info={"race_id": "fixture"})
    assert predictor.build_features(source, source) is features
    assert calls[0]["model_path"] == path
    assert calls[0]["db_path"] == path.parent / "isolated.db"
    assert calls[0]["race_info"] == {"race_id": "fixture"}
    assert predictor.input_diagnostics is diagnostics
    raw, served = predictor.predict_scores(features)
    np.testing.assert_array_equal(raw, probabilities)
    np.testing.assert_array_equal(served, probabilities)
    assert np.argsort(-raw).tolist() == [0, 1]
    assert predictor.calibrator_applied is False
    with pytest.raises(ValueError, match="explanation-not-validated"):
        predictor.explain_scores(features)


def test_worker_reference_has_no_legacy_history_place3_or_false_tree_shap(reference_fixture, monkeypatch):
    path, bundle = reference_fixture
    from keiba_ai import db_ultimate_loader
    loads = []
    monkeypatch.setattr(prediction_compute, "_load_bundle", lambda actual: loads.append(actual) or bundle)
    monkeypatch.setattr(db_ultimate_loader, "load_prediction_history_frame", _forbidden)
    diagnostics = {"history_audit": {"eligible_rows": 4}, "issue_codes": ["win_nll_reference_only"],
                   "status": "unverified", "semantic_parity_verified": False, "reference_only": True}
    monkeypatch.setattr(reference, "build_input", lambda *_args, **_kwargs: (
        pd.DataFrame({"safe_fixture": [1., 2.]}), diagnostics))
    monkeypatch.setattr(reference, "predict", lambda *_args, **_kwargs: {"probability": np.array([.7, .3])})
    result = prediction_compute.compute_prediction({
        "frame": pd.DataFrame({"horse_number": [1, 2]}), "model_path": str(path),
        "db_path": str(path.parent / "isolated.db"), "excluded_race_ids": ["fixture"],
        "native_threads": 2, "include_explanation": True,
        "place3_model_path": str(path.parent / "must-not-load-place3.joblib"),
    })
    assert "error" not in result
    assert loads == [str(path)]
    assert result["place3"] is None and result["explanations"] is None
    assert result["calibrator_applied"] is False and result["history_rows"] == 4
    assert result["input_diagnostics"]["semantic_parity_verified"] is False
    np.testing.assert_array_equal(result["raw"], result["probabilities"])


def test_management_list_and_active_info_use_guarded_reference_metadata(reference_fixture, monkeypatch):
    path, bundle = reference_fixture
    calls = []
    monkeypatch.setattr(models_mgmt, "MODELS_DIR", path.parent)
    monkeypatch.setattr(models_mgmt, "SUPABASE_DATA_ENABLED", False)
    monkeypatch.setattr(models_mgmt, "get_active_model_id", lambda: path.stem)
    monkeypatch.setattr(models_mgmt, "_local_candidate_models", lambda: [])
    monkeypatch.setattr(reference, "load_published_bundle", lambda actual: calls.append(Path(actual)) or bundle)
    monkeypatch.setattr(models_mgmt.joblib, "load", _forbidden)
    listed = asyncio.run(models_mgmt.list_models(ultimate=True))
    assert listed["count"] == 1
    card = listed["models"][0]
    assert card["model_id"] == path.stem and card["is_active"] is True
    assert card["reference_only"] is True and card["can_delete"] is False
    assert card.get("can_activate") is not False
    active = asyncio.run(models_mgmt.get_active_model())
    assert active["model_id"] == path.stem and active["reference_only"] is True
    assert active["probability_validation"]["eligible_for_ev"] is False
    assert calls == [path, path]


def test_valid_reference_activation_checks_publication_before_pointer_change(reference_fixture, monkeypatch):
    path, bundle = reference_fixture
    events = []
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MODEL_ACTIVATION_LOCAL_ENABLED", "true")
    monkeypatch.setattr(models_mgmt, "MODELS_DIR", path.parent)
    monkeypatch.setattr(reference, "load_published_bundle", lambda actual: events.append(("guard", Path(actual))) or bundle)
    monkeypatch.setattr(models_mgmt, "set_active_model_id", lambda value: events.append(("pointer", value)))
    response = asyncio.run(models_mgmt.activate_model(path.stem, {}))
    assert response == {"success": True, "active_model_id": path.stem}
    assert events == [("guard", path), ("pointer", path.stem)]


def test_invalid_reference_publication_cannot_change_active_pointer(reference_fixture, monkeypatch):
    path, _bundle = reference_fixture
    changes = []
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MODEL_ACTIVATION_LOCAL_ENABLED", "true")
    monkeypatch.setattr(models_mgmt, "MODELS_DIR", path.parent)
    monkeypatch.setattr(models_mgmt, "set_active_model_id", changes.append)
    def reject(_path):
        raise ValueError("fixture-publication-hash-mismatch")
    monkeypatch.setattr(reference, "load_published_bundle", reject)
    with pytest.raises(HTTPException) as error:
        asyncio.run(models_mgmt.activate_model(path.stem, {}))
    assert error.value.status_code == 500
    assert changes == []


def test_betting_sidecar_cannot_resurrect_reference_purchase_authority(reference_fixture, monkeypatch):
    path, bundle = reference_fixture
    predictor = predict.ModelPredictor(bundle, path)
    predictor.input_diagnostics = {"schema": "prediction-input-contract-v1", "status": "unverified",
        "expected_count": 50, "actual_count": 50, "missing_columns": [], "blocked_columns": [],
        "issue_codes": ["win_nll_reference_only"], "source_hash_matches": True,
        "history_available": True, "semantic_parity_verified": False, "reference_only": True}
    # Neither an existing nor a forged sidecar is even read: CAL inference must
    # not set the old live `calibrator_applied` purchase-authority signal.
    monkeypatch.setattr(Path, "read_text", _forbidden)
    now = datetime(2026, 10, 12, 5, 0, tzinfo=timezone.utc)
    sources = [{"horse_number": n, "odds_status": "middle", "data_observed_at": now.isoformat()}
               for n in (1, 2)]
    context = value_policy.build_live_context(model_path=path, predictor=predictor,
        source_records=sources, feature_frame=pd.DataFrame({"safe_fixture": [1., 2.]}))
    assert context["probability_validation"] == {"validated": False, "eligible_bet_types": []}
    result = value_policy.recommend([
        {"horse_number": 1, "p_norm": .7, "odds": 10., "expected_value": 7.},
        {"horse_number": 2, "p_norm": .3, "odds": 5., "expected_value": 1.5},
    ], {"race_id": "202608040511", "date": "20261012", "post_time": "15:45", "num_horses": 2},
        bankroll=10000, risk_mode="balanced", min_ev=1.05, use_kelly=True,
        dynamic_unit=True, context=context, now=now + timedelta(seconds=1))
    recommendation = result["recommendation"]
    assert recommendation["decision"] == "skip" and recommendation["tickets"] == []
    assert recommendation["total_cost"] == 0
    assert "probability_unvalidated" in recommendation["reason_codes"]
    assert "input_contract_unverified" in recommendation["reason_codes"]
    assert all(row["expected_value"] is None for row in result["predictions"])
