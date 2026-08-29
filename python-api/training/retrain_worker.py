from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, BinaryIO, Callable, Mapping, Protocol

from training.approved_execution import ApprovedExecutionError
from training.execution_bundle import ApprovedExecutionBundle


MODEL_BUCKET = "models"
ARTIFACT_MEDIA_TYPE = "application/x-python-serialized-object"
MAX_ARTIFACT_BYTES = 100 * 1024 * 1024
FAILURE_CODES = frozenset(
    {"worker-error", "input-invalid", "training-failed", "cancelled-before-write"}
)


class RetrainWorkerError(RuntimeError):
    """Sanitized retrain worker orchestration failure."""


class RetrainLeaseLost(RetrainWorkerError):
    """The current worker no longer owns the durable lease/fence."""


class RetrainGatewayUnavailable(RetrainWorkerError):
    """The service-only retrain gateway did not return a valid response."""


@dataclass(frozen=True)
class RetrainLease:
    job_id: str
    worker_id: str
    fencing_token: int
    record_version: int
    lease_expires_at: datetime
    job_state: str


@dataclass(frozen=True)
class RegisteredArtifact:
    job_id: str
    object_name: str
    sha256: str
    size_bytes: int
    media_type: str


class RetrainGateway(Protocol):
    def claim(
        self,
        *,
        worker_id: str,
        job_id: str,
        expected_version: int,
        ttl_seconds: int,
    ) -> RetrainLease: ...

    def execution_bundle(
        self,
        *,
        lease: RetrainLease,
        candidate_commit_sha: str,
        active_model_id: str,
    ) -> Mapping[str, Any]: ...

    def heartbeat(self, *, lease: RetrainLease, ttl_seconds: int) -> RetrainLease: ...

    def start(self, *, lease: RetrainLease) -> RetrainLease: ...

    def fail(self, *, lease: RetrainLease, failure_code: str) -> RetrainLease: ...

    def upload(self, *, artifact_file: BinaryIO, object_name: str, media_type: str) -> None: ...

    def register(
        self,
        *,
        lease: RetrainLease,
        artifact: RegisteredArtifact,
    ) -> RetrainLease: ...

    def remove(self, *, object_name: str) -> None: ...


class SupabaseRetrainGateway:
    """Synchronous service-role adapter for the fenced retrain RPC boundary."""

    def __init__(self, client: Any) -> None:
        if (
            client is None
            or not callable(getattr(client, "rpc", None))
            or not hasattr(client, "storage")
        ):
            raise RetrainGatewayUnavailable("retrain-service-client-unavailable")
        self._client = client

    @staticmethod
    def _timestamp(value: Any) -> datetime:
        if not isinstance(value, str):
            raise RetrainGatewayUnavailable("retrain-lease-response-invalid")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RetrainGatewayUnavailable("retrain-lease-response-invalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise RetrainGatewayUnavailable("retrain-lease-response-invalid")
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _one(response: Any, code: str) -> dict[str, Any]:
        data = getattr(response, "data", None)
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            raise RetrainGatewayUnavailable(code)
        return dict(data[0])

    def _rpc_row(self, name: str, params: dict[str, Any], code: str) -> dict[str, Any]:
        try:
            return self._one(self._client.rpc(name, params).execute(), code)
        except RetrainGatewayUnavailable:
            raise
        except Exception as exc:
            raise RetrainGatewayUnavailable(code) from exc

    def _rpc_json(self, name: str, params: dict[str, Any], code: str) -> dict[str, Any]:
        try:
            data = getattr(self._client.rpc(name, params).execute(), "data", None)
        except Exception as exc:
            raise RetrainGatewayUnavailable(code) from exc
        if not isinstance(data, dict):
            raise RetrainGatewayUnavailable(code)
        return dict(data)

    @classmethod
    def _lease(cls, row: Mapping[str, Any], code: str) -> RetrainLease:
        try:
            job_id = str(row["job_id"])
            worker_id = str(row["worker_id"])
            fencing_token = row["fencing_token"]
            record_version = row["record_version"]
            job_state = str(row["job_state"])
            lease_expires_at = cls._timestamp(row["lease_expires_at"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RetrainGatewayUnavailable(code) from exc
        if (
            type(fencing_token) is not int
            or fencing_token < 1
            or type(record_version) is not int
            or record_version < 1
            or job_state not in {"claimed", "running", "failed", "artifact-registered"}
        ):
            raise RetrainGatewayUnavailable(code)
        return RetrainLease(
            job_id=job_id,
            worker_id=worker_id,
            fencing_token=fencing_token,
            record_version=record_version,
            lease_expires_at=lease_expires_at,
            job_state=job_state,
        )

    @staticmethod
    def _transition(
        previous: RetrainLease,
        current: RetrainLease,
        *,
        expected_state: str,
        code: str,
    ) -> RetrainLease:
        if (
            current.job_id != previous.job_id
            or current.worker_id != previous.worker_id
            or current.fencing_token != previous.fencing_token
            or current.record_version != previous.record_version + 1
            or current.job_state != expected_state
        ):
            raise RetrainGatewayUnavailable(code)
        return current

    def claim(
        self,
        *,
        worker_id: str,
        job_id: str,
        expected_version: int,
        ttl_seconds: int,
    ) -> RetrainLease:
        row = self._rpc_row(
            "claim_model_retrain_job",
            {
                "p_worker_id": worker_id,
                "p_job_id": job_id,
                "p_expected_version": expected_version,
                "p_ttl_seconds": ttl_seconds,
            },
            "retrain-claim-unavailable",
        )
        lease = self._lease(row, "retrain-claim-response-invalid")
        if (
            lease.job_id != job_id
            or lease.worker_id != worker_id
            or lease.job_state != "claimed"
            or lease.record_version != expected_version + 1
        ):
            raise RetrainGatewayUnavailable("retrain-claim-response-invalid")
        return lease

    def execution_bundle(
        self,
        *,
        lease: RetrainLease,
        candidate_commit_sha: str,
        active_model_id: str,
    ) -> Mapping[str, Any]:
        return self._rpc_json(
            "get_model_retrain_execution_bundle",
            {
                "p_worker_id": lease.worker_id,
                "p_job_id": lease.job_id,
                "p_expected_version": lease.record_version,
                "p_fencing_token": lease.fencing_token,
                "p_candidate_commit_sha": candidate_commit_sha,
                "p_active_model_id": active_model_id,
            },
            "retrain-execution-bundle-unavailable",
        )

    def heartbeat(self, *, lease: RetrainLease, ttl_seconds: int) -> RetrainLease:
        row = self._rpc_row(
            "heartbeat_model_retrain_job",
            {
                "p_worker_id": lease.worker_id,
                "p_job_id": lease.job_id,
                "p_expected_version": lease.record_version,
                "p_fencing_token": lease.fencing_token,
                "p_ttl_seconds": ttl_seconds,
            },
            "retrain-heartbeat-unavailable",
        )
        return self._transition(
            lease,
            self._lease(row, "retrain-heartbeat-response-invalid"),
            expected_state=lease.job_state,
            code="retrain-heartbeat-response-invalid",
        )

    def start(self, *, lease: RetrainLease) -> RetrainLease:
        row = self._rpc_row(
            "start_model_retrain_job",
            {
                "p_worker_id": lease.worker_id,
                "p_job_id": lease.job_id,
                "p_expected_version": lease.record_version,
                "p_fencing_token": lease.fencing_token,
            },
            "retrain-start-unavailable",
        )
        return self._transition(
            lease,
            self._lease(row, "retrain-start-response-invalid"),
            expected_state="running",
            code="retrain-start-response-invalid",
        )

    def fail(self, *, lease: RetrainLease, failure_code: str) -> RetrainLease:
        if failure_code not in FAILURE_CODES:
            raise RetrainWorkerError("retrain-failure-code-invalid")
        row = self._rpc_row(
            "fail_model_retrain_job",
            {
                "p_worker_id": lease.worker_id,
                "p_job_id": lease.job_id,
                "p_expected_version": lease.record_version,
                "p_fencing_token": lease.fencing_token,
                "p_failure_code": failure_code,
            },
            "retrain-failure-report-unavailable",
        )
        return self._transition(
            lease,
            self._lease(row, "retrain-failure-response-invalid"),
            expected_state="failed",
            code="retrain-failure-response-invalid",
        )

    def upload(self, *, artifact_file: BinaryIO, object_name: str, media_type: str) -> None:
        try:
            self._client.storage.from_(MODEL_BUCKET).upload(
                object_name,
                artifact_file,
                file_options={"content-type": media_type, "upsert": "false"},
            )
        except Exception as exc:
            raise RetrainGatewayUnavailable("retrain-artifact-upload-unavailable") from exc

    def register(
        self,
        *,
        lease: RetrainLease,
        artifact: RegisteredArtifact,
    ) -> RetrainLease:
        row = self._rpc_row(
            "register_model_retrain_artifact",
            {
                "p_worker_id": lease.worker_id,
                "p_job_id": lease.job_id,
                "p_expected_version": lease.record_version,
                "p_fencing_token": lease.fencing_token,
                "p_object_name": artifact.object_name,
                "p_artifact_sha256": artifact.sha256,
                "p_artifact_size_bytes": artifact.size_bytes,
                "p_artifact_media_type": artifact.media_type,
            },
            "retrain-artifact-registration-unavailable",
        )
        return self._transition(
            lease,
            self._lease(row, "retrain-artifact-registration-response-invalid"),
            expected_state="artifact-registered",
            code="retrain-artifact-registration-response-invalid",
        )

    def remove(self, *, object_name: str) -> None:
        try:
            self._client.storage.from_(MODEL_BUCKET).remove([object_name])
        except Exception as exc:
            raise RetrainGatewayUnavailable("retrain-orphan-cleanup-unavailable") from exc


class _HeartbeatSupervisor:
    def __init__(
        self,
        gateway: RetrainGateway,
        lease: RetrainLease,
        *,
        ttl_seconds: int,
        interval_seconds: float,
    ) -> None:
        self._gateway = gateway
        self._lease = lease
        self._ttl_seconds = ttl_seconds
        self._interval_seconds = interval_seconds
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._lost: BaseException | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def lease(self) -> RetrainLease:
        return self._lease

    def start(self) -> None:
        if self._task is not None:
            raise RetrainWorkerError("retrain-heartbeat-already-started")
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_seconds)
                return
            except asyncio.TimeoutError:
                pass
            try:
                async with self._lock:
                    self._lease = await asyncio.to_thread(
                        self._gateway.heartbeat,
                        lease=self._lease,
                        ttl_seconds=self._ttl_seconds,
                    )
            except BaseException as exc:
                self._lost = exc
                self._stop.set()
                return

    async def mutate(
        self,
        operation: Callable[[RetrainLease], RetrainLease],
    ) -> RetrainLease:
        async with self._lock:
            self.ensure_live()
            self._lease = await asyncio.to_thread(operation, self._lease)
            return self._lease

    def ensure_live(self) -> None:
        if self._lost is not None:
            raise RetrainLeaseLost("retrain-worker-lease-lost") from self._lost
        if self._lease.lease_expires_at <= datetime.now(timezone.utc):
            raise RetrainLeaseLost("retrain-worker-lease-expired")

    async def close(self) -> RetrainLease:
        self._stop.set()
        if self._task is not None:
            await self._task
        return self._lease

    async def renew_and_close(self) -> RetrainLease:
        await self.close()
        self.ensure_live()
        self._lease = await asyncio.to_thread(
            self._gateway.heartbeat,
            lease=self._lease,
            ttl_seconds=self._ttl_seconds,
        )
        return self._lease


Trainer = Callable[..., Awaitable[Any]]


class ApprovedRetrainCoordinator:
    def __init__(
        self,
        gateway: RetrainGateway,
        *,
        worker_id: str,
        candidate_commit_sha: str,
        active_model_id: str,
        execution_policy: str,
        lease_ttl_seconds: int = 120,
        heartbeat_interval_seconds: float | None = None,
        trainer: Trainer | None = None,
    ) -> None:
        if not 30 <= lease_ttl_seconds <= 300:
            raise RetrainWorkerError("retrain-worker-ttl-invalid")
        if execution_policy not in {"staging-train", "sandbox-train"}:
            raise RetrainWorkerError("retrain-worker-policy-invalid")
        interval = (
            lease_ttl_seconds / 3
            if heartbeat_interval_seconds is None
            else heartbeat_interval_seconds
        )
        if interval <= 0 or interval >= lease_ttl_seconds:
            raise RetrainWorkerError("retrain-heartbeat-interval-invalid")
        self._gateway = gateway
        self._worker_id = worker_id
        self._candidate_commit_sha = candidate_commit_sha
        self._active_model_id = active_model_id
        self._execution_policy = execution_policy
        self._lease_ttl_seconds = lease_ttl_seconds
        self._heartbeat_interval_seconds = interval
        self._trainer = trainer

    @staticmethod
    def _snapshot_source(path: Path) -> Path:
        source = Path(path)
        if source.is_symlink():
            raise ApprovedExecutionError("snapshot-source-symlink-forbidden")
        try:
            resolved = source.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise ApprovedExecutionError("snapshot-source-unavailable") from exc
        if not resolved.is_file() or resolved.suffix.lower() != ".db":
            raise ApprovedExecutionError("snapshot-source-invalid")
        return resolved

    @staticmethod
    def _artifact_path(path: Path, *, artifact_directory: Path) -> Path:
        artifact_path = Path(path)
        if artifact_path.is_symlink():
            raise ApprovedExecutionError("trained-artifact-symlink-forbidden")
        try:
            resolved = artifact_path.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise ApprovedExecutionError("trained-artifact-unavailable") from exc
        if (
            not resolved.is_file()
            or not resolved.is_relative_to(artifact_directory)
            or resolved.suffix.lower() != ".joblib"
        ):
            raise ApprovedExecutionError("trained-artifact-path-invalid")
        return resolved

    async def _run_trainer(self, request: Any, execution: Any) -> Any:
        trainer = self._trainer
        if trainer is None:
            from routers.train import _do_train  # type: ignore

            trainer = _do_train

        cancel_requested = threading.Event()

        def progress_callback(_message: str, _percent: int | None = None) -> None:
            if cancel_requested.is_set():
                raise ApprovedExecutionError("approved-training-cancelled")

        def invoke() -> Any:
            return asyncio.run(
                trainer(
                    request,
                    {"user_id": self._worker_id},
                    progress_cb=progress_callback,
                    approved_execution=execution,
                )
            )

        thread_task = asyncio.create_task(asyncio.to_thread(invoke))
        try:
            return await asyncio.shield(thread_task)
        except asyncio.CancelledError:
            cancel_requested.set()
            try:
                await asyncio.shield(thread_task)
            except Exception:
                pass
            raise

    async def run_job(
        self,
        *,
        job_id: str,
        expected_version: int,
        snapshot_source: Path,
    ) -> RegisteredArtifact:
        from models import TrainRequest  # type: ignore

        source = self._snapshot_source(snapshot_source)
        lease = await asyncio.to_thread(
            self._gateway.claim,
            worker_id=self._worker_id,
            job_id=job_id,
            expected_version=expected_version,
            ttl_seconds=self._lease_ttl_seconds,
        )
        try:
            bundle_value = await asyncio.to_thread(
                self._gateway.execution_bundle,
                lease=lease,
                candidate_commit_sha=self._candidate_commit_sha,
                active_model_id=self._active_model_id,
            )
        except Exception:
            try:
                await asyncio.to_thread(
                    self._gateway.fail,
                    lease=lease,
                    failure_code="worker-error",
                )
            except Exception:
                pass
            raise
        try:
            bundle = ApprovedExecutionBundle.from_rpc(
                bundle_value,
                expected_job_id=job_id,
                expected_worker_id=self._worker_id,
                expected_fencing_token=lease.fencing_token,
                expected_candidate_commit_sha=self._candidate_commit_sha,
                expected_active_model_id=self._active_model_id,
                now=datetime.now(timezone.utc),
            )
            if bundle.execution_policy != self._execution_policy:
                raise ApprovedExecutionError("execution-bundle-policy-binding-mismatch")
        except Exception:
            try:
                await asyncio.to_thread(
                    self._gateway.fail,
                    lease=lease,
                    failure_code="input-invalid",
                )
            except Exception:
                pass
            raise
        supervisor = _HeartbeatSupervisor(
            self._gateway,
            lease,
            ttl_seconds=self._lease_ttl_seconds,
            interval_seconds=self._heartbeat_interval_seconds,
        )
        supervisor.start()
        uploaded_object: str | None = None
        registered = False
        started = False
        failure_code = "input-invalid"
        try:
            with tempfile.TemporaryDirectory(prefix=f"keiba-retrain-{job_id}-") as raw_workspace:
                workspace = Path(raw_workspace)
                snapshot = workspace / "snapshot.db"
                await asyncio.to_thread(shutil.copyfile, source, snapshot)
                execution = bundle.create_execution(
                    workspace=workspace,
                    snapshot_path=snapshot,
                )
                failure_code = "worker-error"
                await supervisor.mutate(
                    lambda current: self._gateway.start(lease=current)
                )
                started = True
                failure_code = "training-failed"
                request = TrainRequest(
                    target=bundle.target,
                    model_type=bundle.model_type,
                    force_sync=False,
                    test_size=0.2,
                    cv_folds=5,
                    use_optuna=False,
                )
                response = await self._run_trainer(request, execution)
                supervisor.ensure_live()
                artifact_path = self._artifact_path(
                    Path(response.model_path),
                    artifact_directory=execution.artifact_directory.resolve(strict=True),
                )
                with artifact_path.open("rb") as artifact_file:
                    digest = hashlib.sha256()
                    size_bytes = 0
                    for chunk in iter(lambda: artifact_file.read(1024 * 1024), b""):
                        size_bytes += len(chunk)
                        if size_bytes > MAX_ARTIFACT_BYTES:
                            raise ApprovedExecutionError("trained-artifact-size-invalid")
                        digest.update(chunk)
                    if size_bytes < 1:
                        raise ApprovedExecutionError("trained-artifact-size-invalid")
                    sha256 = digest.hexdigest()
                    artifact = RegisteredArtifact(
                        job_id=job_id,
                        object_name=f"retrain/{job_id}/{sha256}.joblib",
                        sha256=sha256,
                        size_bytes=size_bytes,
                        media_type=ARTIFACT_MEDIA_TYPE,
                    )
                    artifact_file.seek(0)
                    failure_code = "worker-error"
                    await asyncio.to_thread(
                        self._gateway.upload,
                        artifact_file=artifact_file,
                        object_name=artifact.object_name,
                        media_type=artifact.media_type,
                    )
                uploaded_object = artifact.object_name

                current = await supervisor.renew_and_close()
                await asyncio.to_thread(
                    self._gateway.register,
                    lease=current,
                    artifact=artifact,
                )
                registered = True
                return artifact
        except asyncio.CancelledError:
            failure_code = "cancelled-before-write"
            raise
        finally:
            current = await supervisor.close()
            cleanup_error: Exception | None = None
            if uploaded_object is not None and not registered:
                try:
                    await asyncio.to_thread(
                        self._gateway.remove,
                        object_name=uploaded_object,
                    )
                except Exception as exc:
                    cleanup_error = exc
            if not registered:
                try:
                    supervisor.ensure_live()
                    await asyncio.to_thread(
                        self._gateway.fail,
                        lease=current,
                        failure_code=failure_code if started else "input-invalid",
                    )
                except Exception:
                    pass
            if cleanup_error is not None:
                raise RetrainGatewayUnavailable(
                    "retrain-orphan-cleanup-required"
                ) from cleanup_error
