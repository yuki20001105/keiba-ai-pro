"""Fail-closed acquisition quality and repair ledger for scraped race data.

The raw scrape pipeline is intentionally allowed to observe partially published
pages.  This module decides whether an observation is complete enough to be
stored as a durable race/date checkpoint.  Incomplete observations are kept in
an auditable repair queue instead of being mistaken for a successful scrape.
"""

from __future__ import annotations

import json
import hashlib
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


LIFECYCLE_ORDER = {"entry": 0, "middle": 1, "result": 2, "settled": 3}
NON_FINISH_TOKENS = (
    "中止",
    "取消",
    "除外",
    "失格",
    "競走中止",
    "出走取消",
    "競走除外",
)
# Use escapes so this source stays stable even when Windows consoles use a
# legacy code page.  Result rows may contain either the full status or its
# one-character abbreviation.
NON_FINISH_TOKENS = (
    "\u4e2d\u6b62",  # DNF
    "\u53d6\u6d88",  # withdrawn
    "\u9664\u5916",  # excluded
    "\u5931\u683c",  # disqualified
    "\u7af6\u8d70\u4e2d\u6b62",
    "\u51fa\u8d70\u53d6\u6d88",
    "\u7af6\u8d70\u9664\u5916",
    "\u4e2d",
    "\u53d6",
    "\u9664",
    "\u5931",
)

TERMINAL_SOURCE_REASONS = {
    "http_404",
    "http_410",
    "source_explicitly_unavailable",
    "invalid_race_id",
}
_INIT_LOCK = threading.RLock()
_INITIALIZED_DB_PATHS: set[str] = set()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _present(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _positive_number(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _finish_is_settled(value: Any) -> bool:
    if _positive_number(value):
        return True
    text = str(value or "").strip()
    return any(token in text for token in NON_FINISH_TOKENS)


def _finish_is_domain_nonfinish(value: Any) -> bool:
    """Return true for a legitimate non-finisher (withdrawn/DNF/etc.)."""
    if _positive_number(value):
        return False
    text = str(value or "").strip()
    return bool(text) and any(token in text for token in NON_FINISH_TOKENS)


@dataclass(frozen=True)
class RaceQualityReport:
    race_id: str
    lifecycle: str
    required_errors: tuple[str, ...]
    field_states: dict[str, str]
    horse_count: int
    valid_for_storage: bool
    valid_for_date_completion: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "race_id": self.race_id,
            "lifecycle": self.lifecycle,
            "required_errors": list(self.required_errors),
            "field_states": dict(self.field_states),
            "horse_count": self.horse_count,
            "valid_for_storage": self.valid_for_storage,
            "valid_for_date_completion": self.valid_for_date_completion,
        }


def classify_race_quality(race_data: dict[str, Any] | None) -> RaceQualityReport:
    """Classify one parsed race without mutating it.

    Structural fields must be present before storage.  Publication-timed fields
    (odds and body weight) are represented by a state instead of being treated
    as an unconditional error.  A date can only be completed by settled data.
    """
    payload = race_data if isinstance(race_data, dict) else {}
    race_info = payload.get("race_info") if isinstance(payload.get("race_info"), dict) else {}
    horses = payload.get("horses") if isinstance(payload.get("horses"), list) else []
    race_id = str(race_info.get("race_id") or "").strip()
    errors: list[str] = []

    if not race_id or len(race_id) != 12 or not race_id.isdigit():
        errors.append("invalid_race_id")
    if not _present(race_info.get("date")):
        errors.append("race_date_missing")
    if not _present(race_info.get("venue")):
        errors.append("venue_missing")
    if not _positive_number(race_info.get("distance")) or race_info.get("_invalid_distance"):
        errors.append("distance_invalid")
    if not horses:
        errors.append("horses_missing")

    horse_ids: list[str] = []
    horse_numbers: list[str] = []
    for index, horse in enumerate(horses):
        item = horse if isinstance(horse, dict) else {}
        hid = str(item.get("horse_id") or "").strip()
        number = str(item.get("horse_number") or "").strip()
        if not hid:
            errors.append(f"horse_id_missing:{index}")
        else:
            horse_ids.append(hid)
        if not _present(item.get("horse_name")):
            errors.append(f"horse_name_missing:{index}")
        if not number:
            errors.append(f"horse_number_missing:{index}")
        else:
            horse_numbers.append(number)

    if len(horse_ids) != len(set(horse_ids)):
        errors.append("horse_id_duplicate")
    if len(horse_numbers) != len(set(horse_numbers)):
        errors.append("horse_number_duplicate")

    settled_flags = [_finish_is_settled((h or {}).get("finish_position")) for h in horses]
    any_result = any(settled_flags)
    all_settled = bool(horses) and all(settled_flags)
    any_odds = any(_positive_number((h or {}).get("odds")) for h in horses)

    if all_settled:
        lifecycle = "settled"
    elif any_result:
        lifecycle = "result"
        errors.append("result_partially_settled")
    elif any_odds:
        lifecycle = "middle"
    else:
        lifecycle = "entry"

    # Withdrawn/DNF horses legitimately have no odds, popularity, body weight,
    # or finish time.  Count those as domain exceptions, not scrape failures.
    active_horses = [
        h for h in horses if not _finish_is_domain_nonfinish((h or {}).get("finish_position"))
    ]
    odds_available = sum(1 for h in active_horses if _positive_number((h or {}).get("odds")))
    popularity_available = sum(
        1 for h in active_horses if _positive_number((h or {}).get("popularity"))
    )
    weight_available = sum(
        1
        for h in active_horses
        if _positive_number((h or {}).get("weight_kg") or (h or {}).get("horse_weight"))
    )
    finish_time_available = sum(
        1 for h in active_horses if _present((h or {}).get("finish_time"))
    )
    pedigree_available = sum(
        1
        for h in horses
        if _present((h or {}).get("sire"))
        and _present((h or {}).get("dam"))
        and _present((h or {}).get("damsire") or (h or {}).get("broodmare_sire"))
    )

    def timed_state(available: int, *, publish_stage: str) -> str:
        expected = len(active_horses) if lifecycle in {"result", "settled"} else len(horses)
        if horses and available == expected:
            if expected < len(horses):
                return "available_with_domain_exceptions"
            return "available"
        if lifecycle == "entry" and publish_stage in {"middle", "result"}:
            return "not_published"
        if lifecycle == "middle" and publish_stage == "result":
            return "not_published"
        return "source_missing"

    field_states = {
        "odds": timed_state(odds_available, publish_stage="middle"),
        "popularity": timed_state(popularity_available, publish_stage="middle"),
        "horse_weight": timed_state(weight_available, publish_stage="middle"),
        "finish_position": "available" if all_settled else ("partial" if any_result else "not_published"),
        "finish_time": timed_state(finish_time_available, publish_stage="result"),
        "pedigree": "available" if horses and pedigree_available == len(horses) else "repair_required",
    }

    if all_settled and field_states["finish_time"] == "source_missing":
        errors.append("finish_time_missing")

    structural_errors = [
        error
        for error in errors
        if error != "result_partially_settled"
    ]
    valid_for_storage = not structural_errors
    valid_for_date_completion = valid_for_storage and lifecycle == "settled"
    return RaceQualityReport(
        race_id=race_id,
        lifecycle=lifecycle,
        required_errors=tuple(dict.fromkeys(errors)),
        field_states=field_states,
        horse_count=len(horses),
        valid_for_storage=valid_for_storage,
        valid_for_date_completion=valid_for_date_completion,
    )


def init_acquisition_quality_db(db_path: Path) -> None:
    key = str(db_path.resolve())
    with _INIT_LOCK:
        if key in _INITIALIZED_DB_PATHS:
            return
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS scrape_race_acquisition_state (
                race_id TEXT PRIMARY KEY,
                race_date TEXT NOT NULL,
                lifecycle TEXT NOT NULL DEFAULT 'entry',
                quality_status TEXT NOT NULL DEFAULT 'pending',
                field_states_json TEXT NOT NULL DEFAULT '{}',
                quality_errors_json TEXT NOT NULL DEFAULT '[]',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_source_status TEXT,
                last_error TEXT,
                excluded_from_standard INTEGER NOT NULL DEFAULT 0,
                exclusion_reason TEXT,
                first_seen_at TEXT NOT NULL,
                last_attempt_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS scrape_date_completeness (
                race_date TEXT PRIMARY KEY,
                expected_race_ids_json TEXT NOT NULL,
                expected_race_count INTEGER NOT NULL,
                saved_race_ids_json TEXT NOT NULL DEFAULT '[]',
                complete_race_ids_json TEXT NOT NULL DEFAULT '[]',
                missing_race_ids_json TEXT NOT NULL DEFAULT '[]',
                quarantined_race_ids_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'pending',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scrape_repair_queue (
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                repair_kind TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 5,
                last_error TEXT,
                provenance_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (entity_type, entity_id, repair_kind)
            );
            CREATE INDEX IF NOT EXISTS idx_scrape_repair_status
              ON scrape_repair_queue(status, repair_kind, updated_at);
            CREATE TABLE IF NOT EXISTS scrape_race_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_id TEXT NOT NULL,
                race_date TEXT NOT NULL,
                lifecycle TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                source_status TEXT NOT NULL,
                field_states_json TEXT NOT NULL,
                market_payload_json TEXT,
                payload_digest TEXT NOT NULL UNIQUE
            );
            CREATE INDEX IF NOT EXISTS idx_scrape_observation_race_time
              ON scrape_race_observations(race_id, observed_at);
            """
            )
        _INITIALIZED_DB_PATHS.add(key)


def record_date_expectation(db_path: Path, race_date: str, race_ids: Iterable[str]) -> None:
    expected = sorted({str(race_id) for race_id in race_ids if str(race_id)})
    now = _utc_now()
    init_acquisition_quality_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        existing = conn.execute(
            "SELECT saved_race_ids_json, complete_race_ids_json, quarantined_race_ids_json "
            "FROM scrape_date_completeness WHERE race_date = ?",
            (race_date,),
        ).fetchone()
        saved = json.loads(existing[0]) if existing else []
        complete = json.loads(existing[1]) if existing else []
        quarantined = json.loads(existing[2]) if existing else []
        missing = sorted(set(expected) - set(complete))
        status = "complete" if expected and not missing else "pending"
        conn.execute(
            """INSERT INTO scrape_date_completeness (
                   race_date, expected_race_ids_json, expected_race_count,
                   saved_race_ids_json, complete_race_ids_json, missing_race_ids_json,
                   quarantined_race_ids_json, status, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(race_date) DO UPDATE SET
                   expected_race_ids_json=excluded.expected_race_ids_json,
                   expected_race_count=excluded.expected_race_count,
                   missing_race_ids_json=excluded.missing_race_ids_json,
                   status=excluded.status,
                   updated_at=excluded.updated_at""",
            (
                race_date,
                json.dumps(expected),
                len(expected),
                json.dumps(saved),
                json.dumps(complete),
                json.dumps(missing),
                json.dumps(quarantined),
                status,
                now,
            ),
        )

def queue_repair(
    db_path: Path,
    *,
    entity_type: str,
    entity_id: str,
    repair_kind: str,
    error: str,
    provenance: dict[str, Any] | None = None,
    max_attempts: int = 5,
    count_attempt: bool = True,
) -> dict[str, Any]:
    init_acquisition_quality_db(db_path)
    now = _utc_now()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """INSERT INTO scrape_repair_queue (
                   entity_type, entity_id, repair_kind, status, attempt_count,
                   max_attempts, last_error, provenance_json, created_at, updated_at
               ) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)
               ON CONFLICT(entity_type, entity_id, repair_kind) DO UPDATE SET
                   attempt_count=scrape_repair_queue.attempt_count + ?,
                   last_error=excluded.last_error,
                   provenance_json=excluded.provenance_json,
                   updated_at=excluded.updated_at""",
            (
                entity_type,
                entity_id,
                repair_kind,
                1 if count_attempt else 0,
                max_attempts,
                error,
                json.dumps(provenance or {}, ensure_ascii=False),
                now,
                now,
                1 if count_attempt else 0,
            ),
        )
        row = conn.execute(
            "SELECT attempt_count, max_attempts FROM scrape_repair_queue "
            "WHERE entity_type=? AND entity_id=? AND repair_kind=?",
            (entity_type, entity_id, repair_kind),
        ).fetchone()
        attempts, allowed = int(row[0]), int(row[1])
        terminal = error in TERMINAL_SOURCE_REASONS
        status = "quarantined" if terminal or attempts >= allowed else "pending"
        conn.execute(
            "UPDATE scrape_repair_queue SET status=?, updated_at=? "
            "WHERE entity_type=? AND entity_id=? AND repair_kind=?",
            (status, now, entity_type, entity_id, repair_kind),
        )
    return {"status": status, "attempt_count": attempts, "max_attempts": allowed}


def record_race_quality(
    db_path: Path,
    *,
    race_date: str,
    race_id: str,
    report: RaceQualityReport,
    source_status: str = "http_200",
    race_data: dict[str, Any] | None = None,
    record_observation: bool = True,
) -> None:
    init_acquisition_quality_db(db_path)
    now = _utc_now()
    quality_status = "complete" if report.valid_for_date_completion else (
        "stored_partial" if report.valid_for_storage else "invalid"
    )
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """INSERT INTO scrape_race_acquisition_state (
                   race_id, race_date, lifecycle, quality_status, field_states_json,
                   quality_errors_json, attempt_count, last_source_status, last_error,
                   excluded_from_standard, exclusion_reason, first_seen_at,
                   last_attempt_at, completed_at
               ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, 0, NULL, ?, ?, ?)
               ON CONFLICT(race_id) DO UPDATE SET
                   race_date=excluded.race_date,
                   lifecycle=excluded.lifecycle,
                   quality_status=excluded.quality_status,
                   field_states_json=excluded.field_states_json,
                   quality_errors_json=excluded.quality_errors_json,
                   attempt_count=scrape_race_acquisition_state.attempt_count + 1,
                   last_source_status=excluded.last_source_status,
                   last_error=excluded.last_error,
                   excluded_from_standard=0,
                   exclusion_reason=NULL,
                   last_attempt_at=excluded.last_attempt_at,
                   completed_at=excluded.completed_at""",
            (
                race_id,
                race_date,
                report.lifecycle,
                quality_status,
                json.dumps(report.field_states, ensure_ascii=False),
                json.dumps(list(report.required_errors), ensure_ascii=False),
                source_status,
                ";".join(report.required_errors) or None,
                now,
                now,
                now if report.valid_for_date_completion else None,
            ),
        )

        if report.valid_for_storage:
            conn.execute(
                "UPDATE scrape_repair_queue SET status='completed', last_error=NULL, "
                "updated_at=? WHERE entity_type='race' AND entity_id=? "
                "AND repair_kind='full_race'",
                (now, race_id),
            )
        for field, state in report.field_states.items():
            if state not in {"source_missing", "partial", "repair_required"}:
                conn.execute(
                    "UPDATE scrape_repair_queue SET status='completed', last_error=NULL, "
                    "updated_at=? WHERE entity_type='race' AND entity_id=? "
                    "AND repair_kind=?",
                    (now, race_id, field),
                )

        if isinstance(race_data, dict) and record_observation:
            horses = race_data.get("horses") if isinstance(race_data.get("horses"), list) else []
            market_payload = None
            # Only entry/middle snapshots are point-in-time market evidence.
            # Result-page odds remain in race_results_ultimate and never enter
            # this forward observation table.
            if report.lifecycle in {"entry", "middle"}:
                market_payload = [
                    {
                        "horse_id": (horse or {}).get("horse_id"),
                        "horse_number": (horse or {}).get("horse_number"),
                        "odds": (horse or {}).get("odds"),
                        "popularity": (horse or {}).get("popularity"),
                        "horse_weight": (horse or {}).get("weight_kg")
                        or (horse or {}).get("horse_weight"),
                    }
                    for horse in horses
                ]
            digest_input = {
                "race_id": race_id,
                "race_date": race_date,
                "lifecycle": report.lifecycle,
                "observed_at": now,
                "source_status": source_status,
                "field_states": report.field_states,
                "market_payload": market_payload,
            }
            digest = hashlib.sha256(
                json.dumps(digest_input, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            conn.execute(
                """INSERT OR IGNORE INTO scrape_race_observations (
                       race_id, race_date, lifecycle, observed_at, source_status,
                       field_states_json, market_payload_json, payload_digest
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    race_id,
                    race_date,
                    report.lifecycle,
                    now,
                    source_status,
                    json.dumps(report.field_states, ensure_ascii=False),
                    json.dumps(market_payload, ensure_ascii=False) if market_payload is not None else None,
                    digest,
                ),
            )

    if report.field_states.get("pedigree") == "repair_required":
        horses = race_data.get("horses", []) if isinstance(race_data, dict) else []
        missing_horse_ids = [
            str((horse or {}).get("horse_id") or "").strip()
            for horse in horses
            if not (
                _present((horse or {}).get("sire"))
                and _present((horse or {}).get("dam"))
                and _present((horse or {}).get("damsire") or (horse or {}).get("broodmare_sire"))
            )
        ]
        if missing_horse_ids:
            for horse_id in sorted(set(filter(None, missing_horse_ids))):
                queue_repair(
                    db_path,
                    entity_type="horse",
                    entity_id=horse_id,
                    repair_kind="pedigree",
                    error="pedigree_incomplete",
                    provenance={"race_id": race_id, "race_date": race_date},
                    count_attempt=False,
                )
        else:
            queue_repair(
                db_path,
                entity_type="race",
                entity_id=race_id,
                repair_kind="pedigree",
                error="pedigree_incomplete",
                provenance={"race_date": race_date, "source_status": source_status},
                count_attempt=False,
            )
    for field in ("odds", "popularity", "horse_weight", "finish_position", "finish_time"):
        state = report.field_states.get(field)
        if state in {"source_missing", "partial"}:
            queue_repair(
                db_path,
                entity_type="race",
                entity_id=race_id,
                repair_kind=field,
                error=f"{field}:{state}",
                provenance={"race_date": race_date, "lifecycle": report.lifecycle},
                count_attempt=False,
            )


def record_race_failure(
    db_path: Path,
    *,
    race_date: str,
    race_id: str,
    reason: str,
    source_status: str,
    max_attempts: int = 5,
) -> dict[str, Any]:
    queued = queue_repair(
        db_path,
        entity_type="race",
        entity_id=race_id,
        repair_kind="full_race",
        error=reason,
        provenance={"race_date": race_date, "source_status": source_status},
        max_attempts=max_attempts,
    )
    init_acquisition_quality_db(db_path)
    now = _utc_now()
    excluded = 1 if queued["status"] == "quarantined" else 0
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """INSERT INTO scrape_race_acquisition_state (
                   race_id, race_date, lifecycle, quality_status, field_states_json,
                   quality_errors_json, attempt_count, last_source_status, last_error,
                   excluded_from_standard, exclusion_reason, first_seen_at, last_attempt_at
               ) VALUES (?, ?, 'entry', ?, '{}', ?, 1, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(race_id) DO UPDATE SET
                   quality_status=excluded.quality_status,
                   quality_errors_json=excluded.quality_errors_json,
                   attempt_count=scrape_race_acquisition_state.attempt_count + 1,
                   last_source_status=excluded.last_source_status,
                   last_error=excluded.last_error,
                   excluded_from_standard=excluded.excluded_from_standard,
                   exclusion_reason=excluded.exclusion_reason,
                   last_attempt_at=excluded.last_attempt_at""",
            (
                race_id,
                race_date,
                queued["status"],
                json.dumps([reason], ensure_ascii=False),
                source_status,
                reason,
                excluded,
                reason if excluded else None,
                now,
                now,
            ),
        )
    return {**queued, "excluded_from_standard": bool(excluded)}


def excluded_standard_race_ids(db_path: Path) -> set[str]:
    if not db_path.exists():
        return set()
    try:
        init_acquisition_quality_db(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            return {
                str(row[0])
                for row in conn.execute(
                    "SELECT race_id FROM scrape_race_acquisition_state "
                    "WHERE excluded_from_standard=1"
                )
            }
    except sqlite3.Error:
        return set()


def record_date_failure(
    db_path: Path,
    *,
    race_date: str,
    reason: str,
    source_status: str,
    max_attempts: int = 5,
) -> dict[str, Any]:
    """Record a race-list failure without inventing expected race IDs."""
    queued = queue_repair(
        db_path,
        entity_type="date",
        entity_id=race_date,
        repair_kind="race_list",
        error=reason,
        provenance={"race_date": race_date, "source_status": source_status},
        max_attempts=max_attempts,
    )
    record_date_expectation(db_path, race_date, [])
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE scrape_date_completeness SET status=?, updated_at=? WHERE race_date=?",
            (
                "excluded" if queued["status"] == "quarantined" else "partial",
                _utc_now(),
                race_date,
            ),
        )
    return {**queued, "excluded_from_standard": queued["status"] == "quarantined"}


def excluded_standard_dates(db_path: Path) -> set[str]:
    if not db_path.exists():
        return set()
    try:
        init_acquisition_quality_db(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            return {
                str(row[0])
                for row in conn.execute(
                    "SELECT entity_id FROM scrape_repair_queue "
                    "WHERE entity_type='date' AND repair_kind='race_list' "
                    "AND status='quarantined'"
                )
            }
    except sqlite3.Error:
        return set()


def completed_quality_dates(db_path: Path) -> set[str]:
    if not db_path.exists():
        return set()
    try:
        init_acquisition_quality_db(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            return {
                str(row[0])
                for row in conn.execute(
                    "SELECT race_date FROM scrape_date_completeness WHERE status='complete'"
                )
            }
    except sqlite3.Error:
        return set()


def hydrate_quality_from_existing_data(
    db_path: Path,
    race_date: str,
    expected_race_ids: Iterable[str],
) -> dict[str, Any]:
    """Classify already stored rows before scheduling network repair.

    The daily race-list request remains authoritative, but complete race detail
    pages already in SQLite are not downloaded again.
    """
    expected = sorted({str(race_id) for race_id in expected_race_ids if str(race_id)})
    if not expected:
        return evaluate_date_completeness(db_path, race_date, expected)

    placeholders = ",".join("?" for _ in expected)
    race_info: dict[str, dict[str, Any]] = {}
    horses: dict[str, list[dict[str, Any]]] = {race_id: [] for race_id in expected}
    returns: dict[str, list[dict[str, Any]]] = {race_id: [] for race_id in expected}
    with sqlite3.connect(str(db_path)) as conn:
        for race_id, data_text in conn.execute(
            f"SELECT race_id, data FROM races_ultimate WHERE race_id IN ({placeholders})",
            expected,
        ):
            try:
                value = json.loads(data_text or "{}")
            except json.JSONDecodeError:
                value = {}
            race_info[str(race_id)] = value if isinstance(value, dict) else {}
        for race_id, data_text in conn.execute(
            f"SELECT race_id, data FROM race_results_ultimate WHERE race_id IN ({placeholders})",
            expected,
        ):
            try:
                value = json.loads(data_text or "{}")
            except json.JSONDecodeError:
                value = {}
            if isinstance(value, dict):
                horses.setdefault(str(race_id), []).append(value)
        for race_id, bet_type, combinations, payout, popularity in conn.execute(
            f"SELECT race_id, bet_type, combinations, payout, popularity "
            f"FROM return_tables_ultimate WHERE race_id IN ({placeholders})",
            expected,
        ):
            returns.setdefault(str(race_id), []).append(
                {
                    "race_id": race_id,
                    "bet_type": bet_type,
                    "combinations": combinations,
                    "payout": payout,
                    "popularity": popularity,
                }
            )

    for race_id in expected:
        if race_id not in race_info or not horses.get(race_id):
            continue
        report = classify_race_quality(
            {
                "race_info": race_info[race_id],
                "horses": horses[race_id],
                "return_tables": returns.get(race_id, []),
            }
        )
        race_data = {
            "race_info": race_info[race_id],
            "horses": horses[race_id],
            "return_tables": returns.get(race_id, []),
        }
        record_race_quality(
            db_path,
            race_date=race_date,
            race_id=race_id,
            report=report,
            source_status="existing_sqlite",
            race_data=race_data,
            record_observation=False,
        )
    return evaluate_date_completeness(db_path, race_date, expected)


def evaluate_date_completeness(
    db_path: Path,
    race_date: str,
    expected_race_ids: Iterable[str],
) -> dict[str, Any]:
    expected = sorted({str(race_id) for race_id in expected_race_ids if str(race_id)})
    record_date_expectation(db_path, race_date, expected)
    with sqlite3.connect(str(db_path)) as conn:
        placeholders = ",".join("?" for _ in expected)
        rows = []
        if expected:
            rows = conn.execute(
                f"SELECT race_id, quality_status, excluded_from_standard "
                f"FROM scrape_race_acquisition_state WHERE race_id IN ({placeholders})",
                expected,
            ).fetchall()
        saved = sorted(str(row[0]) for row in rows if row[1] in {"stored_partial", "complete"})
        complete = sorted(str(row[0]) for row in rows if row[1] == "complete")
        quarantined = sorted(str(row[0]) for row in rows if int(row[2] or 0) == 1)
        missing = sorted(set(expected) - set(complete))
        status = "complete" if expected and not missing else (
            "blocked" if quarantined else "partial"
        )
        now = _utc_now()
        conn.execute(
            """UPDATE scrape_date_completeness SET
                   saved_race_ids_json=?, complete_race_ids_json=?,
                   missing_race_ids_json=?, quarantined_race_ids_json=?,
                   status=?, updated_at=? WHERE race_date=?""",
            (
                json.dumps(saved),
                json.dumps(complete),
                json.dumps(missing),
                json.dumps(quarantined),
                status,
                now,
                race_date,
            ),
        )
    return {
        "race_date": race_date,
        "status": status,
        "expected_race_count": len(expected),
        "saved_race_count": len(saved),
        "complete_race_count": len(complete),
        "missing_race_ids": missing,
        "quarantined_race_ids": quarantined,
    }


def run_date_repair_audit(
    db_path: Path,
    race_date: str,
    expected_race_ids: Iterable[str],
) -> dict[str, Any]:
    result = evaluate_date_completeness(db_path, race_date, expected_race_ids)
    for race_id in result["missing_race_ids"]:
        queue_repair(
            db_path,
            entity_type="race",
            entity_id=race_id,
            repair_kind="full_race",
            error="post_job_missing_or_incomplete",
            provenance={"race_date": race_date, "audit": "date_completion"},
            count_attempt=False,
        )
    return result


def summarize_acquisition_quality(
    db_path: Path,
    race_dates: Iterable[str],
) -> dict[str, Any]:
    dates = sorted({str(value) for value in race_dates if str(value)})
    init_acquisition_quality_db(db_path)
    date_rows: list[tuple[Any, ...]] = []
    with sqlite3.connect(str(db_path)) as conn:
        if dates:
            placeholders = ",".join("?" for _ in dates)
            date_rows = conn.execute(
                f"SELECT race_date, status, expected_race_count, missing_race_ids_json, "
                f"quarantined_race_ids_json FROM scrape_date_completeness "
                f"WHERE race_date IN ({placeholders}) ORDER BY race_date",
                dates,
            ).fetchall()
        queue_rows = conn.execute(
            "SELECT status, repair_kind, COUNT(*) FROM scrape_repair_queue "
            "GROUP BY status, repair_kind ORDER BY status, repair_kind"
        ).fetchall()

    by_date = {
        str(row[0]): {
            "status": str(row[1]),
            "expected_race_count": int(row[2] or 0),
            "missing_race_ids": json.loads(row[3] or "[]"),
            "quarantined_race_ids": json.loads(row[4] or "[]"),
        }
        for row in date_rows
    }
    missing_dates = sorted(set(dates) - set(by_date))
    incomplete_dates = sorted(
        [date for date, item in by_date.items() if item["status"] != "complete"]
        + missing_dates
    )
    return {
        "target_date_count": len(dates),
        "audited_date_count": len(by_date),
        "complete_date_count": sum(1 for item in by_date.values() if item["status"] == "complete"),
        "incomplete_date_count": len(incomplete_dates),
        "incomplete_dates": incomplete_dates,
        "missing_race_count": sum(len(item["missing_race_ids"]) for item in by_date.values()),
        "quarantined_race_count": sum(
            len(item["quarantined_race_ids"]) for item in by_date.values()
        ),
        "repair_queue": [
            {"status": str(row[0]), "repair_kind": str(row[1]), "count": int(row[2])}
            for row in queue_rows
        ],
        "quality_complete": not incomplete_dates,
    }
