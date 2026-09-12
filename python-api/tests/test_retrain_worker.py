from __future__ import annotations

import asyncio
import hashlib
import importlib
import os
import stat
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


PYTHON_API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_API))
approved = importlib.import_module("training.approved_execution")
local = importlib.import_module("training.local_retrain")
worker = importlib.import_module("training.retrain_worker")

WORKER_ID = "staging-worker-01"
COMMIT = "c" * 40
ACTIVE_MODEL = "baseline-model"


class FakeGateway:
    def __init__(
        self,
        snapshot_sha256: str,
        *,
        heartbeat_failure: bool = False,
        registration_failure: bool = False,
        cleanup_failure: bool = False,
        invalid_bundle: bool = False,
        execution_policy: str = "staging-train",
    ) -> None:
        self.snapshot_sha256 = snapshot_sha256
        self.heartbeat_failure = heartbeat_failure
        self.registration_failure = registration_failure
        self.cleanup_failure = cleanup_failure
        self.invalid_bundle = invalid_bundle
        self.execution_policy = execution_policy
        self.operations: list[str] = []
        self.uploaded: set[str] = set()
        self.failure_codes: list[str] = []

    @staticmethod
    def _lease(
        job_id: str,
        version: int,
        state: str,
        fencing_token: int = 7,
    ) -> worker.RetrainLease:
        return worker.RetrainLease(
            job_id=job_id,
            worker_id=WORKER_ID,
            fencing_token=fencing_token,
            record_version=version,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=120),
            job_state=state,
        )

    def claim(
        self,
        *,
        worker_id: str,
        job_id: str,
        expected_version: int,
        ttl_seconds: int,
    ) -> worker.RetrainLease:
        assert worker_id == WORKER_ID
        assert ttl_seconds == 30
        self.operations.append("claim")
        return self._lease(job_id, expected_version + 1, "claimed")

    def execution_bundle(
        self,
        *,
        lease: worker.RetrainLease,
        candidate_commit_sha: str,
        active_model_id: str,
    ) -> dict:
        self.operations.append("bundle")
        value = {
            "schema_version": 1,
            "job_id": lease.job_id,
            "approval_id": str(uuid.uuid4()),
            "dry_run_id": str(uuid.uuid4()),
            "approved_payload_hash": "a" * 64,
            "worker_id": lease.worker_id,
            "fencing_token": lease.fencing_token,
            "record_version": lease.record_version,
            "lease_expires_at": lease.lease_expires_at.isoformat(),
            "execution_policy": self.execution_policy,
            "target": "win",
            "model_type": "lightgbm",
            "train_period": {"start": "20250101", "end": "20251231"},
            "validation_period": {"start": "20260101", "end": "20260331"},
            "selected_features": ["feature_a"],
            "removed_features": [],
            "data_snapshot_sha256": self.snapshot_sha256,
            "feature_contract_sha256": "d" * 64,
            "candidate_commit_sha": candidate_commit_sha,
            "active_model_id": active_model_id,
            "training_parameters": {
                "force_sync": False,
                "test_size": 0.2,
                "cv_folds": 5,
                "use_optuna": False,
            },
        }
        if self.invalid_bundle:
            value["snapshot_path"] = "C:/forbidden.db"
        return value

    def heartbeat(
        self,
        *,
        lease: worker.RetrainLease,
        ttl_seconds: int,
    ) -> worker.RetrainLease:
        self.operations.append("heartbeat")
        if self.heartbeat_failure:
            raise worker.RetrainGatewayUnavailable("injected-heartbeat-failure")
        return self._lease(
            lease.job_id,
            lease.record_version + 1,
            lease.job_state,
            lease.fencing_token,
        )

    def start(self, *, lease: worker.RetrainLease) -> worker.RetrainLease:
        self.operations.append("start")
        return self._lease(
            lease.job_id,
            lease.record_version + 1,
            "running",
            lease.fencing_token,
        )

    def fail(self, *, lease: worker.RetrainLease, failure_code: str) -> worker.RetrainLease:
        self.operations.append("fail")
        self.failure_codes.append(failure_code)
        return self._lease(
            lease.job_id,
            lease.record_version + 1,
            "failed",
            lease.fencing_token,
        )

    def upload(self, *, artifact_file, object_name: str, media_type: str) -> None:
        assert media_type == worker.ARTIFACT_MEDIA_TYPE
        assert artifact_file.read() == b"immutable approved model artifact"
        artifact_file.seek(0)
        self.operations.append("upload")
        self.uploaded.add(object_name)

    def register(
        self,
        *,
        lease: worker.RetrainLease,
        artifact: worker.RegisteredArtifact,
    ) -> worker.RetrainLease:
        self.operations.append("register")
        assert artifact.object_name in self.uploaded
        if self.registration_failure:
            raise worker.RetrainGatewayUnavailable("injected-registration-failure")
        return self._lease(
            lease.job_id,
            lease.record_version + 1,
            "artifact-registered",
            lease.fencing_token,
        )

    def remove(self, *, object_name: str) -> None:
        self.operations.append("remove")
        if self.cleanup_failure:
            raise worker.RetrainGatewayUnavailable("injected-cleanup-failure")
        self.uploaded.discard(object_name)


def _snapshot(tmp_path: Path, content: bytes = b"approved sqlite snapshot") -> Path:
    path = tmp_path / "source.db"
    path.write_bytes(content)
    return path


def _coordinator(
    gateway: FakeGateway,
    trainer,
    *,
    execution_policy: str = "staging-train",
) -> worker.ApprovedRetrainCoordinator:
    return worker.ApprovedRetrainCoordinator(
        gateway,
        worker_id=WORKER_ID,
        candidate_commit_sha=COMMIT,
        active_model_id=ACTIVE_MODEL,
        execution_policy=execution_policy,
        lease_ttl_seconds=30,
        heartbeat_interval_seconds=0.01,
        trainer=trainer,
    )


async def _successful_trainer(_request, _user, *, progress_cb, approved_execution):
    await asyncio.sleep(0.04)
    progress_cb("training", 50)
    artifact_directory = approved_execution.prepare_artifact_directory()
    artifact_path = artifact_directory / "candidate.joblib"
    artifact_path.write_bytes(b"immutable approved model artifact")
    return SimpleNamespace(model_path=str(artifact_path))


def test_success_keeps_lease_live_and_registers_digest_bound_artifact(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    gateway = FakeGateway(digest)
    coordinator = _coordinator(gateway, _successful_trainer)
    job_id = str(uuid.uuid4())

    artifact = asyncio.run(
        coordinator.run_job(job_id=job_id, expected_version=1, snapshot_source=snapshot)
    )

    assert artifact.job_id == job_id
    assert artifact.object_name == f"retrain/{job_id}/{artifact.sha256}.joblib"
    assert artifact.object_name in gateway.uploaded
    assert gateway.operations[0:2] == ["claim", "bundle"]
    assert gateway.operations.index("start") < gateway.operations.index("upload")
    assert "heartbeat" in gateway.operations
    assert gateway.operations[-1] == "register"
    assert "fail" not in gateway.operations
    assert "remove" not in gateway.operations


def test_approved_policy_keeps_canonical_parser_request_and_snapshot_copy(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    gateway = FakeGateway(digest)
    observed: dict[str, object] = {}

    async def trainer(request, _user, *, progress_cb, approved_execution):
        observed["request"] = request
        observed["snapshot_is_source"] = approved_execution.snapshot_path.samefile(snapshot)
        progress_cb("training", 50)
        artifact_directory = approved_execution.prepare_artifact_directory()
        artifact_path = artifact_directory / "candidate.joblib"
        artifact_path.write_bytes(b"immutable approved model artifact")
        return SimpleNamespace(model_path=str(artifact_path))

    artifact = asyncio.run(
        _coordinator(gateway, trainer).run_job(
            job_id=str(uuid.uuid4()),
            expected_version=1,
            snapshot_source=snapshot,
        )
    )

    request = observed["request"]
    assert request.target == "win"
    assert request.model_type == "lightgbm"
    assert request.force_sync is False
    assert request.test_size == 0.2
    assert request.cv_folds == 5
    assert request.use_optuna is False
    assert request.training_date_from is None
    assert request.training_date_to is None
    assert observed["snapshot_is_source"] is False
    assert artifact.object_name in gateway.uploaded


@pytest.mark.parametrize("execution_policy", ["staging-train", "sandbox-train"])
@pytest.mark.parametrize(
    "override",
    [
        {"bundle_parser": lambda *_args, **_kwargs: None},
        {"request_factory": lambda _bundle: None},
        {"progress_observer": lambda lease, _message, _percent: lease},
        {"snapshot_copier": lambda _source, _destination: None},
        {"result_observer": lambda _lease, _artifact, _response: _lease},
    ],
    ids=[
        "bundle-parser",
        "request-factory",
        "progress-observer",
        "snapshot-copier",
        "result-observer",
    ],
)
def test_approved_policy_rejects_all_extension_overrides(
    execution_policy: str,
    override: dict[str, object],
) -> None:
    with pytest.raises(
        worker.RetrainWorkerError,
        match="retrain-worker-approved-parser-override-forbidden",
    ):
        worker.ApprovedRetrainCoordinator(
            FakeGateway("a" * 64),
            worker_id=WORKER_ID,
            candidate_commit_sha=COMMIT,
            active_model_id=ACTIVE_MODEL,
            execution_policy=execution_policy,
            lease_ttl_seconds=30,
            trainer=_successful_trainer,
            **override,
        )


def _local_required_interfaces() -> dict[str, object]:
    return {
        "bundle_parser": lambda *_args, **_kwargs: None,
        "request_factory": lambda _bundle: None,
        "progress_observer": lambda lease, _message, _percent: lease,
        "snapshot_copier": lambda _source, _destination: None,
        "result_observer": lambda lease, _artifact, _response: lease,
    }


@pytest.mark.parametrize(
    "missing_interface",
    [
        "bundle_parser",
        "request_factory",
        "progress_observer",
        "snapshot_copier",
        "result_observer",
    ],
)
def test_local_policy_requires_every_execution_interface(missing_interface: str) -> None:
    interfaces = _local_required_interfaces()
    interfaces.pop(missing_interface)

    with pytest.raises(worker.RetrainWorkerError, match="retrain-worker-policy-invalid"):
        worker.ApprovedRetrainCoordinator(
            FakeGateway("a" * 64),
            worker_id=WORKER_ID,
            candidate_commit_sha=COMMIT,
            active_model_id=ACTIVE_MODEL,
            execution_policy="local-admin-train",
            lease_ttl_seconds=30,
            trainer=_successful_trainer,
            **interfaces,
        )


def test_local_policy_uses_custom_bundle_request_progress_and_hardlink(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    gateway = FakeGateway(digest)
    parser_calls: list[tuple[dict, dict]] = []
    factory_calls: list[object] = []
    progress_events: list[tuple[int, str, int | None]] = []
    hardlink_checks: list[bool] = []
    staged_artifacts: list[worker.RegisteredArtifact] = []
    custom_request = SimpleNamespace(kind="local-admin-request")

    def parse_local_bundle(value: dict, **bindings: object):
        parser_calls.append((value, bindings))

        def create_execution(*, workspace: Path, snapshot_path: Path):
            return approved.ApprovedTrainingExecution.create(
                job_id=value["job_id"],
                approved_payload_hash=value["approved_payload_hash"],
                data_snapshot_sha256=value["data_snapshot_sha256"],
                feature_contract_sha256=value["feature_contract_sha256"],
                candidate_commit_sha=value["candidate_commit_sha"],
                target=value["target"],
                model_type=value["model_type"],
                active_model_id=value["active_model_id"],
                train_period_start=value["train_period"]["start"],
                train_period_end=value["train_period"]["end"],
                validation_period_start=value["validation_period"]["start"],
                validation_period_end=value["validation_period"]["end"],
                selected_features=value["selected_features"],
                removed_features=value["removed_features"],
                workspace=workspace,
                snapshot_path=snapshot_path,
            )

        return SimpleNamespace(
            execution_policy="local-admin-train",
            target=value["target"],
            model_type=value["model_type"],
            create_execution=create_execution,
        )

    def create_local_request(bundle: object) -> object:
        factory_calls.append(bundle)
        return custom_request

    def observe_progress(lease, message: str, percent: int | None):
        progress_events.append((lease.record_version, message, percent))
        return lease

    def hardlink_snapshot(source: Path, destination: Path) -> None:
        os.link(source, destination)
        hardlink_checks.append(source.samefile(destination))

    def stage_result(lease, artifact, _response):
        staged_artifacts.append(artifact)
        return lease

    async def trainer(request, _user, *, progress_cb, approved_execution):
        assert request is custom_request
        assert approved_execution.snapshot_path.samefile(snapshot)
        await asyncio.sleep(0.04)
        progress_cb("local training", 61)
        artifact_directory = approved_execution.prepare_artifact_directory()
        artifact_path = artifact_directory / "candidate.joblib"
        artifact_path.write_bytes(b"immutable approved model artifact")
        return SimpleNamespace(model_path=str(artifact_path))

    coordinator = worker.ApprovedRetrainCoordinator(
        gateway,
        worker_id=WORKER_ID,
        candidate_commit_sha=COMMIT,
        active_model_id=ACTIVE_MODEL,
        execution_policy="local-admin-train",
        lease_ttl_seconds=30,
        heartbeat_interval_seconds=0.01,
        trainer=trainer,
        bundle_parser=parse_local_bundle,
        request_factory=create_local_request,
        progress_observer=observe_progress,
        snapshot_copier=hardlink_snapshot,
        result_observer=stage_result,
    )
    job_id = str(uuid.uuid4())

    artifact = asyncio.run(
        coordinator.run_job(
            job_id=job_id,
            expected_version=1,
            snapshot_source=snapshot,
        )
    )

    assert len(parser_calls) == 1
    parsed_value, bindings = parser_calls[0]
    assert parsed_value["job_id"] == job_id
    assert bindings["expected_job_id"] == job_id
    assert bindings["expected_worker_id"] == WORKER_ID
    assert bindings["expected_fencing_token"] == 7
    assert bindings["expected_candidate_commit_sha"] == COMMIT
    assert bindings["expected_active_model_id"] == ACTIVE_MODEL
    assert isinstance(bindings["now"], datetime)
    assert len(factory_calls) == 1
    assert factory_calls[0].execution_policy == "local-admin-train"
    assert hardlink_checks == [True]
    assert len(progress_events) == 1
    progress_version, progress_message, progress_percent = progress_events[0]
    assert progress_version > 3
    assert (progress_message, progress_percent) == ("local training", 61)
    assert staged_artifacts == [artifact]
    assert artifact.object_name in gateway.uploaded


def test_lease_loss_prevents_upload_registration_and_failure_mutation(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    gateway = FakeGateway(digest, heartbeat_failure=True)
    coordinator = _coordinator(gateway, _successful_trainer)

    with pytest.raises(worker.RetrainLeaseLost):
        asyncio.run(
            coordinator.run_job(
                job_id=str(uuid.uuid4()),
                expected_version=1,
                snapshot_source=snapshot,
            )
        )

    assert "upload" not in gateway.operations
    assert "register" not in gateway.operations
    assert "fail" not in gateway.operations


def test_registration_failure_removes_orphan_and_records_bounded_failure(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    gateway = FakeGateway(digest, registration_failure=True)
    coordinator = _coordinator(gateway, _successful_trainer)

    with pytest.raises(worker.RetrainGatewayUnavailable, match="registration-failure"):
        asyncio.run(
            coordinator.run_job(
                job_id=str(uuid.uuid4()),
                expected_version=1,
                snapshot_source=snapshot,
            )
        )

    assert gateway.uploaded == set()
    assert gateway.operations[-2:] == ["remove", "fail"]
    assert gateway.failure_codes == ["worker-error"]


def test_snapshot_digest_mismatch_fails_before_start_or_write(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    gateway = FakeGateway("f" * 64)
    coordinator = _coordinator(gateway, _successful_trainer)

    with pytest.raises(approved.ApprovedExecutionError, match="data-snapshot-hash-mismatch"):
        asyncio.run(
            coordinator.run_job(
                job_id=str(uuid.uuid4()),
                expected_version=1,
                snapshot_source=snapshot,
            )
        )

    assert "start" not in gateway.operations
    assert "upload" not in gateway.operations
    assert gateway.failure_codes == ["input-invalid"]


@pytest.mark.parametrize("execution_policy", ["staging-train", "sandbox-train"])
def test_approved_snapshot_mutation_during_training_fails_before_write(
    tmp_path: Path,
    execution_policy: str,
) -> None:
    original = b"approved sqlite snapshot"
    snapshot = _snapshot(tmp_path, original)
    digest = hashlib.sha256(original).hexdigest()
    gateway = FakeGateway(digest, execution_policy=execution_policy)

    async def mutating_trainer(
        _request,
        _user,
        *,
        progress_cb,
        approved_execution,
    ):
        progress_cb("training", 50)
        approved_execution.snapshot_path.write_bytes(b"tampered workspace snapshot")
        artifact_directory = approved_execution.prepare_artifact_directory()
        artifact_path = artifact_directory / "candidate.joblib"
        artifact_path.write_bytes(b"immutable approved model artifact")
        return SimpleNamespace(model_path=str(artifact_path))

    coordinator = _coordinator(
        gateway,
        mutating_trainer,
        execution_policy=execution_policy,
    )

    with pytest.raises(
        approved.ApprovedExecutionError,
        match="data-snapshot-hash-mismatch",
    ):
        asyncio.run(
            coordinator.run_job(
                job_id=str(uuid.uuid4()),
                expected_version=1,
                snapshot_source=snapshot,
            )
        )

    # Approved staging/sandbox use a private copy; the catalog/source remains
    # unchanged while the mutated workspace is rejected before any write.
    assert snapshot.read_bytes() == original
    assert "upload" not in gateway.operations
    assert "register" not in gateway.operations
    assert "remove" not in gateway.operations
    assert gateway.failure_codes == ["training-failed"]


def test_local_hardlinked_snapshot_mutation_during_training_fails_before_staging(
    tmp_path: Path,
) -> None:
    original = b"local catalog snapshot"
    snapshot_path = _snapshot(tmp_path, original).resolve()
    digest = hashlib.sha256(original).hexdigest()
    job_id = str(uuid.uuid4())
    contract = local.LocalTrainingContract.create(
        job_id=job_id,
        owner_id=str(uuid.uuid4()),
        snapshot=local.LocalSnapshot(snapshot_path, digest, len(original)),
        target="win",
        model_type="lightgbm",
        selected_features=("feature_a",),
        removed_features=(),
        candidate_commit_sha=COMMIT,
        source_tree_sha256="b" * 64,
        active_model_id=ACTIVE_MODEL,
        active_model_sha256="e" * 64,
        training_parameters={
            "force_sync": False,
            "test_size": 0.2,
            "cv_folds": 5,
            "use_sqlite": True,
            "ultimate_mode": True,
            "use_optimizer": True,
            "use_optuna": False,
            "optuna_trials": 50,
            "optuna_timeout": 300,
            "training_date_from": None,
            "training_date_to": None,
        },
        future_fields=(),
    )

    class LocalBundleGateway(FakeGateway):
        def execution_bundle(
            self,
            *,
            lease: worker.RetrainLease,
            candidate_commit_sha: str,
            active_model_id: str,
        ) -> dict:
            assert candidate_commit_sha == COMMIT
            assert active_model_id == ACTIVE_MODEL
            self.operations.append("bundle")
            return {
                "schema_version": 1,
                "execution_policy": local.LOCAL_EXECUTION_POLICY,
                "contract": contract.to_mapping(),
                "contract_sha256": contract.sha256,
                "worker_id": lease.worker_id,
                "fencing_token": lease.fencing_token,
                "record_version": lease.record_version,
                "lease_expires_at": lease.lease_expires_at.isoformat(),
            }

    gateway = LocalBundleGateway(digest)
    staged: list[worker.RegisteredArtifact] = []

    def observe_result(lease, artifact, _response):
        staged.append(artifact)
        return lease

    async def mutating_trainer(
        _request,
        _user,
        *,
        progress_cb,
        approved_execution,
    ):
        progress_cb("training", 50)
        # copy_local_snapshot uses a hardlink and marks it read-only. Re-enable
        # writes to inject the same corruption that would affect the catalog.
        approved_execution.snapshot_path.chmod(stat.S_IWRITE | stat.S_IREAD)
        approved_execution.snapshot_path.write_bytes(b"tampered catalog snapshot")
        artifact_directory = approved_execution.prepare_artifact_directory()
        artifact_path = artifact_directory / "candidate.joblib"
        artifact_path.write_bytes(b"immutable approved model artifact")
        return SimpleNamespace(model_path=str(artifact_path))

    coordinator = worker.ApprovedRetrainCoordinator(
        gateway,
        worker_id=WORKER_ID,
        candidate_commit_sha=COMMIT,
        active_model_id=ACTIVE_MODEL,
        execution_policy=local.LOCAL_EXECUTION_POLICY,
        lease_ttl_seconds=30,
        heartbeat_interval_seconds=0.01,
        trainer=mutating_trainer,
        bundle_parser=local.LocalExecutionBundle.from_gateway,
        request_factory=lambda bundle: SimpleNamespace(
            **bundle.training_request_payload()
        ),
        progress_observer=lambda lease, _message, _percent: lease,
        snapshot_copier=local.copy_local_snapshot,
        result_observer=observe_result,
    )

    with pytest.raises(
        local.LocalRetrainError,
        match="local-execution-snapshot-hash-mismatch",
    ):
        asyncio.run(
            coordinator.run_job(
                job_id=job_id,
                expected_version=1,
                snapshot_source=snapshot_path,
            )
        )

    assert snapshot_path.read_bytes() == b"tampered catalog snapshot"
    assert staged == []
    assert "upload" not in gateway.operations
    assert "register" not in gateway.operations
    assert "remove" not in gateway.operations
    assert gateway.failure_codes == ["training-failed"]


def test_orphan_cleanup_failure_is_never_silently_swallowed(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    gateway = FakeGateway(
        digest,
        registration_failure=True,
        cleanup_failure=True,
    )
    coordinator = _coordinator(gateway, _successful_trainer)

    with pytest.raises(worker.RetrainGatewayUnavailable, match="orphan-cleanup-required"):
        asyncio.run(
            coordinator.run_job(
                job_id=str(uuid.uuid4()),
                expected_version=1,
                snapshot_source=snapshot,
            )
        )

    assert len(gateway.uploaded) == 1
    assert gateway.operations[-2:] == ["remove", "fail"]
    assert gateway.failure_codes == ["worker-error"]


def test_cancellation_before_upload_records_bounded_cancelled_failure(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    gateway = FakeGateway(digest)
    trainer_started = asyncio.Event()

    async def slow_trainer(_request, _user, *, progress_cb, approved_execution):
        del approved_execution
        trainer_started.set()
        while True:
            await asyncio.sleep(0.01)
            progress_cb("training", 50)

    coordinator = _coordinator(gateway, slow_trainer)

    async def scenario() -> None:
        task = asyncio.create_task(
            coordinator.run_job(
                job_id=str(uuid.uuid4()),
                expected_version=1,
                snapshot_source=snapshot,
            )
        )
        await asyncio.wait_for(trainer_started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert "upload" not in gateway.operations
    assert "register" not in gateway.operations
    assert gateway.failure_codes == ["cancelled-before-write"]


def test_invalid_bundle_is_failed_without_starting_heartbeat_or_training(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    gateway = FakeGateway(digest, invalid_bundle=True)
    trainer_calls = 0

    async def trainer(*_args, **_kwargs):
        nonlocal trainer_calls
        trainer_calls += 1

    coordinator = _coordinator(gateway, trainer)
    with pytest.raises(approved.ApprovedExecutionError, match="schema-invalid"):
        asyncio.run(
            coordinator.run_job(
                job_id=str(uuid.uuid4()),
                expected_version=1,
                snapshot_source=snapshot,
            )
        )

    assert trainer_calls == 0
    assert gateway.operations == ["claim", "bundle", "fail"]
    assert gateway.failure_codes == ["input-invalid"]


def test_worker_environment_policy_must_match_approved_bundle(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    gateway = FakeGateway(digest)
    coordinator = worker.ApprovedRetrainCoordinator(
        gateway,
        worker_id=WORKER_ID,
        candidate_commit_sha=COMMIT,
        active_model_id=ACTIVE_MODEL,
        execution_policy="sandbox-train",
        lease_ttl_seconds=30,
        heartbeat_interval_seconds=0.01,
        trainer=_successful_trainer,
    )

    with pytest.raises(approved.ApprovedExecutionError, match="policy-binding-mismatch"):
        asyncio.run(
            coordinator.run_job(
                job_id=str(uuid.uuid4()),
                expected_version=1,
                snapshot_source=snapshot,
            )
        )

    assert gateway.operations == ["claim", "bundle", "fail"]
    assert gateway.failure_codes == ["input-invalid"]


class _RpcBuilder:
    def __init__(self, data) -> None:
        self._data = data

    def execute(self):
        return SimpleNamespace(data=self._data)


class _StorageBucket:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, bytes, dict]] = []
        self.removals: list[list[str]] = []

    def upload(self, object_name: str, handle, *, file_options: dict) -> None:
        self.uploads.append((object_name, handle.read(), dict(file_options)))

    def remove(self, object_names: list[str]) -> None:
        self.removals.append(list(object_names))


class _Storage:
    def __init__(self, bucket: _StorageBucket) -> None:
        self.bucket = bucket
        self.names: list[str] = []

    def from_(self, name: str) -> _StorageBucket:
        self.names.append(name)
        return self.bucket


class _SupabaseClient:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []
        self.bucket = _StorageBucket()
        self.storage = _Storage(self.bucket)

    def rpc(self, name: str, params: dict) -> _RpcBuilder:
        self.calls.append((name, dict(params)))
        return _RpcBuilder(self.responses[name])


def _lease_row(job_id: str, version: int, state: str, fence: int = 9) -> dict:
    return {
        "job_id": job_id,
        "worker_id": WORKER_ID,
        "fencing_token": fence,
        "record_version": version,
        "lease_expires_at": (
            datetime.now(timezone.utc) + timedelta(seconds=120)
        ).isoformat(),
        "job_state": state,
    }


def test_supabase_gateway_uses_exact_fenced_rpcs_and_private_non_upsert_storage(
    tmp_path: Path,
) -> None:
    job_id = str(uuid.uuid4())
    bundle_value = {"schema_version": 1}
    client = _SupabaseClient(
        {
            "claim_model_retrain_job": [_lease_row(job_id, 2, "claimed")],
            "get_model_retrain_execution_bundle": bundle_value,
            "heartbeat_model_retrain_job": [_lease_row(job_id, 3, "claimed")],
            "start_model_retrain_job": [_lease_row(job_id, 4, "running")],
            "register_model_retrain_artifact": [
                _lease_row(job_id, 5, "artifact-registered")
            ],
        }
    )
    gateway = worker.SupabaseRetrainGateway(client)

    claimed = gateway.claim(
        worker_id=WORKER_ID,
        job_id=job_id,
        expected_version=1,
        ttl_seconds=120,
    )
    assert gateway.execution_bundle(
        lease=claimed,
        candidate_commit_sha=COMMIT,
        active_model_id=ACTIVE_MODEL,
    ) == bundle_value
    renewed = gateway.heartbeat(lease=claimed, ttl_seconds=120)
    running = gateway.start(lease=renewed)

    artifact_path = tmp_path / "candidate.joblib"
    artifact_path.write_bytes(b"candidate")
    digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    artifact = worker.RegisteredArtifact(
        job_id=job_id,
        object_name=f"retrain/{job_id}/{digest}.joblib",
        sha256=digest,
        size_bytes=artifact_path.stat().st_size,
        media_type=worker.ARTIFACT_MEDIA_TYPE,
    )
    with artifact_path.open("rb") as artifact_file:
        gateway.upload(
            artifact_file=artifact_file,
            object_name=artifact.object_name,
            media_type=artifact.media_type,
        )
    registered = gateway.register(lease=running, artifact=artifact)
    gateway.remove(object_name=artifact.object_name)

    assert registered.job_state == "artifact-registered"
    assert [name for name, _params in client.calls] == [
        "claim_model_retrain_job",
        "get_model_retrain_execution_bundle",
        "heartbeat_model_retrain_job",
        "start_model_retrain_job",
        "register_model_retrain_artifact",
    ]
    assert client.calls[1][1] == {
        "p_worker_id": WORKER_ID,
        "p_job_id": job_id,
        "p_expected_version": 2,
        "p_fencing_token": 9,
        "p_candidate_commit_sha": COMMIT,
        "p_active_model_id": ACTIVE_MODEL,
    }
    assert client.storage.names == ["models", "models"]
    assert client.bucket.uploads == [
        (
            artifact.object_name,
            b"candidate",
            {
                "content-type": worker.ARTIFACT_MEDIA_TYPE,
                "upsert": "false",
            },
        )
    ]
    assert client.bucket.removals == [[artifact.object_name]]
    assert not hasattr(client, "table")


def test_supabase_gateway_rejects_wrong_transition_binding() -> None:
    job_id = str(uuid.uuid4())
    client = _SupabaseClient(
        {
            "claim_model_retrain_job": [
                _lease_row(str(uuid.uuid4()), 2, "claimed")
            ],
        }
    )
    gateway = worker.SupabaseRetrainGateway(client)

    with pytest.raises(worker.RetrainGatewayUnavailable, match="claim-response-invalid"):
        gateway.claim(
            worker_id=WORKER_ID,
            job_id=job_id,
            expected_version=1,
            ttl_seconds=120,
        )
