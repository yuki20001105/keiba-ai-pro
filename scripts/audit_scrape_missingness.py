#!/usr/bin/env python3
"""Read-only missingness and consistency audit for scraped race data."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT_DIR / "keiba" / "data" / "keiba_ultimate.db"
DEFAULT_OUTPUT_JSON = ROOT_DIR / "reports" / "scrape_missingness_audit.json"

CORE_REQUIRED_FIELDS = [
    "race_id",
    "race_date",
    "venue",
    "race_number",
    "horse_id",
    "horse_name",
    "frame_number",
    "horse_number",
]
RESULT_REQUIRED_FIELDS = ["finish_position", "result_time", "margin"]
IMPORTANT_WARN_FIELDS = [
    "odds",
    "popularity",
    "horse_weight",
    "jockey",
    "trainer",
    "sire",
    "dam",
    "broodmare_sire",
]

GROUP_KEYS = ["year", "month", "venue", "race_type", "distance", "surface", "class", "source_page_type"]
RACE_NUMBER_ALIAS_KEYS = ["race_number", "race_no", "race_num", "race", "round", "race_index", "race_order"]
FINISH_DNF_TOKENS = ("中", "取", "除", "失", "降", "止", "取消", "除外", "中止", "失格")


@dataclass
class AuditContext:
    rows: list[dict[str, Any]]
    races: list[dict[str, Any]]
    horse_details: dict[str, dict[str, Any]]
    target: str
    start_date: str | None
    end_date: str | None


def _parse_date(v: str | None) -> str | None:
    if not v:
        return None
    txt = str(v).strip()
    if not txt:
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(txt, fmt).strftime("%Y%m%d")
        except ValueError:
            pass
    if len(txt) >= 10 and txt[4] in "-/" and txt[7] in "-/":
        try:
            return datetime.strptime(txt[:10].replace("/", "-"), "%Y-%m-%d").strftime("%Y%m%d")
        except ValueError:
            pass
    return None


def _iter_days(start: str, end: str) -> int:
    s = datetime.strptime(start, "%Y%m%d").date()
    e = datetime.strptime(end, "%Y%m%d").date()
    if e < s:
        return 0
    return (e - s).days + 1


def _safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        s = str(v).strip()
        if s == "":
            return None
        return float(s)
    except Exception:
        return None


def _safe_int(v: Any) -> int | None:
    try:
        if v is None:
            return None
        s = str(v).strip()
        if s == "":
            return None
        return int(float(s))
    except Exception:
        return None


def _is_missing(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip() == ""
    return False


def _pick(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in d and d.get(k) is not None:
            return d.get(k)
    return None


def _pick_with_source(d: dict[str, Any], *keys: str) -> tuple[Any, str | None]:
    for k in keys:
        if k in d and d.get(k) is not None:
            return d.get(k), k
    return None, None


def _example_key(row: dict[str, Any]) -> str:
    rid = str(_pick(row, "race_id") or "")
    hid = str(_pick(row, "horse_id") or "")
    if rid and hid:
        return f"{rid}:{hid}"
    if rid:
        return rid
    if hid:
        return f"(race-missing):{hid}"
    return "(missing-key)"


def _priority_for_column(col: str, required_level: str) -> str:
    if col in ("race_id", "horse_id", "finish_position"):
        return "P0"
    if col in ("result_time", "race_date", "venue", "horse_name", "frame_number", "horse_number"):
        return "P1"
    if col in IMPORTANT_WARN_FIELDS:
        return "P2"
    if required_level in ("required", "required_if_result"):
        return "P0"
    return "P2"


def _priority_for_check(name: str) -> str:
    if name in ("race_without_horse_data", "horse_id_missing", "race_id_duplicate"):
        return "P0"
    if name in ("race_date_out_of_target_period", "venue_empty"):
        return "P1"
    return "P1"


def _derive_race_number_from_race_id(race_id: Any) -> int | None:
    s = str(race_id or "").strip()
    if len(s) < 2:
        return None
    tail = s[-2:]
    if not tail.isdigit():
        return None
    val = int(tail)
    if 1 <= val <= 12:
        return val
    return None


def _is_numeric_finish_position(v: Any) -> bool:
    if v is None:
        return False
    txt = str(v).strip()
    return txt.isdigit()


def _is_domain_allowed_missing(col: str, row: dict[str, Any], has_results: bool) -> bool:
    if col not in RESULT_REQUIRED_FIELDS or not has_results:
        return False

    fp = _pick(row, "finish_position")
    fp_txt = str(fp).strip() if fp is not None else ""
    fp_numeric = _is_numeric_finish_position(fp)

    if col == "margin":
        # 勝ち馬の着差空欄、または非完走/未確定レコードは許容。
        if fp_txt == "1":
            return True
        if (not fp_numeric) or _is_missing(fp):
            return True
        return False

    if col == "result_time":
        # 中止・取消・除外・失格等の非完走系はタイム欠損を許容。
        if (not fp_numeric) or _is_missing(fp):
            return True
        return False

    if col == "finish_position":
        page_type = str(_pick(row, "source_page_type") or "").lower()
        if page_type in ("entry", "shutuba"):
            return True
        return False

    return False


def _inspect_schema(db_path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {
        "tables": [],
        "candidate_columns": {},
        "race_results_json_key_presence": {},
    }
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        out["tables"] = tables

        cand_cols: dict[str, list[str]] = {}
        aliases = set(RACE_NUMBER_ALIAS_KEYS + ["horse_no", "bracket", "bracket_number"])
        for t in tables:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
            hits = [c for c in cols if c in aliases]
            if hits:
                cand_cols[t] = hits
        out["candidate_columns"] = cand_cols

        key_presence: dict[str, int] = {}
        for k in ["race_number", "race_no", "race_num", "round", "finish_position", "finish_time", "result_time", "margin"]:
            q = f"SELECT COUNT(*) FROM race_results_ultimate WHERE json_extract(data, '$.{k}') IS NOT NULL"
            try:
                key_presence[k] = int(conn.execute(q).fetchone()[0])
            except sqlite3.Error:
                key_presence[k] = 0
        out["race_results_json_key_presence"] = key_presence
    finally:
        conn.close()
    return out


def _load_races_meta(conn: sqlite3.Connection) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    races: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}

    for race_id, data_txt in conn.execute("SELECT race_id, data FROM races_ultimate"):
        try:
            payload = json.loads(data_txt or "{}")
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}

        race_date = _parse_date(_pick(payload, "date"))
        distance = _pick(payload, "distance")
        surface = _pick(payload, "surface")
        race_class = _pick(payload, "race_class")
        race_name = _pick(payload, "race_name")
        venue = _pick(payload, "venue")
        race_row = {
            "race_id": race_id,
            "race_date": race_date,
            "venue": venue,
            "distance": distance,
            "surface": surface,
            "class": race_class,
            "race_name": race_name,
            "race_type": _pick(payload, "track_type"),
            "source_page_type": "result",
        }
        races.append(race_row)
        by_id[str(race_id)] = race_row

    if not races:
        for race_id, kaisai_date, source, _created_at in conn.execute("SELECT race_id, kaisai_date, source, created_at FROM races"):
            race_row = {
                "race_id": race_id,
                "race_date": _parse_date(kaisai_date),
                "venue": None,
                "distance": None,
                "surface": None,
                "class": None,
                "race_name": None,
                "race_type": None,
                "source_page_type": source or "unknown",
            }
            races.append(race_row)
            by_id[str(race_id)] = race_row

    return races, by_id


def _load_horse_details(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in conn.execute("SELECT horse_id, horse_name, sire, dam, damsire FROM horse_details"):
        horse_id, horse_name, sire, dam, damsire = row
        out[str(horse_id)] = {
            "horse_id": horse_id,
            "horse_name": horse_name,
            "sire": sire,
            "dam": dam,
            "broodmare_sire": damsire,
        }
    return out


def _load_rows_from_db(conn: sqlite3.Connection, target: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    races, race_meta = _load_races_meta(conn)
    horse_details = _load_horse_details(conn)

    rows: list[dict[str, Any]] = []

    if target in ("all", "result", "race", "horse", "odds", "pedigree"):
        for race_id, data_txt in conn.execute("SELECT race_id, data FROM race_results_ultimate"):
            try:
                d = json.loads(data_txt or "{}")
                if not isinstance(d, dict):
                    d = {}
            except Exception:
                d = {}

            rid = str(_pick(d, "race_id") or race_id or "")
            meta = race_meta.get(rid, {})
            horse_id = str(_pick(d, "horse_id") or "")
            hdet = horse_details.get(horse_id, {})
            race_date = _parse_date(_pick(d, "race_date")) or _parse_date(_pick(meta, "race_date"))

            raw_distance = _pick(d, "distance") if _pick(d, "distance") is not None else _pick(meta, "distance")
            distance = _safe_int(raw_distance)
            surface = _pick(d, "surface") or _pick(meta, "surface")

            race_number_val, race_number_src = _pick_with_source(d, *RACE_NUMBER_ALIAS_KEYS)
            result_time_val, result_time_src = _pick_with_source(d, "finish_time", "time", "result_time")
            finish_position_val, finish_position_src = _pick_with_source(d, "finish_position", "finish")

            row = {
                "race_id": rid,
                "race_date": race_date,
                "year": race_date[:4] if race_date else None,
                "month": race_date[4:6] if race_date else None,
                "venue": _pick(d, "venue") or _pick(meta, "venue"),
                "race_name": _pick(d, "race_name") or _pick(meta, "race_name"),
                "race_number": race_number_val,
                "race_number_source": race_number_src,
                "race_number_derived": _derive_race_number_from_race_id(rid),
                "horse_id": horse_id,
                "horse_name": _pick(d, "horse_name") or _pick(hdet, "horse_name"),
                "frame_number": _pick(d, "bracket_number", "frame_number"),
                "horse_number": _pick(d, "horse_number"),
                "finish_position": finish_position_val,
                "finish_position_source": finish_position_src,
                "result_time": result_time_val,
                "result_time_source": result_time_src,
                "margin": _pick(d, "margin"),
                "odds": _pick(d, "odds"),
                "popularity": _pick(d, "popularity"),
                "horse_weight": _pick(d, "weight_kg", "weight", "horse_weight"),
                "jockey": _pick(d, "jockey_name", "jockey"),
                "trainer": _pick(d, "trainer_name", "trainer"),
                "sire": _pick(d, "sire") or _pick(hdet, "sire"),
                "dam": _pick(d, "dam") or _pick(hdet, "dam"),
                "broodmare_sire": _pick(d, "damsire", "broodmare_sire") or _pick(hdet, "broodmare_sire"),
                "race_type": _pick(d, "race_type") or _pick(meta, "race_type"),
                "distance": distance,
                "surface": surface,
                "class": _pick(d, "race_class") or _pick(meta, "class"),
                "source_page_type": "result",
            }
            rows.append(row)

    if not rows and target in ("all", "horse", "odds"):
        for row in conn.execute("SELECT race_id, horse_id, horse_name, horse_no, bracket, odds, popularity, weight, jockey_name, trainer_name FROM entries"):
            race_id, horse_id, horse_name, horse_no, bracket, odds, popularity, weight, jockey_name, trainer_name = row
            meta = race_meta.get(str(race_id), {})
            race_date = _parse_date(_pick(meta, "race_date"))
            rows.append(
                {
                    "race_id": race_id,
                    "race_date": race_date,
                    "year": race_date[:4] if race_date else None,
                    "month": race_date[4:6] if race_date else None,
                    "venue": _pick(meta, "venue"),
                    "race_name": _pick(meta, "race_name"),
                    "race_number": None,
                    "race_number_source": None,
                    "race_number_derived": _derive_race_number_from_race_id(race_id),
                    "horse_id": horse_id,
                    "horse_name": horse_name,
                    "frame_number": bracket,
                    "horse_number": horse_no,
                    "finish_position": None,
                    "finish_position_source": None,
                    "result_time": None,
                    "result_time_source": None,
                    "margin": None,
                    "odds": odds,
                    "popularity": popularity,
                    "horse_weight": weight,
                    "jockey": jockey_name,
                    "trainer": trainer_name,
                    "sire": None,
                    "dam": None,
                    "broodmare_sire": None,
                    "race_type": _pick(meta, "race_type"),
                    "distance": _safe_int(_pick(meta, "distance")),
                    "surface": _pick(meta, "surface"),
                    "class": _pick(meta, "class"),
                    "source_page_type": "entry",
                }
            )

    return rows, races, horse_details


def _load_rows_from_csv(csv_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = [dict(r) for r in reader]

    normalized: list[dict[str, Any]] = []
    for row in rows:
        race_date = _parse_date(_pick(row, "race_date", "date", "kaisai_date"))
        race_number_val, race_number_src = _pick_with_source(row, *RACE_NUMBER_ALIAS_KEYS)
        result_time_val, result_time_src = _pick_with_source(row, "result_time", "finish_time", "time")
        finish_position_val, finish_position_src = _pick_with_source(row, "finish_position", "finish")
        rid = _pick(row, "race_id")

        normalized.append(
            {
            "race_id": rid,
                "race_date": race_date,
                "year": race_date[:4] if race_date else None,
                "month": race_date[4:6] if race_date else None,
                "venue": _pick(row, "venue"),
                "race_name": _pick(row, "race_name"),
                "race_number": race_number_val,
                "race_number_source": race_number_src,
                "race_number_derived": _derive_race_number_from_race_id(rid),
                "horse_id": _pick(row, "horse_id"),
                "horse_name": _pick(row, "horse_name"),
                "frame_number": _pick(row, "frame_number", "bracket_number", "bracket"),
                "horse_number": _pick(row, "horse_number", "horse_no"),
                "finish_position": finish_position_val,
                "finish_position_source": finish_position_src,
                "result_time": result_time_val,
                "result_time_source": result_time_src,
                "margin": _pick(row, "margin"),
                "odds": _pick(row, "odds"),
                "popularity": _pick(row, "popularity"),
                "horse_weight": _pick(row, "horse_weight", "weight", "weight_kg"),
                "jockey": _pick(row, "jockey", "jockey_name"),
                "trainer": _pick(row, "trainer", "trainer_name"),
                "sire": _pick(row, "sire"),
                "dam": _pick(row, "dam"),
                "broodmare_sire": _pick(row, "broodmare_sire", "damsire"),
                "race_type": _pick(row, "race_type"),
                "distance": _safe_int(_pick(row, "distance")),
                "surface": _pick(row, "surface"),
                "class": _pick(row, "class", "race_class"),
                "source_page_type": _pick(row, "source_page_type", "source") or "csv",
            }
        )

    races = []
    seen: set[str] = set()
    for r in normalized:
        rid = str(_pick(r, "race_id") or "")
        if not rid or rid in seen:
            continue
        seen.add(rid)
        races.append(
            {
                "race_id": rid,
                "race_date": _pick(r, "race_date"),
                "venue": _pick(r, "venue"),
                "distance": _pick(r, "distance"),
                "surface": _pick(r, "surface"),
                "class": _pick(r, "class"),
                "race_name": _pick(r, "race_name"),
                "race_type": _pick(r, "race_type"),
                "source_page_type": _pick(r, "source_page_type"),
            }
        )

    horse_details: dict[str, dict[str, Any]] = {}
    for r in normalized:
        hid = str(_pick(r, "horse_id") or "")
        if not hid:
            continue
        horse_details[hid] = {
            "horse_id": hid,
            "horse_name": _pick(r, "horse_name"),
            "sire": _pick(r, "sire"),
            "dam": _pick(r, "dam"),
            "broodmare_sire": _pick(r, "broodmare_sire"),
        }

    return normalized, races, horse_details


def _in_range(race_date: str | None, start_date: str | None, end_date: str | None) -> bool:
    if race_date is None:
        return True
    if start_date and race_date < start_date:
        return False
    if end_date and race_date > end_date:
        return False
    return True


def _filter_by_target(rows: list[dict[str, Any]], target: str) -> list[dict[str, Any]]:
    if target == "all":
        return rows
    if target == "race":
        return rows
    if target == "horse":
        return rows
    if target == "result":
        return [r for r in rows if not _is_missing(_pick(r, "finish_position")) or not _is_missing(_pick(r, "result_time"))]
    if target == "pedigree":
        return [r for r in rows if not _is_missing(_pick(r, "horse_id"))]
    if target == "odds":
        return [r for r in rows if not _is_missing(_pick(r, "odds")) or not _is_missing(_pick(r, "popularity"))]
    return rows


def _audit_column_missingness(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    has_results = any(
        (not _is_missing(_pick(r, "finish_position")))
        or (not _is_missing(_pick(r, "result_time")))
        or (not _is_missing(_pick(r, "margin")))
        for r in rows
    )

    required_fields = list(CORE_REQUIRED_FIELDS)
    if has_results:
        required_fields += RESULT_REQUIRED_FIELDS

    columns = required_fields + [x for x in IMPORTANT_WARN_FIELDS if x not in required_fields]
    total = len(rows)
    out: list[dict[str, Any]] = []

    for col in columns:
        missing = 0
        domain_allowed_missing = 0
        derived_filled = 0
        true_missing = 0
        alias_counter: Counter[str] = Counter()
        true_missing_examples: list[str] = []
        domain_allowed_examples: list[str] = []
        derived_examples: list[str] = []

        for r in rows:
            miss = _is_missing(_pick(r, col))
            if not miss:
                if col == "race_number":
                    src = str(_pick(r, "race_number_source") or "")
                    if src and src != "race_number":
                        alias_counter[src] += 1
                continue

            missing += 1
            if _is_domain_allowed_missing(col, r, has_results):
                domain_allowed_missing += 1
                ex = _example_key(r)
                if len(domain_allowed_examples) < 5 and ex not in domain_allowed_examples:
                    domain_allowed_examples.append(ex)
                continue

            if col == "race_number" and _safe_int(_pick(r, "race_number_derived")) is not None:
                derived_filled += 1
                ex = _example_key(r)
                if len(derived_examples) < 5 and ex not in derived_examples:
                    derived_examples.append(ex)
                continue

            true_missing += 1
            ex = _example_key(r)
            if len(true_missing_examples) < 5 and ex not in true_missing_examples:
                true_missing_examples.append(ex)

        rate = (missing / total) if total > 0 else 0.0
        true_rate = (true_missing / total) if total > 0 else 0.0

        if col in CORE_REQUIRED_FIELDS:
            required_level = "required"
            severity = "fail" if true_rate > 0 else ("warn" if rate > 0 else "pass")
        elif col in RESULT_REQUIRED_FIELDS:
            required_level = "required_if_result"
            severity = "fail" if has_results and true_rate > 0 else ("warn" if has_results and rate > 0 else "pass")
        elif col in IMPORTANT_WARN_FIELDS:
            required_level = "important_warn"
            severity = "warn" if true_rate > 0.10 else "pass"
        else:
            required_level = "optional"
            severity = "info"

        out.append(
            {
                "column": col,
                "missing_count": missing,
                "raw_missing_count": missing,
                "total_count": total,
                "missing_rate": rate,
                "true_missing_count": true_missing,
                "true_missing_rate": true_rate,
                "domain_allowed_missing_count": domain_allowed_missing,
                "derived_field_applied_count": derived_filled,
                "alias_candidate": sorted(alias_counter.keys()),
                "true_missing_example_keys": true_missing_examples,
                "domain_allowed_example_keys": domain_allowed_examples,
                "derived_example_keys": derived_examples,
                "severity": severity,
                "required_level": required_level,
            }
        )

    return out, required_fields, IMPORTANT_WARN_FIELDS


def _audit_group_missingness(rows: list[dict[str, Any]], required_fields: list[str]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}

    for g in GROUP_KEYS:
        stats: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = str(_pick(row, g) or "(missing)")
            cur = stats.setdefault(
                key,
                {
                    "group": g,
                    "value": key,
                    "total_count": 0,
                    "required_missing_row_count": 0,
                    "required_missing_rate": 0.0,
                },
            )
            cur["total_count"] += 1
            missing_any = any(_is_missing(_pick(row, f)) for f in required_fields)
            if missing_any:
                cur["required_missing_row_count"] += 1

        for item in stats.values():
            total = int(item["total_count"])
            miss = int(item["required_missing_row_count"])
            item["required_missing_rate"] = (miss / total) if total > 0 else 0.0

        grouped[g] = sorted(stats.values(), key=lambda x: (x["required_missing_rate"], x["total_count"]), reverse=True)

    return grouped


def _coverage_and_consistency(ctx: AuditContext, required_fields: list[str]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    rows = ctx.rows
    races = [r for r in ctx.races if _in_range(_pick(r, "race_date"), ctx.start_date, ctx.end_date)]

    race_ids_in_rows = {str(_pick(r, "race_id") or "") for r in rows if str(_pick(r, "race_id") or "")}
    race_ids_in_races = {str(_pick(r, "race_id") or "") for r in races if str(_pick(r, "race_id") or "")}

    horses_by_race: dict[str, set[str]] = defaultdict(set)
    horse_num_by_race: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        rid = str(_pick(r, "race_id") or "")
        if not rid:
            continue
        hid = str(_pick(r, "horse_id") or "")
        if hid:
            horses_by_race[rid].add(hid)
        hn = _safe_int(_pick(r, "horse_number"))
        if hn is not None:
            horse_num_by_race[rid].append(hn)

    start = ctx.start_date
    end = ctx.end_date
    target_days = _iter_days(start, end) if start and end else None
    scraped_days = {str(_pick(r, "race_date") or "") for r in races if str(_pick(r, "race_date") or "")}

    race_without_horse_data = [rid for rid in race_ids_in_races if len(horses_by_race.get(rid, set())) == 0]

    required_horse_ids = {str(_pick(r, "horse_id") or "") for r in rows if str(_pick(r, "horse_id") or "")}
    horse_without_pedigree = 0
    for hid in required_horse_ids:
        pd = ctx.horse_details.get(hid, {})
        if _is_missing(_pick(pd, "sire")) and _is_missing(_pick(pd, "dam")) and _is_missing(_pick(pd, "broodmare_sire")):
            horse_without_pedigree += 1

    horses_per_race = [len(v) for v in horses_by_race.values() if len(v) > 0]
    avg_horses = (sum(horses_per_race) / len(horses_per_race)) if horses_per_race else 0.0

    coverage = {
        "target_period": {
            "start_date": start,
            "end_date": end,
            "target_days": target_days,
        },
        "scraped_holding_days": len(scraped_days),
        "scraped_race_count": len(race_ids_in_rows),
        "race_with_missing_horse_data_count": len(race_without_horse_data),
        "horse_with_missing_pedigree_count": horse_without_pedigree,
        "race_horse_count": {
            "min": min(horses_per_race) if horses_per_race else 0,
            "max": max(horses_per_race) if horses_per_race else 0,
            "avg": avg_horses,
        },
    }

    checks: list[dict[str, Any]] = []

    def add_check(name: str, level: str, failed_count: int, detail: str) -> None:
        status = "pass" if failed_count == 0 else ("warn" if level == "warn" else "fail")
        checks.append({"name": name, "level": level, "failed_count": failed_count, "status": status, "detail": detail})

    race_dupe_count = 0
    if races:
        seen_races: set[str] = set()
        for r in races:
            rid = str(_pick(r, "race_id") or "")
            if rid in seen_races:
                race_dupe_count += 1
            seen_races.add(rid)
    add_check("race_id_duplicate", "fail", race_dupe_count, "Duplicate race_id rows in race master")

    horse_missing_count = sum(1 for r in rows if _is_missing(_pick(r, "horse_id")))
    add_check("horse_id_missing", "fail", horse_missing_count, "horse_id should not be missing")

    horse_dupe_count = 0
    if rows:
        seen_pairs: set[tuple[str, str]] = set()
        for r in rows:
            pair = (str(_pick(r, "race_id") or ""), str(_pick(r, "horse_id") or ""))
            if not pair[0] or not pair[1]:
                continue
            if pair in seen_pairs:
                horse_dupe_count += 1
            seen_pairs.add(pair)
    add_check("horse_id_duplicate_in_race", "fail", horse_dupe_count, "Duplicate horse_id within same race_id")

    same_race_horse_num_dupe = 0
    for rid, nums in horse_num_by_race.items():
        if len(nums) != len(set(nums)):
            same_race_horse_num_dupe += 1
    add_check("horse_number_duplicate_in_race", "fail", same_race_horse_num_dupe, "horse_number duplicates within race_id")

    finish_range_fail = 0
    for r in rows:
        fp = _safe_int(_pick(r, "finish_position"))
        if fp is None:
            continue
        if fp < 1 or fp > 30:
            finish_range_fail += 1
    add_check("finish_position_range", "fail", finish_range_fail, "finish_position must be in [1,30]")

    starters_range_fail = 0
    for rid, hs in horses_by_race.items():
        count = len(hs)
        if count < 1 or count > 30:
            starters_range_fail += 1
    add_check("starters_per_race_range", "fail", starters_range_fail, "Starters count per race must be in [1,30]")

    out_of_period = 0
    if ctx.start_date and ctx.end_date:
        for r in rows:
            rd = _parse_date(_pick(r, "race_date"))
            if rd and not _in_range(rd, ctx.start_date, ctx.end_date):
                out_of_period += 1
    add_check("race_date_out_of_target_period", "fail", out_of_period, "Rows outside target period")

    venue_empty = sum(1 for r in rows if _is_missing(_pick(r, "venue")))
    add_check("venue_empty", "fail", venue_empty, "venue should not be empty")

    odds_non_numeric = 0
    for r in rows:
        v = _pick(r, "odds")
        if _is_missing(v):
            continue
        if _safe_float(v) is None:
            odds_non_numeric += 1
    add_check("odds_non_numeric", "warn", odds_non_numeric, "odds should be numeric when present")

    pop_non_numeric = 0
    for r in rows:
        v = _pick(r, "popularity")
        if _is_missing(v):
            continue
        if _safe_int(v) is None:
            pop_non_numeric += 1
    add_check("popularity_non_numeric", "warn", pop_non_numeric, "popularity should be numeric when present")

    add_check(
        "race_without_horse_data",
        "fail",
        len(race_without_horse_data),
        "race_id exists but horse rows are missing",
    )

    pedigree_missing_rate = (horse_without_pedigree / len(required_horse_ids)) if required_horse_ids else 0.0
    ped_warn_count = horse_without_pedigree if pedigree_missing_rate > 0.20 else 0
    add_check(
        "horse_without_pedigree",
        "warn",
        ped_warn_count,
        "pedigree missing rate >20% is warn",
    )

    col_missing, _, important_cols = _audit_column_missingness(rows)
    required_fail_hits = 0
    for item in col_missing:
        level = str(item.get("required_level") or "")
        rate = float(item.get("missing_rate") or 0.0)
        if level in ("required", "required_if_result") and float(item.get("true_missing_rate") or 0.0) > 0.0:
            required_fail_hits += 1
    add_check(
        "required_column_missing_rate_over_0pct",
        "fail",
        required_fail_hits,
        "required / required_if_result column missing rate must be 0%",
    )

    important_warn_hits = 0
    for item in col_missing:
        if item["column"] in important_cols and float(item.get("true_missing_rate") or 0.0) > 0.10:
            important_warn_hits += 1
    add_check(
        "important_column_missing_over_10pct",
        "warn",
        important_warn_hits,
        "important columns with missing rate >10%",
    )

    fail_checks = [c for c in checks if c["status"] == "fail"]
    warn_checks = [c for c in checks if c["status"] == "warn"]
    if fail_checks:
        verdict = "fail"
    elif warn_checks:
        verdict = "warn"
    else:
        verdict = "pass"

    summary = {
        "verdict": verdict,
        "fail_count": len(fail_checks),
        "warn_count": len(warn_checks),
        "pass_count": len([c for c in checks if c["status"] == "pass"]),
        "required_fields": required_fields,
        "row_count": len(rows),
    }

    return coverage, checks, [summary]


def _render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Scrape Missingness Audit")
    lines.append("")

    summary = (report.get("summary") or [{}])[0]
    lines.append(f"- Verdict: **{summary.get('verdict', 'unknown')}**")
    lines.append(f"- Rows Audited: {summary.get('row_count', 0)}")
    lines.append(f"- Fail Checks: {summary.get('fail_count', 0)}")
    lines.append(f"- Warn Checks: {summary.get('warn_count', 0)}")
    lines.append("")

    cov = report.get("coverage", {})
    lines.append("## Coverage")
    lines.append("")
    lines.append(f"- Target Days: {((cov.get('target_period') or {}).get('target_days'))}")
    lines.append(f"- Scraped Holding Days: {cov.get('scraped_holding_days')}")
    lines.append(f"- Scraped Race Count: {cov.get('scraped_race_count')}")
    lines.append(f"- Race with Missing Horse Data: {cov.get('race_with_missing_horse_data_count')}")
    lines.append(f"- Horse with Missing Pedigree: {cov.get('horse_with_missing_pedigree_count')}")
    lines.append("")

    lines.append("## Column Missingness")
    lines.append("")
    lines.append("| column | missing_count | true_missing_count | domain_allowed_missing_count | derived_field_applied_count | total_count | missing_rate | true_missing_rate | severity | required_level |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---|---|")
    for item in report.get("column_missingness", []):
        lines.append(
            "| {column} | {missing_count} | {true_missing_count} | {domain_allowed_missing_count} | {derived_field_applied_count} | {total_count} | {missing_rate:.4f} | {true_missing_rate:.4f} | {severity} | {required_level} |".format(
                column=item.get("column"),
                missing_count=item.get("missing_count"),
                true_missing_count=item.get("true_missing_count"),
                domain_allowed_missing_count=item.get("domain_allowed_missing_count"),
                derived_field_applied_count=item.get("derived_field_applied_count"),
                total_count=item.get("total_count"),
                missing_rate=float(item.get("missing_rate") or 0.0),
                true_missing_rate=float(item.get("true_missing_rate") or 0.0),
                severity=item.get("severity"),
                required_level=item.get("required_level"),
            )
        )
    lines.append("")

    lines.append("## Consistency Checks")
    lines.append("")
    lines.append("| name | status | failed_count | level | detail |")
    lines.append("|---|---|---:|---|---|")
    for c in report.get("consistency_checks", []):
        lines.append(
            "| {name} | {status} | {failed_count} | {level} | {detail} |".format(
                name=c.get("name"),
                status=c.get("status"),
                failed_count=c.get("failed_count"),
                level=c.get("level"),
                detail=c.get("detail"),
            )
        )
    lines.append("")

    lines.append("## Group Missingness (Top 5 each)")
    lines.append("")
    for g, items in (report.get("group_missingness") or {}).items():
        lines.append(f"### {g}")
        lines.append("")
        lines.append("| value | total_count | required_missing_row_count | required_missing_rate |")
        lines.append("|---|---:|---:|---:|")
        for item in list(items)[:5]:
            lines.append(
                "| {value} | {total_count} | {required_missing_row_count} | {required_missing_rate:.4f} |".format(
                    value=item.get("value"),
                    total_count=item.get("total_count"),
                    required_missing_row_count=item.get("required_missing_row_count"),
                    required_missing_rate=float(item.get("required_missing_rate") or 0.0),
                )
            )
        lines.append("")

    return "\n".join(lines) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Read-only missingness audit for scraped data")
    p.add_argument("--input-db", default=None, help="SQLite DB path (read-only)")
    p.add_argument("--input-csv", default=None, help="CSV path for audit input")
    p.add_argument("--start-date", default=None, help="Start date (YYYYMMDD or YYYY-MM-DD)")
    p.add_argument("--end-date", default=None, help="End date (YYYYMMDD or YYYY-MM-DD)")
    p.add_argument("--target", choices=["all", "race", "horse", "result", "pedigree", "odds"], default="all")
    p.add_argument("--output", default=str(DEFAULT_OUTPUT_JSON), help="JSON output path")
    return p


def _build_fail_reason_ranking(column_missingness: list[dict[str, Any]], checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reasons: Counter[str] = Counter()
    for item in column_missingness:
        level = str(item.get("required_level") or "")
        tmiss = int(item.get("true_missing_count") or 0)
        if tmiss <= 0:
            continue
        if level in ("required", "required_if_result"):
            reasons[f"required:{item.get('column')}"] += tmiss

    for c in checks:
        if str(c.get("status")) == "fail":
            reasons[f"check:{c.get('name')}"] += int(c.get("failed_count") or 0)

    out = [{"reason": k, "count": v} for k, v in reasons.most_common()]
    return out


def _detect_source_empty_result_cells(rows: list[dict[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    examples: list[str] = []
    count = 0
    for row in rows:
        page_type = str(_pick(row, "source_page_type") or "").lower()
        if page_type not in ("result", "result_page", "race_detail"):
            continue
        key = _example_key(row)
        if key in seen:
            continue
        seen.add(key)

        has_row_identity = not _is_missing(_pick(row, "horse_id")) or not _is_missing(_pick(row, "horse_number"))
        if not has_row_identity:
            continue

        has_context = (
            not _is_missing(_pick(row, "horse_name"))
            or not _is_missing(_pick(row, "frame_number"))
            or not _is_missing(_pick(row, "horse_number"))
        )
        if not has_context:
            continue

        finish_missing = _is_missing(_pick(row, "finish_position"))
        time_missing = _is_missing(_pick(row, "result_time"))
        margin_missing = _is_missing(_pick(row, "margin"))
        if finish_missing and time_missing and margin_missing:
            count += 1
            if len(examples) < 10:
                examples.append(key)

    return {
        "count": count,
        "example_keys": examples,
    }


def _build_repair_reason_breakdown(
    column_missingness: list[dict[str, Any]],
    checks: list[dict[str, Any]],
    source_empty_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for item in column_missingness:
        col = str(item.get("column") or "")
        level = str(item.get("required_level") or "")
        severity = str(item.get("severity") or "info")

        true_missing_count = int(item.get("true_missing_count") or 0)
        if true_missing_count > 0:
            rows.append(
                {
                    "reason": "true-missing",
                    "column": col,
                    "required_level": level,
                    "count": true_missing_count,
                    "severity": severity,
                    "priority": _priority_for_column(col, level),
                    "example_keys": list(item.get("true_missing_example_keys") or []),
                }
            )

        domain_allowed_count = int(item.get("domain_allowed_missing_count") or 0)
        if domain_allowed_count > 0:
            rows.append(
                {
                    "reason": "domain-allowed-missing",
                    "column": col,
                    "required_level": level,
                    "count": domain_allowed_count,
                    "severity": "info",
                    "priority": "Domain allowed",
                    "example_keys": list(item.get("domain_allowed_example_keys") or []),
                }
            )

        derived_count = int(item.get("derived_field_applied_count") or 0)
        if derived_count > 0:
            rows.append(
                {
                    "reason": "derived-field-candidate",
                    "column": col,
                    "required_level": level,
                    "count": derived_count,
                    "severity": "warn",
                    "priority": "Schema review",
                    "example_keys": list(item.get("derived_example_keys") or []),
                }
            )

        aliases = list(item.get("alias_candidate") or [])
        if aliases:
            rows.append(
                {
                    "reason": "alias-candidate",
                    "column": col,
                    "required_level": level,
                    "count": len(aliases),
                    "severity": "warn",
                    "priority": "Schema review",
                    "example_keys": aliases[:5],
                }
            )

    for c in checks:
        failed_count = int(c.get("failed_count") or 0)
        if failed_count <= 0:
            continue
        status = str(c.get("status") or "")
        if status not in ("fail", "warn"):
            continue
        name = str(c.get("name") or "")
        rows.append(
            {
                "reason": f"consistency:{name}",
                "column": "(check)",
                "required_level": "consistency",
                "count": failed_count,
                "severity": status,
                "priority": _priority_for_check(name),
                "example_keys": [],
            }
        )

    source_empty_count = int(source_empty_summary.get("count") or 0)
    if source_empty_count > 0:
        rows.append(
            {
                "reason": "source-empty-result-cells",
                "column": "finish_position",
                "required_level": "required_if_result",
                "count": source_empty_count,
                "severity": "warn",
                "priority": "P0",
                "example_keys": list(source_empty_summary.get("example_keys") or []),
            }
        )

    return sorted(rows, key=lambda x: (int(x.get("count") or 0), str(x.get("priority") or "")), reverse=True)


def _summarize_by_priority(rows: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {"P0": 0, "P1": 0, "P2": 0, "Schema review": 0, "Domain allowed": 0}
    for r in rows:
        p = str(r.get("priority") or "")
        if p in out:
            out[p] += int(r.get("count") or 0)
    return out


def main() -> int:
    args = _build_parser().parse_args()

    start_date = _parse_date(args.start_date)
    end_date = _parse_date(args.end_date)
    if start_date and end_date and end_date < start_date:
        raise SystemExit("error: end-date must be >= start-date")

    if args.input_csv:
        csv_path = Path(args.input_csv)
        if not csv_path.exists():
            raise SystemExit(f"error: CSV not found: {csv_path}")
        rows, races, horse_details = _load_rows_from_csv(csv_path)
        input_source = {"type": "csv", "path": str(csv_path)}
        schema_info = {
            "tables": [],
            "candidate_columns": {},
            "race_results_json_key_presence": {},
        }
    else:
        db_path = Path(args.input_db) if args.input_db else DEFAULT_DB_PATH
        if not db_path.exists():
            raise SystemExit(f"error: DB not found: {db_path}")
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        try:
            rows, races, horse_details = _load_rows_from_db(conn, args.target)
        finally:
            conn.close()
        input_source = {"type": "db", "path": str(db_path)}
        schema_info = _inspect_schema(db_path)

    rows = [r for r in rows if _in_range(_parse_date(_pick(r, "race_date")), start_date, end_date)]
    rows = _filter_by_target(rows, args.target)

    ctx = AuditContext(
        rows=rows,
        races=races,
        horse_details=horse_details,
        target=args.target,
        start_date=start_date,
        end_date=end_date,
    )

    column_missingness, required_fields, _important_fields = _audit_column_missingness(rows)
    group_missingness = _audit_group_missingness(rows, required_fields)
    coverage, consistency_checks, summary = _coverage_and_consistency(ctx, required_fields)

    race_number_item = next((x for x in column_missingness if x.get("column") == "race_number"), {})
    schema_mismatch_suspected = []
    if race_number_item:
        raw_missing = int(race_number_item.get("raw_missing_count") or 0)
        true_missing = int(race_number_item.get("true_missing_count") or 0)
        derived_cnt = int(race_number_item.get("derived_field_applied_count") or 0)
        if raw_missing > 0 and derived_cnt > 0 and true_missing <= max(3, int(raw_missing * 0.001)):
            schema_mismatch_suspected.append("race_number: payload key absent; mostly derivable from race_id suffix")

    derived_field_candidate = {
        "race_number": {
            "method": "race_id_suffix_last2",
            "applied_count": int(race_number_item.get("derived_field_applied_count") or 0),
            "true_missing_count": int(race_number_item.get("true_missing_count") or 0),
        }
    }

    alias_candidate = {
        "race_number": race_number_item.get("alias_candidate") or [],
        "schema": schema_info.get("candidate_columns") or {},
    }

    source_empty_result_cells_summary = _detect_source_empty_result_cells(rows)
    fail_reason_ranking = _build_fail_reason_ranking(column_missingness, consistency_checks)
    repair_reason_breakdown = _build_repair_reason_breakdown(
        column_missingness,
        consistency_checks,
        source_empty_result_cells_summary,
    )

    true_missing_summary = {
        "total_true_missing_count": sum(int(x.get("true_missing_count") or 0) for x in column_missingness),
        "by_column": [
            {
                "column": str(x.get("column") or ""),
                "count": int(x.get("true_missing_count") or 0),
                "required_level": str(x.get("required_level") or ""),
                "priority": _priority_for_column(str(x.get("column") or ""), str(x.get("required_level") or "")),
            }
            for x in column_missingness
            if int(x.get("true_missing_count") or 0) > 0
        ],
    }

    domain_allowed_missing_summary = {
        "total_domain_allowed_missing_count": sum(int(x.get("domain_allowed_missing_count") or 0) for x in column_missingness),
        "by_column": [
            {
                "column": str(x.get("column") or ""),
                "count": int(x.get("domain_allowed_missing_count") or 0),
            }
            for x in column_missingness
            if int(x.get("domain_allowed_missing_count") or 0) > 0
        ],
    }

    schema_review_summary = {
        "schema_mismatch_suspected": schema_mismatch_suspected,
        "derived_field_candidates": derived_field_candidate,
        "alias_candidate": alias_candidate,
        "total_schema_review_count": sum(
            int(x.get("derived_field_applied_count") or 0) + len(list(x.get("alias_candidate") or []))
            for x in column_missingness
        ),
    }

    report = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "input_source": input_source,
        "target": args.target,
        "start_date": start_date,
        "end_date": end_date,
        "row_count": len(rows),
        "column_missingness": column_missingness,
        "group_missingness": group_missingness,
        "coverage": coverage,
        "consistency_checks": consistency_checks,
        "summary": summary,
        "schema_info": schema_info,
        "schema_mismatch_suspected": schema_mismatch_suspected,
        "alias_candidate": alias_candidate,
        "derived_field_candidate": derived_field_candidate,
        "fail_reason_ranking": fail_reason_ranking,
        "repair_reason_breakdown": repair_reason_breakdown,
        "source_empty_result_cells_summary": source_empty_result_cells_summary,
        "true_missing_summary": true_missing_summary,
        "domain_allowed_missing_summary": domain_allowed_missing_summary,
        "schema_review_summary": {
            **schema_review_summary,
            "priority_summary": _summarize_by_priority(repair_reason_breakdown),
        },
    }

    out_json = Path(args.output)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    out_md = out_json.with_suffix(".md")
    out_md.write_text(_render_markdown(report), encoding="utf-8")

    verdict = summary[0].get("verdict", "unknown") if summary else "unknown"
    print(json.dumps({
        "verdict": verdict,
        "row_count": len(rows),
        "json_report": str(out_json),
        "md_report": str(out_md),
        "target": args.target,
    }, ensure_ascii=False))

    return 0 if verdict in ("pass", "warn") else 1


if __name__ == "__main__":
    raise SystemExit(main())
