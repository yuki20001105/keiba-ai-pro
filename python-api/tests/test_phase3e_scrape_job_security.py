from __future__ import annotations

import asyncio
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, "python-api")

from deps.auth import require_admin  # type: ignore  # noqa: E402
from models import ScrapeRequest  # type: ignore  # noqa: E402
from routers import scrape as scrape_router  # type: ignore  # noqa: E402
from scraping import jobs  # type: ignore  # noqa: E402
from scraping import operational_saga_runtime as operational  # type: ignore  # noqa: E402


OWNER_A = "11111111-1111-4111-8111-111111111111"
OWNER_B = "22222222-2222-4222-8222-222222222222"
JOB_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
JOB_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def _job(owner: str, *, status: str = "queued") -> dict:
    return {
        "status": status,
        "progress": {"done": 0, "total": 1},
        "result": None,
        "error": None,
        "owner_user_id": owner,
        "request_hash": f"hash-{owner}",
    }


@pytest.fixture(autouse=True)
def isolated_jobs_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "scrape_jobs.db"
    monkeypatch.setattr(jobs, "_JOBS_DB_PATH", db_path)
    jobs._scrape_jobs.clear()
    jobs._init_jobs_db()
    runtime_config = operational.OperationalSagaConfig(
        mode=operational.OperationalSagaMode.LOCAL_SQLITE,
        environment="test",
        sqlite_path=tmp_path / "operational-saga.db",
        worker_enabled=True,
        remote_effects_enabled=True,
        execution_unlock_enabled=True,
    )
    runtime = operational.OperationalSagaRuntime(runtime_config)
    runtime.initialize()
    operational.set_operational_saga_runtime_for_tests(runtime)
    yield db_path
    jobs._scrape_jobs.clear()
    operational.set_operational_saga_runtime_for_tests(None)


def test_job_persistence_roundtrip_keeps_owner_and_request_hash_immutable() -> None:
    original = _job(OWNER_A)
    assert jobs._persist_job(JOB_A, original) is True
    assert jobs._load_job_from_db(JOB_A) == original

    changed = {**original, "status": "running", "owner_user_id": OWNER_B, "request_hash": "replacement"}
    assert jobs._persist_job(JOB_A, changed) is False
    loaded = jobs._load_job_from_db(JOB_A)
    assert loaded is not None
    assert loaded["status"] == "queued"
    assert loaded["owner_user_id"] == OWNER_A
    assert loaded["request_hash"] == original["request_hash"]

    assert jobs._persist_job(JOB_A, {**original, "status": "running"}) is True
    assert jobs._load_job_from_db(JOB_A)["status"] == "running"  # type: ignore[index]


def test_job_schema_migration_is_idempotent_and_persist_failure_is_visible(
    isolated_jobs_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs._init_jobs_db()
    with sqlite3.connect(str(isolated_jobs_db)) as conn:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(scrape_jobs)")}
    assert {"owner_user_id", "request_hash"}.issubset(columns)

    monkeypatch.setattr(jobs, "_JOBS_DB_PATH", isolated_jobs_db / "missing" / "jobs.db")
    assert jobs._persist_job(JOB_A, _job(OWNER_A)) is False


def test_job_store_read_failures_are_typed_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*_args, **_kwargs):
        raise sqlite3.OperationalError("job store unavailable")

    monkeypatch.setattr(jobs.sqlite3, "connect", unavailable)

    with pytest.raises(jobs.JobStoreUnavailable):
        jobs._load_job_from_db(JOB_A)
    with pytest.raises(jobs.JobStoreUnavailable):
        jobs.list_recent_jobs(limit=20, owner_user_id=OWNER_A)


def test_recent_jobs_are_owner_scoped_and_do_not_expose_binding_fields() -> None:
    assert jobs._persist_job(JOB_A, _job(OWNER_A)) is True
    assert jobs._persist_job(JOB_B, _job(OWNER_B)) is True
    legacy_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    assert jobs._persist_job(legacy_id, _job("")) is True

    history = jobs.list_recent_jobs(limit=20, owner_user_id=OWNER_A)
    assert [entry["job_id"] for entry in history] == [JOB_A]
    assert "owner_user_id" not in history[0]
    assert "request_hash" not in history[0]
    assert jobs.has_active_job(OWNER_A) is True
    assert jobs.has_active_job("33333333-3333-4333-8333-333333333333") is False


def test_malformed_durable_history_fails_closed(isolated_jobs_db: Path) -> None:
    assert jobs._persist_job(JOB_A, _job(OWNER_A)) is True
    with sqlite3.connect(str(isolated_jobs_db)) as conn:
        conn.execute("UPDATE scrape_jobs SET progress = ? WHERE job_id = ?", ("{", JOB_A))
        conn.commit()

    with pytest.raises(jobs.JobStoreUnavailable):
        jobs.list_recent_jobs(limit=20, owner_user_id=OWNER_A)


def test_start_uses_full_uuid_binds_owner_and_durably_enqueues_without_thread() -> None:
    request = ScrapeRequest(start_date="2026-01-01", end_date="2026-01-31", dry_run=True)
    response = asyncio.run(scrape_router.scrape_start(request, {"user_id": OWNER_A, "role": "admin"}))

    assert str(uuid.UUID(response["job_id"])) == response["job_id"]
    assert len(response["job_id"]) == 36
    assert response["duplicate"] is False
    stored = operational.get_operational_saga_runtime().get_job(response["job_id"], OWNER_A)
    assert stored is not None
    assert stored["owner_user_id"] == OWNER_A
    assert len(stored["request_hash"]) == 64


def test_start_fails_closed_when_operational_enqueue_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = operational.get_operational_saga_runtime()
    monkeypatch.setattr(
        runtime,
        "enqueue",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            operational.OperationalSagaUnavailable("store unavailable")
        ),
    )
    request = ScrapeRequest(start_date="2026-01-01", end_date="2026-01-31", dry_run=True)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(scrape_router.scrape_start(request, {"user_id": OWNER_A, "role": "admin"}))
    assert exc.value.status_code == 503
    assert jobs._scrape_jobs == {}


def test_start_rejects_another_active_operational_job_for_the_same_owner() -> None:
    request = ScrapeRequest(start_date="2026-01-01", end_date="2026-01-31", dry_run=True)
    first = asyncio.run(scrape_router.scrape_start(request, {"user_id": OWNER_A, "role": "admin"}))
    assert first["status"] == "queued"
    with pytest.raises(HTTPException) as exc:
        asyncio.run(scrape_router.scrape_start(request, {"user_id": OWNER_A, "role": "admin"}))

    assert exc.value.status_code == 409


def test_start_fails_closed_when_active_job_state_cannot_be_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = operational.get_operational_saga_runtime()
    monkeypatch.setattr(
        runtime,
        "enqueue",
        lambda *_args, **_kwargs: operational.Mutation(
            operational.MutationCode.UNAVAILABLE, "active-state-unavailable"
        ),
    )
    request = ScrapeRequest(start_date="2026-01-01", end_date="2026-01-31", dry_run=True)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(scrape_router.scrape_start(request, {"user_id": OWNER_A, "role": "admin"}))

    assert exc.value.status_code == 503


def test_worker_stops_before_side_effects_when_running_state_is_not_durable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs._scrape_jobs[JOB_A] = _job(OWNER_A)
    side_effects: list[str] = []

    monkeypatch.setattr(jobs, "_persist_job", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(jobs, "_init_sqlite_db", lambda *_args, **_kwargs: side_effects.append("db-init"))

    asyncio.run(
        jobs._run_scrape_job(
            JOB_A,
            "2026-07-18",
            "2026-07-18",
            dry_run=True,
        )
    )

    assert side_effects == []
    assert jobs._scrape_jobs[JOB_A]["status"] == "error"
    assert jobs._scrape_jobs[JOB_A]["result"] is None
    assert jobs._scrape_jobs[JOB_A]["_store_unavailable"] is True
    with pytest.raises(jobs.JobStoreUnavailable):
        jobs.get_job(JOB_A, owner_user_id=OWNER_A)


def test_worker_converts_completion_persistence_failure_to_durable_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs._scrape_jobs[JOB_A] = _job(OWNER_A)
    real_persist = jobs._persist_job
    persisted_statuses: list[str] = []

    def persist(job_id: str, job: dict) -> bool:
        status = str(job.get("status"))
        persisted_statuses.append(status)
        if status == "completed":
            return False
        return real_persist(job_id, job)

    monkeypatch.setattr(jobs, "_persist_job", persist)
    monkeypatch.setattr(jobs, "_init_sqlite_db", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(jobs, "write_fetch_summary", lambda *_args, **_kwargs: tmp_path / "summary.json")

    asyncio.run(
        jobs._run_scrape_job(
            JOB_A,
            "2099-01-01",
            "2099-01-01",
            dry_run=True,
        )
    )

    assert persisted_statuses == ["running", "completed", "error"]
    assert jobs._scrape_jobs[JOB_A]["status"] == "error"
    assert jobs._scrape_jobs[JOB_A]["result"] is None
    assert jobs._scrape_jobs[JOB_A].get("_store_unavailable") is None
    stored = jobs._load_job_from_db(JOB_A)
    assert stored is not None
    assert stored["status"] == "error"
    assert stored["result"] is None


def test_status_requires_full_uuid_and_hides_cross_owner_jobs() -> None:
    request = ScrapeRequest(
        start_date="2026-01-01", end_date="2026-01-31", dry_run=True, job_id=JOB_A
    )
    asyncio.run(scrape_router.scrape_start(request, {"user_id": OWNER_A, "role": "admin"}))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(scrape_router.scrape_status("aaaaaaaa", {"user_id": OWNER_A, "role": "admin"}))
    assert exc.value.status_code == 400

    missing = asyncio.run(scrape_router.scrape_status(JOB_A, {"user_id": OWNER_B, "role": "admin"}))
    absent = asyncio.run(scrape_router.scrape_status(JOB_B, {"user_id": OWNER_B, "role": "admin"}))
    assert missing["status"] == absent["status"] == "not_found"
    assert set(missing) == set(absent)

    visible = asyncio.run(scrape_router.scrape_status(JOB_A, {"user_id": OWNER_A, "role": "admin"}))
    assert visible["status"] == "queued"
    assert "owner_user_id" not in visible
    assert "request_hash" not in visible


def test_status_and_history_return_503_when_durable_state_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unavailable = operational.OperationalSagaUnavailable("job store unavailable")
    runtime = operational.get_operational_saga_runtime()
    monkeypatch.setattr(runtime, "get_job", lambda *_args, **_kwargs: (_ for _ in ()).throw(unavailable))

    with pytest.raises(HTTPException) as status_exc:
        asyncio.run(scrape_router.scrape_status(JOB_A, {"user_id": OWNER_A, "role": "admin"}))
    assert status_exc.value.status_code == 503

    monkeypatch.setattr(runtime, "list_jobs", lambda *_args, **_kwargs: (_ for _ in ()).throw(unavailable))
    with pytest.raises(HTTPException) as history_exc:
        asyncio.run(scrape_router.scrape_history(limit=20, admin_user={"user_id": OWNER_A, "role": "admin"}))
    assert history_exc.value.status_code == 503


def test_history_is_owner_scoped_and_routes_require_admin() -> None:
    for job_id, owner in ((JOB_A, OWNER_A), (JOB_B, OWNER_B)):
        request = ScrapeRequest(
            start_date="2026-01-01", end_date="2026-01-31", dry_run=True, job_id=job_id
        )
        asyncio.run(scrape_router.scrape_start(request, {"user_id": owner, "role": "admin"}))

    response = asyncio.run(scrape_router.scrape_history(limit=20, admin_user={"user_id": OWNER_A, "role": "admin"}))
    assert response["count"] == 1
    assert response["jobs"][0]["job_id"] == JOB_A
    assert "owner_user_id" not in response["jobs"][0]
    assert "request_hash" not in response["jobs"][0]

    guarded_paths = {"/api/scrape/status/{job_id}", "/api/scrape/history"}
    for route in scrape_router.router.routes:
        if getattr(route, "path", None) in guarded_paths:
            dependency_calls = {dependency.call for dependency in route.dependant.dependencies}
            assert require_admin in dependency_calls
