from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
import threading

import pytest

from scraping.bounded_work import drain_acquisition_batch, race_task_concurrency
from scraping import jobs
from scraping.fetch_pipeline import FetchAccessBlocked
from scraping.quality import classify_race_quality
from scraping.storage import _init_sqlite_db, _save_race_sqlite_only


@pytest.mark.parametrize("raw,expected", [
    (None, 3), ("1", 1), ("2", 2), ("4", 4), ("999999999", 4),
    ("0", 1), ("-1", 1), ("bad", 3), ("2.5", 3), ("", 3),
])
def test_local_worker_capacity_is_sanitized_and_bounded(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("SCRAPE_RACE_CONCURRENCY", raising=False)
    else:
        monkeypatch.setenv("SCRAPE_RACE_CONCURRENCY", raw)
    assert race_task_concurrency() == expected


def test_batch_starts_in_parallel_and_retains_input_order():
    async def scenario():
        entered = set()
        all_entered = asyncio.Event()

        async def work(index):
            entered.add(index)
            if len(entered) == 3:
                all_entered.set()
            await asyncio.wait_for(all_entered.wait(), 1)
            return index

        result = await drain_acquisition_batch(
            (work(index) for index in range(3)), on_stop=lambda: None,
        )
        assert result == [0, 1, 2]

    asyncio.run(scenario())


def test_access_failure_drains_other_writer_and_preserves_original_reason():
    class Blocked(RuntimeError):
        pass

    class CooperativelyStopped(RuntimeError):
        pass

    async def scenario():
        stop = asyncio.Event()
        writer_started = asyncio.Event()
        writer_complete = False
        quality_complete = False

        async def writer():
            nonlocal writer_complete, quality_complete
            writer_started.set()
            await stop.wait()
            await asyncio.sleep(0)
            writer_complete = True
            quality_complete = True
            raise CooperativelyStopped("writer reached safe checkpoint")

        async def blocked():
            await writer_started.wait()
            raise Blocked("provider forbidden")

        # Sibling is first in input order, but the access block is the cause.
        with pytest.raises(Blocked, match="provider forbidden"):
            await drain_acquisition_batch([writer(), blocked()], on_stop=stop.set)
        assert writer_complete and quality_complete

    asyncio.run(scenario())


def test_external_repeated_cancel_does_not_orphan_to_thread_writer():
    async def scenario():
        writer_started = threading.Event()
        release_writer = threading.Event()
        writer_complete = threading.Event()
        stop = asyncio.Event()
        quality_complete = False

        def write():
            writer_started.set()
            assert release_writer.wait(2)
            writer_complete.set()

        async def work():
            nonlocal quality_complete
            await asyncio.to_thread(write)
            quality_complete = True

        task = asyncio.create_task(drain_acquisition_batch([work()], on_stop=stop.set))
        assert await asyncio.to_thread(writer_started.wait, 1)
        task.cancel()
        await stop.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release_writer.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert writer_complete.is_set() and quality_complete

    asyncio.run(scenario())


def test_stop_prevents_sibling_from_starting_a_new_write():
    async def scenario():
        stop = asyncio.Event()
        after_fetch = asyncio.Event()
        writes = []

        async def fetching():
            after_fetch.set()
            await stop.wait()
            if stop.is_set():
                raise RuntimeError("cancelled before write")
            writes.append("unsafe")

        async def failed():
            await after_fetch.wait()
            raise ValueError("first failure")

        with pytest.raises(ValueError, match="first failure"):
            await drain_acquisition_batch([fetching(), failed()], on_stop=stop.set)
        assert writes == []

    asyncio.run(scenario())


def test_empty_batch_is_safe():
    assert asyncio.run(drain_acquisition_batch([], on_stop=lambda: None)) == []


@pytest.fixture
def isolated_job(tmp_path, monkeypatch):
    day = "20200104"
    job_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    db = tmp_path / "keiba/data/keiba_ultimate.db"
    monkeypatch.setattr(jobs, "__file__", str(tmp_path / "python-api/scraping/jobs.py"))
    monkeypatch.setattr(jobs, "_JOBS_DB_PATH", tmp_path / "jobs.db")
    monkeypatch.setattr(jobs, "_scrape_jobs", {job_id: {
        "status": "queued", "progress": {}, "result": None, "error": None,
        "owner_user_id": "owner", "request_hash": "offline-fixture",
    }})
    monkeypatch.setenv("SCRAPE_RACE_CONCURRENCY", "3")
    monkeypatch.setattr(jobs, "write_fetch_summary", lambda *_a, **_k: tmp_path / "unused.json")
    monkeypatch.setattr(jobs, "_resource_capacity_available", lambda *_: True)
    monkeypatch.setattr(jobs, "get_finalized_race_inventory", lambda *_a, **_k: None)
    monkeypatch.setattr(jobs, "record_finalized_race_inventory", lambda *_a, **_k: False)
    jobs._init_jobs_db()
    _init_sqlite_db(db)
    payloads = {}
    for index in range(1, 5):
        race_id = f"2020010101{index:02d}"
        payloads[race_id] = {
            "race_info": {"race_id": race_id, "date": day, "venue": "Tokyo", "distance": 1600, "num_horses": 2},
            "horses": [{
                "race_id": race_id, "horse_id": f"20160000{index}{number}",
                "horse_number": number, "horse_name": f"Horse {index}-{number}",
                "finish_position": number, "finish_time": f"1:3{number}.0", "odds": 3.0,
                "popularity": number, "weight_kg": 470, "sire": "Sire", "dam": "Dam",
                "damsire": "Damsire", "optional_saved_field": f"preserved-{index}-{number}",
            } for number in (1, 2)],
            "return_tables": [{"race_id": race_id, "bet_type": "単勝", "combinations": "1", "payout": 300}],
        }

    async def calendar(*_a, **_k):
        return [day]

    async def race_list(*_a, **_k):
        return list(payloads), "db.netkeiba.com"

    monkeypatch.setattr(jobs, "_build_race_dates_from_calendar", calendar)
    monkeypatch.setattr(jobs, "fetch_race_ids", race_list)
    return job_id, day, db, payloads


def test_normal_job_overlaps_three_races_and_keeps_full_payload_quality(isolated_job, monkeypatch):
    job_id, day, db, payloads = isolated_job

    async def scenario():
        entered = []
        first_batch = asyncio.Event()

        async def fetch(_session, race_id, **kwargs):
            entered.append(race_id)
            if len(entered) == 3:
                first_batch.set()
            await asyncio.wait_for(first_batch.wait(), 1)
            return copy.deepcopy(payloads[race_id])

        monkeypatch.setattr(jobs, "fetch_acquisition_race", fetch)
        await jobs._run_scrape_job(job_id, day, day)
        assert entered == list(payloads)

    asyncio.run(scenario())
    job = jobs._load_job_from_db(job_id)
    assert job["status"] == "completed" and job["result"]["success"]
    assert job["result"]["races_collected"] == 4 and job["result"]["saved_horses"] == 8
    assert job["progress"]["current_date_processed_races"] == 4
    assert job["progress"]["race_task_concurrency"] == 3
    assert job["progress"]["max_concurrent_http_requests"] == 1
    assert job["result"]["fetch_summary"]["rate_limit_policy"]["max_concurrent_requests"] == 1
    with sqlite3.connect(db) as connection:
        for race_id, original in payloads.items():
            saved = {
                "race_info": json.loads(connection.execute(
                    "SELECT data FROM races_ultimate WHERE race_id=?", (race_id,),
                ).fetchone()[0]),
                "horses": [json.loads(row[0]) for row in connection.execute(
                    "SELECT data FROM race_results_ultimate WHERE race_id=? ORDER BY id", (race_id,),
                )],
                "return_tables": original["return_tables"],
            }
            assert saved == original
            assert classify_race_quality(saved).as_dict() == classify_race_quality(original).as_dict()


def test_normal_job_blocked_error_waits_for_writer_quality_and_never_next_batch(isolated_job, monkeypatch):
    job_id, day, db, payloads = isolated_job
    first, second, third, fourth = list(payloads)
    writer_entered = threading.Event()
    release_writer = threading.Event()
    blocked = asyncio.Event()
    fetched = []

    def writer(data, path, *args, **kwargs):
        if data["race_info"]["race_id"] == first:
            writer_entered.set()
            assert release_writer.wait(2)
        return _save_race_sqlite_only(data, path, *args, **kwargs)

    monkeypatch.setattr(jobs, "_save_race_sqlite_only", writer)

    async def fetch(_session, race_id, **kwargs):
        fetched.append(race_id)
        if race_id == second:
            assert await asyncio.to_thread(writer_entered.wait, 1)
            blocked.set()
            raise FetchAccessBlocked("http-403")
        if race_id == third:
            await blocked.wait()
        return copy.deepcopy(payloads[race_id])

    monkeypatch.setattr(jobs, "fetch_acquisition_race", fetch)

    async def scenario():
        task = asyncio.create_task(jobs._run_scrape_job(job_id, day, day))
        await asyncio.wait_for(blocked.wait(), 1)
        await asyncio.sleep(0)
        assert not task.done()
        release_writer.set()
        await task

    asyncio.run(scenario())
    job = jobs._load_job_from_db(job_id)
    assert job["status"] == "error"
    assert job["progress"]["fetch_block_reason"] == "http-403"
    assert fourth not in fetched
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT quality_status FROM scrape_race_acquisition_state WHERE race_id=?", (first,),
        ).fetchone() == ("complete",)
        assert connection.execute("SELECT count(*) FROM race_results_ultimate WHERE race_id=?", (first,)).fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM race_results_ultimate WHERE race_id=?", (third,)).fetchone()[0] == 0
