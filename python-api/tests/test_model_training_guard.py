from __future__ import annotations

import asyncio
import hashlib
import importlib
import inspect
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request


PYTHON_API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_API))
train = importlib.import_module("routers.train")
models = importlib.import_module("models")
job_store = importlib.import_module("training.job_store")
local_retrain = importlib.import_module("training.local_retrain")


def _request_from(client_host: str) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/train/capability",
            "raw_path": b"/api/train/capability",
            "query_string": b"",
            "headers": [],
            "client": (client_host, 12345),
            "server": ("127.0.0.1", 8000),
        }
    )


def _route_dependencies(path: str, method: str) -> set[object]:
    route = next(
        route
        for route in train.router.routes
        if route.path == path and method in route.methods
    )
    return {dependency.call for dependency in route.dependant.dependencies}


@pytest.mark.parametrize("environment", ["staging", "production", "prod", "", "unknown"])
def test_deployed_or_unknown_environment_cannot_enable_direct_training(
    environment: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", environment)
    monkeypatch.setenv("MODEL_TRAINING_LOCAL_ENABLED", "true")

    with pytest.raises(HTTPException) as captured:
        train._require_legacy_model_training_allowed()

    assert captured.value.status_code == 409
    assert "approval-bound durable job" in str(captured.value.detail)


def test_local_training_is_disabled_without_exact_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("MODEL_TRAINING_LOCAL_ENABLED", "false")

    with pytest.raises(HTTPException) as captured:
        train._require_legacy_model_training_allowed()

    assert captured.value.status_code == 409


def test_explicit_local_opt_in_allows_legacy_compatibility(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MODEL_TRAINING_LOCAL_ENABLED", "TRUE")

    assert train._require_legacy_model_training_allowed() is None


def test_blocked_training_does_not_create_job_or_write_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MODEL_TRAINING_LOCAL_ENABLED", "true")
    monkeypatch.setattr(train.joblib, "dump", lambda *_args, **_kwargs: pytest.fail("artifact write"))
    monkeypatch.setattr(
        train,
        "_get_local_retrain_runtime",
        lambda: pytest.fail("ledger must not be opened"),
    )

    with pytest.raises(HTTPException) as captured:
        asyncio.run(train.train_start(models.TrainRequest(), {"user_id": "premium-user"}))

    assert captured.value.status_code == 409


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/train/capability", "GET"),
        ("/api/train", "POST"),
        ("/api/train/start", "POST"),
        ("/api/train/status/{job_id}", "GET"),
    ],
)
def test_local_training_routes_require_strict_admin_and_loopback(
    path: str,
    method: str,
) -> None:
    dependencies = _route_dependencies(path, method)

    assert train.require_local_training_admin in dependencies
    assert train.require_loopback_training_request in dependencies


def test_local_training_admin_guard_wraps_the_standard_admin_guard() -> None:
    dependency = inspect.signature(
        train.require_local_training_admin
    ).parameters["current_user"].default

    assert dependency.dependency is train.require_admin


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"])
def test_loopback_training_dependency_accepts_this_computer(host: str) -> None:
    assert asyncio.run(train.require_loopback_training_request(_request_from(host))) is None


@pytest.mark.parametrize("host", ["192.168.1.20", "10.0.0.4", "example.test", ""])
def test_loopback_training_dependency_rejects_remote_clients(host: str) -> None:
    with pytest.raises(HTTPException) as captured:
        asyncio.run(train.require_loopback_training_request(_request_from(host)))

    assert captured.value.status_code == 403


def test_capability_requires_exact_local_flag_database_and_model_storage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "keiba.db"
    model_directory = tmp_path / "models"
    database.write_bytes(b"sqlite-data")
    model_directory.mkdir()
    active_model_id = "model_test_active"
    train.joblib.dump(
        {"feature_columns": ["odds", "popularity"]},
        model_directory / f"{active_model_id}.joblib",
    )
    monkeypatch.setattr(train, "ULTIMATE_DB", database)
    monkeypatch.setattr(train, "MODELS_DIR", model_directory)
    monkeypatch.setattr(train, "get_active_model_id", lambda: active_model_id)
    monkeypatch.setattr(
        train,
        "_LOCAL_RETRAIN_SNAPSHOT_CATALOG",
        (tmp_path / "snapshots").resolve(),
    )
    monkeypatch.setattr(train, "_LOCAL_RETRAIN_STARTUP_ERROR", None)
    monkeypatch.setattr(train, "_get_local_retrain_runtime", lambda: (object(), object()))
    monkeypatch.setattr(train, "_candidate_commit_sha", lambda: "a" * 40)
    monkeypatch.setattr(train, "compute_source_tree_sha256", lambda _root: "b" * 64)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("MODEL_TRAINING_LOCAL_ENABLED", "true")
    monkeypatch.setenv("MODEL_RUNTIME_STATUS", "observation")

    assert train._training_capability_state() == {
        "enabled": True,
        "mode": "local-admin",
        "reason": None,
    }

    monkeypatch.setenv("MODEL_TRAINING_LOCAL_ENABLED", "1")
    assert train._training_capability_state()["reason"] == "local-training-disabled"

    monkeypatch.setenv("MODEL_TRAINING_LOCAL_ENABLED", "true")
    database.unlink()
    assert train._training_capability_state()["reason"] == "database-unavailable"

    database.write_bytes(b"sqlite-data")
    real_access = train.os.access
    monkeypatch.setattr(
        train.os,
        "access",
        lambda path, mode: False if Path(path) == model_directory else real_access(path, mode),
    )
    assert train._training_capability_state()["reason"] == "model-storage-unavailable"

    monkeypatch.setattr(train.os, "access", real_access)

    def unavailable_ledger() -> tuple[object, object]:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(train, "_get_local_retrain_runtime", unavailable_ledger)
    assert (
        train._training_capability_state()["reason"]
        == "local-training-storage-unavailable"
    )


def test_start_is_single_active_and_status_is_owner_bound(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("MODEL_TRAINING_LOCAL_ENABLED", "true")
    store = train.LocalRetrainStore(tmp_path / "local-train.db")
    monkeypatch.setattr(train, "_get_local_retrain_runtime", lambda: (store, object()))
    monkeypatch.setattr(
        train,
        "_training_capability_state",
        lambda: {"enabled": True, "mode": "local-admin", "reason": None},
    )

    class _DormantThread:
        def __init__(self, **_kwargs):
            pass

        def start(self) -> None:
            pass

    monkeypatch.setattr(train.threading, "Thread", _DormantThread)

    owner_one = "11111111-1111-4111-8111-111111111111"
    owner_two = "22222222-2222-4222-8222-222222222222"
    request = models.TrainRequest(force_sync=False)
    started = asyncio.run(train.train_start(request, {"user_id": owner_one}))
    job_id = started["job_id"]
    assert started["status"] == "queued"
    assert store.get_job(job_id).owner_id == owner_one

    with pytest.raises(HTTPException) as same_owner:
        asyncio.run(train.train_start(request, {"user_id": owner_one}))
    assert same_owner.value.status_code == 409
    assert same_owner.value.detail["code"] == "train-job-active"
    assert same_owner.value.detail["job_id"] == job_id

    with pytest.raises(HTTPException) as other_owner:
        asyncio.run(train.train_start(request, {"user_id": owner_two}))
    assert other_owner.value.status_code == 409
    assert other_owner.value.detail["code"] == "train-job-active"
    assert "job_id" not in other_owner.value.detail

    own_status = asyncio.run(train.train_job_status(job_id, {"user_id": owner_one}))
    other_status = asyncio.run(train.train_job_status(job_id, {"user_id": owner_two}))
    assert own_status["status"] == "queued"
    assert other_status["status"] == "not_found"


def test_local_training_rejects_unauthenticated_development_admin_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "development")

    with pytest.raises(HTTPException) as captured:
        asyncio.run(
            train.require_local_training_admin(
                {
                    "user_id": "local-dev",
                    "role": "admin",
                    "subscription_tier": "premium",
                }
            )
        )

    assert captured.value.status_code == 403
    assert captured.value.detail == "admin identity is unavailable"


def test_train_job_owner_survives_sqlite_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "train_jobs.db"
    job_store.persist_train_job(
        "owned-job",
        {
            "status": "running",
            "progress": "training",
            "pct": 20,
            "result": None,
            "error": None,
            "owner_id": "admin-1",
        },
        db_path=db_path,
    )

    restored = job_store.load_train_job("owned-job", db_path)
    assert restored is not None
    assert restored["owner_id"] == "admin-1"


def test_artifact_publish_is_atomic_and_training_does_not_rewrite_catalog(tmp_path: Path) -> None:
    target = tmp_path / "model.joblib"
    train._atomic_joblib_dump({"model": "test-model"}, target)

    assert train.joblib.load(target) == {"model": "test-model"}
    assert list(tmp_path.iterdir()) == [target]
    assert "sync_with_model_features" not in inspect.getsource(train._do_train)


def test_optuna_timeout_remains_bound_to_the_durable_request() -> None:
    source = inspect.getsource(train._do_train)

    assert source.count("timeout=request.optuna_timeout") == 2
    assert "timeout=300" not in source


def test_interrupted_job_reconciliation_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(
        train,
        "mark_interrupted_train_jobs",
        lambda: calls.append(True) or 2,
    )

    assert train.reconcile_interrupted_train_jobs() == 2
    assert calls == [True]
    assert inspect.getsource(train).count("mark_interrupted_train_jobs()") == 1


def test_router_executes_local_job_through_fenced_coordinator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_root = (tmp_path / "repo").resolve()
    (source_root / "python-api").mkdir(parents=True)
    (source_root / "python-api" / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    source_hash = local_retrain.compute_source_tree_sha256(source_root)

    source_db = (tmp_path / "ultimate.db").resolve()
    with sqlite3.connect(source_db) as connection:
        connection.execute("CREATE TABLE sample(value INTEGER)")
        connection.execute("INSERT INTO sample VALUES (1)")
    snapshot = local_retrain.create_sqlite_snapshot(
        source_db,
        (tmp_path / "snapshots").resolve(),
    )

    store = local_retrain.LocalRetrainStore(tmp_path / "jobs.db")
    models_dir = (tmp_path / "models").resolve()
    models_dir.mkdir()
    active_binding = ("model_active", "c" * 64)
    gateway = local_retrain.LocalRetrainGateway(
        store,
        artifact_root=(models_dir / ".local-retrain" / "artifacts").resolve(),
        published_model_root=models_dir,
        source_root=source_root,
        candidate_commit_provider=lambda: "a" * 40,
        active_model_binding_provider=lambda: active_binding,
    )
    owner_id = "11111111-1111-4111-8111-111111111111"
    job_id = "22222222-2222-4222-8222-222222222222"
    request = models.TrainRequest(force_sync=False)
    preparing = store.create_job(
        job_id=job_id,
        owner_id=owner_id,
        request_payload=request.model_dump(),
        preparation_token="1" * 64,
        preparation_ttl_seconds=300,
    )
    contract = local_retrain.LocalTrainingContract.create(
        job_id=job_id,
        owner_id=owner_id,
        snapshot=snapshot,
        target="win",
        model_type="lightgbm",
        selected_features=("odds",),
        removed_features=(),
        candidate_commit_sha="a" * 40,
        source_tree_sha256=source_hash,
        active_model_id=active_binding[0],
        active_model_sha256=active_binding[1],
        training_parameters=train._local_training_parameters(request),
        future_fields=train.FUTURE_FIELDS,
    )
    queued = store.queue_job(
        job_id=job_id,
        expected_version=preparing.record_version,
        preparation_token="1" * 64,
        snapshot=snapshot,
        contract=contract,
    )
    assert queued.state == "queued"

    async def fake_train(request_value, _user, *, progress_cb, approved_execution):
        assert request_value.force_sync is False
        approved_execution.validate_request(
            target=request_value.target,
            model_type=request_value.model_type,
            force_sync=request_value.force_sync,
            test_size=request_value.test_size,
            cv_folds=request_value.cv_folds,
            use_optuna=request_value.use_optuna,
            training_date_from=request_value.training_date_from,
            training_date_to=request_value.training_date_to,
        )
        progress_cb("作成中", 60)
        model_id = "20250101_20250131_20260912_2000_12345678"
        artifact_path = approved_execution.prepare_artifact_directory() / (
            f"model_win_lightgbm_{model_id}.joblib"
        )
        artifact_path.write_bytes(b"local candidate")
        return models.TrainResponse(
            success=True,
            model_id=model_id,
            model_path=str(artifact_path),
            metrics={"auc": 0.75},
            data_count=1,
            race_count=1,
            feature_count=1,
            training_time=0.1,
            message="完了",
            feature_columns=["odds"],
        )

    monkeypatch.setattr(train, "_get_local_retrain_runtime", lambda: (store, gateway))
    monkeypatch.setattr(train, "_do_train", fake_train)

    asyncio.run(train._execute_queued_local_retrain(job_id))

    completed = store.get_job(job_id)
    assert completed.state == "completed"
    assert completed.result is not None
    published = Path(str(completed.result["model_path"]))
    assert published.parent == models_dir
    assert published.is_file()
    assert completed.artifact_path is not None
    assert published.samefile(completed.artifact_path)
    assert hashlib.sha256(published.read_bytes()).hexdigest() == completed.artifact_sha256


def test_preparation_binds_preflight_schema_not_active_model_schema(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_db = tmp_path / "source.db"
    with sqlite3.connect(source_db) as connection:
        connection.execute("CREATE TABLE sample(value INTEGER)")
        connection.execute("INSERT INTO sample VALUES (1)")
    snapshot = local_retrain.create_sqlite_snapshot(
        source_db,
        (tmp_path / "snapshots").resolve(),
    )
    store = local_retrain.LocalRetrainStore(tmp_path / "jobs.db")
    owner_id = "11111111-1111-4111-8111-111111111111"
    job_id = "22222222-2222-4222-8222-222222222222"
    token = "1" * 64
    request = models.TrainRequest(
        target="speed_deviation",
        force_sync=False,
        training_date_from="2025-01",
        training_date_to="2025-12",
    )
    store.create_job(
        job_id=job_id,
        owner_id=owner_id,
        request_payload=request.model_dump(),
        preparation_token=token,
        preparation_ttl_seconds=300,
    )
    active_features: list[tuple[str, ...]] = []
    observed_phases: list[str] = []
    executions: list[str] = []

    monkeypatch.setattr(train, "_get_local_retrain_runtime", lambda: (store, object()))
    monkeypatch.setattr(train, "_candidate_commit_sha", lambda: "a" * 40)
    monkeypatch.setattr(
        train,
        "compute_source_tree_sha256",
        lambda _root, heartbeat=None: "b" * 64,
    )
    monkeypatch.setattr(train, "_active_model_binding", lambda: ("model_active", "c" * 64))
    monkeypatch.setattr(
        train,
        "_active_model_features",
        lambda _binding: active_features.append(("legacy_only",)) or ("legacy_only",),
    )
    monkeypatch.setattr(
        train,
        "create_sqlite_snapshot",
        lambda *_args, **_kwargs: snapshot,
    )

    def fake_schema(_path: Path, **kwargs: object) -> tuple[str, ...]:
        assert _path == snapshot.path
        assert kwargs["target"] == "speed_deviation"
        assert kwargs["training_date_from"] == "2025-01"
        assert kwargs["training_date_to"] == "2025-12"
        observe = kwargs["observe_phase"]
        assert callable(observe)
        observe("schema-phase", 4)
        observed_phases.append("schema-phase")
        return ("odds", "generated_feature")

    async def fake_execute(queued_job_id: str) -> None:
        executions.append(queued_job_id)

    monkeypatch.setattr(train, "derive_local_feature_schema", fake_schema)
    monkeypatch.setattr(train, "_execute_queued_local_retrain", fake_execute)

    asyncio.run(train._prepare_and_execute_local_retrain(job_id, request, token))

    queued = store.get_job(job_id)
    assert active_features == [("legacy_only",)]
    assert observed_phases == ["schema-phase"]
    assert executions == [job_id]
    assert queued.state == "queued"
    assert queued.contract is not None
    assert queued.contract.selected_features == ("odds", "generated_feature")
    assert queued.contract.active_model_id == "model_active"
    assert queued.contract.active_model_sha256 == "c" * 64


def test_status_is_owner_bound_and_has_no_recovery_or_publication_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id = "11111111-1111-4111-8111-111111111111"
    other_owner_id = "22222222-2222-4222-8222-222222222222"
    job_id = "33333333-3333-4333-8333-333333333333"
    side_effect_calls: list[str] = []

    class _Record:
        def __init__(self, state: str) -> None:
            self.job_id = job_id
            self.state = state

        def to_status(self) -> dict[str, object]:
            return {
                "job_id": job_id,
                "status": "completed" if self.state == "completed" else "running",
                "progress": "完了" if self.state == "completed" else "成果物を登録中",
                "pct": 100 if self.state == "completed" else 99,
                "result": {"model_id": "candidate"} if self.state == "completed" else None,
                "error": None,
            }

    registered = _Record("artifact-registered")

    class _Store:
        @staticmethod
        def recover_expired_jobs() -> list[object]:
            side_effect_calls.append("recover")
            raise AssertionError("status must not recover jobs")

        @staticmethod
        def get_job_for_owner(requested_job_id: str, requested_owner_id: str) -> _Record:
            if requested_job_id != job_id or requested_owner_id != owner_id:
                raise local_retrain.LocalRetrainNotFound("local-job-not-found")
            return registered

    class _Gateway:
        @staticmethod
        def finalize_registered_result(*, job_id: str) -> _Record:
            side_effect_calls.append(f"finalize:{job_id}")
            raise AssertionError("status must not publish artifacts")

    monkeypatch.setattr(train, "_get_local_retrain_runtime", lambda: (_Store(), _Gateway()))
    monkeypatch.setattr(train, "load_train_job", lambda _job_id: None)

    own_status = asyncio.run(train.train_job_status(job_id, {"user_id": owner_id}))
    assert own_status["status"] == "running"
    assert side_effect_calls == []

    other_status = asyncio.run(
        train.train_job_status(job_id, {"user_id": other_owner_id})
    )
    assert other_status["status"] == "not_found"
    assert side_effect_calls == []


def test_fastapi_rejects_synchronous_local_training_before_job_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("MODEL_TRAINING_LOCAL_ENABLED", "true")
    monkeypatch.setattr(
        train,
        "_get_local_retrain_runtime",
        lambda: pytest.fail("ledger must not be opened"),
    )

    with pytest.raises(HTTPException) as captured:
        asyncio.run(
            train.train_start(
                models.TrainRequest(force_sync=True),
                {"user_id": "11111111-1111-4111-8111-111111111111"},
            )
        )

    assert captured.value.status_code == 409
    assert captured.value.detail["code"] == "local-training-sync-forbidden"


def test_start_and_status_fail_closed_when_local_ledger_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("MODEL_TRAINING_LOCAL_ENABLED", "true")
    monkeypatch.setattr(
        train,
        "_training_capability_state",
        lambda: {"enabled": True, "mode": "local-admin", "reason": None},
    )

    def unavailable_ledger() -> tuple[object, object]:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(train, "_get_local_retrain_runtime", unavailable_ledger)
    owner_id = "11111111-1111-4111-8111-111111111111"

    with pytest.raises(HTTPException) as start_error:
        asyncio.run(
            train.train_start(
                models.TrainRequest(force_sync=False),
                {"user_id": owner_id},
            )
        )
    assert start_error.value.status_code == 503
    assert start_error.value.detail == "training job state is unavailable"

    with pytest.raises(HTTPException) as status_error:
        asyncio.run(train.train_job_status(str(uuid.uuid4()), {"user_id": owner_id}))
    assert status_error.value.status_code == 503
    assert status_error.value.detail == "training job state is unavailable"


@pytest.mark.parametrize(
    "payload",
    [
        {"target": "unknown"},
        {"model_type": "logistic_regression"},
        {"test_size": 0.0},
        {"test_size": 0.9},
        {"cv_folds": 1},
        {"cv_folds": 11},
        {"training_date_from": "2026-13"},
        {"training_date_from": "2026-02", "training_date_to": "2026-01"},
    ],
)
def test_training_request_rejects_unsupported_or_unsafe_values(payload: dict) -> None:
    with pytest.raises(ValueError):
        models.TrainRequest(**payload)
