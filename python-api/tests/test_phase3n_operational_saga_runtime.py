from __future__ import annotations

import asyncio
import hashlib
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, "python-api")

from models import ScrapeRequest  # type: ignore  # noqa: E402
from routers import scrape as scrape_router  # type: ignore  # noqa: E402
from scraping.operational_saga_runtime import (  # type: ignore  # noqa: E402
    EffectResult,
    EnqueueRequest,
    Mutation,
    MutationCode,
    OperationalClaim,
    OperationalSagaConfig,
    OperationalSagaConfigError,
    OperationalSagaMode,
    OperationalSagaRuntime,
    OperationalSagaUnavailable,
    ScrapeJobEffectExecutor,
    SQLiteOperationalSagaStore,
    SupabaseOperationalSagaStore,
    load_operational_saga_config,
    set_operational_saga_runtime_for_tests,
)
from scraping.scrape_request_contract import (  # type: ignore  # noqa: E402
    MAX_SCRAPE_RANGE_DAYS,
    MAX_SCRAPE_TARGETS,
    build_bounded_scrape_dates,
)


OWNER = "11111111-1111-4111-8111-111111111111"
OWNER_2 = "22222222-2222-4222-8222-222222222222"
JOB = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
JOB_2 = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
OPERATION = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
OPERATION_2 = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
REQUEST_HASH = hashlib.sha256(b"phase3n-request").hexdigest()
PAYLOAD = {
    "start_date": "2026-07-01",
    "end_date": "2026-07-31",
    "force_rescrape": False,
    "dry_run": True,
}


@pytest.mark.parametrize(
    "start_date,end_date",
    [
        ("20260701", "20260731"),
        ("2026-07-01", "2026-07-31"),
        ("2026/07/01", "2026/07/31"),
    ],
)
def test_scrape_request_accepts_only_explicit_bounded_legacy_date_formats(
    start_date: str, end_date: str
) -> None:
    request = ScrapeRequest(start_date=start_date, end_date=end_date, dry_run=True)
    assert request.start_date == start_date
    assert request.end_date == end_date
    assert len(build_bounded_scrape_dates(start_date, end_date)) == MAX_SCRAPE_RANGE_DAYS
    assert MAX_SCRAPE_TARGETS == MAX_SCRAPE_RANGE_DAYS * 2


@pytest.mark.parametrize(
    "start_date,end_date",
    [
        ("2026-7-01", "2026-07-31"),
        ("2026-02-30", "2026-03-01"),
        (" 2026-07-01", "2026-07-31"),
        (20260701, "20260731"),
        ("2026-07-31", "2026-07-01"),
        ("2026-01-01", "2026-02-01"),
    ],
)
def test_scrape_request_rejects_invalid_reversed_or_oversized_ranges_before_enqueue(
    start_date: object, end_date: object
) -> None:
    with pytest.raises(ValidationError):
        ScrapeRequest(start_date=start_date, end_date=end_date, dry_run=True)


def test_worker_date_builder_independently_rejects_oversized_claim_payload() -> None:
    with pytest.raises(ValueError, match="must not exceed"):
        build_bounded_scrape_dates("0001-01-01", "9999-12-31")


def _config(path: Path) -> OperationalSagaConfig:
    return OperationalSagaConfig(
        mode=OperationalSagaMode.LOCAL_SQLITE,
        environment="test",
        sqlite_path=path,
        worker_enabled=True,
        remote_effects_enabled=True,
        execution_unlock_enabled=True,
        lease_seconds=5,
        poll_interval_ms=50,
        max_attempts=5,
    )


def _request(
    *,
    job_id: str = JOB,
    operation_id: str = OPERATION,
    owner: str = OWNER,
    request_hash: str = REQUEST_HASH,
    payload: dict | None = None,
) -> EnqueueRequest:
    return EnqueueRequest(
        job_id=job_id,
        operation_id=operation_id,
        owner_user_id=owner,
        request_hash=request_hash,
        request_payload=dict(PAYLOAD if payload is None else payload),
    )


def _store(tmp_path: Path) -> SQLiteOperationalSagaStore:
    store = SQLiteOperationalSagaStore(_config(tmp_path / "phase3n-operational.db"))
    store.initialize()
    return store


def test_disabled_is_default_and_unsafe_partial_enablement_fails_closed() -> None:
    config = load_operational_saga_config({})
    assert config.mode is OperationalSagaMode.DISABLED
    assert config.enabled is False

    with pytest.raises(OperationalSagaConfigError, match="disabled-runtime-widened"):
        load_operational_saga_config({"PHASE3N_WORKER_ENABLED": "true"})
    with pytest.raises(OperationalSagaConfigError, match="operational-flags-not-enabled"):
        load_operational_saga_config(
            {
                "APP_ENV": "test",
                "PHASE3N_OPERATIONAL_SAGA_MODE": "local-sqlite",
                "PHASE3N_SAGA_SQLITE_PATH": str(Path.cwd() / "unsafe.db"),
            }
        )
    with pytest.raises(OperationalSagaConfigError, match="local-sqlite-environment-forbidden"):
        OperationalSagaConfig(
            mode=OperationalSagaMode.LOCAL_SQLITE,
            environment="production",
            sqlite_path=Path("unsafe.db"),
            worker_enabled=True,
            remote_effects_enabled=True,
            execution_unlock_enabled=True,
        )


def test_deployed_mode_requires_shared_supabase_and_all_explicit_flags() -> None:
    config = load_operational_saga_config(
        {
            "APP_ENV": "staging",
            "PHASE3N_OPERATIONAL_SAGA_MODE": "supabase",
            "PHASE3N_WORKER_ENABLED": "true",
            "PHASE3N_REMOTE_EFFECTS_ENABLED": "true",
            "PHASE3N_EXECUTION_UNLOCK_ENABLED": "true",
        }
    )
    assert config.mode is OperationalSagaMode.SUPABASE
    assert config.enabled is True
    assert config.sqlite_path is None

    with pytest.raises(OperationalSagaConfigError, match="supabase-environment-required"):
        OperationalSagaConfig(
            mode=OperationalSagaMode.SUPABASE,
            environment="local",
            worker_enabled=True,
            remote_effects_enabled=True,
            execution_unlock_enabled=True,
        )


def test_concurrent_workers_claim_one_outbox_record_exactly_once(tmp_path: Path) -> None:
    first_store = _store(tmp_path)
    assert first_store.enqueue(_request(), 100).code is MutationCode.APPLIED

    stores = [SQLiteOperationalSagaStore(_config(tmp_path / "phase3n-operational.db")) for _ in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda item: item[1].claim_next(f"worker-{item[0]}", 110, 5),
                enumerate(stores),
            )
        )
    winners = [result for result in results if result.code is MutationCode.APPLIED]
    assert len(winners) == 1
    assert winners[0].claim is not None
    assert winners[0].claim.fencing_token == 1
    assert all(
        result.code in {MutationCode.APPLIED, MutationCode.NOT_FOUND}
        for result in results
    )


def test_expired_lease_is_recovered_with_higher_fence_and_stale_worker_is_rejected(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.enqueue(_request(), 100)
    first = store.claim_next("worker-a", 110, 5).claim
    assert first is not None
    assert store.claim_next("worker-b", 114, 5).code is MutationCode.NOT_FOUND

    second = store.claim_next("worker-b", 115, 5).claim
    assert second is not None
    assert second.fencing_token > first.fencing_token
    assert second.idempotency_key == first.idempotency_key

    effect = EffectResult({"success": True}, hashlib.sha256(b"receipt").hexdigest())
    stale = store.complete(first, effect, 116)
    assert stale.code is MutationCode.CONFLICT
    assert stale.reason == "stale-worker-fence"
    assert store.complete(second, effect, 116).code is MutationCode.APPLIED


def test_heartbeat_extends_current_lease_and_refuses_an_expired_lease(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.enqueue(_request(), 100)
    claim = store.claim_next("worker-a", 110, 5).claim
    assert claim is not None
    renewed = store.heartbeat(claim, 112, 5)
    assert renewed.code is MutationCode.APPLIED
    assert renewed.claim is not None
    assert renewed.claim.lease_expires_at_epoch == 117
    lost = store.heartbeat(renewed.claim, 117, 5)
    assert lost.code is MutationCode.CONFLICT
    assert lost.reason == "lease-lost"


def test_exhausted_crash_recovery_becomes_terminal_and_unlocks_owner(tmp_path: Path) -> None:
    config = OperationalSagaConfig(
        **{
            **_config(tmp_path / "phase3n-operational.db").__dict__,
            "max_attempts": 2,
        }
    )
    store = SQLiteOperationalSagaStore(config)
    store.initialize()
    store.enqueue(_request(), 100)
    assert store.claim_next("worker-a", 110, 5).code is MutationCode.APPLIED
    assert store.claim_next("worker-b", 115, 5).code is MutationCode.APPLIED
    # The next poll materializes attempt exhaustion before returning no work.
    assert store.claim_next("worker-c", 120, 5).code is MutationCode.NOT_FOUND
    job = store.get_job(JOB, OWNER)
    assert job is not None
    assert job["status"] == "error"
    assert job["error"] == "max-attempts-exhausted"
    assert store.enqueue(_request(job_id=JOB_2, operation_id=OPERATION_2), 121).code is MutationCode.APPLIED


def test_effect_settlement_replay_is_idempotent_and_changed_receipt_conflicts(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.enqueue(_request(), 100)
    claim = store.claim_next("worker-a", 110, 5).claim
    assert claim is not None
    effect = EffectResult({"success": True, "races_collected": 0}, hashlib.sha256(b"one").hexdigest())
    assert store.complete(claim, effect, 111).code is MutationCode.APPLIED
    assert store.complete(claim, effect, 112).code is MutationCode.DUPLICATE

    changed = EffectResult(effect.result, hashlib.sha256(b"two").hexdigest())
    conflict = store.complete(claim, changed, 112)
    assert conflict.code is MutationCode.CONFLICT
    assert conflict.reason == "effect-receipt-conflict"


def test_crash_recovery_reuses_downstream_idempotency_key_and_effect_occurs_once(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.enqueue(_request(), 100)
    first = store.claim_next("worker-a", 110, 5).claim
    assert first is not None

    downstream_receipts: dict[str, str] = {}
    effect_count = 0

    def downstream(claim: OperationalClaim) -> EffectResult:
        nonlocal effect_count
        receipt = downstream_receipts.get(claim.idempotency_key)
        if receipt is None:
            effect_count += 1
            receipt = hashlib.sha256((claim.idempotency_key + "|effect").encode()).hexdigest()
            downstream_receipts[claim.idempotency_key] = receipt
        return EffectResult({"success": True}, receipt)

    first_effect = downstream(first)
    # Injected crash: the effect exists but the outbox acknowledgement does not.
    second = store.claim_next("worker-b", 115, 5).claim
    assert second is not None
    second_effect = downstream(second)
    assert first_effect == second_effect
    assert effect_count == 1
    assert store.complete(second, second_effect, 116).code is MutationCode.APPLIED


def test_terminal_completion_unlocks_owner_for_a_new_job_only_after_durable_settlement(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    assert store.enqueue(_request(), 100).code is MutationCode.APPLIED
    blocked = store.enqueue(
        _request(job_id=JOB_2, operation_id=OPERATION_2), 101
    )
    assert blocked.code is MutationCode.CONFLICT
    assert blocked.reason == "owner-active-job"

    claim = store.claim_next("worker-a", 110, 5).claim
    assert claim is not None
    effect = EffectResult({"success": True}, hashlib.sha256(b"unlock").hexdigest())
    assert store.complete(claim, effect, 111).code is MutationCode.APPLIED
    unlocked = store.enqueue(_request(job_id=JOB_2, operation_id=OPERATION_2), 112)
    assert unlocked.code is MutationCode.APPLIED


def test_terminal_error_also_releases_the_durable_owner_lock(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.enqueue(_request(), 100)
    claim = store.claim_next("worker-a", 110, 5).claim
    assert claim is not None
    assert store.fail(claim, "downstream-rejected", 111).code is MutationCode.APPLIED
    job = store.get_job(JOB, OWNER)
    assert job is not None and job["status"] == "error"
    assert store.enqueue(_request(job_id=JOB_2, operation_id=OPERATION_2), 112).code is MutationCode.APPLIED


class _NonIdempotentExecutor:
    idempotent = False

    async def execute(self, claim: OperationalClaim) -> EffectResult:  # pragma: no cover
        raise AssertionError("must not execute")


def test_worker_refuses_non_idempotent_downstream_before_claim(tmp_path: Path) -> None:
    config = _config(tmp_path / "phase3n-operational.db")
    store = SQLiteOperationalSagaStore(config)
    runtime = OperationalSagaRuntime(config, store, _NonIdempotentExecutor())
    runtime.initialize()
    runtime.enqueue(_request())
    result = asyncio.run(runtime.run_once())
    assert result.code is MutationCode.UNAVAILABLE
    assert result.reason == "idempotent-executor-required"
    winner = store.claim_next("real-worker", 2_000_000_000, 5)
    assert winner.code is MutationCode.APPLIED


class _IdempotentExecutor:
    idempotent = True

    def __init__(self) -> None:
        self.claims: list[OperationalClaim] = []

    async def execute(self, claim: OperationalClaim) -> EffectResult:
        self.claims.append(claim)
        return EffectResult(
            {"success": True, "races_collected": 0},
            hashlib.sha256((claim.idempotency_key + "|done").encode()).hexdigest(),
        )


def test_runtime_worker_claims_executes_and_durably_completes(tmp_path: Path) -> None:
    config = _config(tmp_path / "phase3n-operational.db")
    store = SQLiteOperationalSagaStore(config)
    executor = _IdempotentExecutor()
    runtime = OperationalSagaRuntime(config, store, executor, worker_owner="worker-a")
    runtime.initialize()
    assert runtime.enqueue(_request()).code is MutationCode.APPLIED
    result = asyncio.run(runtime.run_once())
    assert result.code is MutationCode.APPLIED
    assert len(executor.claims) == 1
    job = runtime.get_job(JOB, OWNER)
    assert job is not None and job["status"] == "completed"


def test_dry_run_sync_preprocessing_is_bounded_and_off_event_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scraping import jobs as jobs_module  # type: ignore

    event_loop_thread = threading.get_ident()
    sync_calls: list[tuple[str, int]] = []
    original_builder = jobs_module.build_bounded_scrape_dates

    def record(name: str, result=None, *, delay: float = 0.03):
        def wrapped(*args, **kwargs):
            sync_calls.append((name, threading.get_ident()))
            time.sleep(delay)
            return result(*args, **kwargs) if callable(result) else result

        return wrapped

    monkeypatch.setattr(
        jobs_module,
        "build_bounded_scrape_dates",
        record("date-range", original_builder),
    )
    monkeypatch.setattr(jobs_module, "_init_sqlite_db", record("sqlite-init"))
    monkeypatch.setattr(jobs_module, "_persist_job_or_raise", record("persist"))
    monkeypatch.setattr(
        jobs_module,
        "estimate_fetch_plan",
        record(
            "estimate",
            {
                "total_input_urls": 62,
                "unique_urls": 62,
                "estimated_network_requests": 62,
                "cache_hits": 0,
                "resume_hits": 0,
            },
        ),
    )
    monkeypatch.setattr(
        jobs_module,
        "write_fetch_summary",
        record("summary", tmp_path / "summary.json"),
    )

    jobs_module._scrape_jobs[JOB] = {
        "status": "queued",
        "progress": {},
        "result": None,
        "error": None,
        "owner_user_id": OWNER,
        "request_hash": REQUEST_HASH,
    }

    async def scenario() -> None:
        task = asyncio.create_task(
            jobs_module._run_scrape_job(
                JOB,
                "2026-07-01",
                "2026-07-31",
                force_rescrape=True,
                dry_run=True,
            )
        )
        await asyncio.sleep(0.005)
        assert not task.done()
        await task

    try:
        asyncio.run(scenario())
        assert jobs_module._scrape_jobs[JOB]["status"] == "completed"
        assert {name for name, _thread in sync_calls} >= {
            "date-range",
            "sqlite-init",
            "persist",
            "estimate",
            "summary",
        }
        assert all(thread_id != event_loop_thread for _name, thread_id in sync_calls)
    finally:
        jobs_module._scrape_jobs.pop(JOB, None)


def test_dry_run_rejects_target_expansion_before_plan_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scraping import jobs as jobs_module  # type: ignore

    plan_calls = 0

    def unexpected_plan(*_args, **_kwargs):
        nonlocal plan_calls
        plan_calls += 1
        raise AssertionError("oversized targets reached planner")

    monkeypatch.setattr(jobs_module, "_persist_job_or_raise", lambda *_args: None)
    monkeypatch.setattr(jobs_module, "_persist_job", lambda *_args: True)
    monkeypatch.setattr(jobs_module, "_init_sqlite_db", lambda *_args: None)
    monkeypatch.setattr(
        jobs_module,
        "build_bounded_scrape_dates",
        lambda *_args: [f"202607{day:02d}" for day in range(1, 33)],
    )
    monkeypatch.setattr(jobs_module, "estimate_fetch_plan", unexpected_plan)
    jobs_module._scrape_jobs[JOB] = {
        "status": "queued",
        "progress": {},
        "result": None,
        "error": None,
        "owner_user_id": OWNER,
        "request_hash": REQUEST_HASH,
    }

    try:
        asyncio.run(
            jobs_module._run_scrape_job(
                JOB,
                "2026-07-01",
                "2026-07-31",
                force_rescrape=True,
                dry_run=True,
            )
        )
        assert plan_calls == 0
        assert jobs_module._scrape_jobs[JOB]["status"] == "error"
        assert "target limit exceeded" in jobs_module._scrape_jobs[JOB]["error"]
    finally:
        jobs_module._scrape_jobs.pop(JOB, None)


def test_http_start_is_disabled_fail_closed_before_any_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = OperationalSagaRuntime(OperationalSagaConfig())
    set_operational_saga_runtime_for_tests(runtime)
    request = ScrapeRequest(start_date="2026-07-01", end_date="2026-07-31", dry_run=True)
    try:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(scrape_router.scrape_start(request, {"user_id": OWNER, "role": "admin"}))
        assert exc.value.status_code == 503
    finally:
        set_operational_saga_runtime_for_tests(None)


def test_http_retry_with_same_binding_is_idempotent_and_changed_payload_conflicts(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path / "phase3n-operational.db")
    runtime = OperationalSagaRuntime(config)
    runtime.initialize()
    set_operational_saga_runtime_for_tests(runtime)
    try:
        request = ScrapeRequest(
            start_date="2026-07-01",
            end_date="2026-07-31",
            dry_run=True,
            job_id=JOB,
            operation_id=OPERATION,
        )
        first = asyncio.run(scrape_router.scrape_start(request, {"user_id": OWNER, "role": "admin"}))
        replay = asyncio.run(scrape_router.scrape_start(request, {"user_id": OWNER, "role": "admin"}))
        assert first["duplicate"] is False
        assert replay["duplicate"] is True
        assert first["job_id"] == replay["job_id"] == JOB
        assert first["operation_id"] == replay["operation_id"] == OPERATION

        changed = request.model_copy(update={"end_date": "2026-08-31"})
        with pytest.raises(HTTPException) as exc:
            asyncio.run(scrape_router.scrape_start(changed, {"user_id": OWNER, "role": "admin"}))
        assert exc.value.status_code == 409
    finally:
        set_operational_saga_runtime_for_tests(None)


class _NoopSharedStore:
    enqueue_calls = 0

    def initialize(self) -> None: pass
    def enqueue(self, request: EnqueueRequest, now_epoch: int) -> Mutation:
        self.enqueue_calls += 1
        return Mutation(MutationCode.APPLIED)
    def claim_next(self, *args) -> Mutation: return Mutation(MutationCode.NOT_FOUND)
    def heartbeat(self, *args) -> Mutation: return Mutation(MutationCode.CONFLICT)
    def complete(self, *args) -> Mutation: return Mutation(MutationCode.CONFLICT)
    def fail(self, *args) -> Mutation: return Mutation(MutationCode.CONFLICT)
    def get_job(self, *args): return None
    def list_jobs(self, *args): return []


def test_runtime_initialization_does_not_block_lifespan_event_loop() -> None:
    events: list[str] = []

    class SlowInitStore(_NoopSharedStore):
        def initialize(self) -> None:
            events.append("initialize-start")
            time.sleep(0.1)
            events.append("initialize-end")

    config = OperationalSagaConfig(
        mode=OperationalSagaMode.SUPABASE,
        environment="staging",
        worker_enabled=True,
        remote_effects_enabled=True,
        execution_unlock_enabled=True,
    )
    runtime = OperationalSagaRuntime(config, SlowInitStore(), _IdempotentExecutor())

    async def scenario() -> None:
        start_task = asyncio.create_task(runtime.start())
        await asyncio.sleep(0.01)
        events.append("event-loop-tick")
        await start_task
        await runtime.stop()

    asyncio.run(scenario())
    assert events.index("event-loop-tick") < events.index("initialize-end")


def test_deployed_http_start_requires_complete_independent_authorization_tuple() -> None:
    config = OperationalSagaConfig(
        mode=OperationalSagaMode.SUPABASE,
        environment="staging",
        worker_enabled=True,
        remote_effects_enabled=True,
        execution_unlock_enabled=True,
    )
    store = _NoopSharedStore()
    runtime = OperationalSagaRuntime(config, store)
    set_operational_saga_runtime_for_tests(runtime)
    try:
        request = ScrapeRequest(start_date="2026-07-01", end_date="2026-07-31")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(scrape_router.scrape_start(request, {"user_id": OWNER, "role": "admin"}))
        assert exc.value.status_code == 403
        assert store.enqueue_calls == 0
    finally:
        set_operational_saga_runtime_for_tests(None)


def _deployed_scrape_request(*, dry_run: bool) -> ScrapeRequest:
    return ScrapeRequest(
        start_date="2026-07-01",
        end_date="2026-07-31",
        dry_run=dry_run,
        job_id=JOB,
        operation_id=OPERATION,
        authorization_id="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
        reservation_id="ffffffff-ffff-4fff-8fff-ffffffffffff",
        review_id="12121212-1212-4212-8212-121212121212",
        review_version=1,
        expected_authorization_version=1,
        consume_request_id="34343434-3434-4434-8434-343434343434",
    )


def test_deployed_execute_is_rejected_before_authorization_consumption() -> None:
    config = OperationalSagaConfig(
        mode=OperationalSagaMode.SUPABASE,
        environment="production",
        worker_enabled=True,
        remote_effects_enabled=True,
        execution_unlock_enabled=True,
    )
    store = _NoopSharedStore()
    runtime = OperationalSagaRuntime(config, store)
    set_operational_saga_runtime_for_tests(runtime)
    try:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                scrape_router.scrape_start(
                    _deployed_scrape_request(dry_run=False),
                    {"user_id": OWNER, "role": "admin"},
                )
            )
        assert exc.value.status_code == 503
        assert store.enqueue_calls == 0
    finally:
        set_operational_saga_runtime_for_tests(None)


def test_deployed_dry_run_uses_durable_enqueue_without_blocking_event_loop() -> None:
    events: list[str] = []

    class SlowStore(_NoopSharedStore):
        def enqueue(self, request: EnqueueRequest, now_epoch: int) -> Mutation:
            events.append("enqueue-start")
            time.sleep(0.1)
            events.append("enqueue-end")
            return super().enqueue(request, now_epoch)

    config = OperationalSagaConfig(
        mode=OperationalSagaMode.SUPABASE,
        environment="staging",
        worker_enabled=True,
        remote_effects_enabled=True,
        execution_unlock_enabled=True,
    )
    store = SlowStore()
    runtime = OperationalSagaRuntime(config, store)
    set_operational_saga_runtime_for_tests(runtime)

    async def scenario() -> dict:
        async def ticker() -> None:
            await asyncio.sleep(0.01)
            events.append("event-loop-tick")

        start_task = asyncio.create_task(
            scrape_router.scrape_start(
                _deployed_scrape_request(dry_run=True),
                {"user_id": OWNER, "role": "admin"},
            )
        )
        await ticker()
        return await start_task

    try:
        response = asyncio.run(scenario())
        assert response["status"] == "queued"
        assert events.index("event-loop-tick") < events.index("enqueue-end")
        assert store.enqueue_calls == 1
    finally:
        set_operational_saga_runtime_for_tests(None)


def test_default_effect_executor_refuses_unfenced_write_before_legacy_runner() -> None:
    claim = OperationalClaim(
        job_id=JOB,
        operation_id=OPERATION,
        owner_user_id=OWNER,
        request_hash=REQUEST_HASH,
        request_payload={**PAYLOAD, "dry_run": False},
        idempotency_key=_request().idempotency_key,
        worker_owner="worker-a",
        fencing_token=1,
        lease_expires_at_epoch=9999999999,
        attempt_count=1,
    )
    with pytest.raises(OperationalSagaUnavailable, match="fenced-operational-execute-not-enabled"):
        asyncio.run(ScrapeJobEffectExecutor().execute(claim))


@pytest.mark.parametrize("route_name", ["scrape_data", "rescrape_incomplete", "repair_race"])
def test_legacy_direct_write_routes_are_fail_closed_in_deployed_environments(
    monkeypatch: pytest.MonkeyPatch, route_name: str
) -> None:
    monkeypatch.setattr(scrape_router, "APP_ENV", "production")
    monkeypatch.setenv("PHASE3N_ALLOW_LEGACY_SCRAPE_WRITES", "true")
    if route_name == "scrape_data":
        call = scrape_router.scrape_data(
            ScrapeRequest(start_date="2026-07-01", end_date="2026-07-31"), {}
        )
    elif route_name == "rescrape_incomplete":
        call = scrape_router.rescrape_incomplete(1, {})
    else:
        call = scrape_router.repair_race("202601010101", {})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(call)
    assert exc.value.status_code == 503


def test_legacy_direct_writer_requires_explicit_local_test_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scrape_router, "APP_ENV", "test")
    monkeypatch.delenv("PHASE3N_ALLOW_LEGACY_SCRAPE_WRITES", raising=False)
    with pytest.raises(HTTPException) as exc:
        scrape_router._require_legacy_scrape_write_allowed()
    assert exc.value.status_code == 503

    monkeypatch.setenv("PHASE3N_ALLOW_LEGACY_SCRAPE_WRITES", "true")
    scrape_router._require_legacy_scrape_write_allowed()

    monkeypatch.setattr(scrape_router, "APP_ENV", "unknown")
    with pytest.raises(HTTPException) as unknown_exc:
        scrape_router._require_legacy_scrape_write_allowed()
    assert unknown_exc.value.status_code == 503


def test_supabase_adapter_rejects_malformed_rpc_response() -> None:
    class Query:
        def execute(self):
            return type("Response", (), {"data": {"not": "a list"}})()

    class Client:
        def rpc(self, *_args, **_kwargs):
            return Query()

    store = SupabaseOperationalSagaStore(Client())
    with pytest.raises(OperationalSagaUnavailable, match="operational-schema-unavailable"):
        store.initialize()


def test_supabase_claim_binds_configured_retry_ceiling() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class Query:
        def __init__(self, data):
            self._data = data

        def execute(self):
            return type("Response", (), {"data": self._data})()

    class Client:
        def rpc(self, name, params):
            calls.append((name, params))
            return Query([{"mutation_code": "not_found", "reason": None, "job": None}])

    store = SupabaseOperationalSagaStore(Client(), max_attempts=7)
    result = store.claim_next("worker-a", 0, 30)

    assert result.code is MutationCode.NOT_FOUND
    assert calls == [
        (
            "claim_scrape_operational_outbox",
            {
                "p_worker_owner": "worker-a",
                "p_lease_seconds": 30,
                "p_max_attempts": 7,
            },
        )
    ]


@pytest.mark.parametrize("max_attempts", [0, 21, True])
def test_supabase_adapter_rejects_invalid_retry_ceiling(max_attempts: object) -> None:
    class Client:
        def rpc(self, *_args, **_kwargs):
            raise AssertionError("RPC must not be called")

    with pytest.raises(OperationalSagaConfigError, match="max-attempts-invalid"):
        SupabaseOperationalSagaStore(Client(), max_attempts=max_attempts)  # type: ignore[arg-type]


def test_postgres_migration_has_shared_claim_fence_idempotency_and_service_role_boundary() -> None:
    sql = Path("supabase/migrations/20260720_scrape_operational_outbox.sql").read_text(
        encoding="utf-8"
    )
    for marker in (
        "scrape_operational_jobs",
        "scrape_operational_outbox",
        "FOR UPDATE SKIP LOCKED",
        "scrape_operational_worker_fencing_seq",
        "stale-worker-fence",
        "effect_receipt_hash TEXT NULL UNIQUE",
        "scrape_operational_one_active_job_per_owner",
        "enqueue_scrape_operational_job",
        "claim_scrape_operational_outbox",
        "heartbeat_scrape_operational_outbox",
        "settle_scrape_operational_outbox",
    ):
        assert marker in sql
    assert "REVOKE ALL ON TABLE public.scrape_operational_jobs" in sql
    assert "FROM PUBLIC, anon, authenticated, service_role" in sql
    assert "GRANT EXECUTE ON FUNCTION public.claim_scrape_operational_outbox" in sql
    assert "p_max_attempts INTEGER" in sql
    assert "o.attempt_count >= p_max_attempts" in sql
    assert "o.attempt_count < p_max_attempts" in sql
    assert "claim_scrape_operational_outbox(TEXT, INTEGER, INTEGER)" in sql
    assert "TO service_role" in sql
    assert "GRANT" not in "\n".join(
        line for line in sql.splitlines() if " TO anon" in line or " TO authenticated" in line
    )


def test_operational_http_path_contains_no_thread_or_legacy_direct_runner() -> None:
    source = Path("python-api/routers/scrape.py").read_text(encoding="utf-8")
    start_slice = source.split('@router.post("/api/scrape/start")', 1)[1].split(
        '@router.get("/api/scrape/status/{job_id}")', 1
    )[0]
    assert "Thread(" not in start_slice
    assert "_run_scrape_job" not in start_slice
    assert "create_job_if_owner_idle" not in start_slice
    assert "get_operational_saga_runtime" in start_slice
    assert "await asyncio.to_thread" in start_slice
    assert "fenced operational execute is not enabled" in start_slice

    for route_name in ("scrape_data", "rescrape_incomplete", "repair_race"):
        function_slice = source.split(f"async def {route_name}", 1)[1].split("\n\n@router", 1)[0]
        assert "_require_legacy_scrape_write_allowed()" in function_slice
