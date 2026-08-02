from __future__ import annotations

import importlib
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


PYTHON_API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_API))
reconciler = importlib.import_module("training.retrain_reconciler")

RECONCILER_ID = "staging-reconciler-01"
JOB_ID = str(uuid.uuid4())
DIGEST = "a" * 64
OBJECT_NAME = f"retrain/{JOB_ID}/{DIGEST}.joblib"
OLD_TIMESTAMP = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
TOKEN = "b" * 64


class RpcCall:
    def __init__(self, data: Any) -> None:
        self._data = data

    def execute(self) -> SimpleNamespace:
        if isinstance(self._data, Exception):
            raise self._data
        return SimpleNamespace(data=self._data)


class StorageBucket:
    def __init__(self, owner: "FakeClient") -> None:
        self.owner = owner

    def remove(self, names: list[str]) -> list[dict[str, str]]:
        self.owner.removals.append(names)
        if self.owner.remove_failure:
            raise RuntimeError("storage unavailable")
        if self.owner.not_found:
            return []
        return [{"name": names[0]}]


class Storage:
    def __init__(self, owner: "FakeClient") -> None:
        self.owner = owner

    def from_(self, bucket: str) -> StorageBucket:
        self.owner.buckets.append(bucket)
        return StorageBucket(self.owner)


class FakeClient:
    def __init__(
        self,
        *,
        expired: list[dict[str, Any]] | None = None,
        orphans: list[dict[str, Any]] | None = None,
        remove_failure: bool = False,
        not_found: bool = False,
    ) -> None:
        self.expired = expired or []
        self.orphans = orphans or []
        self.remove_failure = remove_failure
        self.not_found = not_found
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.removals: list[list[str]] = []
        self.buckets: list[str] = []
        self.storage = Storage(self)

    def rpc(self, name: str, params: dict[str, Any]) -> RpcCall:
        self.calls.append((name, params))
        if name == "list_expired_model_retrain_job_candidates":
            return RpcCall(self.expired)
        if name == "recover_expired_model_retrain_job":
            candidate = self.expired[0]
            state = "queued" if candidate["job_state"] == "claimed" else "failed"
            return RpcCall(
                [{
                    "job_id": candidate["job_id"],
                    "record_version": candidate["record_version"] + 1,
                    "job_state": state,
                    "failure_code": "lease-expired-running" if state == "failed" else None,
                    "worker_id": "staging-worker-01" if state == "failed" else None,
                    "fencing_token": 9 if state == "failed" else None,
                    "lease_expires_at": candidate["lease_expires_at"] if state == "failed" else None,
                    "artifact_written": False,
                    "artifact_uri": None,
                    "artifact_sha256": None,
                }]
            )
        if name == "list_model_retrain_orphan_candidates":
            return RpcCall(self.orphans)
        if name == "record_model_retrain_orphan_reconciliation":
            observations = params["p_observations"]
            deleted = sum(row["outcome"] == "deleted" for row in observations)
            missing = sum(row["outcome"] == "not-found" for row in observations)
            failed = sum(row["outcome"] == "delete-failed" for row in observations)
            return RpcCall(
                [{
                    "run_id": str(uuid.uuid4()),
                    "reconciler_id": params["p_reconciler_id"],
                    "min_age_seconds": params["p_min_age_seconds"],
                    "requested_limit": params["p_limit"],
                    "candidate_count": len(observations),
                    "deleted_count": deleted,
                    "not_found_count": missing,
                    "failed_count": failed,
                    "successful": failed == 0,
                }]
            )
        return RpcCall(RuntimeError("unexpected rpc"))


def _expired(state: str = "running") -> dict[str, Any]:
    return {
        "job_id": JOB_ID,
        "record_version": 8,
        "job_state": state,
        "lease_expires_at": (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),
    }


def _orphan(**updates: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "job_id": JOB_ID,
        "record_version": 9,
        "failure_code": "lease-expired-running",
        "object_name": OBJECT_NAME,
        "artifact_sha256": DIGEST,
        "storage_created_at": OLD_TIMESTAMP,
        "cleanup_token": TOKEN,
    }
    value.update(updates)
    return value


def _runner(client: FakeClient) -> reconciler.RetrainOrphanReconciler:
    return reconciler.RetrainOrphanReconciler(
        reconciler.SupabaseRetrainReconciliationGateway(client),
        reconciler_id=RECONCILER_ID,
        min_age_seconds=3600,
        limit=10,
    )


def test_reconciler_recovers_expired_running_job_then_deletes_and_audits() -> None:
    client = FakeClient(expired=[_expired()], orphans=[_orphan()])

    result = _runner(client).run_once()

    assert result.successful is True
    assert result.recovered_count == 1
    assert result.deleted_count == 1
    assert client.buckets == ["models"]
    assert client.removals == [[OBJECT_NAME]]
    assert [name for name, _ in client.calls] == [
        "list_expired_model_retrain_job_candidates",
        "recover_expired_model_retrain_job",
        "list_model_retrain_orphan_candidates",
        "record_model_retrain_orphan_reconciliation",
    ]
    record = client.calls[-1][1]
    assert record["p_observations"] == [{
        "job_id": JOB_ID,
        "object_name": OBJECT_NAME,
        "artifact_sha256": DIGEST,
        "storage_created_at": OLD_TIMESTAMP,
        "cleanup_token": TOKEN,
        "outcome": "deleted",
    }]


def test_claimed_expiry_is_requeued_and_never_treated_as_terminal_by_client() -> None:
    client = FakeClient(expired=[_expired("claimed")])

    result = _runner(client).run_once()

    assert result.recovered_count == 1
    assert result.candidate_count == 0
    assert client.removals == []


def test_empty_scan_is_still_recorded_as_successful_noop() -> None:
    client = FakeClient()

    result = _runner(client).run_once()

    assert result.candidate_count == 0
    assert result.successful is True
    assert client.calls[-1][0] == "record_model_retrain_orphan_reconciliation"
    assert client.calls[-1][1]["p_observations"] == []


def test_storage_not_found_is_audited_without_failure() -> None:
    client = FakeClient(orphans=[_orphan()], not_found=True)

    result = _runner(client).run_once()

    assert result.not_found_count == 1
    assert result.successful is True


def test_storage_failure_is_audited_then_fails_closed() -> None:
    client = FakeClient(orphans=[_orphan()], remove_failure=True)

    with pytest.raises(
        reconciler.RetrainReconciliationError,
        match="reconciliation-incomplete",
    ):
        _runner(client).run_once()

    assert client.calls[-1][0] == "record_model_retrain_orphan_reconciliation"
    assert client.calls[-1][1]["p_observations"][0]["outcome"] == "delete-failed"


@pytest.mark.parametrize(
    "updates",
    [
        {"object_name": f"retrain/{JOB_ID}/registered.joblib"},
        {"artifact_sha256": "0" * 63},
        {"cleanup_token": "0" * 63},
        {"job_id": str(uuid.uuid4())},
        {"storage_created_at": "not-a-time"},
        {"storage_created_at": datetime.now(timezone.utc).isoformat()},
        {"failure_code": "unknown-failure"},
        {"extra": True},
    ],
)
def test_malformed_orphan_projection_never_reaches_storage(updates: dict[str, Any]) -> None:
    client = FakeClient(orphans=[_orphan(**updates)])

    with pytest.raises(reconciler.RetrainGatewayUnavailable, match="scan-response-invalid"):
        _runner(client).run_once()

    assert client.removals == []


def test_duplicate_candidate_is_rejected_before_storage() -> None:
    client = FakeClient(orphans=[_orphan(), _orphan()])

    with pytest.raises(reconciler.RetrainGatewayUnavailable, match="scan-response-invalid"):
        _runner(client).run_once()

    assert client.removals == []


def test_recovery_binding_failure_stops_before_orphan_scan() -> None:
    client = FakeClient(expired=[_expired()], orphans=[_orphan()])
    client.expired[0]["record_version"] = True

    with pytest.raises(reconciler.RetrainGatewayUnavailable, match="scan-response-invalid"):
        _runner(client).run_once()

    assert client.removals == []
    assert [name for name, _ in client.calls] == [
        "list_expired_model_retrain_job_candidates"
    ]


@pytest.mark.parametrize(
    ("reconciler_id", "min_age", "limit", "code"),
    [
        ("bad id", 3600, 10, "id-invalid"),
        (RECONCILER_ID, 3599, 10, "min-age-invalid"),
        (RECONCILER_ID, 3600, 51, "limit-invalid"),
    ],
)
def test_reconciler_configuration_is_bounded(
    reconciler_id: str,
    min_age: int,
    limit: int,
    code: str,
) -> None:
    gateway = reconciler.SupabaseRetrainReconciliationGateway(FakeClient())
    with pytest.raises(reconciler.RetrainReconciliationError, match=code):
        reconciler.RetrainOrphanReconciler(
            gateway,
            reconciler_id=reconciler_id,
            min_age_seconds=min_age,
            limit=limit,
        )
