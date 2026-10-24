#!/usr/bin/env python3
"""Matched offline job-ingest benchmark: no HTTP, no application DB writes.

This measures race-task orchestration, saved-payload JSON decode, real sandbox
SQLite writes and unchanged quality/date checkpoints. It does NOT measure HTML
parsing, HTTP latency/pacing, horse-information reuse or whole live acquisition.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from contextlib import closing
import gc
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import statistics
import sys
import tempfile
import time
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python-api"))
from scraping import jobs  # noqa: E402
from scraping.quality import classify_race_quality  # noqa: E402
from scraping.storage import _init_sqlite_db  # noqa: E402


def digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def corpus(db_path: Path, per_year: int = 8):
    """Freeze supported complete races from several eras using one RO snapshot."""
    payloads = []
    connection = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    with connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        for year in (2016, 2020, 2025):
            candidates = connection.execute(
                "SELECT r.race_id,r.data FROM races_ultimate r "
                "JOIN scrape_race_acquisition_state q ON q.race_id=r.race_id "
                "WHERE r.race_id>=? AND r.race_id<? AND q.quality_status='complete' "
                "ORDER BY r.race_id LIMIT ?",
                (str(year), str(year + 1), per_year * 20),
            ).fetchall()
            kept = 0
            for race_id, raw in candidates:
                info = json.loads(raw)
                horses = [json.loads(row[0]) for row in connection.execute(
                    "SELECT data FROM race_results_ultimate WHERE race_id=? ORDER BY id", (race_id,),
                )]
                returns = [dict(zip(
                    ("race_id", "bet_type", "combinations", "payout", "popularity"), row,
                )) for row in connection.execute(
                    "SELECT race_id,bet_type,combinations,payout,popularity "
                    "FROM return_tables_ultimate WHERE race_id=? ORDER BY id", (race_id,),
                )]
                payload = {"race_info": info, "horses": horses, "return_tables": returns}
                if classify_race_quality(payload).valid_for_date_completion:
                    payloads.append(payload)
                    kept += 1
                    if kept == per_year:
                        break
            if kept != per_year:
                raise ValueError(f"Insufficient complete frozen fixtures for {year}: {kept}")
    connection.close()
    return sorted(payloads, key=lambda item: item["race_info"]["race_id"])


def read_back(path: Path, ids: list[str]):
    payloads = []
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        for race_id in sorted(ids):
            info = json.loads(connection.execute(
                "SELECT data FROM races_ultimate WHERE race_id=?", (race_id,),
            ).fetchone()[0])
            horses = [json.loads(row[0]) for row in connection.execute(
                "SELECT data FROM race_results_ultimate WHERE race_id=? ORDER BY id", (race_id,),
            )]
            returns = [dict(zip(
                ("race_id", "bet_type", "combinations", "payout", "popularity"), row,
            )) for row in connection.execute(
                "SELECT race_id,bet_type,combinations,payout,popularity "
                "FROM return_tables_ultimate WHERE race_id=? ORDER BY id", (race_id,),
            )]
            payloads.append({"race_info": info, "horses": horses, "return_tables": returns})
    return payloads


async def run_one(payloads, concurrency: int):
    by_day = defaultdict(list)
    frozen = {}
    for payload in payloads:
        race_id = payload["race_info"]["race_id"]
        day = str(payload["race_info"]["date"]).replace("-", "").replace("/", "")
        by_day[day].append(race_id)
        frozen[race_id] = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    days = sorted(by_day)
    active = 0
    max_active = 0

    async def calendar(*args, **kwargs):
        return days

    async def race_list(day, **kwargs):
        return list(by_day[day]), "db.netkeiba.com"

    async def fetch(_session, race_id, **kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        try:
            # The same actual saved provider fields are decoded in both modes.
            return await asyncio.to_thread(json.loads, frozen[race_id])
        finally:
            active -= 1

    def forbidden(*args, **kwargs):
        raise AssertionError("Live network is forbidden in this benchmark")

    with tempfile.TemporaryDirectory(prefix="keiba-race-concurrency-") as temporary:
        sandbox = Path(temporary)
        path = sandbox / "keiba/data/keiba_ultimate.db"
        _init_sqlite_db(path)
        job_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        store = {job_id: {"status": "queued", "progress": {}, "result": None, "error": None,
                          "owner_user_id": "offline-benchmark", "request_hash": "same-corpus"}}
        with (
            patch.object(jobs, "__file__", str(sandbox / "python-api/scraping/jobs.py")),
            patch.object(jobs, "_JOBS_DB_PATH", sandbox / "jobs.db"),
            patch.object(jobs, "_scrape_jobs", store),
            patch.object(jobs, "build_bounded_scrape_dates", lambda *_: days),
            patch.object(jobs, "_build_race_dates_from_calendar", calendar),
            patch.object(jobs, "fetch_race_ids", race_list),
            patch.object(jobs, "fetch_acquisition_race", fetch),
            patch.object(jobs, "get_finalized_race_inventory", lambda *_a, **_k: None),
            patch.object(jobs, "record_finalized_race_inventory", lambda *_a, **_k: False),
            patch.object(jobs, "write_fetch_summary", lambda *_a, **_k: sandbox / "unused.json"),
            patch.object(jobs, "_resource_capacity_available", lambda *_: True),
            patch.dict(os.environ, {"SCRAPE_RACE_CONCURRENCY": str(concurrency)}),
            patch.object(socket, "create_connection", forbidden),
            patch.object(socket.socket, "connect", forbidden),
            patch.object(socket.socket, "connect_ex", forbidden),
        ):
            jobs._init_jobs_db()
            started = time.perf_counter()
            await jobs._run_scrape_job(job_id, days[0], days[-1])
            elapsed = time.perf_counter() - started
            result = store[job_id]["result"]
            saved = read_back(path, sorted(frozen))
            metrics = result["fetch_summary"]["metrics"]
            if metrics.get("network_requests", 0):
                raise AssertionError("Benchmark performed an external request")
            if not result["success"] or result["races_collected"] != len(payloads):
                raise AssertionError("Benchmark acquisition/quality failed")
            # The unchanged legacy quality helpers use transaction context
            # managers; collect their released connection cycles before Windows
            # removes this isolated sandbox. Cleanup is outside timed work.
            await asyncio.to_thread(gc.collect)
            return {
                "race_task_concurrency": concurrency, "observed_max_fetch_tasks": max_active,
                "local_job_ingest_wall_sec": elapsed, "saved_races": result["races_collected"],
                "saved_horses": result["saved_horses"], "payload_sha256": digest(saved),
                "quality_sha256": digest([classify_race_quality(item).as_dict() for item in saved]),
                "quality_complete": result["fetch_summary"]["quality_audit"]["quality_complete"],
                "external_http_requests": 0,
            }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "keiba/data/keiba_ultimate.db")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--per-year", type=int, default=8)
    args = parser.parse_args()
    if args.output.exists() or not 1 <= args.repeats <= 10 or not 1 <= args.per_year <= 24:
        raise ValueError("Require a new output and bounded workload/repetitions")
    fixtures = corpus(args.db, args.per_year)
    fixture_sha = digest(fixtures)
    fixture_quality_sha = digest([classify_race_quality(item).as_dict() for item in fixtures])
    runs = []
    for repeat in range(args.repeats):
        for concurrency in ((1, 3) if repeat % 2 == 0 else (3, 1)):
            result = asyncio.run(run_one(fixtures, concurrency))
            result["repeat"] = repeat
            runs.append(result)
    equal = all(run["payload_sha256"] == fixture_sha
                and run["quality_sha256"] == fixture_quality_sha for run in runs)
    if not equal:
        raise AssertionError("Serial/parallel full payload or quality changed")
    medians = {str(capacity): statistics.median(
        run["local_job_ingest_wall_sec"] for run in runs if run["race_task_concurrency"] == capacity
    ) for capacity in (1, 3)}
    result = {
        "schema": "matched-offline-race-job-ingest-v1",
        "scope": "saved_payload_decode_sqlite_save_quality_checkpoint_only",
        "race_ids": [item["race_info"]["race_id"] for item in fixtures],
        "fixture_payload_sha256": fixture_sha, "fixture_quality_sha256": fixture_quality_sha,
        "identical_all_provider_fields_and_quality": equal, "external_http_requests": 0,
        "median_local_job_ingest_sec": medians,
        "local_job_ingest_speedup": medians["1"] / medians["3"],
        "whole_live_acquisition_speedup": None, "horse_reuse_speedup": None,
        "limitations": ["No live HTTP, HTML parsing, provider latency or pacing was measured.",
                        "Actual DB source was read-only; every destination was a disposable sandbox.",
                        "Concurrency changes neither provider rate nor required fields/quality gates."],
        "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in (Path(__file__), ROOT / "python-api/scraping/jobs.py",
                                       ROOT / "python-api/scraping/bounded_work.py")},
        "runs": runs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "identical_all_provider_fields_and_quality", "median_local_job_ingest_sec",
        "local_job_ingest_speedup", "whole_live_acquisition_speedup", "external_http_requests",
    )}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
