from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from training.approved_execution import MAX_SNAPSHOT_BYTES
from training.retrain_worker import (
    ApprovedRetrainCoordinator,
    RegisteredArtifact,
    RetrainGatewayUnavailable,
    SupabaseRetrainGateway,
)


DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
DISPATCHER_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{2,49}$")
POLICIES = frozenset({"staging-train", "sandbox-train"})


class RetrainDispatchError(RuntimeError):
    """Sanitized bounded retrain dispatcher failure."""


@dataclass(frozen=True)
class DispatchCandidate:
    job_id: str
    record_version: int
    execution_policy: str
    data_snapshot_sha256: str
    candidate_commit_sha: str
    active_model_id: str


@dataclass(frozen=True)
class DispatchResult:
    candidate_count: int
    dispatched_count: int
    skipped_count: int
    failed_count: int
    successful: bool


class DispatchGateway(Protocol):
    def list_dispatchable(
        self,
        *,
        dispatcher_id: str,
        execution_policy: str,
        candidate_commit_sha: str,
        active_model_id: str,
        limit: int,
    ) -> list[DispatchCandidate]: ...


class SupabaseRetrainDispatchGateway(SupabaseRetrainGateway):
    """Worker gateway plus exact service-only dispatch queue projection."""

    def list_dispatchable(
        self,
        *,
        dispatcher_id: str,
        execution_policy: str,
        candidate_commit_sha: str,
        active_model_id: str,
        limit: int,
    ) -> list[DispatchCandidate]:
        try:
            response = self._client.rpc(  # type: ignore[attr-defined]
                "list_dispatchable_model_retrain_jobs",
                {
                    "p_dispatcher_id": dispatcher_id,
                    "p_execution_policy": execution_policy,
                    "p_candidate_commit_sha": candidate_commit_sha,
                    "p_active_model_id": active_model_id,
                    "p_limit": limit,
                },
            ).execute()
            data = getattr(response, "data", None)
        except Exception as exc:
            raise RetrainGatewayUnavailable("retrain-dispatch-scan-unavailable") from exc
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise RetrainGatewayUnavailable("retrain-dispatch-scan-response-invalid")
        result: list[DispatchCandidate] = []
        seen: set[str] = set()
        keys = {
            "job_id",
            "record_version",
            "execution_policy",
            "data_snapshot_sha256",
            "candidate_commit_sha",
            "active_model_id",
        }
        for row in data:
            if set(row) != keys:
                raise RetrainGatewayUnavailable("retrain-dispatch-scan-response-invalid")
            try:
                job_id = str(uuid.UUID(str(row["job_id"])))
            except ValueError as exc:
                raise RetrainGatewayUnavailable(
                    "retrain-dispatch-scan-response-invalid"
                ) from exc
            version = row["record_version"]
            policy = row["execution_policy"]
            snapshot_sha256 = row["data_snapshot_sha256"]
            commit_sha = row["candidate_commit_sha"]
            model_id = row["active_model_id"]
            if (
                job_id != row["job_id"]
                or job_id in seen
                or type(version) is not int
                or version < 1
                or policy != execution_policy
                or snapshot_sha256 is None
                or DIGEST_RE.fullmatch(str(snapshot_sha256)) is None
                or snapshot_sha256 == "0" * 64
                or commit_sha != candidate_commit_sha
                or COMMIT_RE.fullmatch(str(commit_sha)) is None
                or model_id != active_model_id
                or MODEL_RE.fullmatch(str(model_id)) is None
            ):
                raise RetrainGatewayUnavailable("retrain-dispatch-scan-response-invalid")
            seen.add(job_id)
            result.append(
                DispatchCandidate(
                    job_id=job_id,
                    record_version=version,
                    execution_policy=policy,
                    data_snapshot_sha256=snapshot_sha256,
                    candidate_commit_sha=commit_sha,
                    active_model_id=model_id,
                )
            )
        if len(result) > limit:
            raise RetrainGatewayUnavailable("retrain-dispatch-scan-response-invalid")
        return result


class SnapshotCatalog:
    """Immutable digest-named local snapshot catalog."""

    def __init__(self, root: Path) -> None:
        if not root.is_absolute() or root.is_symlink():
            raise RetrainDispatchError("retrain-snapshot-catalog-invalid")
        try:
            resolved = root.resolve(strict=True)
        except OSError as exc:
            raise RetrainDispatchError("retrain-snapshot-catalog-invalid") from exc
        if not resolved.is_dir():
            raise RetrainDispatchError("retrain-snapshot-catalog-invalid")
        self._root = resolved

    @staticmethod
    def _digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def resolve(self, sha256: str) -> Path:
        if DIGEST_RE.fullmatch(sha256 or "") is None or sha256 == "0" * 64:
            raise RetrainDispatchError("retrain-snapshot-catalog-digest-invalid")
        source = self._root / f"{sha256}.db"
        if source.is_symlink():
            raise RetrainDispatchError("retrain-snapshot-catalog-entry-invalid")
        try:
            resolved = source.resolve(strict=True)
        except OSError as exc:
            raise RetrainDispatchError("retrain-snapshot-catalog-entry-unavailable") from exc
        size = resolved.stat().st_size
        if (
            not resolved.is_file()
            or not resolved.is_relative_to(self._root)
            or size < 1
            or size > MAX_SNAPSHOT_BYTES
        ):
            raise RetrainDispatchError("retrain-snapshot-catalog-entry-invalid")
        if self._digest(resolved) != sha256:
            raise RetrainDispatchError("retrain-snapshot-catalog-digest-mismatch")
        return resolved


CoordinatorFactory = Callable[[str], ApprovedRetrainCoordinator]


class BoundedRetrainDispatcher:
    def __init__(
        self,
        gateway: DispatchGateway,
        *,
        snapshot_catalog: SnapshotCatalog,
        dispatcher_id: str,
        execution_policy: str,
        candidate_commit_sha: str,
        active_model_id: str,
        limit: int = 1,
        coordinator_factory: CoordinatorFactory | None = None,
        lease_ttl_seconds: int = 120,
    ) -> None:
        if DISPATCHER_RE.fullmatch(dispatcher_id or "") is None:
            raise RetrainDispatchError("retrain-dispatcher-id-invalid")
        if execution_policy not in POLICIES:
            raise RetrainDispatchError("retrain-dispatcher-policy-invalid")
        if (
            COMMIT_RE.fullmatch(candidate_commit_sha or "") is None
            or candidate_commit_sha == "0" * 40
        ):
            raise RetrainDispatchError("retrain-dispatcher-commit-invalid")
        if MODEL_RE.fullmatch(active_model_id or "") is None:
            raise RetrainDispatchError("retrain-dispatcher-active-model-invalid")
        if not 1 <= limit <= 5:
            raise RetrainDispatchError("retrain-dispatcher-limit-invalid")
        if not 30 <= lease_ttl_seconds <= 300:
            raise RetrainDispatchError("retrain-dispatcher-ttl-invalid")
        self._gateway = gateway
        self._catalog = snapshot_catalog
        self._dispatcher_id = dispatcher_id
        self._execution_policy = execution_policy
        self._candidate_commit_sha = candidate_commit_sha
        self._active_model_id = active_model_id
        self._limit = limit
        self._coordinator_factory = coordinator_factory
        self._lease_ttl_seconds = lease_ttl_seconds

    def _coordinator(self, worker_id: str) -> ApprovedRetrainCoordinator:
        if self._coordinator_factory is not None:
            return self._coordinator_factory(worker_id)
        return ApprovedRetrainCoordinator(
            self._gateway,  # type: ignore[arg-type]
            worker_id=worker_id,
            candidate_commit_sha=self._candidate_commit_sha,
            active_model_id=self._active_model_id,
            execution_policy=self._execution_policy,
            lease_ttl_seconds=self._lease_ttl_seconds,
        )

    def _validate_candidates(self, candidates: object) -> list[DispatchCandidate]:
        if not isinstance(candidates, list) or len(candidates) > self._limit:
            raise RetrainDispatchError("retrain-dispatch-candidates-invalid")
        seen: set[str] = set()
        for candidate in candidates:
            if not isinstance(candidate, DispatchCandidate):
                raise RetrainDispatchError("retrain-dispatch-candidates-invalid")
            try:
                canonical_job_id = str(uuid.UUID(candidate.job_id))
            except (AttributeError, TypeError, ValueError) as exc:
                raise RetrainDispatchError(
                    "retrain-dispatch-candidates-invalid"
                ) from exc
            if (
                canonical_job_id != candidate.job_id
                or candidate.job_id in seen
                or type(candidate.record_version) is not int
                or candidate.record_version < 1
                or candidate.execution_policy != self._execution_policy
                or not isinstance(candidate.data_snapshot_sha256, str)
                or DIGEST_RE.fullmatch(candidate.data_snapshot_sha256) is None
                or candidate.data_snapshot_sha256 == "0" * 64
                or candidate.candidate_commit_sha != self._candidate_commit_sha
                or COMMIT_RE.fullmatch(candidate.candidate_commit_sha) is None
                or candidate.active_model_id != self._active_model_id
                or MODEL_RE.fullmatch(candidate.active_model_id) is None
            ):
                raise RetrainDispatchError("retrain-dispatch-candidates-invalid")
            seen.add(candidate.job_id)
        return candidates

    async def run_once(self) -> DispatchResult:
        candidates = self._validate_candidates(
            self._gateway.list_dispatchable(
                dispatcher_id=self._dispatcher_id,
                execution_policy=self._execution_policy,
                candidate_commit_sha=self._candidate_commit_sha,
                active_model_id=self._active_model_id,
                limit=self._limit,
            )
        )
        dispatched = 0
        skipped = 0
        failed = 0
        for candidate in candidates:
            try:
                snapshot = self._catalog.resolve(candidate.data_snapshot_sha256)
            except RetrainDispatchError:
                skipped += 1
                continue
            worker_id = f"{self._dispatcher_id}:{candidate.job_id[:8]}"
            try:
                artifact: RegisteredArtifact = await self._coordinator(worker_id).run_job(
                    job_id=candidate.job_id,
                    expected_version=candidate.record_version,
                    snapshot_source=snapshot,
                )
                if artifact.job_id != candidate.job_id:
                    raise RetrainGatewayUnavailable("retrain-dispatch-artifact-binding-invalid")
                dispatched += 1
            except Exception:
                failed += 1
        return DispatchResult(
            candidate_count=len(candidates),
            dispatched_count=dispatched,
            skipped_count=skipped,
            failed_count=failed,
            successful=skipped == 0 and failed == 0,
        )
