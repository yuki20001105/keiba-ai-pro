"""Acquisition gates must preserve provider data and durable job semantics."""
from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

from scraping import jobs
from scraping.fetch_pipeline import record_fetch_metric
from scraping.quality import (
    classify_race_quality, record_date_expectation, record_race_quality,
    run_date_repair_audit,
)
from scraping.storage import _init_sqlite_db, _save_race_sqlite_only

DAY = "20200104"
RID = "202001010101"
JOB = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def race(*, settled=True):
    return {
        "race_info": {"race_id": RID, "date": DAY, "venue": "Tokyo", "distance": 1600, "num_horses": 2},
        "horses": [
            {"horse_id": f"201600000{number}", "horse_name": f"Horse {number}",
             "race_id": RID, "horse_number": number, "finish_position": number if settled else None,
             "finish_time": "1:32.0" if settled else None, "odds": 3.0,
             "popularity": number, "weight_kg": 470, "sire": "Sire", "dam": "Dam", "damsire": "Damsire"}
            for number in (1, 2)
        ],
        "return_tables": [{"bet_type": "単勝", "combinations": "1", "payout": 300}] if settled else [],
    }


@pytest.fixture
def worker(tmp_path, monkeypatch):
    # jobs computes the application's data root from this file; redirect it
    # before ANY worker effect, not only the scrape-job receipt database.
    monkeypatch.setattr(jobs, "__file__", str(tmp_path / "python-api" / "scraping" / "jobs.py"))
    monkeypatch.setattr(jobs, "_JOBS_DB_PATH", tmp_path / "children.db")
    monkeypatch.setattr(jobs, "_scrape_jobs", {JOB: {
        "status": "queued", "progress": {}, "result": None, "error": None,
        "owner_user_id": "owner", "request_hash": "request",
    }})
    jobs._init_jobs_db()
    db = tmp_path / "keiba" / "data" / "keiba_ultimate.db"
    _init_sqlite_db(db)
    monkeypatch.setattr(jobs, "write_fetch_summary", lambda *_a, **_k: tmp_path / "summary.json")
    monkeypatch.setattr(jobs, "_resource_capacity_available", lambda *_: True)

    async def calendar(*_a, **_k):
        return [DAY]

    monkeypatch.setattr(jobs, "_build_race_dates_from_calendar", calendar)
    return db


def seed_complete(db):
    payload = race()
    assert _save_race_sqlite_only(payload, db)
    record_date_expectation(db, DAY, [RID])
    record_race_quality(db, race_date=DAY, race_id=RID,
                        report=classify_race_quality(payload), source_status="http_200", race_data=payload)
    assert run_date_repair_audit(db, DAY, [RID])["status"] == "complete"


@pytest.mark.parametrize("reused", [False, True])
def test_old_positive_date_checkpoint_does_not_bypass_inventory_gate(worker, monkeypatch, reused):
    seed_complete(worker)
    calls = []

    def inventory(path, day, **kwargs):
        assert path == worker and day == DAY and kwargs["force_refresh"] is False
        calls.append("inventory")
        return ([RID], "db.netkeiba.com") if reused else None

    async def fetch(day):
        assert not reused
        calls.append("fetch")
        return [RID], "db.netkeiba.com"

    def promote(*_a, **_k):
        assert not reused, "cache hit must not extend inventory TTL"
        calls.append("promote")
        return False

    monkeypatch.setattr(jobs, "get_finalized_race_inventory", inventory)
    monkeypatch.setattr(jobs, "fetch_race_ids", fetch)
    monkeypatch.setattr(jobs, "record_finalized_race_inventory", promote)
    asyncio.run(jobs._run_scrape_job(JOB, DAY, DAY))
    result = jobs._scrape_jobs[JOB]["result"]
    assert result["success"] is True
    assert result["existing_races_skipped"] == 1
    assert calls == (["inventory"] if reused else ["inventory", "fetch", "promote"])
    assert result["fetch_summary"]["timings"]["scope"] == "current_worker_attempt"


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("settled", [False, True])
def test_worker_binds_reuse_context_and_promotes_only_after_quality(worker, monkeypatch, force, settled):
    flags = []
    active = []
    promotions = []

    def inventory(_path, _day, **kwargs):
        assert kwargs["force_refresh"] is force
        return None

    async def fetch_list(_day):
        return [RID], "db.netkeiba.com"

    @contextmanager
    def context(path, day, *, force_refresh):
        assert path == worker and day == DAY
        active.append(True)
        flags.append(force_refresh)
        try:
            yield
        finally:
            active.pop()

    async def fetch_race(*_a, **_k):
        assert active == [True]
        record_fetch_metric("network_requests", url=f"https://db.netkeiba.com/race/{RID}/")
        return race(settled=settled)

    def promote(*_a, **kwargs):
        assert kwargs["audit"]["status"] == "complete"
        promotions.append(kwargs["source"])
        return True

    monkeypatch.setattr(jobs, "get_finalized_race_inventory", inventory)
    monkeypatch.setattr(jobs, "fetch_race_ids", fetch_list)
    monkeypatch.setattr(jobs, "acquisition_horse_reuse", context)
    monkeypatch.setattr(jobs, "fetch_acquisition_race", fetch_race)
    monkeypatch.setattr(jobs, "record_finalized_race_inventory", promote)
    asyncio.run(jobs._run_scrape_job(JOB, DAY, DAY, force_rescrape=force))
    result = jobs._scrape_jobs[JOB]["result"]
    assert flags == [force] and active == []
    assert result["success"] is settled
    assert len(promotions) == int(settled)
    assert result["fetch_summary"]["metrics"]["by_kind_race_network_requests"] == 1
    with sqlite3.connect(worker) as conn:
        assert conn.execute("SELECT count(*) FROM race_results_ultimate").fetchone()[0] == 2


def test_audit_metadata_is_atomic_sidecar_not_training_input(tmp_path):
    db = tmp_path / "races.db"
    payload = race()
    baseline = copy.deepcopy(payload)
    payload["horses"][0]["_acquisition_reuse"] = {
        "version": 1, "target_date": DAY,
        "history": {"source_race_ids": ["201901010101"], "coverage_complete": False},
    }
    original = copy.deepcopy(payload)
    assert _save_race_sqlite_only(payload, db)
    assert payload == original
    with sqlite3.connect(db) as conn:
        stored = [json.loads(row[0]) for row in conn.execute("SELECT data FROM race_results_ultimate ORDER BY id")]
        audit = conn.execute("SELECT target_date,audit_json FROM scrape_horse_reuse_audit").fetchone()
    assert stored == baseline["horses"]
    assert audit[0] == DAY
    assert json.loads(audit[1]) == payload["horses"][0]["_acquisition_reuse"]
    # A later conventional refresh clears stale evidence rather than retaining
    # an old history claim for newly written provider data.
    assert _save_race_sqlite_only(baseline, db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM scrape_horse_reuse_audit").fetchone()[0] == 0


def test_failed_evidence_write_rolls_back_provider_data_and_releases_connection(tmp_path):
    db = tmp_path / "races.db"
    original = race()
    assert _save_race_sqlite_only(original, db)
    changed = copy.deepcopy(original)
    changed["horses"][0]["horse_name"] = "changed"
    changed["horses"][0]["_acquisition_reuse"] = {"target_date": DAY, "invalid": float("nan")}
    assert not _save_race_sqlite_only(changed, db)
    with sqlite3.connect(db) as conn:
        assert [json.loads(row[0]) for row in conn.execute("SELECT data FROM race_results_ultimate ORDER BY id")] == original["horses"]
    assert _save_race_sqlite_only(original, db)


def test_reuse_evidence_does_not_change_loaded_training_frame(tmp_path):
    import pandas as pd
    from keiba_ai.db_ultimate_loader import load_ultimate_training_frame

    baseline_db, optimized_db = tmp_path / "baseline.db", tmp_path / "optimized.db"
    baseline = race()
    optimized = copy.deepcopy(baseline)
    for horse in optimized["horses"]:
        horse["_acquisition_reuse"] = {
            "target_date": DAY, "pedigree": {"reused": True},
            "history": {"fields": {"prev_race_finish": 1}, "audit_only": True},
        }
    assert _save_race_sqlite_only(baseline, baseline_db)
    assert _save_race_sqlite_only(optimized, optimized_db)
    before = load_ultimate_training_frame(baseline_db, read_only=True)
    after = load_ultimate_training_frame(optimized_db, read_only=True)
    assert not before.empty
    assert "_acquisition_reuse" not in after.columns
    pd.testing.assert_frame_equal(before, after)


def test_optional_terminal_timing_write_failure_cannot_fail_acquisition(worker, monkeypatch):
    async def implementation(*_a, **_k):
        current = jobs._scrape_jobs[JOB]
        current["status"] = "completed"
        current["result"] = {"success": True, "fetch_summary": {}, "fetch_summary_path": "unused.json"}
        jobs._persist_job_or_raise(JOB, current)

    real_persist = jobs._persist_job
    writes = []

    def persist(job_id, job):
        writes.append(job["status"])
        return real_persist(job_id, job) if len(writes) == 1 else False

    def fail_report(*_a, **_k):
        raise OSError("telemetry disk unavailable")

    monkeypatch.setattr(jobs, "_run_scrape_job_impl", implementation)
    monkeypatch.setattr(jobs, "_persist_job", persist)
    monkeypatch.setattr(jobs, "write_fetch_summary", fail_report)
    asyncio.run(jobs._run_scrape_job(JOB, DAY, DAY))
    assert writes == ["completed", "completed"]
    assert jobs._scrape_jobs[JOB]["status"] == "completed"
    assert jobs._load_job_from_db(JOB)["result"]["success"] is True
