"""Keep acquisition failure details across the real non-batch durable boundary."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import threading
from pathlib import Path
from types import ModuleType

from scraping.acquisition_outcome import acquisition_failure, list_failure
from scraping.operational_saga_runtime import (
    EnqueueRequest,
    MutationCode,
    OperationalSagaConfig,
    OperationalSagaMode,
    OperationalSagaRuntime,
    ScrapeJobEffectExecutor,
    SQLiteOperationalSagaStore,
)


def test_nonbatch_failure_details_survive_executor_and_durable_settlement(tmp_path, monkeypatch):
    owner = "11111111-1111-4111-8111-111111111111"
    job_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    payload = {
        "start_date": "20260919", "end_date": "20260919",
        "force_rescrape": False, "dry_run": False,
    }
    failures = [list_failure("20260919", "current_race_list_unconfirmed", [{"status": 400}])]
    quality = {"quality_complete": False, "incomplete_date_count": 1}
    expected = {
        "success": False,
        **acquisition_failure(quality, failures),
        "races_collected": 0,
        "fetch_summary": {"quality_audit": quality, "list_failures": failures},
    }

    # Importing the production jobs module initializes its app DB. Replace only
    # that worker boundary, leaving executor, claim, settlement and store real.
    worker_module = ModuleType("scraping.jobs")
    worker_module._JOBS_LOCK = threading.RLock()
    worker_module._scrape_jobs = {}
    calls = []

    async def worker(received_job_id, start, end, force, dry_run, *, cancel_requested):
        assert (received_job_id, start, end, force, dry_run) == (
            job_id, "20260919", "20260919", False, False,
        )
        assert not cancel_requested()
        calls.append(received_job_id)
        worker_module._scrape_jobs[received_job_id].update(
            status="completed", result=expected,
        )

    worker_module._run_scrape_job = worker
    monkeypatch.setitem(sys.modules, "scraping.jobs", worker_module)
    config = OperationalSagaConfig(
        mode=OperationalSagaMode.LOCAL_SQLITE, environment="test",
        sqlite_path=Path(tmp_path) / "outcome-boundary.db",
        worker_enabled=True, remote_effects_enabled=True, execution_unlock_enabled=True,
        lease_seconds=5, poll_interval_ms=50, max_attempts=2,
    )
    store = SQLiteOperationalSagaStore(config)
    executor = ScrapeJobEffectExecutor(allow_unfenced_local_writes=True)
    runtime = OperationalSagaRuntime(config, store, executor, worker_owner="outcome-test")
    runtime.initialize()
    request = EnqueueRequest(
        job_id=job_id, operation_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        owner_user_id=owner,
        request_hash=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
        request_payload=payload,
    )
    assert runtime.enqueue(request).code is MutationCode.APPLIED
    assert asyncio.run(runtime.run_once()).code is MutationCode.APPLIED
    assert calls == [job_id]

    # A new store instance proves that the full diagnostic is persisted, not
    # accidentally supplied by the in-memory worker used above.
    reopened = SQLiteOperationalSagaStore(config).get_job(job_id, owner)
    assert reopened["status"] == "completed"
    assert reopened["result"] == expected
    assert reopened["result"]["success"] is False
    assert reopened["result"]["failure_code"] == "race_list_unavailable"
    assert "HTTP 400" in reopened["result"]["failure_reason"]
    assert reopened["result"]["fetch_summary"]["list_failures"] == failures
    assert runtime.get_job(job_id, owner)["result"] == reopened["result"]
