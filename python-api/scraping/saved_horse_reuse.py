"""Read-only, acquisition-scoped reuse of saved horse evidence.

Pedigree is immutable, but a legacy spelling is not automatically a canonical
name. Ambiguous/contradictory sources fall back to the provider. Local history
is strictly point-in-time and is returned as audit evidence, never as a current
profile or as permission to skip the provider's complete horse detail response.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
import unicodedata


_VERSION = 1
_MAX_HORSE_ROWS = 1024
_READ_BUDGET_SECONDS = 0.35
_MAX_COVERAGE_DAYS = 366
_PEDIGREE_FIELDS = ("sire", "dam", "damsire")
_EMPTY = {"", "-", "--", "unknown", "unknown_local", "none", "null", "nan",
          "不明", "未取得", "なし"}


@dataclass(frozen=True)
class AcquisitionHorseReuse:
    db_path: Path
    target_date: date
    force_refresh: bool = False


@dataclass(frozen=True)
class SavedHorseEvidence:
    pedigree: dict[str, str] | None
    metadata: dict
    cache_token: str


_CONTEXT: ContextVar[AcquisitionHorseReuse | None] = ContextVar(
    "acquisition_horse_reuse", default=None,
)


def saved_horse_reuse_enabled() -> bool:
    context = _CONTEXT.get()
    return context is not None and not context.force_refresh


def _date(value: object) -> date | None:
    text = str(value or "").strip().replace("-", "").replace("/", "")
    if not re.fullmatch(r"\d{8}", text):
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None


@contextmanager
def acquisition_horse_reuse(
    db_path: Path, target_date: str | date, *, force_refresh: bool = False,
):
    """Opt in only the historical acquisition caller, not prediction/training."""
    target = _date(target_date)
    context = AcquisitionHorseReuse(Path(db_path), target, force_refresh) if target else None
    token = _CONTEXT.set(context)
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
    ).encode()).hexdigest()


def _name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = unicodedata.normalize("NFKC", value).strip()
    if text.lower() in _EMPTY or len(text) > 100:
        return None
    # No heuristic removal of bilingual suffixes, country tags, IDs, HTML,
    # or control characters. They need an identity-aware migration instead.
    if any(unicodedata.category(char).startswith("C") for char in text):
        return None
    if re.search(r"[()\[\]{}<>/\\\d]", text):
        return None
    if re.search(r"[A-Za-z]", text) and re.search(r"[\u3040-\u30ff\u3400-\u9fff]", text):
        return None
    return text


def _present(value: object) -> bool:
    return value is not None and str(value).strip().lower() not in _EMPTY


def _saved_pedigree(rows: list[dict]) -> tuple[dict[str, str] | None, str]:
    values: dict[str, set[str]] = {field: set() for field in _PEDIGREE_FIELDS}
    complete_rows = 0
    for row in rows:
        item = row["horse"]
        complete = True
        for field in _PEDIGREE_FIELDS:
            raw = item.get(field)
            if not _present(raw):
                complete = False
                continue
            normalized = _name(raw)
            if normalized is None:
                return None, "ambiguous_saved_name"
            values[field].add(normalized)
            if len(values[field]) > 1:
                return None, "conflicting_saved_pedigree"
        complete_rows += complete
    if not complete_rows or any(not choices for choices in values.values()):
        return None, "incomplete_saved_pedigree"
    return {field: next(iter(choices)) for field, choices in values.items()}, "saved_pedigree_verified"


def compatible_saved_pedigree(
    evidence: SavedHorseEvidence | None, current: dict | None,
) -> dict[str, str] | None:
    """Fill only a missing/partial cache whose known values agree exactly."""
    if evidence is None or evidence.pedigree is None:
        return None
    current = current or {}
    if all(_present(current.get(field)) for field in _PEDIGREE_FIELDS):
        return None  # The dedicated/provider cache retains authority.
    for field in _PEDIGREE_FIELDS:
        if _present(current.get(field)) and _name(current[field]) != evidence.pedigree[field]:
            return None
    return dict(evidence.pedigree)


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (TypeError, ValueError):
        return None


def _seconds(value: object) -> float | None:
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d+):(\d{1,2}(?:\.\d+)?)", text)
    if match:
        seconds = float(match[2])
        total = int(match[1]) * 60 + seconds
        return total if 0 <= seconds < 60 and total > 0 else None
    return _number(value)


def _date_coverage(
    connection: sqlite3.Connection, start: date, target: date, observed_races: dict[str, set[str]],
) -> tuple[bool, str, list]:
    days = (target - start).days
    if days <= 0 or days > _MAX_COVERAGE_DAYS:
        return False, "coverage_window_unproven", []
    try:
        rows = connection.execute(
            "SELECT race_date, expected_race_ids_json, expected_race_count, "
            "saved_race_ids_json, complete_race_ids_json, missing_race_ids_json, "
            "quarantined_race_ids_json, status, updated_at "
            "FROM scrape_date_completeness WHERE race_date>=? AND race_date<?",
            (start.strftime("%Y%m%d"), target.strftime("%Y%m%d")),
        ).fetchall()
    except sqlite3.OperationalError:
        return False, "coverage_ledger_unavailable", []
    expected_days = {(start + timedelta(days=offset)).strftime("%Y%m%d") for offset in range(days)}
    if {row[0] for row in rows} != expected_days:
        return False, "coverage_dates_missing", rows
    for row in rows:
        try:
            lists = [json.loads(row[index]) for index in (1, 3, 4, 5, 6)]
            if any(not isinstance(items, list) or any(not isinstance(v, str) for v in items)
                   for items in lists):
                return False, "coverage_ledger_invalid", rows
            expected, saved, complete, missing, quarantined = map(set, lists)
            if (row[7] != "complete" or isinstance(row[2], bool)
                    or row[2] != len(expected) or len(lists[0]) != len(expected)
                    or not expected.issubset(saved) or complete != expected
                    or missing or quarantined
                    or not observed_races.get(row[0], set()).issubset(expected)):
                return False, "coverage_dates_incomplete", rows
        except (TypeError, ValueError):
            return False, "coverage_ledger_invalid", rows
    return True, "date_ledger_complete", rows


def _history(connection: sqlite3.Connection, rows: list[dict], target: date) -> tuple[dict, list]:
    prior: dict[str, dict] = {}
    conflict = False
    invalid_prior = False
    for row in rows:
        day = _date(row["race"].get("date"))
        if day is None:
            invalid_prior = True  # It cannot safely be classified as past/future.
            continue
        if day >= target:
            continue
        horse, race = row["horse"], row["race"]
        finish, seconds, distance = (
            _number(horse.get("finish_position")), _seconds(horse.get("finish_time")),
            _number(race.get("distance")),
        )
        if finish is None or not finish.is_integer() or seconds is None or distance is None:
            invalid_prior = True
            continue
        fields: dict = {"race_date": day.strftime("%Y/%m/%d"), "race_finish": int(finish),
                        "race_time": seconds, "race_distance": int(distance)}
        for field, value in (
            ("race_venue", race.get("venue")),
            ("race_surface", race.get("surface") or race.get("track_type")),
        ):
            if _present(value):
                fields[field] = value
        weight = _number(horse.get("weight_kg") or horse.get("horse_weight") or horse.get("weight"))
        if weight is not None:
            fields["race_weight"] = int(weight)
        item = {"race_id": row["race_id"], "date": day, "fields": fields}
        if row["race_id"] in prior and prior[row["race_id"]] != item:
            conflict = True
        prior[row["race_id"]] = item
    ordered = sorted(prior.values(), key=lambda item: (item["date"], item["race_id"]), reverse=True)
    if len({item["date"] for item in ordered}) != len(ordered):
        conflict = True  # No race-ID ordering assumption for same-day appearances.
    selected = ordered[:2] if not conflict else []
    fields = {}
    for index, item in enumerate(selected):
        prefix = "prev" if index == 0 else "prev2"
        fields.update({f"{prefix}_{key}": value for key, value in item["fields"].items()})
    coverage_rows: list = []
    if conflict:
        complete, reason = False, "conflicting_history"
    elif invalid_prior:
        complete, reason = False, "history_contains_unresolved_result"
    elif len(selected) < 2:
        complete, reason = False, "insufficient_local_history"
    else:
        observed: dict[str, set[str]] = {}
        for item in ordered:
            observed.setdefault(item["date"].strftime("%Y%m%d"), set()).add(item["race_id"])
        complete, reason, coverage_rows = _date_coverage(
            connection, selected[-1]["date"], target, observed,
        )
    return {
        "strict_predicate": "source_race_date < target_race_date",
        "available_prior_count": len(ordered), "required_count": 2,
        "coverage_complete": complete, "reason": reason,
        "fields": fields, "source_race_ids": [item["race_id"] for item in selected],
        # A date ledger does not prove completeness of current owner/career
        # profile fields or overseas races. Keep the provider detail fallback.
        "can_replace_provider_detail": False, "audit_only": True,
    }, coverage_rows


def load_saved_horse_evidence(horse_id: str) -> SavedHorseEvidence | None:
    """Bounded indexed reads; failure means the original fetch path is used."""
    context = _CONTEXT.get()
    if context is None or context.force_refresh or not re.fullmatch(r"[A-Za-z0-9]{1,32}", str(horse_id)):
        return None
    started = time.monotonic()
    try:
        with closing(sqlite3.connect(
            context.db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.1,
        )) as connection:
            try:
                connection.execute("PRAGMA query_only=ON")
                connection.set_progress_handler(
                    lambda: int(time.monotonic() - started > _READ_BUDGET_SECONDS), 2000,
                )
                # Read one coherent snapshot. Never keep a transaction across
                # an await, HTTP request, or callback to another component.
                connection.execute("BEGIN")
                query = (
                    "SELECT rr.id, rr.race_id, rr.data, rr.created_at, r.data "
                    "FROM race_results_ultimate rr LEFT JOIN races_ultimate r ON r.race_id=rr.race_id "
                    "WHERE json_extract(rr.data,'$.horse_id')=? LIMIT ?"
                )
                arguments = (str(horse_id), _MAX_HORSE_ROWS + 1)
                plan = connection.execute("EXPLAIN QUERY PLAN " + query, arguments).fetchall()
                if not any("SEARCH rr USING INDEX" in row[3] and "<expr>" in row[3] for row in plan):
                    return None  # A legacy DB without an index must not be scanned.
                raw_rows = connection.execute(query, arguments).fetchall()
                if len(raw_rows) > _MAX_HORSE_ROWS:
                    return None
                rows = []
                for identifier, race_id, raw, created_at, raw_race in raw_rows:
                    horse, race = json.loads(raw), json.loads(raw_race)
                    if (not isinstance(horse, dict) or not isinstance(race, dict)
                            or str(horse.get("horse_id")) != str(horse_id)
                            or not re.fullmatch(r"\d{12}", str(race_id))
                            or str(horse.get("race_id")) != str(race_id)
                            or str(race.get("race_id")) != str(race_id)):
                        return None
                    rows.append({"id": identifier, "race_id": race_id, "horse": horse,
                                 "created_at": created_at, "race": race})
                pedigree, reason = _saved_pedigree(rows)
                history, coverage_rows = _history(connection, rows, context.target_date)
                source_hash = _hash([_VERSION, rows, coverage_rows])
                pedigree_source_ids = sorted({
                    row["race_id"] for row in rows
                    if pedigree and all(_name(row["horse"].get(field)) == pedigree[field]
                                        for field in _PEDIGREE_FIELDS)
                })
                metadata = {
                    "version": _VERSION, "target_date": context.target_date.strftime("%Y%m%d"),
                    "source": "saved_race_database", "source_hash": source_hash,
                    "pedigree": {"reused": False, "reason": reason,
                                 "source_race_ids": pedigree_source_ids[:8],
                                 "source_count": len(pedigree_source_ids)},
                    "history": history,
                }
                return SavedHorseEvidence(
                    pedigree, metadata,
                    _hash([str(context.db_path.resolve()), context.target_date.isoformat(), source_hash]),
                )
            finally:
                connection.set_progress_handler(None, 0)
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
        return None
