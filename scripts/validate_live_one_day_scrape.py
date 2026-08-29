#!/usr/bin/env python3
"""Validate one finalized JRA day against live netkeiba pages from empty state.

The validator deliberately uses an empty temporary race database, HTTP cache,
resume ledger, and pedigree cache.  It never opens the application's normal
``keiba/data`` databases, so it can run while a long acquisition job is active.
Only the small JSON evidence report is retained after the temporary databases
have been deleted.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp


REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_API = REPO_ROOT / "python-api"
if str(PYTHON_API) not in sys.path:
    sys.path.insert(0, str(PYTHON_API))

from scraping import fetch_pipeline, horse  # noqa: E402
from scraping.constants import get_random_headers  # noqa: E402
from scraping.fetch_pipeline import fetch_text, get_fetch_metrics  # noqa: E402
from scraping.quality import (  # noqa: E402
    classify_race_quality,
    record_date_expectation,
    record_race_failure,
    record_race_quality,
    run_date_repair_audit,
    summarize_acquisition_quality,
)
from scraping.race import scrape_race_full  # noqa: E402
from scraping.race_list import extract_race_ids  # noqa: E402
from scraping.storage import (  # noqa: E402
    _init_sqlite_db,
    _save_race_sqlite_only,
    _save_scraped_date_sqlite,
)


ALLOWED_COMPLETE_STATES = {"available", "available_with_domain_exceptions"}
KNOWN_JRA_VALIDATION_DAYS = {
    # 2025-01-05: 1st Nakayama day 1 and 1st Chukyo day 1.
    "20250105": [
        *[f"2025060101{race_no:02d}" for race_no in range(1, 13)],
        *[f"2025070101{race_no:02d}" for race_no in range(1, 13)],
    ],
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _jra_race_ids(race_ids: list[str]) -> list[str]:
    """Keep central JRA venue codes 01..10 and preserve page order."""
    return [
        race_id
        for race_id in race_ids
        if len(race_id) == 12
        and race_id.isdigit()
        and 1 <= int(race_id[4:6]) <= 10
    ]


def _database_counts(db_path: Path) -> dict[str, Any]:
    with sqlite3.connect(str(db_path)) as conn:
        quick_check = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        counts = {
            "races": int(conn.execute("SELECT COUNT(*) FROM races_ultimate").fetchone()[0]),
            "horses": int(
                conn.execute("SELECT COUNT(*) FROM race_results_ultimate").fetchone()[0]
            ),
            "returns": int(
                conn.execute("SELECT COUNT(*) FROM return_tables_ultimate").fetchone()[0]
            ),
            "scraped_dates": int(conn.execute("SELECT COUNT(*) FROM scraped_dates").fetchone()[0]),
        }
        saved_ids = [
            str(row[0])
            for row in conn.execute("SELECT race_id FROM races_ultimate ORDER BY race_id")
        ]
        ledger = conn.execute(
            "SELECT race_count, no_race FROM scraped_dates WHERE date=?",
            (TARGET_DATE_CONTEXT.get("date", ""),),
        ).fetchone()
    return {
        "quick_check": quick_check,
        "counts": counts,
        "saved_race_ids": saved_ids,
        "date_ledger": {
            "race_count": int(ledger[0]),
            "no_race": int(ledger[1]),
        }
        if ledger
        else None,
    }


TARGET_DATE_CONTEXT: dict[str, str] = {}


async def _run(date: str, temp_dir: Path) -> dict[str, Any]:
    TARGET_DATE_CONTEXT["date"] = date
    db_path = temp_dir / "keiba_ultimate.db"
    cache_path = temp_dir / "fetch_cache.db"
    pedigree_path = temp_dir / "pedigree_cache.db"

    # These module paths are mutable specifically so a validation process can
    # be isolated.  This process has its own interpreter; the running API is
    # unaffected.
    fetch_pipeline._CACHE_DB_PATH = cache_path
    horse._PEDIGREE_DB_PATH = pedigree_path
    fetch_pipeline._init_cache_db()
    horse._init_pedigree_table()
    get_fetch_metrics(reset=True)
    _init_sqlite_db(db_path)

    started = time.monotonic()
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    timeout = aiohttp.ClientTimeout(total=90, connect=15)
    connector = aiohttp.TCPConnector(limit=4, limit_per_host=4)
    list_urls = [
        f"https://db.netkeiba.com/race/list/{date}/",
        f"https://race.netkeiba.com/top/race_list_sub.html?kaisai_date={date}",
        f"https://db.sp.netkeiba.com/race/list/{date}/",
    ]
    list_attempts: list[dict[str, Any]] = []
    per_race: list[dict[str, Any]] = []
    failures: list[str] = []

    async with aiohttp.ClientSession(
        headers=get_random_headers(), timeout=timeout, connector=connector
    ) as session:
        all_race_ids: list[str] = []
        list_fetch = None
        selected_list_url = ""
        for attempt_index, list_url in enumerate(list_urls):
            list_fetch, list_html = await fetch_text(
                session,
                list_url,
                force_refresh=True,
                cache_ttl_sec=0,
                resume_key=f"live-validation:race-list:{date}:{attempt_index}",
                min_interval_sec=1.0,
                max_retries=3,
                retry_statuses={429, 500, 503},
                retry_base_sec=2.0,
                retry_jitter_sec=0.6,
                circuit_threshold=3,
                circuit_cooldown_sec=120.0,
            )
            parsed_ids = extract_race_ids(list_html) if list_fetch.status == 200 else []
            list_attempts.append(
                {
                    "url": list_url,
                    "http_status": list_fetch.status,
                    "source": list_fetch.source,
                    "parsed_race_count": len(parsed_ids),
                }
            )
            parsed_jra_ids = _jra_race_ids(parsed_ids)
            if parsed_jra_ids and len(parsed_jra_ids) % 12 == 0:
                all_race_ids = parsed_ids
                selected_list_url = list_url
                break
        assert list_fetch is not None
        expected_ids = _jra_race_ids(all_race_ids)
        expectation_source = selected_list_url
        if not expected_ids and date in KNOWN_JRA_VALIDATION_DAYS:
            expected_ids = list(KNOWN_JRA_VALIDATION_DAYS[date])
            expectation_source = "immutable_validation_manifest"
        if not selected_list_url:
            if not expected_ids:
                failures.append(
                    "race_list_unavailable:"
                    + ",".join(str(item["http_status"]) for item in list_attempts)
                )
        if not expected_ids:
            failures.append("jra_race_ids_empty")
        if len(expected_ids) % 12 != 0:
            failures.append(f"unexpected_jra_race_count:{len(expected_ids)}")

        record_date_expectation(db_path, date, expected_ids)
        for index, race_id in enumerate(expected_ids, start=1):
            print(
                json.dumps(
                    {
                        "event": "race_start",
                        "index": index,
                        "total": len(expected_ids),
                        "race_id": race_id,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            try:
                race_data = await scrape_race_full(
                    session,
                    race_id,
                    date_hint=date,
                    quick_mode=True,
                    force_refresh=True,
                )
                report = classify_race_quality(race_data)
                if race_data is None:
                    raise RuntimeError("empty_race_payload")
                record_race_quality(
                    db_path,
                    race_date=date,
                    race_id=race_id,
                    report=report,
                    source_status="live_network_http_200",
                    race_data=race_data,
                )
                stored = report.valid_for_storage and _save_race_sqlite_only(
                    race_data, db_path, overwrite=True
                )
                strict_states = {
                    field: state
                    for field, state in report.field_states.items()
                    if field
                    in {
                        "odds",
                        "popularity",
                        "horse_weight",
                        "finish_position",
                        "finish_time",
                        "pedigree",
                    }
                    and state not in ALLOWED_COMPLETE_STATES
                }
                race_pass = bool(
                    stored
                    and report.valid_for_date_completion
                    and not report.required_errors
                    and not strict_states
                )
                if not race_pass:
                    failures.append(
                        f"{race_id}:quality:{','.join(report.required_errors) or strict_states}"
                    )
                per_race.append(
                    {
                        "race_id": race_id,
                        "horse_count": report.horse_count,
                        "lifecycle": report.lifecycle,
                        "required_errors": list(report.required_errors),
                        "field_states": report.field_states,
                        "stored": bool(stored),
                        "pass": race_pass,
                    }
                )
            except Exception as exc:
                reason = f"{type(exc).__name__}:{exc}"
                record_race_failure(
                    db_path,
                    race_date=date,
                    race_id=race_id,
                    reason=reason,
                    source_status="live_network_exception",
                )
                failures.append(f"{race_id}:{reason}")
                per_race.append({"race_id": race_id, "pass": False, "error": reason})

    audit = run_date_repair_audit(db_path, date, expected_ids)
    if audit["status"] == "complete":
        _save_scraped_date_sqlite(db_path, date, len(expected_ids))
    database = _database_counts(db_path)
    acquisition = summarize_acquisition_quality(db_path, [date])
    metrics = get_fetch_metrics()

    if database["quick_check"] != "ok":
        failures.append(f"sqlite_quick_check:{database['quick_check']}")
    if database["saved_race_ids"] != sorted(expected_ids):
        failures.append("saved_race_ids_do_not_match_expected")
    if not database["date_ledger"] or database["date_ledger"]["race_count"] != len(expected_ids):
        failures.append("scraped_dates_ledger_incomplete")
    if not acquisition["quality_complete"]:
        failures.append("quality_date_incomplete")
    if metrics.get("cache_hits", 0) or metrics.get("resume_hits", 0):
        failures.append("non_network_cache_or_resume_reuse_detected")

    return {
        "schema_version": 1,
        "validation": "live_one_day_empty_state",
        "date": date,
        "scope": "JRA venue codes 01-10",
        "source_url": selected_list_url,
        "race_id_expectation_source": expectation_source,
        "source_attempts": list_attempts,
        "source_fetched_at_utc": fetched_at,
        "empty_state": {
            "race_db": True,
            "http_cache": True,
            "resume_ledger": True,
            "pedigree_cache": True,
            "application_databases_touched": False,
        },
        "race_list_http_status": list_fetch.status,
        "race_list_source": list_fetch.source,
        "all_source_race_count": len(all_race_ids),
        "expected_jra_race_ids": expected_ids,
        "race_results": per_race,
        "date_audit": audit,
        "acquisition_quality": acquisition,
        "database": database,
        "fetch_metrics": metrics,
        "database_sha256": _sha256(db_path),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "failures": sorted(set(failures)),
        "passed": not failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--date",
        default="20200331",
        help="Finalized YYYYMMDD date. 20200331 is a compact one-venue JRA day.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPO_ROOT / "reports" / "scrape-validation" / "live-one-day-20200331.json",
    )
    args = parser.parse_args()
    try:
        datetime.strptime(args.date, "%Y%m%d")
    except ValueError:
        parser.error("--date must be a valid YYYYMMDD date")

    # Some Windows SQLite builds keep a file handle alive until interpreter
    # teardown. A cleanup-only lock must not hide the actual validation result.
    with tempfile.TemporaryDirectory(
        prefix=f"keiba-live-{args.date}-", ignore_cleanup_errors=True
    ) as raw_temp:
        report = asyncio.run(_run(args.date, Path(raw_temp)))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False), flush=True)
    print(f"evidence_report={args.report.resolve()}", flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
