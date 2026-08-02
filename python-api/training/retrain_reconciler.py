from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from training.retrain_worker import MODEL_BUCKET, RetrainGatewayUnavailable


DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = DIGEST_RE
IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{2,79}$")
OBJECT_RE = re.compile(
    r"^retrain/([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})/"
    r"([0-9a-f]{64})\.joblib$"
)
JOB_STATES = frozenset({"claimed", "running"})
OUTCOMES = frozenset({"deleted", "not-found", "delete-failed"})
FAILURE_CODES = frozenset(
    {
        "worker-error",
        "input-invalid",
        "training-failed",
        "cancelled-before-write",
        "lease-expired-running",
    }
)


class RetrainReconciliationError(RuntimeError):
    """Sanitized bounded retrain reconciliation failure."""


@dataclass(frozen=True)
class ExpiredJobCandidate:
    job_id: str
    record_version: int
    job_state: str
    lease_expires_at: datetime


@dataclass(frozen=True)
class OrphanCandidate:
    job_id: str
    record_version: int
    failure_code: str
    object_name: str
    artifact_sha256: str
    storage_created_at: datetime
    storage_created_at_raw: str
    cleanup_token: str

    def observation(self, outcome: str) -> dict[str, str]:
        if outcome not in OUTCOMES:
            raise RetrainReconciliationError("retrain-reconciliation-outcome-invalid")
        return {
            "job_id": self.job_id,
            "object_name": self.object_name,
            "artifact_sha256": self.artifact_sha256,
            "storage_created_at": self.storage_created_at_raw,
            "cleanup_token": self.cleanup_token,
            "outcome": outcome,
        }


@dataclass(frozen=True)
class ReconciliationResult:
    run_id: str
    recovered_count: int
    candidate_count: int
    deleted_count: int
    not_found_count: int
    failed_count: int
    successful: bool


class SupabaseRetrainReconciliationGateway:
    """Exact service-role RPC/Storage adapter for bounded reconciliation."""

    def __init__(self, client: Any) -> None:
        if (
            client is None
            or not callable(getattr(client, "rpc", None))
            or not hasattr(client, "storage")
        ):
            raise RetrainGatewayUnavailable("retrain-reconciliation-client-unavailable")
        self._client = client

    @staticmethod
    def _timestamp(value: Any, code: str) -> tuple[datetime, str]:
        if not isinstance(value, str):
            raise RetrainGatewayUnavailable(code)
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RetrainGatewayUnavailable(code) from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise RetrainGatewayUnavailable(code)
        return parsed.astimezone(timezone.utc), value

    def _rpc_rows(
        self,
        name: str,
        params: dict[str, Any],
        code: str,
    ) -> list[dict[str, Any]]:
        try:
            data = getattr(self._client.rpc(name, params).execute(), "data", None)
        except Exception as exc:
            raise RetrainGatewayUnavailable(code) from exc
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise RetrainGatewayUnavailable(code)
        return [dict(row) for row in data]

    @staticmethod
    def _uuid(value: Any, code: str) -> str:
        if not isinstance(value, str):
            raise RetrainGatewayUnavailable(code)
        try:
            parsed = str(uuid.UUID(value))
        except ValueError as exc:
            raise RetrainGatewayUnavailable(code) from exc
        if parsed != value:
            raise RetrainGatewayUnavailable(code)
        return parsed

    def list_expired(self, *, reconciler_id: str, limit: int) -> list[ExpiredJobCandidate]:
        rows = self._rpc_rows(
            "list_expired_model_retrain_job_candidates",
            {"p_reconciler_id": reconciler_id, "p_limit": limit},
            "retrain-expired-scan-unavailable",
        )
        result: list[ExpiredJobCandidate] = []
        seen: set[str] = set()
        now = datetime.now(timezone.utc)
        for row in rows:
            if set(row) != {"job_id", "record_version", "job_state", "lease_expires_at"}:
                raise RetrainGatewayUnavailable("retrain-expired-scan-response-invalid")
            job_id = self._uuid(row["job_id"], "retrain-expired-scan-response-invalid")
            version = row["record_version"]
            state = row["job_state"]
            expires_at, _ = self._timestamp(
                row["lease_expires_at"], "retrain-expired-scan-response-invalid"
            )
            if (
                job_id in seen
                or type(version) is not int
                or version < 1
                or state not in JOB_STATES
                or expires_at > now
            ):
                raise RetrainGatewayUnavailable("retrain-expired-scan-response-invalid")
            seen.add(job_id)
            result.append(ExpiredJobCandidate(job_id, version, state, expires_at))
        if len(result) > limit:
            raise RetrainGatewayUnavailable("retrain-expired-scan-response-invalid")
        return result

    def recover(self, candidate: ExpiredJobCandidate) -> None:
        rows = self._rpc_rows(
            "recover_expired_model_retrain_job",
            {
                "p_job_id": candidate.job_id,
                "p_expected_version": candidate.record_version,
            },
            "retrain-expired-recovery-unavailable",
        )
        if len(rows) != 1:
            raise RetrainGatewayUnavailable("retrain-expired-recovery-response-invalid")
        row = rows[0]
        expected_state = "queued" if candidate.job_state == "claimed" else "failed"
        if (
            str(row.get("job_id")) != candidate.job_id
            or row.get("record_version") != candidate.record_version + 1
            or row.get("job_state") != expected_state
            or row.get("artifact_written") is not False
            or row.get("artifact_uri") is not None
            or row.get("artifact_sha256") is not None
            or (expected_state == "failed" and row.get("failure_code") != "lease-expired-running")
            or (expected_state == "queued" and row.get("failure_code") is not None)
            or (expected_state == "queued" and row.get("worker_id") is not None)
            or (expected_state == "queued" and row.get("fencing_token") is not None)
            or (expected_state == "queued" and row.get("lease_expires_at") is not None)
        ):
            raise RetrainGatewayUnavailable("retrain-expired-recovery-response-invalid")

    def list_orphans(
        self,
        *,
        reconciler_id: str,
        min_age_seconds: int,
        limit: int,
    ) -> list[OrphanCandidate]:
        rows = self._rpc_rows(
            "list_model_retrain_orphan_candidates",
            {
                "p_reconciler_id": reconciler_id,
                "p_min_age_seconds": min_age_seconds,
                "p_limit": limit,
            },
            "retrain-orphan-scan-unavailable",
        )
        result: list[OrphanCandidate] = []
        seen: set[str] = set()
        now = datetime.now(timezone.utc)
        for row in rows:
            if set(row) != {
                "job_id",
                "record_version",
                "failure_code",
                "object_name",
                "artifact_sha256",
                "storage_created_at",
                "cleanup_token",
            }:
                raise RetrainGatewayUnavailable("retrain-orphan-scan-response-invalid")
            job_id = self._uuid(row["job_id"], "retrain-orphan-scan-response-invalid")
            version = row["record_version"]
            failure_code = row["failure_code"]
            object_name = row["object_name"]
            digest = row["artifact_sha256"]
            token = row["cleanup_token"]
            created_at, created_at_raw = self._timestamp(
                row["storage_created_at"], "retrain-orphan-scan-response-invalid"
            )
            match = OBJECT_RE.fullmatch(object_name) if isinstance(object_name, str) else None
            if (
                object_name in seen
                or type(version) is not int
                or version < 1
                or failure_code not in FAILURE_CODES
                or match is None
                or match.group(1) != job_id
                or match.group(2) != digest
                or not isinstance(digest, str)
                or DIGEST_RE.fullmatch(digest) is None
                or not isinstance(token, str)
                or TOKEN_RE.fullmatch(token) is None
                or created_at > now - timedelta(seconds=min_age_seconds)
            ):
                raise RetrainGatewayUnavailable("retrain-orphan-scan-response-invalid")
            seen.add(object_name)
            result.append(
                OrphanCandidate(
                    job_id=job_id,
                    record_version=version,
                    failure_code=failure_code,
                    object_name=object_name,
                    artifact_sha256=digest,
                    storage_created_at=created_at,
                    storage_created_at_raw=created_at_raw,
                    cleanup_token=token,
                )
            )
        if len(result) > limit:
            raise RetrainGatewayUnavailable("retrain-orphan-scan-response-invalid")
        return result

    def remove(self, *, object_name: str) -> str:
        try:
            response = self._client.storage.from_(MODEL_BUCKET).remove([object_name])
        except Exception:
            return "delete-failed"
        data = response if isinstance(response, list) else getattr(response, "data", None)
        if not isinstance(data, list):
            return "delete-failed"
        if data == []:
            return "not-found"
        if len(data) != 1 or not isinstance(data[0], Mapping):
            return "delete-failed"
        returned_name = data[0].get("name")
        return "deleted" if returned_name == object_name else "delete-failed"

    def record(
        self,
        *,
        reconciler_id: str,
        min_age_seconds: int,
        limit: int,
        observations: list[dict[str, str]],
    ) -> Mapping[str, Any]:
        rows = self._rpc_rows(
            "record_model_retrain_orphan_reconciliation",
            {
                "p_reconciler_id": reconciler_id,
                "p_min_age_seconds": min_age_seconds,
                "p_limit": limit,
                "p_observations": observations,
            },
            "retrain-orphan-audit-unavailable",
        )
        if len(rows) != 1:
            raise RetrainGatewayUnavailable("retrain-orphan-audit-response-invalid")
        return rows[0]


class RetrainOrphanReconciler:
    def __init__(
        self,
        gateway: SupabaseRetrainReconciliationGateway,
        *,
        reconciler_id: str,
        min_age_seconds: int = 3600,
        limit: int = 20,
    ) -> None:
        if IDENTIFIER_RE.fullmatch(reconciler_id or "") is None:
            raise RetrainReconciliationError("retrain-reconciler-id-invalid")
        if not 3600 <= min_age_seconds <= 604800:
            raise RetrainReconciliationError("retrain-reconciler-min-age-invalid")
        if not 1 <= limit <= 50:
            raise RetrainReconciliationError("retrain-reconciler-limit-invalid")
        self._gateway = gateway
        self._reconciler_id = reconciler_id
        self._min_age_seconds = min_age_seconds
        self._limit = limit

    def run_once(self) -> ReconciliationResult:
        expired = self._gateway.list_expired(
            reconciler_id=self._reconciler_id,
            limit=self._limit,
        )
        for candidate in expired:
            self._gateway.recover(candidate)

        candidates = self._gateway.list_orphans(
            reconciler_id=self._reconciler_id,
            min_age_seconds=self._min_age_seconds,
            limit=self._limit,
        )
        observations: list[dict[str, str]] = []
        for candidate in candidates:
            outcome = self._gateway.remove(object_name=candidate.object_name)
            observations.append(candidate.observation(outcome))

        row = self._gateway.record(
            reconciler_id=self._reconciler_id,
            min_age_seconds=self._min_age_seconds,
            limit=self._limit,
            observations=observations,
        )
        expected_counts = {
            "candidate_count": len(observations),
            "deleted_count": sum(item["outcome"] == "deleted" for item in observations),
            "not_found_count": sum(item["outcome"] == "not-found" for item in observations),
            "failed_count": sum(item["outcome"] == "delete-failed" for item in observations),
        }
        try:
            run_id = str(uuid.UUID(str(row["run_id"])))
        except (KeyError, ValueError) as exc:
            raise RetrainGatewayUnavailable("retrain-orphan-audit-response-invalid") from exc
        if (
            str(row.get("reconciler_id")) != self._reconciler_id
            or row.get("min_age_seconds") != self._min_age_seconds
            or row.get("requested_limit") != self._limit
            or any(row.get(key) != value for key, value in expected_counts.items())
            or type(row.get("successful")) is not bool
            or row.get("successful") != (expected_counts["failed_count"] == 0)
        ):
            raise RetrainGatewayUnavailable("retrain-orphan-audit-response-invalid")
        result = ReconciliationResult(
            run_id=run_id,
            recovered_count=len(expired),
            successful=bool(row["successful"]),
            **expected_counts,
        )
        if not result.successful:
            raise RetrainReconciliationError("retrain-orphan-reconciliation-incomplete")
        return result
