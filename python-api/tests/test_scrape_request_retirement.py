"""Offline regression for safe replacement of an absent persisted UI intent."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
import httpx
from pydantic import ValidationError

from models import ScrapeRequest
from routers import scrape as routes
from scraping import jobs as legacy_jobs
from scraping.operational_saga_runtime import (
    EnqueueRequest, MutationCode, OperationalSagaConfig, OperationalSagaMode,
    OperationalSagaRuntime, OperationalSagaUnavailable, SQLiteOperationalSagaStore,
    RETIRED_UNACCEPTED_REQUEST,
)

OWNER = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
JOB = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OP = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
OTHER_JOB = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
OTHER_OP = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
PAYLOAD = {
    "start_date": "20160101", "end_date": "20260930",
    "force_rescrape": False, "dry_run": False, "server_batch": True,
}
BODY = {key: value for key, value in PAYLOAD.items() if key != "dry_run"} | {"operation_id": OP}


def bound(*, payload=None, **changes):
    data = PAYLOAD.copy() if payload is None else payload
    checksum = hashlib.sha256(json.dumps(
        data, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    values = dict(job_id=JOB, operation_id=OP, owner_user_id=OWNER,
                  request_hash=checksum, request_payload=data)
    return EnqueueRequest(**(values | changes))


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config = OperationalSagaConfig(
        mode=OperationalSagaMode.LOCAL_SQLITE, environment="test",
        sqlite_path=tmp_path / "retirement.sqlite3", worker_enabled=True,
        remote_effects_enabled=True, execution_unlock_enabled=True,
    )
    value = OperationalSagaRuntime(config)
    value.initialize()
    monkeypatch.setattr(routes, "APP_ENV", "test")
    monkeypatch.setattr(routes, "get_operational_saga_runtime", lambda: value)
    monkeypatch.setattr(legacy_jobs, "_JOBS_DB_PATH", tmp_path / "retirement-legacy.sqlite3")
    monkeypatch.setattr(legacy_jobs, "_scrape_jobs", {})
    monkeypatch.setattr(routes, "_scrape_jobs", legacy_jobs._scrape_jobs)
    legacy_jobs._init_jobs_db()

    async def current_admin(user):
        assert user.get("user_id") == OWNER
        return user

    monkeypatch.setattr(routes, "require_current_admin", current_admin)
    return value


def db_counts(runtime):
    with sqlite3.connect(runtime.config.sqlite_path) as connection:
        return tuple(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                     for table in ("operational_scrape_jobs", "operational_scrape_outbox", "operational_scrape_batch_months"))


def test_absent_id_is_terminal_and_durable_without_outbox_or_worker(runtime):
    result = runtime.retire_unaccepted(bound())
    assert result.code is MutationCode.APPLIED
    assert result.job["status"] == "cancelled"
    assert result.job["retired"] is True
    assert result.job["error"] == RETIRED_UNACCEPTED_REQUEST
    assert result.job["request_payload"] == PAYLOAD
    assert db_counts(runtime) == (1, 0, 0)
    assert asyncio.run(runtime.run_once()).code is MutationCode.NOT_FOUND
    reopened = SQLiteOperationalSagaStore(runtime.config)
    reopened.initialize()
    assert reopened.get_job(JOB, OWNER)["retired"] is True
    assert reopened.get_job(JOB, OTHER) is None
    assert reopened.enqueue(bound(), 200).code is MutationCode.DUPLICATE
    assert reopened.get_job(JOB, OWNER)["status"] == "cancelled"
    assert db_counts(runtime) == (1, 0, 0)


def test_retirement_duplicate_is_idempotent(runtime):
    first = runtime.retire_unaccepted(bound())
    second = runtime.retire_unaccepted(bound())
    assert first.code is MutationCode.APPLIED
    assert second.code is MutationCode.DUPLICATE
    assert first.job == second.job
    assert db_counts(runtime) == (1, 0, 0)


def test_uuid_case_cannot_bypass_tombstone_or_original_binding(runtime):
    runtime.retire_unaccepted(bound())
    uppercase = bound(job_id=JOB.upper(), operation_id=OP.upper())
    assert uppercase.job_id == JOB and uppercase.operation_id == OP
    result = runtime.enqueue(uppercase)
    assert result.code is MutationCode.DUPLICATE and result.job["retired"]
    assert runtime.retire_unaccepted(uppercase).code is MutationCode.DUPLICATE
    late = asyncio.run(routes.scrape_start(
        ScrapeRequest(**PAYLOAD, job_id=JOB.upper(), operation_id=OP.upper()), {"user_id": OWNER},
    ))
    assert late["job_id"] == JOB and late["operation_id"] == OP
    assert late["status"] == "cancelled"
    assert db_counts(runtime) == (1, 0, 0)


def test_legacy_uppercase_accepted_id_is_not_misclassified_absent(runtime):
    runtime.enqueue(bound())
    with sqlite3.connect(runtime.config.sqlite_path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("UPDATE operational_scrape_jobs SET job_id=?,operation_id=? WHERE job_id=?",
                           (JOB.upper(), OP.upper(), JOB))
        connection.execute("UPDATE operational_scrape_outbox SET job_id=? WHERE job_id=?", (JOB.upper(), JOB))
    assert runtime.retire_unaccepted(bound()).code is MutationCode.CONFLICT
    assert runtime.enqueue(bound()).code is MutationCode.CONFLICT
    assert db_counts(runtime) == (1, 1, 0)


def test_legacy_uppercase_operation_id_cannot_be_reused(runtime):
    runtime.enqueue(bound(job_id=OTHER_JOB, operation_id=OTHER_OP, owner_user_id=OTHER))
    with sqlite3.connect(runtime.config.sqlite_path) as connection:
        connection.execute("UPDATE operational_scrape_jobs SET operation_id=? WHERE job_id=?", (OP.upper(), OTHER_JOB))
    assert runtime.retire_unaccepted(bound()).reason == "operation-id-binding-conflict"
    assert runtime.enqueue(bound()).reason == "operation-id-binding-conflict"
    assert db_counts(runtime) == (1, 1, 0)


@pytest.mark.parametrize("changes", [
    {"owner_user_id": OTHER}, {"operation_id": OTHER_OP},
    {"request_payload": PAYLOAD | {"race_scope": "jra"}},
    {"request_payload": PAYLOAD | {"force_rescrape": True}},
    {"request_payload": PAYLOAD | {"end_date": "20260831"}},
])
def test_retired_id_cannot_be_rebound_by_retirement_or_late_start(runtime, changes):
    runtime.retire_unaccepted(bound())
    changed_payload = changes.get("request_payload", PAYLOAD)
    request = bound(payload=changed_payload, **{k: v for k, v in changes.items() if k != "request_payload"})
    assert runtime.retire_unaccepted(request).code is MutationCode.CONFLICT
    assert runtime.enqueue(request).code is MutationCode.CONFLICT
    assert db_counts(runtime) == (1, 0, 0)


@pytest.mark.parametrize("status", ["queued", "running", "completed", "error"])
def test_existing_accepted_job_is_never_retired_or_cancelled(runtime, status):
    runtime.enqueue(bound())
    with sqlite3.connect(runtime.config.sqlite_path) as connection:
        connection.execute("UPDATE operational_scrape_jobs SET status=? WHERE job_id=?", (status, JOB))
    before = runtime.get_job(JOB, OWNER)
    result = runtime.retire_unaccepted(bound())
    assert result.code is MutationCode.CONFLICT
    assert result.reason == "request-already-accepted"
    assert runtime.get_job(JOB, OWNER) == before
    assert db_counts(runtime) == (1, 1, 0)


def test_other_active_job_blocks_retirement_without_tombstone(runtime):
    runtime.enqueue(bound(job_id=OTHER_JOB, operation_id=OTHER_OP))
    result = runtime.retire_unaccepted(bound())
    assert result.code is MutationCode.CONFLICT
    assert result.reason == "owner-active-job"
    assert runtime.get_job(JOB, OWNER) is None
    assert db_counts(runtime) == (1, 1, 0)


def test_operation_collision_blocks_retirement(runtime):
    runtime.enqueue(bound(job_id=OTHER_JOB, owner_user_id=OTHER))
    assert runtime.retire_unaccepted(bound()).reason == "operation-id-binding-conflict"
    assert runtime.get_job(JOB, OWNER) is None


def test_enqueue_retire_race_has_one_winner_and_no_late_work(runtime):
    store = runtime._store
    assert isinstance(store, SQLiteOperationalSagaStore)
    barrier = threading.Barrier(2)

    def start():
        barrier.wait()
        return store.enqueue(bound(), 100)

    def retire():
        barrier.wait()
        return store.retire_unaccepted(bound(), 100)

    with ThreadPoolExecutor(max_workers=2) as pool:
        started, retired = [future.result() for future in (pool.submit(start), pool.submit(retire))]
    assert [started.code, retired.code].count(MutationCode.APPLIED) == 1
    state = runtime.get_job(JOB, OWNER)
    if state["retired"]:
        assert started.code is MutationCode.DUPLICATE
        assert db_counts(runtime) == (1, 0, 0)
        assert store.claim_next("worker", 200, 30).code is MutationCode.NOT_FOUND
    else:
        assert retired.code is MutationCode.CONFLICT
        assert db_counts(runtime) == (1, 1, 0)


@pytest.mark.parametrize("payload", [
    PAYLOAD | {"dry_run": True}, PAYLOAD | {"server_batch": False},
    PAYLOAD | {"force_rescrape": 0}, PAYLOAD | {"race_scope": "all"},
    PAYLOAD | {"race_scope": "nar"}, PAYLOAD | {"extra": "unbound"},
    PAYLOAD | {"start_date": "20260132"}, PAYLOAD | {"end_date": "20150101"},
])
def test_invalid_or_unbound_retirement_payload_is_rejected(runtime, payload):
    with pytest.raises(ValueError):
        runtime.retire_unaccepted(bound(payload=payload))
    assert db_counts(runtime) == (0, 0, 0)


def test_payload_hash_cannot_be_forged(runtime):
    with pytest.raises(ValueError):
        runtime.retire_unaccepted(bound(request_hash="f" * 64))
    assert db_counts(runtime) == (0, 0, 0)


@pytest.mark.parametrize("mode", ["disabled", "deployed"])
def test_retirement_never_uses_disabled_or_deployed_store(mode):
    if mode == "disabled":
        value = OperationalSagaRuntime(OperationalSagaConfig())
    else:
        config = OperationalSagaConfig(
            mode=OperationalSagaMode.SUPABASE, environment="production",
            worker_enabled=True, remote_effects_enabled=True, execution_unlock_enabled=True,
        )
        value = OperationalSagaRuntime(config, store=object())
    assert value.retire_unaccepted(bound()).code is MutationCode.UNAVAILABLE


@pytest.mark.parametrize("scope", [None, "all", "jra"])
def test_http_retirement_receipt_status_and_late_start_retain_original_binding(runtime, scope):
    body = BODY.copy()
    if scope is not None:
        body["race_scope"] = scope
    result = asyncio.run(routes.scrape_retire_unaccepted(
        JOB, routes.RetireUnacceptedScrapeRequest(**body), {"user_id": OWNER},
    ))
    receipt = json.loads(result.body)
    expected = PAYLOAD | ({"race_scope": "jra"} if scope == "jra" else {})
    assert receipt["request_payload"] == expected
    assert receipt["job_id"] == JOB and receipt["operation_id"] == OP
    assert receipt["retired"] is receipt["replacement_allowed"] is True
    status = asyncio.run(routes.scrape_status(JOB, {"user_id": OWNER}))
    assert status["retired"] is status["retirement_supported"] is True
    assert status["operation_id"] == OP and status["request_payload"] == expected
    late = asyncio.run(routes.scrape_start(
        ScrapeRequest(**expected, job_id=JOB, operation_id=OP), {"user_id": OWNER},
    ))
    assert late["status"] == "cancelled" and late["duplicate"] and late["retired"]
    assert db_counts(runtime) == (1, 0, 0)


@pytest.mark.parametrize("field,value", [
    ("server_batch", 1), ("server_batch", "true"), ("server_batch", False),
    ("force_rescrape", 0), ("race_scope", "nar"), ("dry_run", True),
])
def test_http_body_rejects_coercion_and_extra_fields(field, value):
    with pytest.raises(ValidationError):
        routes.RetireUnacceptedScrapeRequest(**(BODY | {field: value}))


def test_http_not_found_capability_and_no_automatic_mutation(runtime):
    status = asyncio.run(routes.scrape_status(JOB, {"user_id": OWNER}))
    assert status["status"] == "not_found" and status["retirement_supported"]
    assert db_counts(runtime) == (0, 0, 0)


@pytest.mark.parametrize("setting,status_code", [("disabled", 503), ("deployed", 403), ("env", 403)])
def test_http_disabled_deployed_and_mismatched_app_environment_fail_closed(runtime, monkeypatch, setting, status_code):
    if setting == "env":
        monkeypatch.setattr(routes, "APP_ENV", "production")
        assert asyncio.run(routes.scrape_status(JOB, {"user_id": OWNER}))["retirement_supported"] is False
    else:
        config = OperationalSagaConfig() if setting == "disabled" else OperationalSagaConfig(
            mode=OperationalSagaMode.SUPABASE, environment="staging", worker_enabled=True,
            remote_effects_enabled=True, execution_unlock_enabled=True,
        )
        other = OperationalSagaRuntime(config, store=None if setting == "disabled" else object())
        monkeypatch.setattr(routes, "get_operational_saga_runtime", lambda: other)
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.scrape_retire_unaccepted(JOB, routes.RetireUnacceptedScrapeRequest(**BODY), {"user_id": OWNER}))
    assert error.value.status_code == status_code
    assert db_counts(runtime) == (0, 0, 0)


def test_http_current_admin_failure_or_storage_failure_keeps_intent_unretired(runtime, monkeypatch):
    async def revoked(_user):
        raise HTTPException(status_code=403, detail="revoked")

    original = routes.require_current_admin
    monkeypatch.setattr(routes, "require_current_admin", revoked)
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.scrape_retire_unaccepted(JOB, routes.RetireUnacceptedScrapeRequest(**BODY), {"user_id": OWNER}))
    assert error.value.status_code == 403 and db_counts(runtime) == (0, 0, 0)
    monkeypatch.setattr(routes, "require_current_admin", original)

    def unavailable(_request):
        raise OperationalSagaUnavailable("unavailable")

    monkeypatch.setattr(runtime, "retire_unaccepted", unavailable)
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.scrape_retire_unaccepted(JOB, routes.RetireUnacceptedScrapeRequest(**BODY), {"user_id": OWNER}))
    assert error.value.status_code == 503 and db_counts(runtime) == (0, 0, 0)


@pytest.mark.parametrize("status", ["queued", "running", "recovering", "waiting_resources", "paused_resource", "unknown", "completed", "error", "cancelled"])
@pytest.mark.parametrize("owner", [OWNER, OTHER])
def test_legacy_record_for_same_id_blocks_retirement_even_when_saga_absent(runtime, status, owner):
    assert legacy_jobs._persist_job(JOB, {"status": status, "owner_user_id": owner, "request_hash": "f" * 64})
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.scrape_retire_unaccepted(JOB, routes.RetireUnacceptedScrapeRequest(**BODY), {"user_id": OWNER}))
    assert error.value.status_code == 409
    assert error.value.detail == "legacy-request-recorded"
    assert db_counts(runtime) == (0, 0, 0)


@pytest.mark.parametrize("status", ["queued", "running", "recovering", "waiting_resources", "paused_resource", "unknown"])
def test_other_unresolved_owner_legacy_job_blocks_retirement(runtime, status):
    assert legacy_jobs._persist_job(OTHER_JOB, {"status": status, "owner_user_id": OWNER, "request_hash": "f" * 64})
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.scrape_retire_unaccepted(JOB, routes.RetireUnacceptedScrapeRequest(**BODY), {"user_id": OWNER}))
    assert error.value.status_code == 409
    assert error.value.detail == "owner-legacy-job-unresolved"
    assert db_counts(runtime) == (0, 0, 0)


def test_missing_legacy_ledger_never_creates_empty_db_and_preserves_intent(runtime, monkeypatch, tmp_path):
    unavailable_path = tmp_path / "missing-legacy.sqlite3"
    monkeypatch.setattr(legacy_jobs, "_JOBS_DB_PATH", unavailable_path)
    # The compatibility lookup is normally also fail closed, but prove the
    # independent read-only guard cannot create an empty missing ledger.
    monkeypatch.setattr(routes, "get_legacy_scrape_job", lambda *args, **kwargs: None)
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.scrape_retire_unaccepted(JOB, routes.RetireUnacceptedScrapeRequest(**BODY), {"user_id": OWNER}))
    assert error.value.status_code == 503
    assert not unavailable_path.exists()
    assert db_counts(runtime) == (0, 0, 0)


def test_unavailable_memory_worker_blocks_retirement(runtime):
    legacy_jobs._scrape_jobs[OTHER_JOB] = {"owner_user_id": OWNER, "status": "running", "_store_unavailable": True}
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.scrape_retire_unaccepted(JOB, routes.RetireUnacceptedScrapeRequest(**BODY), {"user_id": OWNER}))
    assert error.value.status_code == 503
    assert db_counts(runtime) == (0, 0, 0)


def test_http_endpoint_requires_authentication_even_for_absent_id(runtime):
    app = FastAPI()
    app.include_router(routes.router)

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local.test") as client:
            return await client.post(f"/api/scrape/retire/{JOB}", json=BODY)

    response = asyncio.run(request())
    assert response.status_code in {401, 403}
    assert db_counts(runtime) == (0, 0, 0)
