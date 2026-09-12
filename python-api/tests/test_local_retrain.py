from __future__ import annotations

import hashlib
import importlib
import io
import sqlite3
import stat
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


PYTHON_API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_API))
local = importlib.import_module("training.local_retrain")


COMMIT_SHA = "a" * 40
ACTIVE_MODEL_ID = "active-speed-model"
WORKER_ID = "local-worker-01"
MODEL_ID = "20250101_20251231_20260912_120000000001"
PREPARATION_TOKEN = "1" * 64


class _MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _parameters(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "force_sync": False,
        "test_size": 0.2,
        "cv_folds": 5,
        "use_sqlite": True,
        "ultimate_mode": True,
        "use_optimizer": True,
        "use_optuna": False,
        "optuna_trials": 50,
        "optuna_timeout": 300,
        "training_date_from": "2025-01",
        "training_date_to": "2025-12",
    }
    values.update(overrides)
    return values


def _result(tmp_path: Path) -> dict[str, object]:
    filename = f"model_speed_deviation_lightgbm_{MODEL_ID}.joblib"
    return {
        "success": True,
        "model_id": MODEL_ID,
        "model_path": str(tmp_path / "worker" / "artifacts" / filename),
        "metrics": {"rmse": 1.25},
        "data_count": 10,
        "race_count": 2,
        "feature_count": 1,
        "training_time": 0.1,
        "message": "done",
    }


def _source_tree(root: Path) -> Path:
    (root / "python-api").mkdir(parents=True)
    (root / "python-api" / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "keiba" / "keiba_ai").mkdir(parents=True)
    (root / "keiba" / "keiba_ai" / "feature.py").write_text(
        "FEATURE = 'speed'\n",
        encoding="utf-8",
    )
    (root / "keiba" / "feature_catalog.yaml").write_text(
        "features:\n  - speed\n",
        encoding="utf-8",
    )
    return root


def _snapshot(tmp_path: Path) -> local.LocalSnapshot:
    source = tmp_path / "ultimate.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE races(id TEXT PRIMARY KEY, value INTEGER)")
        connection.execute("INSERT INTO races VALUES ('r1', 1)")
    return local.create_sqlite_snapshot(source.resolve(), (tmp_path / "snapshots").resolve())


def _prepared(tmp_path: Path) -> dict[str, object]:
    source_root = _source_tree((tmp_path / "repo").resolve())
    snapshot = _snapshot(tmp_path)
    assert [path.name for path in snapshot.path.parent.iterdir()] == [snapshot.path.name]
    active_bytes = b"current active model"
    binding = [ACTIVE_MODEL_ID, _digest(active_bytes)]
    candidate_commit = [COMMIT_SHA]
    store = local.LocalRetrainStore((tmp_path / "state" / "jobs.db").resolve())
    job_id = str(uuid.uuid4())
    owner_id = str(uuid.uuid4())
    created = store.create_job(
        job_id=job_id,
        owner_id=owner_id,
        request_payload=_parameters(),
        preparation_token=PREPARATION_TOKEN,
        preparation_ttl_seconds=300,
    )
    contract = local.LocalTrainingContract.create(
        job_id=job_id,
        owner_id=owner_id,
        snapshot=snapshot,
        target="speed_deviation",
        model_type="lightgbm",
        selected_features=("speed", "pace"),
        removed_features=("pace",),
        candidate_commit_sha=COMMIT_SHA,
        source_tree_sha256=local.compute_source_tree_sha256(source_root),
        active_model_id=binding[0],
        active_model_sha256=binding[1],
        training_parameters=_parameters(),
        future_fields={"result"},
    )
    queued = store.queue_job(
        job_id=job_id,
        expected_version=created.record_version,
        preparation_token=PREPARATION_TOKEN,
        snapshot=snapshot,
        contract=contract,
    )
    models = (tmp_path / "models").resolve()
    gateway = local.LocalRetrainGateway(
        store,
        artifact_root=(models / ".local-retrain" / "artifacts").resolve(),
        published_model_root=models,
        source_root=source_root,
        candidate_commit_provider=lambda: candidate_commit[0],
        active_model_binding_provider=lambda: (binding[0], binding[1]),
    )
    return {
        "binding": binding,
        "candidate_commit": candidate_commit,
        "contract": contract,
        "gateway": gateway,
        "job_id": job_id,
        "models": models,
        "queued": queued,
        "snapshot": snapshot,
        "source_root": source_root,
        "store": store,
    }


def _register(prepared: dict[str, object]) -> tuple[object, local.RegisteredArtifact]:
    gateway = prepared["gateway"]
    queued = prepared["queued"]
    assert isinstance(gateway, local.LocalRetrainGateway)
    assert isinstance(queued, local.LocalJobRecord)
    lease = gateway.claim(
        worker_id=WORKER_ID,
        job_id=queued.job_id,
        expected_version=queued.record_version,
        ttl_seconds=120,
    )
    bundle = gateway.execution_bundle(
        lease=lease,
        candidate_commit_sha=COMMIT_SHA,
        active_model_id=ACTIVE_MODEL_ID,
    )
    parsed = local.LocalExecutionBundle.from_gateway(
        bundle,
        expected_job_id=lease.job_id,
        expected_worker_id=WORKER_ID,
        expected_fencing_token=lease.fencing_token,
        expected_candidate_commit_sha=COMMIT_SHA,
        expected_active_model_id=ACTIVE_MODEL_ID,
        now=datetime.now(timezone.utc),
    )
    assert parsed.execution_policy == local.LOCAL_EXECUTION_POLICY
    lease = gateway.start(lease=lease)
    value = b"local trained model fixture"
    digest = _digest(value)
    artifact = local.RegisteredArtifact(
        job_id=lease.job_id,
        object_name=f"retrain/{lease.job_id}/{digest}.joblib",
        sha256=digest,
        size_bytes=len(value),
        media_type=local.ARTIFACT_MEDIA_TYPE,
    )
    gateway.stage_result(lease, artifact, _result(Path(prepared["models"]).parent))
    gateway.upload(
        artifact_file=io.BytesIO(value),
        object_name=artifact.object_name,
        media_type=artifact.media_type,
    )
    gateway.register(lease=lease, artifact=artifact)
    return gateway, artifact


def test_snapshot_and_source_tree_bind_immutable_inputs(tmp_path: Path) -> None:
    source_root = _source_tree((tmp_path / "repo").resolve())
    before = local.compute_source_tree_sha256(source_root)
    (source_root / "data").mkdir()
    (source_root / "data" / "ignored.db").write_bytes(b"ignored")
    assert local.compute_source_tree_sha256(source_root) == before
    (source_root / "python-api" / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert local.compute_source_tree_sha256(source_root) != before

    snapshot = _snapshot(tmp_path)
    with sqlite3.connect(f"{snapshot.path.as_uri()}?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT value FROM races").fetchone() == (1,)
    workspace = (tmp_path / "worker").resolve()
    workspace.mkdir()
    linked = local.copy_local_snapshot(snapshot.path, workspace / "snapshot.db")
    assert linked.read_bytes() == snapshot.path.read_bytes()


def test_local_execution_requires_exact_ordered_feature_schema(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)
    contract = prepared["contract"]
    snapshot = prepared["snapshot"]
    assert isinstance(contract, local.LocalTrainingContract)
    assert isinstance(snapshot, local.LocalSnapshot)
    execution = local.LocalTrainingExecution(
        contract=contract,
        workspace=tmp_path,
        snapshot_path=snapshot.path,
    )

    assert execution.select_feature_columns(
        ["speed"],
        future_fields={"result"},
    ) == ("speed",)
    with pytest.raises(local.LocalRetrainError, match="schema-mismatch"):
        execution.select_feature_columns(
            ["speed", "extra"],
            future_fields={"result"},
        )
    with pytest.raises(local.LocalRetrainError, match="schema-mismatch"):
        execution.select_feature_columns(
            ["extra", "speed"],
            future_fields={"result"},
        )
    with pytest.raises(local.LocalRetrainError, match="future-field"):
        execution.select_feature_columns(
            ["result"],
            future_fields={"result"},
        )


def test_feature_contract_hash_binds_column_order() -> None:
    first = local._feature_contract_sha256(
        target="speed_deviation",
        model_type="lightgbm",
        selected_features=("speed", "pace"),
        removed_features=(),
    )
    reversed_order = local._feature_contract_sha256(
        target="speed_deviation",
        model_type="lightgbm",
        selected_features=("pace", "speed"),
        removed_features=(),
    )

    assert first != reversed_order


def test_progress_observer_is_bound_to_current_live_lease(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)
    gateway = prepared["gateway"]
    queued = prepared["queued"]
    store = prepared["store"]
    assert isinstance(gateway, local.LocalRetrainGateway)
    assert isinstance(queued, local.LocalJobRecord)
    assert isinstance(store, local.LocalRetrainStore)

    lease = gateway.claim(
        worker_id=WORKER_ID,
        job_id=queued.job_id,
        expected_version=queued.record_version,
        ttl_seconds=120,
    )
    lease = gateway.start(lease=lease)
    observe = gateway.progress_observer(queued.job_id)
    assert observe(lease, "first", 20) == lease
    assert store.get_job(queued.job_id).progress == "first"

    renewed = gateway.heartbeat(lease=lease, ttl_seconds=120)
    with pytest.raises(local.RetrainLeaseLost, match="local-retrain-lease-lost"):
        observe(lease, "stale version", 40)
    assert observe(renewed, "renewed", 50) == renewed
    assert store.get_job(queued.job_id).progress == "renewed"

    gateway.fail(lease=renewed, failure_code="training-failed")
    with pytest.raises(local.RetrainLeaseLost, match="local-retrain-lease-lost"):
        observe(renewed, "stale terminal write", 90)
    terminal = store.get_job(queued.job_id)
    assert terminal.state == "failed"
    assert terminal.progress == "失敗"
    assert terminal.pct == 100


def test_artifact_is_visible_only_after_registered_result_is_completed(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)
    gateway, artifact = _register(prepared)
    store = prepared["store"]
    models = prepared["models"]
    job_id = prepared["job_id"]
    assert isinstance(store, local.LocalRetrainStore)
    assert isinstance(models, Path)
    assert isinstance(job_id, str)
    registered = store.get_job(job_id)
    assert registered.state == "artifact-registered"
    assert registered.to_status()["status"] == "running"
    assert list(models.glob("model_*.joblib")) == []

    filename = f"model_speed_deviation_lightgbm_{MODEL_ID}.joblib"
    completed = gateway.record_result(
        job_id=job_id,
        artifact=artifact,
    )
    published = models / filename
    assert completed.state == "completed"
    assert completed.to_status()["status"] == "completed"
    assert completed.result is not None
    assert completed.result["model_path"] == str(published)
    assert published.read_bytes() == b"local trained model fixture"
    assert store.events(job_id)[-1]["event_type"] == "completed"


def test_register_rejects_changed_source_or_active_artifact(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)
    gateway = prepared["gateway"]
    queued = prepared["queued"]
    binding = prepared["binding"]
    assert isinstance(gateway, local.LocalRetrainGateway)
    assert isinstance(queued, local.LocalJobRecord)
    assert isinstance(binding, list)
    lease = gateway.claim(
        worker_id=WORKER_ID,
        job_id=queued.job_id,
        expected_version=queued.record_version,
        ttl_seconds=120,
    )
    lease = gateway.start(lease=lease)
    value = b"candidate"
    digest = _digest(value)
    artifact = local.RegisteredArtifact(
        job_id=lease.job_id,
        object_name=f"retrain/{lease.job_id}/{digest}.joblib",
        sha256=digest,
        size_bytes=len(value),
        media_type=local.ARTIFACT_MEDIA_TYPE,
    )
    gateway.stage_result(lease, artifact, _result(tmp_path))
    gateway.upload(
        artifact_file=io.BytesIO(value),
        object_name=artifact.object_name,
        media_type=artifact.media_type,
    )
    binding[1] = _digest(b"active model changed")
    with pytest.raises(local.RetrainLeaseLost, match="local-active-model-binding-changed"):
        gateway.register(lease=lease, artifact=artifact)


def test_register_rejects_candidate_commit_changed_after_staging(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)
    gateway = prepared["gateway"]
    queued = prepared["queued"]
    candidate_commit = prepared["candidate_commit"]
    assert isinstance(gateway, local.LocalRetrainGateway)
    assert isinstance(queued, local.LocalJobRecord)
    assert isinstance(candidate_commit, list)
    lease = gateway.claim(
        worker_id=WORKER_ID,
        job_id=queued.job_id,
        expected_version=queued.record_version,
        ttl_seconds=120,
    )
    lease = gateway.start(lease=lease)
    value = b"candidate from old commit"
    digest = _digest(value)
    artifact = local.RegisteredArtifact(
        job_id=lease.job_id,
        object_name=f"retrain/{lease.job_id}/{digest}.joblib",
        sha256=digest,
        size_bytes=len(value),
        media_type=local.ARTIFACT_MEDIA_TYPE,
    )
    gateway.stage_result(lease, artifact, _result(tmp_path))
    gateway.upload(
        artifact_file=io.BytesIO(value),
        object_name=artifact.object_name,
        media_type=artifact.media_type,
    )
    candidate_commit[0] = "b" * 40

    with pytest.raises(
        local.RetrainLeaseLost,
        match="local-candidate-commit-binding-changed",
    ):
        gateway.register(lease=lease, artifact=artifact)


def test_register_requires_durable_staged_output(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)
    gateway = prepared["gateway"]
    queued = prepared["queued"]
    assert isinstance(gateway, local.LocalRetrainGateway)
    assert isinstance(queued, local.LocalJobRecord)
    lease = gateway.claim(
        worker_id=WORKER_ID,
        job_id=queued.job_id,
        expected_version=queued.record_version,
        ttl_seconds=120,
    )
    lease = gateway.start(lease=lease)
    value = b"unstaged candidate"
    digest = _digest(value)
    artifact = local.RegisteredArtifact(
        job_id=lease.job_id,
        object_name=f"retrain/{lease.job_id}/{digest}.joblib",
        sha256=digest,
        size_bytes=len(value),
        media_type=local.ARTIFACT_MEDIA_TYPE,
    )
    gateway.upload(
        artifact_file=io.BytesIO(value),
        object_name=artifact.object_name,
        media_type=artifact.media_type,
    )

    with pytest.raises(
        local.RetrainGatewayUnavailable,
        match="local-staged-output-binding-mismatch",
    ):
        gateway.register(lease=lease, artifact=artifact)


def test_contract_rejects_sync_and_future_features(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)
    snapshot = prepared["snapshot"]
    contract = prepared["contract"]
    assert isinstance(snapshot, local.LocalSnapshot)
    assert isinstance(contract, local.LocalTrainingContract)
    with pytest.raises(local.LocalRetrainError, match="local-training-force-sync-forbidden"):
        local.LocalTrainingContract.create(
            job_id=str(uuid.uuid4()),
            owner_id=str(uuid.uuid4()),
            snapshot=snapshot,
            target="win",
            model_type="lightgbm",
            selected_features=("speed",),
            removed_features=(),
            candidate_commit_sha=COMMIT_SHA,
            source_tree_sha256=contract.source_tree_sha256,
            active_model_id=ACTIVE_MODEL_ID,
            active_model_sha256=contract.active_model_sha256,
            training_parameters=_parameters(force_sync=True),
            future_fields=(),
        )
    with pytest.raises(local.LocalRetrainError, match="future-field"):
        local.LocalTrainingContract.create(
            job_id=str(uuid.uuid4()),
            owner_id=str(uuid.uuid4()),
            snapshot=snapshot,
            target="win",
            model_type="lightgbm",
            selected_features=("result",),
            removed_features=(),
            candidate_commit_sha=COMMIT_SHA,
            source_tree_sha256=contract.source_tree_sha256,
            active_model_id=ACTIVE_MODEL_ID,
            active_model_sha256=contract.active_model_sha256,
            training_parameters=_parameters(),
            future_fields={"result"},
        )


def test_fail_queued_releases_active_slot_after_claim_failure(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)
    store = prepared["store"]
    queued = prepared["queued"]
    assert isinstance(store, local.LocalRetrainStore)
    assert isinstance(queued, local.LocalJobRecord)

    failed = store.fail_queued(
        job_id=queued.job_id,
        expected_version=queued.record_version,
        failure_code="claim-runtime-binding-failed",
    )

    assert failed.state == "failed"
    assert failed.failure_code == "claim-runtime-binding-failed"
    replacement = store.create_job(
        job_id=str(uuid.uuid4()),
        owner_id=str(uuid.uuid4()),
        request_payload=_parameters(),
        preparation_token="2" * 64,
        preparation_ttl_seconds=300,
    )
    assert replacement.state == "preparing"


def test_startup_reconcile_completes_registered_staged_result(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)
    _gateway, _artifact = _register(prepared)
    gateway = prepared["gateway"]
    store = prepared["store"]
    job_id = prepared["job_id"]
    source_root = prepared["source_root"]
    binding = prepared["binding"]
    assert isinstance(gateway, local.LocalRetrainGateway)
    assert isinstance(store, local.LocalRetrainStore)
    assert isinstance(job_id, str)
    assert isinstance(source_root, Path)
    assert isinstance(binding, list)
    assert store.get_job(job_id).state == "artifact-registered"
    # Registration is the authoritative runtime-binding checkpoint. Later
    # code/pointer changes must not strand a registered immutable artifact.
    (source_root / "python-api" / "main.py").write_text("VALUE = 99\n", encoding="utf-8")
    binding[1] = _digest(b"new active model")

    report = gateway.reconcile_orphan_artifacts()

    assert report.completed_publications == 1
    assert report.redispatch_job_ids == ()
    assert store.get_job(job_id).state == "completed"
    published = Path(store.get_job(job_id).result["model_path"])
    assert published.is_file()


def test_registered_result_finalizer_recovers_after_completion_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared(tmp_path)
    gateway, _artifact = _register(prepared)
    store = prepared["store"]
    job_id = prepared["job_id"]
    assert isinstance(store, local.LocalRetrainStore)
    assert isinstance(job_id, str)

    original_complete = store.complete_publication
    completion_attempts = 0

    def fail_first_completion(**kwargs: object) -> local.LocalJobRecord:
        nonlocal completion_attempts
        completion_attempts += 1
        if completion_attempts == 1:
            raise local.LocalRetrainError("injected-publication-completion-failure")
        return original_complete(**kwargs)

    monkeypatch.setattr(store, "complete_publication", fail_first_completion)

    with pytest.raises(
        local.LocalRetrainError,
        match="injected-publication-completion-failure",
    ):
        gateway.finalize_registered_result(job_id=job_id)

    registered = store.get_job(job_id)
    publication = store.get_publication(job_id)
    assert registered.state == "artifact-registered"
    assert publication.state == "prepared"
    assert publication.published_path.is_file()

    completed = gateway.finalize_registered_result(job_id=job_id)
    repeated = gateway.finalize_registered_result(job_id=job_id)

    assert completed.state == "completed"
    assert repeated.state == "completed"
    assert repeated.result == completed.result
    assert completion_attempts == 3
    assert [event["event_type"] for event in store.events(job_id)].count("completed") == 1


def test_startup_reconcile_fences_running_job_and_removes_orphan(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)
    gateway = prepared["gateway"]
    queued = prepared["queued"]
    store = prepared["store"]
    assert isinstance(gateway, local.LocalRetrainGateway)
    assert isinstance(queued, local.LocalJobRecord)
    assert isinstance(store, local.LocalRetrainStore)
    lease = gateway.claim(
        worker_id=WORKER_ID,
        job_id=queued.job_id,
        expected_version=queued.record_version,
        ttl_seconds=120,
    )
    lease = gateway.start(lease=lease)
    value = b"interrupted candidate"
    digest = _digest(value)
    artifact = local.RegisteredArtifact(
        job_id=lease.job_id,
        object_name=f"retrain/{lease.job_id}/{digest}.joblib",
        sha256=digest,
        size_bytes=len(value),
        media_type=local.ARTIFACT_MEDIA_TYPE,
    )
    gateway.stage_result(lease, artifact, _result(tmp_path))
    gateway.upload(
        artifact_file=io.BytesIO(value),
        object_name=artifact.object_name,
        media_type=artifact.media_type,
    )

    report = gateway.reconcile_orphan_artifacts()

    assert lease.job_id in report.failed_job_ids
    assert report.removed_orphans == 1
    assert store.get_job(lease.job_id).state == "failed"
    with pytest.raises(local.RetrainLeaseLost):
        gateway.heartbeat(lease=lease, ttl_seconds=120)


def test_published_artifact_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)
    gateway, artifact = _register(prepared)
    completed = gateway.record_result(
        job_id=artifact.job_id,
        artifact=artifact,
        result=_result(tmp_path),
    )
    published = Path(completed.result["model_path"])
    published.chmod(stat.S_IWRITE | stat.S_IREAD)
    published.write_bytes(b"tampered")

    with pytest.raises(local.LocalRetrainError, match="digest-mismatch"):
        gateway.reconcile_orphan_artifacts()


def test_startup_reconcile_restores_missing_published_link(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)
    gateway, artifact = _register(prepared)
    completed = gateway.record_result(job_id=artifact.job_id, artifact=artifact)
    published = Path(completed.result["model_path"])
    published.chmod(stat.S_IWRITE | stat.S_IREAD)
    published.unlink()

    report = gateway.reconcile_orphan_artifacts()

    assert report.restored_publications == 1
    assert published.read_bytes() == b"local trained model fixture"


def test_snapshot_catalog_reconcile_only_deletes_strict_temp(tmp_path: Path) -> None:
    catalog = (tmp_path / "snapshots").resolve()
    catalog.mkdir()
    snapshot = catalog / f"{'a' * 64}.db"
    snapshot.write_bytes(b"keep")
    interrupted = catalog / f".snapshot.{uuid.uuid4().hex}.tmp"
    interrupted.write_bytes(b"remove")

    assert local.reconcile_snapshot_catalog(catalog) == 1
    assert snapshot.read_bytes() == b"keep"
    assert not interrupted.exists()


def test_preparation_heartbeat_prevents_false_recovery_then_expires(
    tmp_path: Path,
) -> None:
    clock = _MutableClock()
    store = local.LocalRetrainStore((tmp_path / "jobs.db").resolve(), clock=clock)
    job_id = str(uuid.uuid4())
    created = store.create_job(
        job_id=job_id,
        owner_id=str(uuid.uuid4()),
        request_payload=_parameters(),
        preparation_token=PREPARATION_TOKEN,
        preparation_ttl_seconds=30,
    )
    original_deadline = created.preparation_expires_at

    clock.advance(20)
    renewed = store.heartbeat_preparation(
        job_id=job_id,
        expected_version=created.record_version,
        preparation_token=PREPARATION_TOKEN,
        ttl_seconds=30,
    )
    assert renewed.preparation_expires_at is not None
    assert original_deadline is not None
    assert renewed.preparation_expires_at > original_deadline

    clock.advance(20)
    assert store.recover_expired_jobs() == []
    assert store.get_job(job_id).state == "preparing"
    with pytest.raises(local.LocalRetrainConflict, match="local-preparation-lease-lost"):
        store.heartbeat_preparation(
            job_id=job_id,
            expected_version=created.record_version,
            preparation_token="f" * 64,
            ttl_seconds=30,
        )

    clock.advance(11)
    recovered = store.recover_expired_jobs()
    assert [record.job_id for record in recovered] == [job_id]
    assert recovered[0].failure_code == "preparation-lease-expired"
    assert store.events(job_id)[-1]["event_type"] == "preparation-lease-expired"


def test_new_start_reclaims_expired_preparation_and_fences_stale_worker(
    tmp_path: Path,
) -> None:
    clock = _MutableClock()
    source_root = _source_tree((tmp_path / "repo").resolve())
    snapshot = _snapshot(tmp_path)
    store = local.LocalRetrainStore((tmp_path / "jobs.db").resolve(), clock=clock)
    owner_id = str(uuid.uuid4())
    stale_job_id = str(uuid.uuid4())
    stale = store.create_job(
        job_id=stale_job_id,
        owner_id=owner_id,
        request_payload=_parameters(),
        preparation_token=PREPARATION_TOKEN,
        preparation_ttl_seconds=30,
    )
    contract = local.LocalTrainingContract.create(
        job_id=stale_job_id,
        owner_id=owner_id,
        snapshot=snapshot,
        target="speed_deviation",
        model_type="lightgbm",
        selected_features=("speed",),
        removed_features=(),
        candidate_commit_sha=COMMIT_SHA,
        source_tree_sha256=local.compute_source_tree_sha256(source_root),
        active_model_id=ACTIVE_MODEL_ID,
        active_model_sha256="c" * 64,
        training_parameters=_parameters(),
        future_fields=(),
    )

    clock.advance(31)
    replacement = store.create_job(
        job_id=str(uuid.uuid4()),
        owner_id=str(uuid.uuid4()),
        request_payload=_parameters(),
        preparation_token="2" * 64,
        preparation_ttl_seconds=30,
    )

    assert replacement.state == "preparing"
    assert store.get_job(stale_job_id).failure_code == "preparation-lease-expired"
    with pytest.raises(local.LocalRetrainConflict, match="local-job-queue-conflict"):
        store.queue_job(
            job_id=stale_job_id,
            expected_version=stale.record_version,
            preparation_token=PREPARATION_TOKEN,
            snapshot=snapshot,
            contract=contract,
        )


def test_snapshot_heartbeat_failure_aborts_and_removes_partial_file(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sample(value BLOB)")
        connection.executemany(
            "INSERT INTO sample VALUES (?)",
            [(b"x" * 4096,) for _ in range(32)],
        )
    catalog = (tmp_path / "snapshots").resolve()
    calls = 0

    def injected_lease_loss() -> None:
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise local.LocalRetrainConflict("injected-preparation-lease-loss")

    with pytest.raises(
        local.LocalRetrainConflict,
        match="injected-preparation-lease-loss",
    ):
        local.create_sqlite_snapshot(
            source.resolve(),
            catalog,
            heartbeat=injected_lease_loss,
        )

    assert calls >= 2
    assert list(catalog.iterdir()) == []
