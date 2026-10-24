"""Offline scope binding; existing all-venue authorization remains unchanged."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from models import ScrapeRequest
from routers import scrape as routes
from scraping import jobs
from scraping.operational_saga_runtime import (
    EnqueueRequest, MutationCode, OperationalClaim, OperationalSagaConfig,
    OperationalSagaError, OperationalSagaMode, OperationalSagaRuntime,
    ScrapeJobEffectExecutor,
)


OWNER = "11111111-1111-4111-8111-111111111111"
JOB = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OPERATION = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
PAYLOAD = {
    "start_date": "20260701", "end_date": "20260731",
    "force_rescrape": False, "dry_run": True,
}


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config = OperationalSagaConfig(
        mode=OperationalSagaMode.LOCAL_SQLITE, environment="test",
        sqlite_path=tmp_path / "scope-saga.db", worker_enabled=True,
        remote_effects_enabled=True, execution_unlock_enabled=True,
    )
    value = OperationalSagaRuntime(config)
    value.initialize()
    monkeypatch.setattr(routes, "get_operational_saga_runtime", lambda: value)
    monkeypatch.setattr(jobs, "_JOBS_DB_PATH", tmp_path / "scope-children.db")
    original = dict(jobs._scrape_jobs)
    jobs._scrape_jobs.clear()
    yield value
    jobs._scrape_jobs.clear()
    jobs._scrape_jobs.update(original)


@pytest.mark.parametrize("scope", ["all", "jra"])
def test_request_accepts_exact_scopes(scope):
    assert ScrapeRequest(**PAYLOAD, race_scope=scope).race_scope == scope


@pytest.mark.parametrize("scope", [None, "", "nar", "JRA", " jra", 1, True, [], {}])
def test_request_rejects_unknown_or_coerced_scope(scope):
    with pytest.raises(ValidationError):
        ScrapeRequest(**PAYLOAD, race_scope=scope)


def test_omitted_and_explicit_all_keep_legacy_hash_and_retry(runtime):
    request = ScrapeRequest(**PAYLOAD, job_id=JOB, operation_id=OPERATION)
    asyncio.run(routes.scrape_start(request, {"user_id": OWNER}))
    row = runtime.get_job(JOB, OWNER)
    legacy_hash = hashlib.sha256(json.dumps(
        PAYLOAD, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    assert row["request_payload"] == PAYLOAD
    assert row["request_hash"] == legacy_hash
    retry = ScrapeRequest(**PAYLOAD, race_scope="all", job_id=JOB, operation_id=OPERATION)
    assert asyncio.run(routes.scrape_start(retry, {"user_id": OWNER}))["duplicate"] is True


def test_jra_scope_bound_to_payload_hash_and_effect_key(runtime):
    request = ScrapeRequest(**PAYLOAD, race_scope="jra", job_id=JOB, operation_id=OPERATION)
    first = asyncio.run(routes.scrape_start(request, {"user_id": OWNER}))
    assert first["duplicate"] is False
    row = runtime.get_job(JOB, OWNER)
    expected = {**PAYLOAD, "race_scope": "jra"}
    assert row["request_payload"] == expected
    expected_hash = hashlib.sha256(json.dumps(
        expected, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    assert row["request_hash"] == expected_hash
    with sqlite3.connect(runtime.config.sqlite_path) as connection:
        effect_key = connection.execute(
            "SELECT idempotency_key FROM operational_scrape_jobs WHERE job_id=?", (JOB,),
        ).fetchone()[0]
    assert effect_key == EnqueueRequest(
        JOB, OPERATION, OWNER, expected_hash, expected,
    ).idempotency_key
    assert asyncio.run(routes.scrape_start(request, {"user_id": OWNER}))["duplicate"] is True
    changed = request.model_copy(update={"race_scope": "all"})
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.scrape_start(changed, {"user_id": OWNER}))
    assert error.value.status_code == 409


def test_jra_deployed_request_rejected_before_enqueue(monkeypatch):
    config = OperationalSagaConfig(
        mode=OperationalSagaMode.SUPABASE, environment="staging",
        worker_enabled=True, remote_effects_enabled=True, execution_unlock_enabled=True,
    )
    value = OperationalSagaRuntime(config, store=object())
    monkeypatch.setattr(routes, "get_operational_saga_runtime", lambda: value)
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.scrape_start(ScrapeRequest(**PAYLOAD, race_scope="jra"), {"user_id": OWNER}))
    assert error.value.status_code == 403
    request = EnqueueRequest(JOB, OPERATION, OWNER, "f" * 64, {**PAYLOAD, "race_scope": "jra"})
    assert value.enqueue(request).code is MutationCode.UNAVAILABLE


def test_jra_batch_scope_included_in_bound_payload(runtime, monkeypatch):
    checked = []

    async def current_admin(user):
        checked.append(user["user_id"])
        return user

    monkeypatch.setattr(routes, "require_current_admin", current_admin)
    request = ScrapeRequest(
        start_date="20160101", end_date="20260930", server_batch=True,
        race_scope="jra", dry_run=True, job_id=JOB, operation_id=OPERATION,
    )
    asyncio.run(routes.scrape_start(request, {"user_id": OWNER}))
    row = runtime.get_job(JOB, OWNER)
    assert row["request_payload"]["race_scope"] == "jra"
    assert row["request_payload"]["server_batch"] is True
    assert checked == [OWNER]


def test_constructed_invalid_scope_cannot_silently_become_all(runtime):
    request = ScrapeRequest(**PAYLOAD).model_copy(update={"race_scope": []})
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.scrape_start(request, {"user_id": OWNER}))
    assert error.value.status_code == 400
    assert runtime.list_jobs(OWNER, 20) == []


@pytest.mark.parametrize("scope", ["all", "jra"])
def test_worker_passes_only_explicit_jra_kwarg(runtime, monkeypatch, scope):
    calls = []

    async def runner(job_id, start, end, force, dry_run, **kwargs):
        calls.append(kwargs)
        if scope == "jra":
            assert jobs._scrape_jobs[job_id]["request"] == {**PAYLOAD, "race_scope": "jra"}
        jobs._scrape_jobs[job_id].update({"status": "completed", "result": {"success": True}})

    monkeypatch.setattr(jobs, "_run_scrape_job", runner)
    request = ScrapeRequest(**PAYLOAD, race_scope=scope, job_id=JOB, operation_id=OPERATION)
    asyncio.run(routes.scrape_start(request, {"user_id": OWNER}))
    assert asyncio.run(runtime.run_once()).code is MutationCode.APPLIED
    assert len(calls) == 1
    assert callable(calls[0]["cancel_requested"])
    assert calls[0].get("race_scope", "all") == scope
    assert ("race_scope" in calls[0]) is (scope == "jra")


@pytest.mark.parametrize("scope", [None, "nar", [], {}])
def test_worker_rejects_invalid_scope_before_runner(runtime, monkeypatch, scope):
    async def forbidden(*args, **kwargs):
        raise AssertionError("invalid scope reached worker")

    monkeypatch.setattr(jobs, "_run_scrape_job", forbidden)
    claim = OperationalClaim(
        job_id=JOB, operation_id=OPERATION, owner_user_id=OWNER, request_hash="f" * 64,
        request_payload={**PAYLOAD, "race_scope": scope}, idempotency_key="scope-test",
        worker_owner="test-worker", fencing_token=1, lease_expires_at_epoch=2_000_000_000,
        attempt_count=1,
    )
    executor = ScrapeJobEffectExecutor(allow_unfenced_local_writes=True)
    with pytest.raises(OperationalSagaError, match="scrape-payload-scope-invalid"):
        asyncio.run(executor._execute_owned(claim))


def test_jra_list_skips_legacy_proxy_and_filters_final_return(monkeypatch):
    calls = []

    async def fake(date, **kwargs):
        calls.append((date, kwargs))
        return ["202605010101", "202644010101", "202505010101", "202605010113", 1], "test-source"

    def forbidden_session(*args, **kwargs):
        raise AssertionError("JRA request reached old all-venue proxy")

    monkeypatch.setitem(sys.modules, "scraping.jra_listing", SimpleNamespace(fetch_jra_race_ids=fake))
    monkeypatch.setattr(routes.aiohttp, "ClientSession", forbidden_session)
    result = asyncio.run(routes.netkeiba_race_list("2026-01-01", {"user_id": OWNER}, race_scope="jra"))
    assert calls == [("20260101", {})]
    assert result["raceIds"] == ["202605010101"]
    assert result["count"] == 1
    assert result["race_scope"] == "jra"


@pytest.mark.parametrize("returned_id,expected_saves", [("202605010101", 1), ("202644010101", 0)])
def test_legacy_jra_sync_filters_before_detail_and_checks_return_identity(monkeypatch, returned_id, expected_saves):
    details, saves = [], []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    async def fetch_list(*args, **kwargs):
        return SimpleNamespace(status=200), '<a href="/race/202605010101/">JRA</a><a href="/race/202644010101/">NAR</a>'

    async def fetch_detail(session, race_id, **kwargs):
        details.append(race_id)
        return {"race_info": {"race_id": returned_id, "race_name": "fixture"}, "horses": [{}]}

    async def no_sleep(*args):
        return None

    monkeypatch.setattr(routes, "_require_legacy_scrape_write_allowed", lambda: None)
    monkeypatch.setattr(routes.aiohttp, "ClientSession", lambda **kwargs: Session())
    monkeypatch.setattr(routes, "fetch_text", fetch_list)
    monkeypatch.setattr(routes, "scrape_race_full", fetch_detail)
    monkeypatch.setattr(routes, "_save_race_to_ultimate_db", lambda *args, **kwargs: saves.append(args[0]))
    monkeypatch.setattr(routes.asyncio, "sleep", no_sleep)
    result = asyncio.run(routes.scrape_data(ScrapeRequest(
        start_date="20260105", end_date="20260105", race_scope="jra",
    ), {"user_id": OWNER}))
    assert details == ["202605010101"]
    assert len(saves) == expected_saves
    assert result.races_collected == expected_saves


@pytest.mark.parametrize("database_ok,expected_status", [(True, "healthy"), (False, "degraded")])
def test_scrape_health_advertises_scope_capability_not_an_auth_unlock(runtime, monkeypatch, database_ok, expected_status):
    monkeypatch.setattr(routes, "get_scrape_runtime_health", lambda: {
        "database_ok": database_ok, "stale_job_ids": [],
    })
    monkeypatch.setattr(routes, "_purge_old_jobs", lambda values: None)
    monkeypatch.delenv("SCRAPE_RACE_CONCURRENCY", raising=False)
    result = asyncio.run(routes.scrape_health())
    assert result["status"] == expected_status
    assert result["scrape_capabilities"] == {
        "race_scopes": ["all", "jra"], "jra_month_reuse": True,
        "race_task_concurrency": 3, "max_concurrent_http_requests": 1,
        "safety_policy_version": "normal-acquisition-safe-v1",
        "minimum_provider_interval_seconds": 3,
        "halt_http400": True,
        "preserve_saved_values": True,
    }
    assert not result["scrape_capabilities"].get("execution_unlock_enabled", False)
