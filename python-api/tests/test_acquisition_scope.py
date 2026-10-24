from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from scraping import acquisition_scope as scope
from scraping import jobs, jra_listing
from scraping.quality import init_acquisition_quality_db, record_date_expectation
from scraping.storage import _init_sqlite_db, _save_race_sqlite_only
from scraping.server_batch import _execute_months, batch_progress


DAY = "20200104"
CENTRAL = "202001010101"
LOCAL = "202030010101"
JOB = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def payload(race_id, day=DAY, settled=True):
    return {
        "race_info": {"race_id": race_id, "date": day, "venue": "Tokyo", "distance": 1600, "num_horses": 2},
        "horses": [{"race_id": race_id, "horse_id": f"201600000{number}", "horse_number": number,
                    "horse_name": f"Horse {number}", "finish_position": number if settled else None,
                    "finish_time": "1:32.0" if settled else None, "odds": 3.0, "popularity": number,
                    "weight_kg": 470, "sire": "Sire", "dam": "Dam", "damsire": "Damsire"}
                   for number in (1, 2)],
        "return_tables": [{"race_id": race_id, "bet_type": "単勝", "combinations": "1", "payout": 300}]
        if settled else [],
    }


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "races.db"
    _init_sqlite_db(path)
    init_acquisition_quality_db(path)
    scope.init_scoped_date_ledger(path)
    return path


@pytest.mark.parametrize("race_id,allowed", [("202001010101", True), ("202010010101", True),
                                           ("202000010101", False), (LOCAL, False), ("bad", False),
                                           ("202001010100", False), ("202001010113", False),
                                           ("202001010199", False)])
def test_identity_venue_range(race_id, allowed):
    assert scope.is_jra_race_id(race_id, DAY) is allowed
    assert not scope.is_jra_race_id(race_id, "20190104")


def test_filter_validates_year_and_preserves_legacy_all():
    assert scope.filter_scoped_race_ids([LOCAL, CENTRAL, CENTRAL], DAY, "jra") == [CENTRAL]
    assert scope.filter_scoped_race_ids([LOCAL, "legacy"], DAY, "all") == [LOCAL, "legacy"]
    with pytest.raises(ValueError, match="identity"):
        scope.filter_scoped_race_ids([CENTRAL, "201901010101"], DAY, "jra")
    with pytest.raises(ValueError, match="identity"):
        scope.filter_scoped_race_ids([CENTRAL, "bad"], DAY, "jra")
    with pytest.raises(ValueError, match="identity"):
        scope.filter_scoped_race_ids([CENTRAL, "202001010113"], DAY, "jra")


@pytest.mark.parametrize("invalid", [None, "", "nar", "ALL", [], {}, True])
def test_scope_is_fail_closed(invalid):
    with pytest.raises(ValueError):
        scope.normalize_race_scope(invalid)


def test_save_guard_checks_provider_id_date_and_runner_identity():
    source = payload(CENTRAL)
    scope.validate_scoped_payload(source, CENTRAL, DAY, "jra")
    changed = copy.deepcopy(source)
    changed["race_info"]["race_id"] = LOCAL
    with pytest.raises(ValueError, match="race_id_mismatch"):
        scope.validate_scoped_payload(changed, CENTRAL, DAY, "jra")
    changed = copy.deepcopy(source)
    changed["race_info"]["date"] = "20200105"
    with pytest.raises(ValueError, match="date_mismatch"):
        scope.validate_scoped_payload(changed, CENTRAL, DAY, "jra")
    changed = copy.deepcopy(source)
    changed["horses"][0]["race_id"] = LOCAL
    with pytest.raises(ValueError, match="runner"):
        scope.validate_scoped_payload(changed, CENTRAL, DAY, "jra")


def test_central_complete_never_overwrites_all_venue_expectation(database):
    record_date_expectation(database, DAY, [CENTRAL, LOCAL])
    assert _save_race_sqlite_only(payload(CENTRAL), database)
    with sqlite3.connect(database) as connection:
        before = connection.execute("SELECT * FROM scrape_date_completeness").fetchall()
    result = scope.hydrate_scoped_quality(database, DAY, [CENTRAL])
    assert result["status"] == "complete" and result["race_scope"] == "jra"
    assert scope.summarize_scoped_quality(database, [DAY])["quality_complete"]
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT * FROM scrape_date_completeness").fetchall() == before
        assert connection.execute("SELECT count(*) FROM scraped_dates").fetchone()[0] == 0


def test_scoped_no_race_and_invalidation_are_independent(database):
    record_date_expectation(database, DAY, [LOCAL])
    with sqlite3.connect(database) as connection:
        before = connection.execute("SELECT * FROM scrape_date_completeness").fetchall()
    scope.record_scoped_no_race_dates(database, [DAY])
    assert DAY in scope.completed_scoped_no_race_dates(database, 3600)
    assert scope.invalidate_scoped_no_race_dates(database, [DAY]) == 1
    assert DAY not in scope.completed_scoped_no_race_dates(database, 3600)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT * FROM scrape_date_completeness").fetchall() == before


def test_scoped_missing_or_malformed_stored_result_never_passes(database):
    changed = payload(CENTRAL, day="20200105")
    assert _save_race_sqlite_only(changed, database)
    result = scope.hydrate_scoped_quality(database, DAY, [CENTRAL])
    assert result["missing_race_ids"] == [CENTRAL] and result["status"] == "partial"
    with pytest.raises(ValueError, match="non_jra"):
        scope.hydrate_scoped_quality(database, DAY, [LOCAL])


@pytest.fixture
def worker(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "__file__", str(tmp_path / "python-api/scraping/jobs.py"))
    monkeypatch.setattr(jobs, "_JOBS_DB_PATH", tmp_path / "children.db")
    monkeypatch.setattr(jobs, "_scrape_jobs", {JOB: {
        "status": "queued", "progress": {}, "result": None, "error": None,
        "owner_user_id": "owner", "request_hash": "scope-request",
    }})
    jobs._init_jobs_db()
    db = tmp_path / "keiba/data/keiba_ultimate.db"
    _init_sqlite_db(db)
    init_acquisition_quality_db(db)
    record_date_expectation(db, DAY, [CENTRAL, LOCAL])
    monkeypatch.setattr(jobs, "write_fetch_summary", lambda *_a, **_k: tmp_path / "unused.json")
    monkeypatch.setattr(jobs, "_resource_capacity_available", lambda *_: True)
    monkeypatch.setattr(jobs, "get_finalized_race_inventory", lambda *_a, **_k: None)
    monkeypatch.setattr(jobs, "record_finalized_race_inventory", lambda *_a, **_k: pytest.fail("all promotion in JRA job"))

    async def calendar(*_a, **_k):
        return [DAY]

    monkeypatch.setattr(jobs, "_build_race_dates_from_calendar", calendar)
    return db


def test_jra_worker_filters_nar_and_keeps_all_ledger_identical(worker, monkeypatch):
    fetched, inventory_scopes = [], []
    with sqlite3.connect(worker) as connection:
        before = connection.execute("SELECT * FROM scrape_date_completeness").fetchall()

    def inventory(_path, _day, **kwargs):
        inventory_scopes.append(kwargs["scope"])
        return None

    async def listing(*_a, **_k):
        return [LOCAL, CENTRAL], "db.netkeiba.com"

    async def fetch(_session, race_id, **kwargs):
        fetched.append(race_id)
        return payload(race_id)

    monkeypatch.setattr(jobs, "get_finalized_race_inventory", inventory)
    monkeypatch.setattr(jra_listing, "fetch_jra_race_ids", listing)
    monkeypatch.setattr(jobs, "fetch_acquisition_race", fetch)
    asyncio.run(jobs._run_scrape_job(JOB, DAY, DAY, race_scope="jra"))
    result = jobs._load_job_from_db(JOB)["result"]
    assert result["success"] and result["race_scope"] == "jra" and fetched == [CENTRAL]
    assert inventory_scopes == ["jra"]
    with sqlite3.connect(worker) as connection:
        assert connection.execute("SELECT * FROM scrape_date_completeness").fetchall() == before
        assert connection.execute("SELECT count(*) FROM races_ultimate WHERE race_id=?", (LOCAL,)).fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM scraped_dates").fetchone()[0] == 0


def test_jra_worker_rejects_wrong_payload_before_storage(worker, monkeypatch):
    async def listing(*_a, **_k):
        return [CENTRAL], "db.netkeiba.com"

    async def fetch(*_a, **_k):
        return payload(LOCAL)

    monkeypatch.setattr(jra_listing, "fetch_jra_race_ids", listing)
    monkeypatch.setattr(jobs, "fetch_acquisition_race", fetch)
    asyncio.run(jobs._run_scrape_job(JOB, DAY, DAY, race_scope="jra"))
    result = jobs._load_job_from_db(JOB)["result"]
    assert not result["success"] and result["races_collected"] == 0
    with sqlite3.connect(worker) as connection:
        assert connection.execute("SELECT count(*) FROM races_ultimate").fetchone()[0] == 0


def test_nar_only_existing_day_does_not_expand_jra_calendar_dates(worker, monkeypatch):
    assert _save_race_sqlite_only(payload(LOCAL), worker)
    assert jobs._existing_race_dates(worker, [DAY], "jra") == set()
    assert jobs._existing_race_dates(worker, [DAY], "all") == {DAY}


def test_legacy_all_completed_progress_cannot_skip_jra_job(worker, monkeypatch):
    jobs._scrape_jobs[JOB]["progress"] = {"completed_dates": [DAY], "saved_races": 99}
    calls = []

    async def listing(*_a, **_k):
        calls.append(DAY)
        return [CENTRAL], "db.netkeiba.com"

    async def fetch(*_a, **_k):
        return payload(CENTRAL)

    monkeypatch.setattr(jra_listing, "fetch_jra_race_ids", listing)
    monkeypatch.setattr(jobs, "fetch_acquisition_race", fetch)
    asyncio.run(jobs._run_scrape_job(JOB, DAY, DAY, race_scope="jra"))
    assert calls == [DAY]
    assert jobs._load_job_from_db(JOB)["result"]["races_collected"] == 1


def test_server_batch_keeps_scope_in_each_child_request_and_completed_progress(monkeypatch):
    payload = {"start_date": "20260101", "end_date": "20260228", "force_rescrape": False,
               "dry_run": False, "race_scope": "jra"}
    claim = SimpleNamespace(request_payload=payload, job_id=JOB, request_hash="bound-jra",
                            owner_user_id="owner", fencing_token=1, idempotency_key="effect-jra")
    calls, projected = [], []

    class Checkpoint:
        def completed(self):
            return {}

        def cancel_requested(self):
            return False

        def save(self, progress, **kwargs):
            projected.append(progress)

        def timings(self):
            return {}

    monkeypatch.setattr(jobs, "_scrape_jobs", {})
    monkeypatch.setattr(jobs, "get_job", lambda *_a, **_k: None)

    async def runner(child_id, start, end, force, dry_run, *, cancel_requested, race_scope):
        assert race_scope == "jra" and not cancel_requested()
        child = jobs._scrape_jobs[child_id]
        assert child["request"]["race_scope"] == "jra"
        calls.append(start)
        child["status"] = "completed"
        child["result"] = {"success": True, "races_collected": 1, "saved_horses": 2, "elapsed_time": 1,
                           "race_scope": "jra", "fetch_summary": {"metrics": {}}}

    monkeypatch.setattr(jobs, "_run_scrape_job", runner)
    result = asyncio.run(_execute_months(claim, Checkpoint(), threading.Event()))
    assert calls == ["20260101", "20260201"]
    assert all(progress["race_scope"] == "jra" for progress in projected)
    assert result.result["race_scope"] == "jra"
    assert result.result["fetch_summary"]["race_scope"] == "jra"
    assert batch_progress([("20260101", "20260131")], {})["race_scope"] == "all"
