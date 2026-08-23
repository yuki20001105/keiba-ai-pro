from __future__ import annotations

import asyncio
import hashlib
import importlib
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


PYTHON_API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_API))
dispatcher = importlib.import_module("training.retrain_dispatcher")
worker = importlib.import_module("training.retrain_worker")

DISPATCHER_ID = "staging-dispatcher-01"
COMMIT = "c" * 40
ACTIVE_MODEL = "baseline-model"
JOB_ID = str(uuid.uuid4())


def _snapshot(tmp_path: Path, content: bytes = b"approved-snapshot") -> tuple[Path, str]:
    digest = hashlib.sha256(content).hexdigest()
    path = tmp_path / f"{digest}.db"
    path.write_bytes(content)
    return path, digest


def _candidate(digest: str, **updates: Any) -> dispatcher.DispatchCandidate:
    value: dict[str, Any] = {
        "job_id": JOB_ID,
        "record_version": 3,
        "execution_policy": "staging-train",
        "data_snapshot_sha256": digest,
        "candidate_commit_sha": COMMIT,
        "active_model_id": ACTIVE_MODEL,
    }
    value.update(updates)
    return dispatcher.DispatchCandidate(**value)


class FakeGateway:
    def __init__(self, candidates: list[dispatcher.DispatchCandidate]) -> None:
        self.candidates = candidates
        self.calls: list[dict[str, Any]] = []

    def list_dispatchable(self, **kwargs: Any) -> list[dispatcher.DispatchCandidate]:
        self.calls.append(kwargs)
        return self.candidates


class FakeCoordinator:
    def __init__(
        self,
        worker_id: str,
        calls: list[tuple[str, str, int, Path]],
        *,
        failure: bool = False,
        wrong_job: bool = False,
    ) -> None:
        self.worker_id = worker_id
        self.calls = calls
        self.failure = failure
        self.wrong_job = wrong_job

    async def run_job(
        self,
        *,
        job_id: str,
        expected_version: int,
        snapshot_source: Path,
    ) -> worker.RegisteredArtifact:
        self.calls.append((self.worker_id, job_id, expected_version, snapshot_source))
        if self.failure:
            raise RuntimeError("training failed")
        return worker.RegisteredArtifact(
            job_id=str(uuid.uuid4()) if self.wrong_job else job_id,
            object_name=f"retrain/{job_id}/{'a' * 64}.joblib",
            sha256="a" * 64,
            size_bytes=10,
            media_type="application/x-python-serialized-object",
        )


def _runner(
    tmp_path: Path,
    gateway: FakeGateway,
    calls: list[tuple[str, str, int, Path]],
    *,
    failure: bool = False,
    wrong_job: bool = False,
) -> dispatcher.BoundedRetrainDispatcher:
    return dispatcher.BoundedRetrainDispatcher(
        gateway,
        snapshot_catalog=dispatcher.SnapshotCatalog(tmp_path.resolve()),
        dispatcher_id=DISPATCHER_ID,
        execution_policy="staging-train",
        candidate_commit_sha=COMMIT,
        active_model_id=ACTIVE_MODEL,
        limit=3,
        coordinator_factory=lambda worker_id: FakeCoordinator(
            worker_id, calls, failure=failure, wrong_job=wrong_job
        ),
    )


def test_dispatcher_resolves_digest_snapshot_and_runs_exact_job(tmp_path: Path) -> None:
    snapshot, digest = _snapshot(tmp_path)
    gateway = FakeGateway([_candidate(digest)])
    calls: list[tuple[str, str, int, Path]] = []

    result = asyncio.run(_runner(tmp_path, gateway, calls).run_once())

    assert result.successful is True
    assert result.dispatched_count == 1
    assert result.skipped_count == result.failed_count == 0
    assert calls == [(f"{DISPATCHER_ID}:{JOB_ID[:8]}", JOB_ID, 3, snapshot.resolve())]
    assert gateway.calls == [{
        "dispatcher_id": DISPATCHER_ID,
        "execution_policy": "staging-train",
        "candidate_commit_sha": COMMIT,
        "active_model_id": ACTIVE_MODEL,
        "limit": 3,
    }]


def test_missing_snapshot_is_skipped_without_claiming(tmp_path: Path) -> None:
    gateway = FakeGateway([_candidate("a" * 64)])
    calls: list[tuple[str, str, int, Path]] = []

    result = asyncio.run(_runner(tmp_path, gateway, calls).run_once())

    assert result.successful is False
    assert result.skipped_count == 1
    assert result.dispatched_count == result.failed_count == 0
    assert calls == []


def test_snapshot_digest_mismatch_is_skipped_before_claim(tmp_path: Path) -> None:
    path, digest = _snapshot(tmp_path)
    path.write_bytes(b"mutated")
    gateway = FakeGateway([_candidate(digest)])
    calls: list[tuple[str, str, int, Path]] = []

    result = asyncio.run(_runner(tmp_path, gateway, calls).run_once())

    assert result.skipped_count == 1
    assert calls == []


@pytest.mark.parametrize("wrong_job", [False, True])
def test_coordinator_or_artifact_binding_failure_is_counted(
    tmp_path: Path,
    wrong_job: bool,
) -> None:
    _, digest = _snapshot(tmp_path)
    calls: list[tuple[str, str, int, Path]] = []
    result = asyncio.run(
        _runner(
            tmp_path,
            FakeGateway([_candidate(digest)]),
            calls,
            failure=not wrong_job,
            wrong_job=wrong_job,
        ).run_once()
    )
    assert result.successful is False
    assert result.failed_count == 1
    assert result.dispatched_count == 0


def test_empty_dispatch_scan_is_successful_noop(tmp_path: Path) -> None:
    result = asyncio.run(_runner(tmp_path, FakeGateway([]), []).run_once())
    assert result == dispatcher.DispatchResult(0, 0, 0, 0, True)


@pytest.mark.parametrize(
    "candidate",
    [
        _candidate("a" * 64, job_id="not-a-uuid"),
        _candidate("a" * 64, record_version=True),
        _candidate("a" * 64, execution_policy="sandbox-train"),
        _candidate("0" * 64),
        _candidate("a" * 64, candidate_commit_sha="d" * 40),
        _candidate("a" * 64, active_model_id="other-model"),
    ],
)
def test_dispatcher_revalidates_untrusted_gateway_candidates_before_claim(
    tmp_path: Path,
    candidate: dispatcher.DispatchCandidate,
) -> None:
    calls: list[tuple[str, str, int, Path]] = []
    with pytest.raises(dispatcher.RetrainDispatchError, match="candidates-invalid"):
        asyncio.run(_runner(tmp_path, FakeGateway([candidate]), calls).run_once())
    assert calls == []


@pytest.mark.parametrize(
    "candidates",
    [
        [_candidate("a" * 64), _candidate("a" * 64)],
        [_candidate("a" * 64)] * 4,
        (_candidate("a" * 64),),
    ],
)
def test_dispatcher_rejects_duplicate_oversized_or_non_list_scan(
    tmp_path: Path,
    candidates: object,
) -> None:
    gateway = FakeGateway([])
    gateway.candidates = candidates  # type: ignore[assignment]
    calls: list[tuple[str, str, int, Path]] = []
    with pytest.raises(dispatcher.RetrainDispatchError, match="candidates-invalid"):
        asyncio.run(_runner(tmp_path, gateway, calls).run_once())
    assert calls == []


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"dispatcher_id": "bad id"}, "id-invalid"),
        ({"execution_policy": "production-train"}, "policy-invalid"),
        ({"candidate_commit_sha": "a" * 39}, "commit-invalid"),
        ({"candidate_commit_sha": "0" * 40}, "commit-invalid"),
        ({"active_model_id": "bad/model"}, "active-model-invalid"),
        ({"limit": 6}, "limit-invalid"),
        ({"lease_ttl_seconds": 301}, "ttl-invalid"),
    ],
)
def test_dispatcher_configuration_is_fail_closed(
    tmp_path: Path,
    kwargs: dict[str, Any],
    code: str,
) -> None:
    values: dict[str, Any] = {
        "snapshot_catalog": dispatcher.SnapshotCatalog(tmp_path.resolve()),
        "dispatcher_id": DISPATCHER_ID,
        "execution_policy": "staging-train",
        "candidate_commit_sha": COMMIT,
        "active_model_id": ACTIVE_MODEL,
        "limit": 1,
        "lease_ttl_seconds": 120,
    }
    values.update(kwargs)
    with pytest.raises(dispatcher.RetrainDispatchError, match=code):
        dispatcher.BoundedRetrainDispatcher(FakeGateway([]), **values)


class RpcClient:
    def __init__(self, data: Any) -> None:
        self.data = data
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.storage = SimpleNamespace()

    def rpc(self, name: str, params: dict[str, Any]) -> SimpleNamespace:
        self.calls.append((name, params))
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=self.data))


def _row(digest: str, **updates: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "job_id": JOB_ID,
        "record_version": 3,
        "execution_policy": "staging-train",
        "data_snapshot_sha256": digest,
        "candidate_commit_sha": COMMIT,
        "active_model_id": ACTIVE_MODEL,
    }
    value.update(updates)
    return value


def test_supabase_dispatch_projection_uses_exact_service_rpc(tmp_path: Path) -> None:
    _, digest = _snapshot(tmp_path)
    client = RpcClient([_row(digest)])
    gateway = dispatcher.SupabaseRetrainDispatchGateway(client)

    result = gateway.list_dispatchable(
        dispatcher_id=DISPATCHER_ID,
        execution_policy="staging-train",
        candidate_commit_sha=COMMIT,
        active_model_id=ACTIVE_MODEL,
        limit=2,
    )

    assert result == [_candidate(digest)]
    assert client.calls == [(
        "list_dispatchable_model_retrain_jobs",
        {
            "p_dispatcher_id": DISPATCHER_ID,
            "p_execution_policy": "staging-train",
            "p_candidate_commit_sha": COMMIT,
            "p_active_model_id": ACTIVE_MODEL,
            "p_limit": 2,
        },
    )]


@pytest.mark.parametrize(
    "updates",
    [
        {"record_version": True},
        {"execution_policy": "sandbox-train"},
        {"data_snapshot_sha256": "0" * 64},
        {"candidate_commit_sha": "d" * 40},
        {"active_model_id": "other-model"},
        {"extra": True},
    ],
)
def test_invalid_dispatch_projection_is_rejected(updates: dict[str, Any]) -> None:
    client = RpcClient([_row("a" * 64, **updates)])
    gateway = dispatcher.SupabaseRetrainDispatchGateway(client)
    with pytest.raises(worker.RetrainGatewayUnavailable, match="response-invalid"):
        gateway.list_dispatchable(
            dispatcher_id=DISPATCHER_ID,
            execution_policy="staging-train",
            candidate_commit_sha=COMMIT,
            active_model_id=ACTIVE_MODEL,
            limit=2,
        )
