from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
import time

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from models import ScrapeRequest
from routers import scrape as routes
from scraping import jobs
from scraping.operational_saga_runtime import (
    EnqueueRequest, MutationCode, OperationalSagaConfig, OperationalSagaConflict,
    OperationalSagaMode, OperationalSagaRuntime, OperationalSagaUnavailable,
    ScrapeJobEffectExecutor,
)
from scraping.scrape_request_contract import build_scrape_months
from scraping.server_batch import BatchCheckpointStore, month_job_id

OWNER = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
JOB = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OP = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
PAYLOAD = {
    "start_date": "20260101", "end_date": "20260228", "force_rescrape": False,
    "dry_run": False, "server_batch": True,
}


def request(**changes):
    payload = dict(PAYLOAD)
    payload.update(changes)
    if payload.get("server_batch") is False:
        payload.pop("server_batch")
    return EnqueueRequest(
        job_id=JOB, operation_id=OP, owner_user_id=OWNER,
        request_payload=payload, request_hash=hashlib.sha256(json.dumps(payload).encode()).hexdigest(),
    )


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "_JOBS_DB_PATH", tmp_path / "children.db")
    monkeypatch.setattr(jobs, "_scrape_jobs", {})
    jobs._init_jobs_db()
    config = OperationalSagaConfig(
        mode=OperationalSagaMode.LOCAL_SQLITE, environment="test", sqlite_path=tmp_path / "saga.db",
        worker_enabled=True, remote_effects_enabled=True, execution_unlock_enabled=True,
        lease_seconds=5, poll_interval_ms=50,
    )
    result = OperationalSagaRuntime(config)
    result.initialize()
    return result


def finish(child_id: str, races: int = 3, *, dry_run=False):
    child = jobs._scrape_jobs[child_id]
    child["status"] = "completed"
    child["progress"] = {"done": 2, "total": 2, "saved_races": races, "saved_horses": races * 10}
    child["result"] = {
        "success": True, "races_collected": races, "saved_horses": races * 10,
        "existing_races_skipped": 5, "verified_no_race_dates": 20, "elapsed_time": 2.5,
        "fetch_summary": {"metrics": {"http_requests": 2, "cache_hits": 4}},
    }
    if dry_run:
        child["result"]["fetch_summary"] = {"dry_run": {"estimated_request_count": 10}}
    jobs._persist_job_or_raise(child_id, child)


def expire(runtime):
    with sqlite3.connect(runtime.config.sqlite_path) as conn:
        conn.execute("UPDATE operational_scrape_outbox SET lease_expires_at_epoch=0 WHERE job_id=?", (JOB,))


def test_date_bounds_keep_legacy_limits_and_partial_calendar_months():
    with pytest.raises(ValidationError):
        ScrapeRequest(start_date="20260101", end_date="20260228")
    assert build_scrape_months("20260129", "20260302") == [
        ("20260129", "20260131"), ("20260201", "20260228"), ("20260301", "20260302"),
    ]
    assert len(build_scrape_months("20160101", "20260930")) == 129
    assert len(build_scrape_months("20000101", "20191231")) == 240
    with pytest.raises(ValidationError):
        ScrapeRequest(start_date="20000101", end_date="20200101", server_batch=True)
    with pytest.raises(ValidationError):
        ScrapeRequest(start_date="20260201", end_date="20260101", server_batch=True)


def test_two_months_continue_without_client_and_aggregate(runtime, monkeypatch):
    calls = []

    async def runner(child_id, start, end, force, dry_run, cancel_requested):
        assert not cancel_requested()
        calls.append((child_id, start, end))
        finish(child_id)

    monkeypatch.setattr(jobs, "_run_scrape_job", runner)
    assert runtime.enqueue(request()).code is MutationCode.APPLIED
    assert asyncio.run(runtime.run_once()).code is MutationCode.APPLIED
    parent = runtime.get_job(JOB, OWNER)
    assert parent["status"] == "completed"
    assert parent["result"]["races_collected"] == 6
    assert parent["result"]["saved_horses"] == 60
    assert parent["result"]["elapsed_time"] == 5
    assert parent["result"]["fetch_summary"]["elapsed_time_sec"] == 5
    assert parent["result"]["fetch_summary"]["metrics"] == {"http_requests": 4, "cache_hits": 8}
    assert parent["progress"]["completed_months"] == 2
    assert parent["progress"]["done"] == parent["progress"]["total"] == 2000
    assert calls == [(month_job_id(JOB, start, end), start, end) for start, end in build_scrape_months("20260101", "20260228")]
    assert runtime.list_jobs(OWNER, 20)[0]["request_payload"]["server_batch"] is True
    assert runtime.get_job(JOB, OTHER) is None


def test_parent_idempotency_and_old_month_job_conflict(runtime):
    first = request()
    assert runtime.enqueue(first).code is MutationCode.APPLIED
    assert runtime.enqueue(first).code is MutationCode.DUPLICATE
    different = EnqueueRequest(
        job_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc", operation_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        owner_user_id=OWNER, request_hash=first.request_hash,
        request_payload={"start_date": "20260301", "end_date": "20260331", "force_rescrape": False, "dry_run": True},
    )
    result = runtime.enqueue(different)
    assert result.code is MutationCode.CONFLICT
    assert result.reason == "owner-active-job"
    assert runtime.request_cancel(JOB, OTHER).code is MutationCode.NOT_FOUND
    assert runtime.request_cancel(JOB, OWNER).job["status"] == "cancelled"
    assert runtime.enqueue(first).code is MutationCode.DUPLICATE
    assert asyncio.run(runtime.run_once()).code is MutationCode.NOT_FOUND


def test_resume_after_shutdown_keeps_month_receipts_and_child_checkpoint(runtime, monkeypatch):
    calls = []
    second_entered = asyncio.Event()
    second_attempt = 0

    async def runner(child_id, start, end, force, dry_run, cancel_requested):
        nonlocal second_attempt
        calls.append(start)
        child = jobs._scrape_jobs[child_id]
        if start == "20260201":
            second_attempt += 1
            if second_attempt == 1:
                child["progress"] = {"done": 1, "total": 2, "saved_races": 2, "completed_dates": ["20260201"]}
                jobs._persist_job_or_raise(child_id, child)
                second_entered.set()
                while not cancel_requested():
                    await asyncio.sleep(0.01)
                child["status"] = "cancelled"
                jobs._persist_job_or_raise(child_id, child)
                return
            assert child["progress"]["completed_dates"] == ["20260201"]
            assert child["resume_count"] == 1
        finish(child_id)

    monkeypatch.setattr(jobs, "_run_scrape_job", runner)
    runtime.enqueue(request())

    async def scenario():
        task = asyncio.create_task(runtime.run_once())
        await asyncio.wait_for(second_entered.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert runtime.get_job(JOB, OWNER)["progress"]["completed_months"] == 1
        jobs._scrape_jobs.clear()
        expire(runtime)
        restarted = OperationalSagaRuntime(runtime.config)
        assert (await restarted.run_once()).code is MutationCode.APPLIED
        assert restarted.get_job(JOB, OWNER)["status"] == "completed"

    asyncio.run(scenario())
    assert calls == ["20260101", "20260201", "20260201"]


def test_crash_after_child_receipt_before_parent_checkpoint_does_not_repeat_month(runtime, monkeypatch):
    calls = []

    async def runner(child_id, start, *args, **kwargs):
        calls.append(start)
        finish(child_id)

    real_save = BatchCheckpointStore.save
    interrupted = False

    def interrupt_once(self, progress, **kwargs):
        nonlocal interrupted
        if kwargs.get("month_index") == 0 and not interrupted:
            interrupted = True
            raise asyncio.CancelledError()
        return real_save(self, progress, **kwargs)

    monkeypatch.setattr(jobs, "_run_scrape_job", runner)
    monkeypatch.setattr(BatchCheckpointStore, "save", interrupt_once)
    runtime.enqueue(request())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime.run_once())
    jobs._scrape_jobs.clear()
    expire(runtime)
    assert asyncio.run(OperationalSagaRuntime(runtime.config).run_once()).code is MutationCode.APPLIED
    assert calls == ["20260101", "20260201"]


def test_owner_stop_safely_finishes_current_io_and_never_starts_next_month(runtime, monkeypatch):
    entered, released = asyncio.Event(), asyncio.Event()
    calls = []

    async def runner(child_id, start, *args, cancel_requested):
        calls.append(start)
        child = jobs._scrape_jobs[child_id]
        child["progress"] = {"saved_races": 2, "saved_horses": 20}
        entered.set()
        await released.wait()  # Simulates a write already in progress.
        assert cancel_requested()
        child["status"] = "cancelled"
        jobs._persist_job_or_raise(child_id, child)

    monkeypatch.setattr(jobs, "_run_scrape_job", runner)
    runtime.enqueue(request())

    async def scenario():
        task = asyncio.create_task(runtime.run_once())
        await entered.wait()
        mutation = runtime.request_cancel(JOB, OWNER)
        assert mutation.job["status"] == "cancelling"
        assert not task.done()
        released.set()
        assert (await task).code is MutationCode.APPLIED

    asyncio.run(scenario())
    parent = runtime.get_job(JOB, OWNER)
    assert parent["status"] == "cancelled"
    assert parent["progress"]["saved_races"] == 2
    assert parent["progress"]["saved_horses"] == 20
    assert calls == ["20260101"]


@pytest.mark.parametrize("last_month", [False, True])
def test_stop_racing_month_receipt_never_starts_another_month(runtime, monkeypatch, last_month):
    calls = []

    async def runner(child_id, start, *args, **kwargs):
        calls.append(start)
        finish(child_id)
        runtime.request_cancel(JOB, OWNER)

    monkeypatch.setattr(jobs, "_run_scrape_job", runner)
    runtime.enqueue(request(end_date="20260131" if last_month else "20260228"))
    assert asyncio.run(runtime.run_once()).code is MutationCode.APPLIED
    parent = runtime.get_job(JOB, OWNER)
    assert parent["status"] == ("completed" if last_month else "cancelled")
    assert parent["progress"]["completed_months"] == 1
    assert calls == ["20260101"]


def test_stale_fence_cannot_save_progress_or_start_child(runtime, monkeypatch):
    runtime.enqueue(request())
    store = runtime._store
    claim = store.claim_next("old", int(time.time()), 5).claim
    checkpoint = BatchCheckpointStore(store, claim)
    checkpoint.save({"done": 1})
    saved_progress = dict(runtime.get_job(JOB, OWNER)["progress"])
    expire(runtime)
    newer = store.claim_next("new", int(time.time()), 5).claim
    assert newer.fencing_token == claim.fencing_token + 1
    with pytest.raises(OperationalSagaConflict):
        checkpoint.save({"done": 999})
    with pytest.raises(OperationalSagaConflict):
        asyncio.run(runtime._executor.execute(claim))
    assert saved_progress["done"] == 1
    assert saved_progress["batch_wall_time_complete"] is True
    assert runtime.get_job(JOB, OWNER)["progress"] == saved_progress


def test_batch_wall_clock_survives_recovery_without_reset(runtime):
    runtime.enqueue(request())
    store = runtime._store
    first = store.claim_next("first", int(time.time()), 5).claim
    checkpoint = BatchCheckpointStore(store, first)
    checkpoint.save({"completed_months": 0})
    original = checkpoint.timings()
    expire(runtime)
    second = store.claim_next("second", int(time.time()), 5).claim
    recovered = BatchCheckpointStore(store, second)
    recovered.save({"completed_months": 1})
    timings = recovered.timings()
    assert timings["batch_started_at_epoch"] == original["batch_started_at_epoch"]
    assert timings["batch_wall_time_complete"] is True
    assert timings["batch_wall_time_sec"] >= original["batch_wall_time_sec"]


def test_upgrading_untimed_batch_never_labels_tail_as_entire_duration(runtime):
    runtime.enqueue(request())
    store = runtime._store
    store.claim_next("old-version", int(time.time()), 5)
    with sqlite3.connect(runtime.config.sqlite_path) as conn:
        conn.execute("UPDATE operational_scrape_jobs SET progress=? WHERE job_id=?",
                     (json.dumps({"completed_months": 1}), JOB))
    expire(runtime)
    claim = store.claim_next("new-version", int(time.time()), 5).claim
    checkpoint = BatchCheckpointStore(store, claim)
    checkpoint.save({"completed_months": 1})
    timings = checkpoint.timings()
    assert timings["batch_wall_time_complete"] is False
    assert timings["batch_wall_time_sec"] is None
    assert timings["observed_batch_wall_time_sec"] >= 0


def test_old_lease_drains_io_before_recovered_worker_enters(runtime, monkeypatch):
    entered, release_io, old_finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    async def runner(child_id, start, *args, cancel_requested):
        calls.append(start)
        if len(calls) == 1:
            entered.set()
            await release_io.wait()
            assert cancel_requested()
            jobs._scrape_jobs[child_id]["status"] = "cancelled"
            jobs._persist_job_or_raise(child_id, jobs._scrape_jobs[child_id])
            old_finished.set()
            return
        assert old_finished.is_set()
        finish(child_id)

    monkeypatch.setattr(jobs, "_run_scrape_job", runner)
    runtime.enqueue(request(end_date="20260131"))
    old_claim = runtime._store.claim_next("old", int(time.time()), 5).claim

    async def scenario():
        old = asyncio.create_task(runtime._executor.execute(old_claim))
        await entered.wait()
        expire(runtime)
        new_claim = runtime._store.claim_next("new", int(time.time()), 5).claim
        new_executor = ScrapeJobEffectExecutor(allow_unfenced_local_writes=True, batch_store=runtime._store)
        new = asyncio.create_task(new_executor.execute(new_claim))
        old.cancel()
        await asyncio.sleep(0.15)
        old.cancel()  # A second runtime shutdown must not release the effect lock.
        await asyncio.sleep(0.05)
        assert calls == ["20260101"]
        assert not old.done() and not new.done()
        release_io.set()
        with pytest.raises(asyncio.CancelledError):
            await old
        effect = await asyncio.wait_for(new, 3)
        assert effect.result["races_collected"] == 3

    asyncio.run(scenario())
    assert calls == ["20260101", "20260101"]


@pytest.mark.parametrize("old_batch,new_batch", [(True, True), (True, False), (False, True), (False, False)])
def test_expired_cancelled_parent_drain_blocks_new_parent_for_same_owner(runtime, monkeypatch, old_batch, new_batch):
    entered, release_io, old_finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    async def runner(child_id, *args, cancel_requested):
        calls.append(child_id)
        if len(calls) == 1:
            entered.set()
            await release_io.wait()
            assert cancel_requested()
            jobs._scrape_jobs[child_id]["status"] = "cancelled"
            jobs._persist_job_or_raise(child_id, jobs._scrape_jobs[child_id])
            old_finished.set()
            return
        assert old_finished.is_set()
        finish(child_id)

    monkeypatch.setattr(jobs, "_run_scrape_job", runner)
    runtime.enqueue(request(end_date="20260131", server_batch=old_batch))
    old_claim = runtime._store.claim_next("old", int(time.time()), 5).claim

    async def scenario():
        old = asyncio.create_task(runtime._executor.execute(old_claim))
        await entered.wait()
        runtime.request_cancel(JOB, OWNER)
        expire(runtime)
        assert runtime._store.claim_next("cleanup", int(time.time()), 5).code is MutationCode.NOT_FOUND
        assert runtime.get_job(JOB, OWNER)["status"] == "cancelled"
        next_request = EnqueueRequest(
            job_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc", operation_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            owner_user_id=OWNER, request_hash=request().request_hash,
            request_payload=request(end_date="20260131", server_batch=new_batch).request_payload,
        )
        assert runtime.enqueue(next_request).code is MutationCode.APPLIED
        next_claim = runtime._store.claim_next("new", int(time.time()), 5).claim
        new_executor = ScrapeJobEffectExecutor(allow_unfenced_local_writes=True, batch_store=runtime._store)
        new = asyncio.create_task(new_executor.execute(next_claim))
        old.cancel()
        await asyncio.sleep(0.15)
        assert len(calls) == 1
        assert not new.done()
        release_io.set()
        with pytest.raises(asyncio.CancelledError):
            await old
        assert (await asyncio.wait_for(new, 3)).result["races_collected"] == 3

    asyncio.run(scenario())
    assert len(calls) == 2


@pytest.mark.parametrize("shutdown_trigger", ["forced-shutdown", "lease-lost"])
def test_legacy_to_thread_write_drains_before_new_batch_after_shutdown(
    runtime, monkeypatch, tmp_path, shutdown_trigger,
):
    entered, release_write, write_finished = threading.Event(), threading.Event(), threading.Event()
    destination = tmp_path / "effect.db"
    with sqlite3.connect(destination) as connection:
        connection.execute("CREATE TABLE effects (name TEXT PRIMARY KEY)")
    callbacks = []
    calls = []

    def in_flight_write():
        entered.set()
        assert release_write.wait(8), "test must release the simulated write"
        with sqlite3.connect(destination) as connection:
            connection.execute("INSERT INTO effects VALUES ('legacy-write')")
        write_finished.set()

    async def runner(child_id, *args, cancel_requested):
        calls.append(child_id)
        if child_id == JOB:
            callbacks.append(cancel_requested)
            await asyncio.to_thread(in_flight_write)
            assert cancel_requested()
            jobs._scrape_jobs[child_id]["status"] = "cancelled"
            jobs._persist_job_or_raise(child_id, jobs._scrape_jobs[child_id])
            return
        assert write_finished.is_set()
        with sqlite3.connect(destination) as connection:
            assert connection.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 1
        finish(child_id)

    monkeypatch.setattr(jobs, "_run_scrape_job", runner)
    runtime.enqueue(request(end_date="20260131", server_batch=False))

    async def scenario():
        old_task = asyncio.create_task(runtime.run_once())
        next_task = None
        try:
            while not entered.is_set():
                await asyncio.sleep(0.01)
            # Set the durable marker directly so a local cancel signal does
            # not mask the runtime shutdown/lease-loss drain being tested.
            runtime._store.request_cancel(JOB, OWNER, int(time.time()))
            expire(runtime)
            assert runtime._store.claim_next("cleanup", int(time.time()), 5).code is MutationCode.NOT_FOUND
            next_request = EnqueueRequest(
                job_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc", operation_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                owner_user_id=OWNER, request_hash=request().request_hash,
                request_payload=request(end_date="20260131").request_payload,
            )
            assert runtime.enqueue(next_request).code is MutationCode.APPLIED
            next_runtime = OperationalSagaRuntime(runtime.config)
            next_task = asyncio.create_task(next_runtime.run_once())
            if shutdown_trigger == "forced-shutdown":
                old_task.cancel()
            else:
                # The old run_once heartbeat must notice the expired fence.
                async def wait_for_drain_signal():
                    while not callbacks[0]():
                        await asyncio.sleep(0.01)
                await asyncio.wait_for(wait_for_drain_signal(), 3)
            await asyncio.sleep(0.05)
            old_task.cancel()  # Concurrent shutdown while the first drain is pending.
            await asyncio.sleep(0.05)
            assert not old_task.done() and not next_task.done()
            assert calls == [JOB]
            assert not write_finished.is_set()
            release_write.set()
            if shutdown_trigger == "forced-shutdown":
                with pytest.raises(asyncio.CancelledError):
                    await old_task
            else:
                outcome = await old_task
                assert outcome.code is MutationCode.CONFLICT
                assert outcome.reason == "worker-lease-lost"
            assert (await asyncio.wait_for(next_task, 3)).code is MutationCode.APPLIED
            assert write_finished.is_set()
            assert len(calls) == 2
        finally:
            release_write.set()
            for task in (old_task, next_task):
                if task is not None and not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

    asyncio.run(scenario())


def test_legacy_recovery_never_dispatches_saga_child(runtime, monkeypatch):
    child_id = month_job_id(JOB, "20260101", "20260131")
    child = {
        "status": "running", "owner_user_id": OWNER, "request_hash": "x",
        "request": {"start_date": "20260101", "end_date": "20260131", "operational_parent_job_id": JOB},
    }
    jobs._persist_job_or_raise(child_id, child)
    assert jobs.start_scrape_job_worker(child_id) is False
    assert jobs.resume_interrupted_scrape_jobs() == 0
    assert jobs._load_job_from_db(child_id)["status"] == "running"


def test_heartbeat_failure_drains_write_before_releasing_owner_lock(runtime, monkeypatch):
    entered, heartbeat_failed, release_io = asyncio.Event(), asyncio.Event(), asyncio.Event()
    event_loop = []

    async def runner(child_id, *args, cancel_requested):
        entered.set()
        await release_io.wait()
        assert cancel_requested()
        jobs._scrape_jobs[child_id]["status"] = "cancelled"
        jobs._persist_job_or_raise(child_id, jobs._scrape_jobs[child_id])

    def unavailable(*_args):
        event_loop[0].call_soon_threadsafe(heartbeat_failed.set)
        raise OperationalSagaUnavailable("test-heartbeat-unavailable")

    monkeypatch.setattr(jobs, "_run_scrape_job", runner)
    monkeypatch.setattr(runtime._store, "heartbeat", unavailable)
    runtime.enqueue(request())

    async def scenario():
        event_loop.append(asyncio.get_running_loop())
        task = asyncio.create_task(runtime.run_once())
        await entered.wait()
        await asyncio.wait_for(heartbeat_failed.wait(), 3)
        await asyncio.sleep(0.05)
        assert not task.done()
        assert runtime.get_job(JOB, OWNER)["status"] == "running"
        other = EnqueueRequest(
            job_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc", operation_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            owner_user_id=OWNER, request_hash=request().request_hash, request_payload=PAYLOAD,
        )
        assert runtime.enqueue(other).reason == "owner-active-job"
        release_io.set()
        assert (await task).code is MutationCode.APPLIED
        assert runtime.get_job(JOB, OWNER)["status"] == "error"
        assert runtime.enqueue(other).code is MutationCode.APPLIED

    asyncio.run(scenario())


def test_dry_run_aggregate_uses_the_same_parent_without_http(runtime, monkeypatch):
    calls = []

    async def runner(child_id, start, end, force, dry_run, cancel_requested):
        assert dry_run is True
        calls.append(start)
        finish(child_id, 0, dry_run=True)

    monkeypatch.setattr(jobs, "_run_scrape_job", runner)
    runtime.enqueue(request(dry_run=True))
    assert asyncio.run(runtime.run_once()).code is MutationCode.APPLIED
    parent = runtime.get_job(JOB, OWNER)
    assert parent["result"]["fetch_summary"]["dry_run"]["estimated_request_count"] == 20
    assert len(calls) == 2


def test_route_validates_fresh_admin_and_preserves_preallocated_ids(runtime, monkeypatch):
    checked = []

    async def strict(user):
        checked.append(user["user_id"])
        return user

    monkeypatch.setattr(routes, "require_current_admin", strict)
    monkeypatch.setattr(routes, "get_operational_saga_runtime", lambda: runtime)
    response = asyncio.run(routes.scrape_start(ScrapeRequest(**PAYLOAD, job_id=JOB, operation_id=OP), {"user_id": OWNER}))
    assert response["job_id"] == JOB
    assert response["operation_id"] == OP
    assert response["server_batch"] is True
    assert checked == [OWNER]
    status = asyncio.run(routes.scrape_status(JOB, {"user_id": OTHER}))
    assert status["status"] == "not_found"
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.scrape_cancel(JOB, {"user_id": OTHER}))
    assert exc.value.status_code == 404


def test_revoked_admin_and_profile_unavailable_cannot_enqueue(runtime, monkeypatch):
    monkeypatch.setattr(routes, "get_operational_saga_runtime", lambda: runtime)
    for code in (403, 503):
        async def denied(_user):
            raise HTTPException(code, "unavailable")
        monkeypatch.setattr(routes, "require_current_admin", denied)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(routes.scrape_start(ScrapeRequest(**PAYLOAD), {"user_id": OWNER}))
        assert exc.value.status_code == code
        assert runtime.list_jobs(OWNER, 20) == []


def test_deployed_batch_rejected_even_for_dry_run(monkeypatch):
    config = OperationalSagaConfig(
        mode=OperationalSagaMode.SUPABASE, environment="production",
        worker_enabled=True, remote_effects_enabled=True, execution_unlock_enabled=True,
    )
    # An inert adapter cannot be contacted: the route/mode boundary must deny first.
    runtime = OperationalSagaRuntime(config, store=object())
    monkeypatch.setattr(routes, "get_operational_saga_runtime", lambda: runtime)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.scrape_start(ScrapeRequest(**{**PAYLOAD, "dry_run": True}), {"user_id": OWNER}))
    assert exc.value.status_code == 403
    assert runtime.enqueue(request(dry_run=True)).code is MutationCode.UNAVAILABLE
