from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path

import pytest


PYTHON_API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_API))
worker = importlib.import_module("local_retrain_worker_main")
train = importlib.import_module("routers.train")

JOB_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
TOKEN = "b" * 64


def _payload(**updates: object) -> bytes:
    value: dict[str, object] = {
        "schema_version": 1,
        "mode": "prepare",
        "job_id": JOB_ID,
        "preparation_token": TOKEN,
        "request": {"force_sync": False},
    }
    value.update(updates)
    return json.dumps(value).encode("utf-8")


def test_parse_envelope_accepts_strict_prepare_payload() -> None:
    parsed = worker.parse_envelope(_payload())

    assert parsed.mode == "prepare"
    assert parsed.job_id == JOB_ID
    assert parsed.preparation_token == TOKEN
    assert parsed.request == {"force_sync": False}


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not-json",
        _payload(schema_version=2),
        _payload(extra="unexpected"),
        _payload(job_id="not-a-uuid"),
        _payload(preparation_token="visible-secret"),
        _payload(mode="queued"),
    ],
)
def test_parse_envelope_rejects_malformed_or_ambiguous_payload(raw: bytes) -> None:
    with pytest.raises(worker.WorkerEnvelopeError):
        worker.parse_envelope(raw)


def test_queued_worker_dispatches_without_request_or_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_execute(job_id: str) -> None:
        calls.append(job_id)

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr(train, "_execute_queued_local_retrain", fake_execute)
    parsed = worker.parse_envelope(
        _payload(mode="queued", preparation_token=None, request=None)
    )

    asyncio.run(worker.run_worker(parsed))

    assert calls == [JOB_ID]


def test_worker_rejects_non_local_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    parsed = worker.parse_envelope(_payload())

    with pytest.raises(worker.WorkerEnvelopeError, match="environment-forbidden"):
        asyncio.run(worker.run_worker(parsed))


@pytest.mark.parametrize(("requested", "resolved", "fallback"), [
    ("cpu", "cpu", None), ("gpu", "gpu", None), ("auto", "gpu", None),
    ("auto", "cpu", "gpu-unavailable"),
])
def test_context_prepare_worker_reaches_coordinator_only_after_verified_device_admission(
    monkeypatch, requested, resolved, fallback,
):
    from training import db_shared_acceleration as acceleration
    from test_db_shared_acceleration import receipt
    proof = receipt(requested, resolved, fallback)
    events = []
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr(acceleration, "configure_worker_imports", lambda actual: events.append(("imports", actual)))
    monkeypatch.setattr(acceleration, "validate_execution_receipt", lambda actual: events.append(("validated", actual)))
    async def prepare(job_id, request, preparation_token):
        assert events == [("imports", proof), ("validated", proof)]
        events.append(("prepare", request.feature_pipeline, request.compute_device))
        assert job_id == JOB_ID and preparation_token == TOKEN
    monkeypatch.setattr(train, "_prepare_and_execute_local_retrain", prepare)
    parsed = worker.parse_envelope(_payload(schema_version=2, execution_receipt=proof,
        request={"force_sync": False, "target": "speed_deviation",
                 "feature_pipeline": "local-db-context-v1", "compute_device": requested}))
    asyncio.run(worker.run_worker(parsed))
    assert events[-1] == ("prepare", "local-db-context-v1", requested)


@pytest.mark.parametrize("pipeline", ["unknown", "local-db-context-v2", "legacy", None, 80])
def test_unknown_pipeline_cannot_use_bound_cpu_or_gpu_preparation_receipt(monkeypatch, pipeline):
    from training import db_shared_acceleration as acceleration
    from test_db_shared_acceleration import receipt
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr(acceleration, "configure_worker_imports", lambda _: None)
    monkeypatch.setattr(acceleration, "validate_execution_receipt", lambda _: None)
    monkeypatch.setattr(train, "_prepare_and_execute_local_retrain", lambda *_: pytest.fail("untrusted profile admitted"))
    parsed = worker.parse_envelope(_payload(schema_version=2, execution_receipt=receipt("cpu", "cpu"),
        request={"force_sync": False, "feature_pipeline": pipeline, "compute_device": "cpu"}))
    with pytest.raises(worker.WorkerEnvelopeError, match="execution-request-mismatch"):
        asyncio.run(worker.run_worker(parsed))


def test_context_request_cannot_change_requested_device_after_receipt_is_bound(monkeypatch):
    from training import db_shared_acceleration as acceleration
    from test_db_shared_acceleration import receipt
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr(acceleration, "configure_worker_imports", lambda _: None)
    monkeypatch.setattr(acceleration, "validate_execution_receipt", lambda _: None)
    monkeypatch.setattr(train, "_prepare_and_execute_local_retrain", lambda *_: pytest.fail("device mismatch admitted"))
    parsed = worker.parse_envelope(_payload(schema_version=2, execution_receipt=receipt(),
        request={"force_sync": False, "feature_pipeline": "local-db-context-v1", "compute_device": "cpu"}))
    with pytest.raises(worker.WorkerEnvelopeError, match="execution-request-mismatch"):
        asyncio.run(worker.run_worker(parsed))


def test_context_invalid_runtime_identity_fails_before_preparation(monkeypatch):
    from training import db_shared_acceleration as acceleration
    from test_db_shared_acceleration import receipt
    events = []
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr(acceleration, "configure_worker_imports", lambda _: events.append("configured"))
    def reject(_):
        events.append("identity-rejected")
        raise ValueError("db-shared-worker-runtime-identity-mismatch")
    monkeypatch.setattr(acceleration, "validate_execution_receipt", reject)
    monkeypatch.setattr(train, "_prepare_and_execute_local_retrain", lambda *_: pytest.fail("identity mismatch admitted"))
    parsed = worker.parse_envelope(_payload(schema_version=2, execution_receipt=receipt(),
        request={"force_sync": False, "feature_pipeline": "local-db-context-v1", "compute_device": "gpu"}))
    with pytest.raises(ValueError, match="runtime-identity-mismatch"):
        asyncio.run(worker.run_worker(parsed))
    assert events == ["configured", "identity-rejected"]


def test_context_bad_receipt_hash_fails_before_runtime_imports_or_coordinator():
    from test_db_shared_acceleration import receipt
    proof = receipt()
    proof["receipt_sha256"] = "0" * 64
    with pytest.raises(worker.WorkerEnvelopeError, match="execution-receipt-invalid"):
        worker.parse_envelope(_payload(schema_version=2, execution_receipt=proof,
            request={"force_sync": False, "feature_pipeline": "local-db-context-v1", "compute_device": "gpu"}))
