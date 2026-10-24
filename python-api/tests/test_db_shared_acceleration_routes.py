"""Normal-page acceleration is bound without changing the legacy lease schema.

All preparation, fit and artifact operations below are stubs. Only test-local
paths are passed, and no runtime probe, production DB or learner is invoked.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import HTTPException

from models import TrainRequest
from routers import train
from training import db_shared_acceleration as acceleration
from training import db_shared_cache as cache
from training import db_shared_features as features_module
from training import db_shared_fit as fit
from training.local_retrain import LocalRetrainError


def shared_request(**changes):
    return TrainRequest(target="speed_deviation", feature_pipeline="local-db-shared-v1",
                        force_sync=False, training_date_from="2016-01", training_date_to="2026-09",
                        **changes)


@pytest.mark.parametrize("device", ["auto", "cpu", "gpu"])
@pytest.mark.parametrize("reuse", [True, False])
def test_scheduling_controls_do_not_expand_the_legacy_training_parameter_schema(device, reuse):
    request = shared_request(compute_device=device, reuse_features=reuse)
    assert train._local_training_parameters(request) == {
        "test_size": .2, "cv_folds": 5, "use_sqlite": True, "ultimate_mode": True,
        "use_optimizer": True, "use_optuna": False, "optuna_trials": 50,
        "optuna_timeout": 300, "training_date_from": "2016-01",
        "training_date_to": "2026-09", "force_sync": False,
    }


def _stub_shared_pipeline(monkeypatch, tmp_path, *, requested="auto", resolved="gpu", cache_hit=True):
    observed = {"selected": [], "prepared": [], "fit": [], "artifacts": [], "progress": []}
    snapshot_sha = "a" * 64
    runtime = {"requested_device": requested, "resolved_device": resolved,
               "fallback_reason": "gpu-unavailable" if requested == "auto" and resolved == "cpu" else None}
    receipt = {"snapshot_sha256": snapshot_sha, "cache_hit": cache_hit,
               "cache_status": "hit" if cache_hit else "generated", "stage_seconds": {"db_read": .01}}
    target = pd.DataFrame({"race_date": ["20160105", "20260913"]})
    raw_features = pd.DataFrame({name: [1.0, 2.0] for name in features_module.FEATURE_COLUMNS})
    sources = {"fixed-source": "b" * 64}
    bundle = {"model": "stub-model"}
    result = {"evaluation": {"primary": {"rank_correlation": .4, "rmse": .25}},
              "probability_validation": {"status": "validated", "eligible_for_ev": True},
              "cv_rank_correlation": .39, "data_count": 2, "race_count": 2,
              "training_parallel_workers": 1 if resolved == "gpu" else 2,
              "training_native_threads": 4}

    def prepare(path, date_from, date_to, cache_root, **kwargs):
        observed["prepared"].append((path, date_from, date_to, cache_root, kwargs))
        kwargs["progress"]("特徴量再利用" if cache_hit else "特徴量生成", 12)
        return target, raw_features, {"eligible_runners": 2}, receipt

    def stub_fit(frame, inputs, **kwargs):
        assert frame is target and inputs is raw_features
        observed["fit"].append(kwargs)
        return bundle, deepcopy(result)

    def dump(payload, path):
        observed["artifacts"].append((deepcopy(payload), path))

    monkeypatch.setattr(acceleration, "current_execution_receipt", lambda: runtime)
    monkeypatch.setattr(acceleration, "accelerated_fit_shared_model", stub_fit)
    monkeypatch.setattr(cache, "prepare_shared_inputs", prepare)
    monkeypatch.setattr(fit, "source_hashes", lambda: sources)
    monkeypatch.setattr(features_module, "source_hashes", lambda: sources)
    monkeypatch.setattr(train, "_atomic_joblib_dump", dump)
    monkeypatch.setattr(train, "sha256_file", lambda _path: ("c" * 64, 1))
    monkeypatch.setattr(train, "_SHARED_FEATURE_CACHE_ROOT", tmp_path / "cache")
    execution = SimpleNamespace(
        snapshot_path=tmp_path / "snapshot.db", data_snapshot_sha256=snapshot_sha,
        approved_payload_hash="d" * 64, execution_policy="local-admin-v1", job_id="isolated-job",
        feature_contract_sha256="e" * 64, candidate_commit_sha="f" * 40,
        source_tree_sha256="b" * 64, active_model_id="active-preserved", active_model_sha256="c" * 64,
        select_feature_columns=lambda columns, **kwargs: observed["selected"].append((columns, kwargs)),
        prepare_artifact_directory=lambda: tmp_path,
    )
    return observed, execution, runtime, receipt, sources


@pytest.mark.parametrize(("requested", "resolved", "reuse", "cache_hit"), [
    ("auto", "gpu", True, True), ("gpu", "gpu", True, False),
    ("cpu", "cpu", False, False), ("auto", "cpu", True, True),
])
def test_shared_route_propagates_verified_cache_and_device_receipts_without_model_switch(
    monkeypatch, tmp_path, requested, resolved, reuse, cache_hit,
):
    seen, execution, runtime, _receipt, sources = _stub_shared_pipeline(
        monkeypatch, tmp_path, requested=requested, resolved=resolved, cache_hit=cache_hit)
    response = asyncio.run(train._do_shared_db_train(
        shared_request(compute_device=requested, reuse_features=reuse), execution,
        lambda message, pct: seen["progress"].append((message, pct))))

    assert seen["selected"][0][0] == features_module.FEATURE_COLUMNS
    assert len(seen["prepared"]) == len(seen["fit"]) == len(seen["artifacts"]) == 1
    assert seen["prepared"][0][1:3] == ("2016-01", "2026-09")
    assert seen["prepared"][0][4]["expected_source_hashes"] == sources
    assert seen["prepared"][0][4]["reuse_features"] is reuse
    assert seen["fit"][0]["compute_device"] == requested
    assert seen["fit"][0]["execution_receipt"] is runtime
    assert seen["fit"][0]["n_threads"] == 4
    assert response.acceleration["device"] == resolved
    assert response.acceleration["cache_hit"] is cache_hit
    assert response.acceleration["data_snapshot_sha256"] == execution.data_snapshot_sha256
    assert response.acceleration["approved_payload_hash"] == execution.approved_payload_hash
    assert response.acceleration["total_seconds"] >= response.acceleration["fit_seconds"] >= 0
    assert response.evaluation["primary"] == {"rank_correlation": .4, "rmse": .25}
    assert response.metrics["auc"] == .4 and response.metrics["logloss"] == .25
    assert response.feature_count == 47
    saved, artifact = seen["artifacts"][0]
    assert artifact.parent == tmp_path
    assert saved["acceleration"] == response.acceleration
    assert saved["local_execution"]["active_model_id"] == "active-preserved"
    assert saved["probability_validation"]["status"] == "validated"
    assert any(resolved.upper() in message for message, _pct in seen["progress"])
    assert not list(tmp_path.glob("*.joblib"))  # Artifact writer is a stub, never production.


@pytest.mark.parametrize("receipt_mode", ["absent", "different_requested_device"])
def test_shared_route_rejects_unbound_execution_before_preparation(monkeypatch, tmp_path, receipt_mode):
    seen, execution, _runtime, _receipt, _sources = _stub_shared_pipeline(monkeypatch, tmp_path)
    monkeypatch.setattr(acceleration, "current_execution_receipt", lambda: None if receipt_mode == "absent"
                        else {"requested_device": "cpu"})
    with pytest.raises(LocalRetrainError, match="execution-policy-unbound"):
        asyncio.run(train._do_shared_db_train(shared_request(), execution, lambda *_args: None))
    assert not seen["prepared"] and not seen["fit"] and not seen["artifacts"]


def test_shared_route_rejects_cache_snapshot_mismatch_before_fit_or_artifact(monkeypatch, tmp_path):
    seen, execution, _runtime, receipt, _sources = _stub_shared_pipeline(monkeypatch, tmp_path)
    receipt["snapshot_sha256"] = "9" * 64
    with pytest.raises(LocalRetrainError, match="cache-snapshot-mismatch"):
        asyncio.run(train._do_shared_db_train(shared_request(), execution, lambda *_args: None))
    assert len(seen["prepared"]) == 1
    assert not seen["fit"] and not seen["artifacts"]


@pytest.mark.parametrize("invalid", [{"target": "win"}, {"use_optuna": True}, {"use_optimizer": False}])
def test_shared_route_rejects_unsafe_learning_conditions_before_preparation(monkeypatch, tmp_path, invalid):
    seen, execution, _runtime, _receipt, _sources = _stub_shared_pipeline(monkeypatch, tmp_path)
    request = shared_request().model_copy(update=invalid)
    with pytest.raises(HTTPException) as captured:
        asyncio.run(train._do_shared_db_train(request, execution, lambda *_args: None))
    assert captured.value.status_code == 409
    assert not seen["selected"] and not seen["prepared"] and not seen["fit"]


def _stub_queue(monkeypatch, tmp_path, *, durable_device="gpu", receipt_device="gpu", mismatch=False):
    seen = {"requests": [], "failed": [], "recorded": [], "trainer": []}
    request = shared_request(compute_device=durable_device, reuse_features=False)
    parameters = train._local_training_parameters(request)
    if mismatch:
        parameters = {**parameters, "test_size": .3}
    contract = SimpleNamespace(selected_features=tuple(features_module.FEATURE_COLUMNS), removed_features=(),
        target="speed_deviation", model_type="lightgbm", training_parameters=parameters,
        source_tree_sha256="a" * 64, active_model_sha256="b" * 64,
        candidate_commit_sha="c" * 40, active_model_id="active-preserved")
    bundle = SimpleNamespace(contract=contract, training_request_payload=lambda: {
        "target": contract.target, "model_type": contract.model_type, **parameters})
    queued = SimpleNamespace(state="queued", contract=contract, snapshot_path=tmp_path / "snapshot.db",
        request_payload=request.model_dump(), record_version=2)
    store = SimpleNamespace(get_job=lambda _id: queued,
        fail_queued=lambda **kwargs: seen["failed"].append(kwargs))
    gateway = SimpleNamespace(progress_observer=lambda _id: lambda *_args: None,
        stage_result=lambda *_args, **_kwargs: None,
        record_result=lambda **kwargs: seen["recorded"].append(kwargs))

    class Coordinator:
        def __init__(self, *_args, **kwargs):
            seen["trainer"].append(kwargs["trainer"])
            self.request_factory = kwargs["request_factory"]

        async def run_job(self, **_kwargs):
            seen["requests"].append(self.request_factory(bundle))
            return "stub-artifact"

    monkeypatch.setattr(train, "_get_local_retrain_runtime", lambda: (store, gateway))
    monkeypatch.setattr(train, "ApprovedRetrainCoordinator", Coordinator)
    monkeypatch.setattr(acceleration, "current_execution_receipt", lambda: {"requested_device": receipt_device})
    return seen


@pytest.mark.parametrize("device", ["auto", "cpu", "gpu"])
def test_queued_job_restores_device_and_reuse_from_owner_scoped_durable_request(monkeypatch, tmp_path, device):
    seen = _stub_queue(monkeypatch, tmp_path, durable_device=device, receipt_device=device)
    asyncio.run(train._execute_queued_local_retrain("isolated-queued-job"))
    assert len(seen["requests"]) == len(seen["recorded"]) == 1
    restored = seen["requests"][0]
    assert restored.compute_device == device and restored.reuse_features is False
    assert restored.feature_pipeline == "local-db-shared-v1"
    assert restored.training_date_from == "2016-01" and restored.training_date_to == "2026-09"
    assert seen["trainer"] == [train._do_train]
    assert not seen["failed"]


@pytest.mark.parametrize("mismatch", ["hyperparameter", "device"])
def test_queued_job_policy_mismatch_fails_closed_without_train_or_publication(monkeypatch, tmp_path, mismatch):
    seen = _stub_queue(monkeypatch, tmp_path, mismatch=mismatch == "hyperparameter",
                       receipt_device="cpu" if mismatch == "device" else "gpu")
    asyncio.run(train._execute_queued_local_retrain("isolated-queued-job"))
    assert not seen["requests"] and not seen["recorded"]
    assert seen["failed"] == [{"job_id": "isolated-queued-job", "expected_version": 2,
                               "failure_code": "claim-runtime-binding-failed"}]
