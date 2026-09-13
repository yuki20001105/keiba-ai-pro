"""Quality-gated, source-scoped inventories for finalized historical dates.

The inventory is promoted only after a successful date audit and expires from
the original source observation, never from a cache hit. Reads are read-only
and fail closed when source bodies, parser/quality rules, or stored race data
change. A JRA fallback is never evidence for an all-venue day.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from . import fetch_pipeline
from .parsed_cache import parser_version
from .quality import classify_race_quality


SCHEMA_VERSION = 1
FINALIZED_AFTER_DAYS = 30
INVENTORY_TTL_SECONDS = 30 * 24 * 60 * 60
_SOURCE_SCOPES = {"db.netkeiba.com": "all", "race.netkeiba.com": "jra"}
_VERSIONS = {
    name: parser_version(str(Path(__file__).with_name(name)))
    for name in ("race_list.py", "quality.py")
}
_TABLES = (
    "races_ultimate", "race_results_ultimate", "return_tables_ultimate",
    "scrape_race_acquisition_state", "scrape_date_completeness",
)


class RaceListSource(str):
    """Preserve the legacy string API together with exact-response evidence."""

    def __new__(cls, value: str, *, fetched: Any, parser: str):
        instance = super().__new__(cls, value)
        stamp = getattr(fetched, "cache_snapshot", None)
        body = getattr(fetched, "body", b"")
        instance.inventory_sources = []
        if stamp and body:
            instance.inventory_sources.append({
                "url": stamp["url"],
                "fetched_at": stamp["fetched_at"],
                "body_sha256": hashlib.sha256(body).hexdigest(),
            })
        instance.inventory_parser = parser
        return instance


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _old_date(race_date: str, today: date | None = None) -> bool:
    try:
        target = datetime.strptime(race_date, "%Y%m%d").date()
        return target.strftime("%Y%m%d") == race_date and target < (
            today or date.today()
        ) - timedelta(days=FINALIZED_AFTER_DAYS)
    except (TypeError, ValueError):
        return False


def _valid_ids(race_date: str, ids: list[str], scope: str) -> bool:
    return bool(ids) and len(ids) == len(set(ids)) and all(
        isinstance(value, str) and len(value) == 12 and value.isdigit()
        and value[:4] == race_date[:4]
        and (scope != "jra" or 1 <= int(value[4:6]) <= 10)
        for value in ids
    )


def _read_source_stamp(url: str) -> dict | None:
    # The historical inventory has its own bounded TTL. The short HTTP-cache
    # expiry is intentionally not used here, but replacement/deletion is.
    with fetch_pipeline._cache_connection() as conn:
        if conn is None:
            return None
        row = conn.execute(
            "SELECT status, fetched_at, body FROM http_cache WHERE normalized_url=?",
            (url,),
        ).fetchone()
    if not row or row[0] != 200 or not row[2]:
        return None
    return {"url": url, "fetched_at": row[1],
            "body_sha256": hashlib.sha256(bytes(row[2])).hexdigest()}


def _source_matches(race_date: str, source: str, scope: str, stamps: list[dict]) -> bool:
    if scope not in {"all", "jra"} or _SOURCE_SCOPES.get(source) != scope:
        return False
    if len(stamps) != 1:
        return False
    expected_url = (
        f"https://db.netkeiba.com/race/list/{race_date}/" if scope == "all" else
        "https://race.netkeiba.com/top/race_list_sub.html?kaisai_date=" + race_date
    )
    return stamps[0]["url"] == expected_url and all(
        stamp == _read_source_stamp(stamp["url"]) for stamp in stamps
    )


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS scrape_finalized_race_inventories (
        race_date TEXT NOT NULL, scope TEXT NOT NULL, schema_version INTEGER NOT NULL,
        payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
        observed_at REAL NOT NULL, expires_at REAL NOT NULL,
        PRIMARY KEY (race_date, scope)
    )""")
    # Legacy databases do not guarantee a race_id index, while newer ones may
    # already have several. Reuse any complete index with this leading column;
    # building an additional large index would delay the acquisition batch.
    for table, fallback_index in (
        ("race_results_ultimate", "idx_finalized_inventory_horses"),
        ("return_tables_ultimate", "idx_finalized_inventory_returns"),
    ):
        if _race_id_index(conn, table) is None:
            conn.execute(f"CREATE INDEX IF NOT EXISTS {fallback_index} ON {table} (race_id)")


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _race_id_index(conn: sqlite3.Connection, table: str) -> str | None:
    for name, partial in conn.execute(
        "SELECT name, partial FROM pragma_index_list(?) ORDER BY name", (table,),
    ).fetchall():
        if partial:
            continue
        column = conn.execute(
            "SELECT name FROM pragma_index_info(?) WHERE seqno=0", (name,),
        ).fetchone()
        if column and column[0] == "race_id":
            return str(name)
    return None


def _database_digest(conn: sqlite3.Connection, race_date: str, ids: list[str], *,
                     validate_payloads: bool = False) -> str | None:
    horse_index = _race_id_index(conn, "race_results_ultimate")
    return_index = _race_id_index(conn, "return_tables_ultimate")
    if horse_index is None or return_index is None:
        return None
    expected = sorted(ids)
    row = conn.execute(
        "SELECT expected_race_ids_json, expected_race_count, complete_race_ids_json, "
        "missing_race_ids_json, quarantined_race_ids_json, status "
        "FROM scrape_date_completeness WHERE race_date=?", (race_date,),
    ).fetchone()
    if not row or row[5] != "complete" or row[1] != len(expected):
        return None
    if (sorted(json.loads(row[0])) != expected
            or sorted(json.loads(row[2])) != expected
            or json.loads(row[3]) or json.loads(row[4])):
        return None
    placeholders = ",".join("?" for _ in ids)
    states = conn.execute(
        "SELECT race_id, race_date, lifecycle, quality_status, field_states_json, "
        "quality_errors_json, excluded_from_standard "
        f"FROM scrape_race_acquisition_state WHERE race_id IN ({placeholders}) ORDER BY race_id",
        ids,
    ).fetchall()
    if len(states) != len(ids):
        return None
    for state in states:
        fields = json.loads(state[4])
        if (state[1] != race_date or state[2:4] != ("settled", "complete")
                or state[6] or json.loads(state[5]) or not fields
                or any(value not in {"available", "available_with_domain_exceptions"}
                       for value in fields.values())):
            return None
    races = conn.execute(
        f"SELECT race_id, data FROM races_ultimate WHERE race_id IN ({placeholders}) ORDER BY race_id",
        ids,
    ).fetchall()
    horses = conn.execute(
        "SELECT race_id, data FROM race_results_ultimate INDEXED BY " + _quote_identifier(horse_index) + " "
        f"WHERE race_id IN ({placeholders}) ORDER BY race_id, id", ids,
    ).fetchall()
    returns = conn.execute(
        "SELECT race_id, bet_type, combinations, payout, popularity "
        "FROM return_tables_ultimate INDEXED BY " + _quote_identifier(return_index) + " "
        f"WHERE race_id IN ({placeholders}) ORDER BY race_id, id", ids,
    ).fetchall()
    if sorted(item[0] for item in races) != expected or sorted({item[0] for item in horses}) != expected:
        return None
    if validate_payloads:
        for race_id, data in races:
            info = json.loads(data)
            if (info.get("race_id") != race_id
                    or str(info.get("date", "")).replace("-", "") != race_date):
                return None
            report = classify_race_quality({
                "race_info": info,
                "horses": [json.loads(item[1]) for item in horses if item[0] == race_id],
                "return_tables": [dict(zip(
                    ("race_id", "bet_type", "combinations", "payout", "popularity"), item,
                )) for item in returns if item[0] == race_id],
            })
            if not report.valid_for_date_completion or any(
                value not in {"available", "available_with_domain_exceptions"}
                for value in report.field_states.values()
            ):
                return None
    schema = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' AND name IN (?,?,?,?,?) ORDER BY name",
        _TABLES,
    ).fetchall()
    return _digest({"date": row, "states": states, "races": races,
                    "horses": horses, "returns": returns, "schema": schema})


def get_finalized_race_inventory(
    db_path: Path, race_date: str, *, scope: str = "all", force_refresh: bool = False,
    today: date | None = None, now: float | None = None,
) -> tuple[list[str], str] | None:
    """Return a still-audited inventory, or None to use normal list retrieval."""
    if force_refresh or not _old_date(race_date, today) or not Path(db_path).is_file():
        return None
    observed_now = time.time() if now is None else now
    try:
        with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            conn.execute("BEGIN")
            row = conn.execute(
                "SELECT schema_version, payload_json, payload_sha256, observed_at, expires_at "
                "FROM scrape_finalized_race_inventories WHERE race_date=? AND scope=?",
                (race_date, scope),
            ).fetchone()
            if (not row or row[0] != SCHEMA_VERSION or not row[3] <= observed_now < row[4]
                    or row[4] - row[3] > INVENTORY_TTL_SECONDS):
                return None
            payload = json.loads(row[1])
            ids = payload["race_ids"]
            if (_digest(payload) != row[2] or payload["versions"] != _VERSIONS
                    or payload["race_date"] != race_date or payload["scope"] != scope
                    or not _valid_ids(race_date, ids, scope)
                    or not _source_matches(race_date, payload["source"], scope, payload["sources"])
                    or payload["database_sha256"] != _database_digest(conn, race_date, ids)):
                return None
            return list(ids), payload["source"]
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def record_finalized_race_inventory(
    db_path: Path, race_date: str, race_ids: Iterable[str], *, source: str,
    audit: dict[str, Any], scope: str = "all", today: date | None = None,
    now: float | None = None,
) -> bool:
    """Promote original source evidence only after the complete date audit."""
    ids = list(race_ids)
    if (not _old_date(race_date, today) or not _valid_ids(race_date, ids, scope)
            or not Path(db_path).is_file() or audit.get("status") != "complete"
            or audit.get("race_date") != race_date
            or audit.get("expected_race_count") != len(ids)
            or audit.get("complete_race_count") != len(ids)
            or audit.get("missing_race_ids") or audit.get("quarantined_race_ids")
            or audit.get("no_race")):
        return False
    observed_now = time.time() if now is None else now
    try:
        stamps = getattr(source, "inventory_sources", [])
        if (getattr(source, "inventory_parser", None) != _VERSIONS["race_list.py"]
                or not _source_matches(race_date, str(source), scope, stamps)):
            return False
        observed_at = min(float(stamp["fetched_at"]) for stamp in stamps)
        expires_at = observed_at + INVENTORY_TTL_SECONDS
        if not observed_at <= observed_now < expires_at:
            return False
        with closing(sqlite3.connect(str(db_path))) as conn, conn:
            _ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            database_sha256 = _database_digest(conn, race_date, ids, validate_payloads=True)
            if not database_sha256:
                return False
            payload = {"race_date": race_date, "race_ids": ids, "scope": scope,
                       "source": str(source), "sources": stamps, "versions": _VERSIONS,
                       "database_sha256": database_sha256}
            conn.execute(
                "INSERT OR REPLACE INTO scrape_finalized_race_inventories VALUES (?,?,?,?,?,?,?)",
                (race_date, scope, SCHEMA_VERSION, json.dumps(payload, ensure_ascii=False),
                 _digest(payload), observed_at, expires_at),
            )
        return True
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
