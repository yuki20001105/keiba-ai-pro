"""Central-only acquisition gates and an independent scoped date ledger.

Central completeness is never promoted into the legacy all-venue date ledger.
The ordinary per-race storage/quality rules remain unchanged.
"""
from __future__ import annotations

from collections.abc import Iterable
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3

from scraping.quality import classify_race_quality, queue_repair, record_race_quality


def normalize_race_scope(value: object = "all") -> str:
    if not isinstance(value, str) or value not in {"all", "jra"}:
        raise ValueError("race_scope must be all or jra")
    return str(value)


def normalized_date(value: object) -> str | None:
    text = str(value or "").strip().replace("-", "").replace("/", "")
    try:
        return text if datetime.strptime(text, "%Y%m%d").strftime("%Y%m%d") == text else None
    except (TypeError, ValueError):
        return None


def is_jra_race_id(value: object, race_date: str | None = None) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"\d{12}", value):
        return False
    return (1 <= int(value[4:6]) <= 10 and 1 <= int(value[10:12]) <= 12
            and (race_date is None or value[:4] == race_date[:4]))


def filter_scoped_race_ids(values: Iterable[str], race_date: str, race_scope: str) -> list[str]:
    scope = normalize_race_scope(race_scope)
    ids = list(values)
    if scope == "all":
        return ids
    if normalized_date(race_date) != race_date:
        raise ValueError("scoped_race_date_invalid")
    if any(not isinstance(item, str) or not re.fullmatch(r"\d{12}", item)
           or item[:4] != race_date[:4]
           or (1 <= int(item[4:6]) <= 10 and not 1 <= int(item[10:12]) <= 12) for item in ids):
        raise ValueError("scoped_race_list_identity_invalid")
    return list(dict.fromkeys(item for item in ids if is_jra_race_id(item, race_date)))


def validate_scoped_payload(payload: dict | None, race_id: str, race_date: str, race_scope: str) -> None:
    if normalize_race_scope(race_scope) == "all":
        return
    if not is_jra_race_id(race_id, race_date):
        raise ValueError("scoped_race_id_not_jra")
    info = (payload or {}).get("race_info")
    if not isinstance(info, dict) or info.get("race_id") != race_id:
        raise ValueError("scoped_payload_race_id_mismatch")
    if normalized_date(info.get("date")) != race_date:
        raise ValueError("scoped_payload_date_mismatch")
    if any(isinstance(item, dict) and item.get("race_id") is not None
           and item.get("race_id") != race_id for item in (payload or {}).get("horses", [])):
        raise ValueError("scoped_payload_runner_race_id_mismatch")


@contextmanager
def _writer(db_path: Path):
    with closing(sqlite3.connect(str(db_path), timeout=30)) as connection:
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def init_scoped_date_ledger(db_path: Path) -> None:
    with _writer(db_path) as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS scrape_scoped_date_completeness (
            race_scope TEXT NOT NULL CHECK(race_scope='jra'), race_date TEXT NOT NULL,
            expected_race_ids_json TEXT NOT NULL DEFAULT '[]', expected_race_count INTEGER NOT NULL DEFAULT 0,
            saved_race_ids_json TEXT NOT NULL DEFAULT '[]', complete_race_ids_json TEXT NOT NULL DEFAULT '[]',
            missing_race_ids_json TEXT NOT NULL DEFAULT '[]', quarantined_race_ids_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL, no_race INTEGER NOT NULL DEFAULT 0, source_status TEXT,
            updated_at TEXT NOT NULL, PRIMARY KEY (race_scope,race_date)
        )""")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def record_scoped_expectation(db_path: Path, race_date: str, race_ids: Iterable[str]) -> None:
    ids = sorted(set(race_ids))
    if filter_scoped_race_ids(ids, race_date, "jra") != ids:
        raise ValueError("scope_expectation_contains_non_jra")
    with _writer(db_path) as connection:
        connection.execute(
            "INSERT INTO scrape_scoped_date_completeness "
            "(race_scope,race_date,expected_race_ids_json,expected_race_count,status,updated_at) "
            "VALUES ('jra',?,?,?,'partial',?) ON CONFLICT(race_scope,race_date) DO UPDATE SET "
            "expected_race_ids_json=excluded.expected_race_ids_json,expected_race_count=excluded.expected_race_count,"
            "status='partial',no_race=0,updated_at=excluded.updated_at",
            (race_date, json.dumps(ids), len(ids), _now()),
        )


def record_scoped_no_race_dates(db_path: Path, dates: Iterable[str]) -> None:
    with _writer(db_path) as connection:
        for race_date in sorted(set(dates)):
            if normalized_date(race_date) != race_date:
                raise ValueError("scoped_no_race_date_invalid")
            connection.execute(
                "INSERT INTO scrape_scoped_date_completeness "
                "(race_scope,race_date,status,no_race,source_status,updated_at) "
                "VALUES ('jra',?,'complete',1,'jra_calendar_or_verified_empty',?) "
                "ON CONFLICT(race_scope,race_date) DO UPDATE SET expected_race_ids_json='[]',expected_race_count=0,"
                "saved_race_ids_json='[]',complete_race_ids_json='[]',missing_race_ids_json='[]',"
                "quarantined_race_ids_json='[]',status='complete',no_race=1,"
                "source_status=excluded.source_status,updated_at=excluded.updated_at",
                (race_date, _now()),
            )


def invalidate_scoped_no_race_dates(db_path: Path, dates: Iterable[str]) -> int:
    changed = 0
    with _writer(db_path) as connection:
        for race_date in sorted(set(dates)):
            changed += connection.execute(
                "UPDATE scrape_scoped_date_completeness SET status='partial',no_race=0,updated_at=? "
                "WHERE race_scope='jra' AND race_date=? AND no_race=1", (_now(), race_date),
            ).rowcount
    return changed


def record_scoped_date_failure(db_path: Path, race_date: str, source_status: str) -> None:
    record_scoped_expectation(db_path, race_date, [])
    with _writer(db_path) as connection:
        connection.execute(
            "UPDATE scrape_scoped_date_completeness SET status='partial',no_race=0,source_status=?,updated_at=? "
            "WHERE race_scope='jra' AND race_date=?", (source_status, _now(), race_date),
        )


def _stored_payloads(db_path: Path, race_date: str, ids: list[str]) -> tuple[dict, set]:
    payloads, excluded = {}, set()
    if not ids:
        return payloads, excluded
    placeholders = ",".join("?" for _ in ids)
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        infos = connection.execute(
            f"SELECT race_id,data FROM races_ultimate WHERE race_id IN ({placeholders})", ids,
        ).fetchall()
        horses = {race_id: [] for race_id in ids}
        for race_id, raw in connection.execute(
            f"SELECT race_id,data FROM race_results_ultimate WHERE race_id IN ({placeholders}) ORDER BY id", ids,
        ):
            try:
                horses[race_id].append(json.loads(raw))
            except (ValueError, TypeError):
                horses[race_id].append({})
        returns = {race_id: [] for race_id in ids}
        for row in connection.execute(
            "SELECT race_id,bet_type,combinations,payout,popularity "
            f"FROM return_tables_ultimate WHERE race_id IN ({placeholders}) ORDER BY id", ids,
        ):
            returns[row[0]].append(dict(zip(("race_id", "bet_type", "combinations", "payout", "popularity"), row)))
        excluded = {row[0] for row in connection.execute(
            f"SELECT race_id FROM scrape_race_acquisition_state WHERE excluded_from_standard=1 AND race_id IN ({placeholders})", ids,
        )}
        for race_id, raw in infos:
            try:
                item = {"race_info": json.loads(raw), "horses": horses[race_id], "return_tables": returns[race_id]}
                validate_scoped_payload(item, race_id, race_date, "jra")
                payloads[race_id] = item
            except (ValueError, TypeError, AttributeError):
                continue
    return payloads, excluded


def evaluate_scoped_date(db_path: Path, race_date: str, race_ids: Iterable[str]) -> dict:
    ids = sorted(set(race_ids))
    record_scoped_expectation(db_path, race_date, ids)
    payloads, excluded = _stored_payloads(db_path, race_date, ids)
    reports = {race_id: classify_race_quality(payload) for race_id, payload in payloads.items()}
    saved = sorted(race_id for race_id, report in reports.items() if report.valid_for_storage)
    complete = sorted(race_id for race_id, report in reports.items()
                      if report.valid_for_date_completion and race_id not in excluded)
    missing = sorted(set(ids) - set(complete))
    status = "complete" if ids and not missing else "blocked" if excluded else "partial"
    with _writer(db_path) as connection:
        connection.execute(
            "UPDATE scrape_scoped_date_completeness SET saved_race_ids_json=?,complete_race_ids_json=?,"
            "missing_race_ids_json=?,quarantined_race_ids_json=?,status=?,no_race=0,updated_at=? "
            "WHERE race_scope='jra' AND race_date=?",
            tuple(json.dumps(value) for value in (saved, complete, missing, sorted(excluded))) + (status, _now(), race_date),
        )
    return {"race_scope": "jra", "race_date": race_date, "status": status,
            "expected_race_count": len(ids), "saved_race_count": len(saved), "complete_race_count": len(complete),
            "missing_race_ids": missing, "quarantined_race_ids": sorted(excluded), "no_race": False}


def hydrate_scoped_quality(db_path: Path, race_date: str, race_ids: Iterable[str]) -> dict:
    ids = sorted(set(race_ids))
    if filter_scoped_race_ids(ids, race_date, "jra") != ids:
        raise ValueError("scope_hydration_contains_non_jra")
    payloads, excluded = _stored_payloads(db_path, race_date, ids)
    for race_id, payload in payloads.items():
        if race_id not in excluded:
            record_race_quality(db_path, race_date=race_date, race_id=race_id,
                                report=classify_race_quality(payload), source_status="existing_sqlite",
                                race_data=payload, record_observation=False)
    return evaluate_scoped_date(db_path, race_date, ids)


def run_scoped_date_repair_audit(db_path: Path, race_date: str, race_ids: Iterable[str]) -> dict:
    result = evaluate_scoped_date(db_path, race_date, race_ids)
    for race_id in result["missing_race_ids"]:
        queue_repair(db_path, entity_type="race", entity_id=race_id, repair_kind="full_race",
                     error="post_job_missing_or_incomplete",
                     provenance={"race_date": race_date, "race_scope": "jra", "audit": "scoped_date_completion"},
                     count_attempt=False)
    return result


def summarize_scoped_quality(db_path: Path, dates: Iterable[str]) -> dict:
    dates = sorted(set(dates))
    rows = []
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        for day in dates:
            row = connection.execute(
                "SELECT race_date,status,missing_race_ids_json,quarantined_race_ids_json "
                "FROM scrape_scoped_date_completeness WHERE race_scope='jra' AND race_date=?", (day,),
            ).fetchone()
            if row:
                rows.append(row)
    incomplete = sorted((set(dates) - {row[0] for row in rows})
                        | {row[0] for row in rows if row[1] != "complete"})
    return {"race_scope": "jra", "target_date_count": len(dates), "audited_date_count": len(rows),
            "complete_date_count": sum(row[1] == "complete" for row in rows), "incomplete_date_count": len(incomplete),
            "incomplete_dates": incomplete, "missing_race_count": sum(len(json.loads(row[2])) for row in rows),
            "quarantined_race_count": sum(len(json.loads(row[3])) for row in rows), "repair_queue": [],
            "quality_complete": not incomplete, "all_venue_completeness_updated": False}


def completed_scoped_no_race_dates(db_path: Path, no_race_ttl_seconds: int) -> set[str]:
    fresh_after = datetime.now(timezone.utc) - timedelta(seconds=max(0, no_race_ttl_seconds))
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        rows = connection.execute(
            "SELECT race_date,updated_at FROM scrape_scoped_date_completeness "
            "WHERE race_scope='jra' AND status='complete' AND no_race=1 AND expected_race_count=0",
        ).fetchall()
    found = set()
    for day, observed in rows:
        try:
            if datetime.fromisoformat(observed.replace("Z", "+00:00")) >= fresh_after:
                found.add(day)
        except (TypeError, ValueError):
            continue
    return found
