#!/usr/bin/env python3
"""Build read-only scrape refresh plans with safeguard policies.

This phase is planning-only (dry-run). No DB writes are performed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT_DIR / "keiba" / "data" / "keiba_ultimate.db"
DEFAULT_OUTPUT = ROOT_DIR / "reports" / "scrape_refresh_plan.json"
DEFAULT_AVG_SEC_PER_REQ = 1.2
CURRENT_PARSER_VERSION = "2.0.0"

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


@dataclass
class RowQuality:
    required_missing: bool
    required_missing_fields: list[str]
    important_missing_count: int
    invalid_value: bool
    stale_parser_version: bool
    stale_fetched_at: bool
    source_html_missing: bool
    quality_score: float
    error_page_like: bool


@dataclass
class PlanDecision:
    key: str
    action: str
    reason: str
    quality_score: float
    missing_fields: list[str]
    parser_version: str | None
    fetched_at: str | None


def _parse_date(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(s[:19], fmt)
            return dt.strftime("%Y%m%d")
        except ValueError:
            pass
    return None


def _parse_dt(v: Any) -> datetime | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            pass
    return None


def _safe_int(v: Any) -> int | None:
    try:
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        return int(float(s))
    except Exception:
        return None


def _safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        return float(s)
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


def _row_key(row: dict[str, Any]) -> str:
    rid = str(_pick(row, "race_id") or "")
    hid = str(_pick(row, "horse_id") or "")
    if rid and hid:
        return f"{rid}:{hid}"
    if rid:
        return rid
    return hashlib.sha1(json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _calc_record_hash(row: dict[str, Any]) -> str:
    fields = {
        "race_id": _pick(row, "race_id"),
        "race_date": _pick(row, "race_date"),
        "venue": _pick(row, "venue"),
        "race_number": _pick(row, "race_number"),
        "horse_id": _pick(row, "horse_id"),
        "horse_name": _pick(row, "horse_name"),
        "frame_number": _pick(row, "frame_number"),
        "horse_number": _pick(row, "horse_number"),
        "finish_position": _pick(row, "finish_position"),
        "result_time": _pick(row, "result_time"),
        "margin": _pick(row, "margin"),
        "odds": _pick(row, "odds"),
        "popularity": _pick(row, "popularity"),
        "parser_version": _pick(row, "parser_version"),
    }
    raw = json.dumps(fields, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _version_tuple(v: str) -> tuple[int, int, int]:
    parts = [p for p in str(v).strip().split(".") if p]
    nums: list[int] = []
    for p in parts[:3]:
        nums.append(_safe_int(p) or 0)
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]


def _version_lt(a: str | None, b: str) -> bool:
    if not a:
        return False
    return _version_tuple(a) < _version_tuple(b)


def _expected_race_ids(conn: sqlite3.Connection, start_date: str | None, end_date: str | None) -> set[str]:
    out: set[str] = set()
    try:
        for race_id, data_txt in conn.execute("SELECT race_id, data FROM races_ultimate"):
            date_txt = None
            try:
                d = json.loads(data_txt or "{}")
                if isinstance(d, dict):
                    date_txt = _parse_date(_pick(d, "date", "race_date"))
            except Exception:
                date_txt = None
            if start_date and date_txt and date_txt < start_date:
                continue
            if end_date and date_txt and date_txt > end_date:
                continue
            out.add(str(race_id))
    except sqlite3.Error:
        pass

    return out


def _normalize_row(raw: dict[str, Any], source_page_type: str = "result") -> dict[str, Any]:
    race_date = _parse_date(_pick(raw, "race_date", "date", "kaisai_date"))
    parser_version = _pick(raw, "parser_version")
    fetched_at = _pick(raw, "fetched_at", "created_at", "updated_at")
    source_html = _pick(raw, "source_html", "html", "raw_html")
    source_html_present = bool(source_html and str(source_html).strip())

    row = {
        "race_id": _pick(raw, "race_id"),
        "race_date": race_date,
        "venue": _pick(raw, "venue"),
        "race_number": _pick(raw, "race_number"),
        "horse_id": _pick(raw, "horse_id"),
        "horse_name": _pick(raw, "horse_name"),
        "frame_number": _pick(raw, "frame_number", "bracket_number", "bracket"),
        "horse_number": _pick(raw, "horse_number", "horse_no"),
        "finish_position": _pick(raw, "finish_position", "finish"),
        "result_time": _pick(raw, "result_time", "finish_time", "time"),
        "margin": _pick(raw, "margin"),
        "odds": _pick(raw, "odds"),
        "popularity": _pick(raw, "popularity"),
        "horse_weight": _pick(raw, "horse_weight", "weight_kg", "weight"),
        "jockey": _pick(raw, "jockey", "jockey_name"),
        "trainer": _pick(raw, "trainer", "trainer_name"),
        "sire": _pick(raw, "sire"),
        "dam": _pick(raw, "dam"),
        "broodmare_sire": _pick(raw, "broodmare_sire", "damsire"),
        "source_page_type": _pick(raw, "source_page_type") or source_page_type,
        "parser_version": parser_version,
        "fetched_at": fetched_at,
        "source_html_present": source_html_present,
        "quality_score": _safe_float(_pick(raw, "quality_score")),
        "record_hash": _pick(raw, "record_hash"),
        "http_status": _safe_int(_pick(raw, "http_status", "status_code")),
        "is_error_page": bool(_pick(raw, "is_error_page")),
    }

    if not row["record_hash"]:
        row["record_hash"] = _calc_record_hash(row)

    return row


def _load_rows_from_db(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    try:
        for race_id, data_txt, created_at in conn.execute("SELECT race_id, data, created_at FROM race_results_ultimate"):
            try:
                d = json.loads(data_txt or "{}")
                if not isinstance(d, dict):
                    d = {}
            except Exception:
                d = {}
            d.setdefault("race_id", race_id)
            d.setdefault("created_at", created_at)
            rows.append(_normalize_row(d, source_page_type="result"))
    except sqlite3.Error:
        pass

    if rows:
        return rows

    try:
        for row in conn.execute(
            "SELECT race_id, horse_id, horse_name, horse_no, bracket, odds, popularity, weight, jockey_name, trainer_name, created_at FROM entries"
        ):
            (
                race_id,
                horse_id,
                horse_name,
                horse_no,
                bracket,
                odds,
                popularity,
                weight,
                jockey_name,
                trainer_name,
                created_at,
            ) = row
            r = {
                "race_id": race_id,
                "horse_id": horse_id,
                "horse_name": horse_name,
                "horse_number": horse_no,
                "frame_number": bracket,
                "odds": odds,
                "popularity": popularity,
                "horse_weight": weight,
                "jockey": jockey_name,
                "trainer": trainer_name,
                "created_at": created_at,
                "source_page_type": "entry",
            }
            rows.append(_normalize_row(r, source_page_type="entry"))
    except sqlite3.Error:
        pass

    return rows


def _load_rows_from_csv(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            out.append(_normalize_row(dict(row), source_page_type=str(row.get("source_page_type") or "csv")))
    return out


def _in_period(row: dict[str, Any], start_date: str | None, end_date: str | None) -> bool:
    rd = _parse_date(_pick(row, "race_date"))
    if start_date and rd and rd < start_date:
        return False
    if end_date and rd and rd > end_date:
        return False
    return True


def _target_filter(rows: list[dict[str, Any]], target: str) -> list[dict[str, Any]]:
    if target == "all":
        return rows
    if target == "race":
        return rows
    if target == "horse":
        return [r for r in rows if not _is_missing(_pick(r, "horse_id"))]
    if target == "result":
        return [r for r in rows if not _is_missing(_pick(r, "finish_position")) or not _is_missing(_pick(r, "result_time"))]
    if target == "pedigree":
        return [r for r in rows if not _is_missing(_pick(r, "sire")) or not _is_missing(_pick(r, "dam"))]
    if target == "odds":
        return [r for r in rows if not _is_missing(_pick(r, "odds")) or not _is_missing(_pick(r, "popularity"))]
    return rows


def _assess_row_quality(row: dict[str, Any], current_parser_version: str, stale_days: int) -> RowQuality:
    has_result = (not _is_missing(_pick(row, "finish_position"))) or (not _is_missing(_pick(row, "result_time")))
    required_fields = list(CORE_REQUIRED_FIELDS)
    if has_result:
        required_fields += RESULT_REQUIRED_FIELDS

    required_missing_fields = [f for f in required_fields if _is_missing(_pick(row, f))]
    required_missing = len(required_missing_fields) > 0

    important_missing_count = sum(1 for f in IMPORTANT_WARN_FIELDS if _is_missing(_pick(row, f)))

    invalid_value = False
    odds = _pick(row, "odds")
    pop = _pick(row, "popularity")
    if not _is_missing(odds) and _safe_float(odds) is None:
        invalid_value = True
    if not _is_missing(pop) and _safe_int(pop) is None:
        invalid_value = True

    parser_version = str(_pick(row, "parser_version") or "")
    stale_parser_version = _version_lt(parser_version, current_parser_version)

    fetched_at = _parse_dt(_pick(row, "fetched_at"))
    stale_fetched_at = False
    if fetched_at is not None:
        stale_fetched_at = fetched_at < (datetime.now() - timedelta(days=max(1, stale_days)))

    source_html_present = bool(_pick(row, "source_html_present"))
    source_html_missing = not source_html_present

    status_code = _safe_int(_pick(row, "http_status"))
    error_page_like = bool(_pick(row, "is_error_page"))
    if status_code in (403, 404, 429, 500, 503):
        error_page_like = True

    quality_score = _safe_float(_pick(row, "quality_score"))
    if quality_score is None:
        score = 100.0
        if required_missing:
            score -= 35.0
        score -= min(25.0, important_missing_count * 3.0)
        if invalid_value:
            score -= 20.0
        if source_html_missing:
            score -= 10.0
        if error_page_like:
            score -= 25.0
        quality_score = max(0.0, min(100.0, score))

    return RowQuality(
        required_missing=required_missing,
        required_missing_fields=required_missing_fields,
        important_missing_count=important_missing_count,
        invalid_value=invalid_value,
        stale_parser_version=stale_parser_version,
        stale_fetched_at=stale_fetched_at,
        source_html_missing=source_html_missing,
        quality_score=quality_score,
        error_page_like=error_page_like,
    )


def _evaluate_existing_duplicates(rows: list[dict[str, Any]]) -> dict[str, int]:
    pair_counter: Counter[str] = Counter()
    for r in rows:
        pair_counter[_row_key(r)] += 1

    dup_by_key = {k: c for k, c in pair_counter.items() if c > 1}
    return dup_by_key


def _decide_existing_action(policy: str, q: RowQuality, duplicate: bool) -> tuple[str, str]:
    if duplicate:
        return "quarantine", "duplicate-existing-record"

    if policy == "skip-existing":
        return "skip", "policy-skip-existing"

    if policy == "reparse-cache":
        if q.stale_parser_version:
            return "reparse-cache", "stale-parser-version"
        return "skip", "no-stale-parser"

    if policy in ("repair-missing", "dry-run"):
        if q.required_missing or q.invalid_value:
            return "repair", "required-missing-or-invalid"
        if q.stale_parser_version:
            return "reparse-cache", "stale-parser-version"
        if q.stale_fetched_at:
            return "refetch", "stale-fetched-at"
        return "skip", "healthy-existing"

    if policy == "refresh-stale":
        if q.stale_parser_version:
            return "reparse-cache", "stale-parser-version"
        if q.stale_fetched_at:
            return "refetch", "stale-fetched-at"
        return "skip", "fresh-existing"

    if policy == "force-refresh":
        return "refetch", "policy-force-refresh"

    return "skip", "policy-default"


def _no_downgrade_decision(existing_q: RowQuality, cand_q: RowQuality, existing_row: dict[str, Any], candidate_row: dict[str, Any]) -> tuple[str, str]:
    if str(_pick(existing_row, "record_hash") or "") == str(_pick(candidate_row, "record_hash") or ""):
        return "no-downgrade-skip", "record-hash-same"

    if cand_q.required_missing:
        return "no-downgrade-skip", "candidate-required-missing"

    if cand_q.error_page_like or cand_q.source_html_missing:
        return "no-downgrade-skip", "candidate-error-or-empty-html"

    if cand_q.quality_score < existing_q.quality_score:
        return "no-downgrade-skip", "candidate-quality-lower-than-existing"

    return "update-candidate", "candidate-quality-acceptable"


def _build_plan(
    rows: list[dict[str, Any],],
    policy: str,
    target: str,
    start_date: str | None,
    end_date: str | None,
    candidate_rows: list[dict[str, Any]],
    expected_race_count: int,
    avg_sec_per_req: float,
    stale_days: int,
    current_parser_version: str,
) -> dict[str, Any]:
    filtered = [r for r in rows if _in_period(r, start_date, end_date)]
    filtered = _target_filter(filtered, target)

    candidates = [r for r in candidate_rows if _in_period(r, start_date, end_date)]
    candidates = _target_filter(candidates, target)

    existing_by_key = {_row_key(r): r for r in filtered}
    candidate_by_key = {_row_key(r): r for r in candidates}
    duplicate_map = _evaluate_existing_duplicates(filtered)

    decisions: list[PlanDecision] = []

    counts = {
        "target_count": expected_race_count if expected_race_count > 0 else len(filtered),
        "existing_count": len(filtered),
        "missing_count": 0,
        "skip_count": 0,
        "reparse_count": 0,
        "refetch_count": 0,
        "update_candidate_count": 0,
        "repair_count": 0,
        "quarantine_count": 0,
        "no_downgrade_skip_count": 0,
    }

    reasons: Counter[str] = Counter()

    existing_race_ids = {str(_pick(r, "race_id") or "") for r in filtered if str(_pick(r, "race_id") or "")}
    if expected_race_count > 0:
        counts["missing_count"] = max(0, expected_race_count - len(existing_race_ids))
    else:
        counts["missing_count"] = 0

    for key, existing_row in existing_by_key.items():
        q = _assess_row_quality(existing_row, current_parser_version=current_parser_version, stale_days=stale_days)
        duplicate = bool(duplicate_map.get(key))
        action, reason = _decide_existing_action(policy=policy, q=q, duplicate=duplicate)

        candidate_row = candidate_by_key.get(key)
        if candidate_row is not None:
            cand_q = _assess_row_quality(candidate_row, current_parser_version=current_parser_version, stale_days=stale_days)
            nd_action, nd_reason = _no_downgrade_decision(q, cand_q, existing_row, candidate_row)
            if nd_action == "no-downgrade-skip":
                action = "no-downgrade-skip"
                reason = nd_reason
                if nd_reason in ("candidate-required-missing", "candidate-error-or-empty-html"):
                    counts["quarantine_count"] += 1
            else:
                if action in ("refetch", "repair", "reparse-cache"):
                    action = "update-candidate"
                    reason = "candidate-quality-acceptable"

        decisions.append(
            PlanDecision(
                key=key,
                action=action,
                reason=reason,
                quality_score=q.quality_score,
                missing_fields=list(q.required_missing_fields),
                parser_version=str(_pick(existing_row, "parser_version") or "") or None,
                fetched_at=str(_pick(existing_row, "fetched_at") or "") or None,
            )
        )
        reasons[reason] += 1

        if action == "skip":
            counts["skip_count"] += 1
        elif action == "reparse-cache":
            counts["reparse_count"] += 1
        elif action == "refetch":
            counts["refetch_count"] += 1
        elif action == "repair":
            counts["repair_count"] += 1
        elif action == "update-candidate":
            counts["update_candidate_count"] += 1
        elif action == "quarantine":
            counts["quarantine_count"] += 1
        elif action == "no-downgrade-skip":
            counts["no_downgrade_skip_count"] += 1

    # Missing targets become refetch candidates in planning, except skip-existing.
    if counts["missing_count"] > 0 and policy != "skip-existing":
        counts["refetch_count"] += counts["missing_count"]
        reasons["missing-target-data"] += counts["missing_count"]

    estimated_http_request_count = counts["refetch_count"] + max(0, counts["repair_count"] - counts["update_candidate_count"])
    estimated_runtime = float(estimated_http_request_count) * max(0.1, avg_sec_per_req)

    return {
        **counts,
        "estimated_http_request_count": estimated_http_request_count,
        "estimated_runtime": estimated_runtime,
        "reasons": dict(reasons),
        "decisions": [
            {
                "key": d.key,
                "action": d.action,
                "reason": d.reason,
                "quality_score": d.quality_score,
                "missing_fields": d.missing_fields,
                "parser_version": d.parser_version,
                "fetched_at": d.fetched_at,
            }
            for d in sorted(decisions, key=lambda x: (x.action, x.key))
        ],
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Plan scrape refresh with no-write safeguards (dry-run)")
    p.add_argument("--input-db", default=None, help="SQLite DB path (read-only)")
    p.add_argument("--input-csv", default=None, help="Optional existing rows CSV")
    p.add_argument("--candidate-csv", default=None, help="Optional candidate rows CSV for no-downgrade planning")
    p.add_argument("--start-date", default=None)
    p.add_argument("--end-date", default=None)
    p.add_argument("--target", choices=["all", "race", "horse", "result", "pedigree", "odds"], default="all")
    p.add_argument(
        "--policy",
        choices=["repair-missing", "refresh-stale", "force-refresh", "reparse-cache", "skip-existing", "dry-run"],
        default="repair-missing",
    )
    p.add_argument("--stale-days", type=int, default=30, help="Rows older than this are stale for refresh-stale")
    p.add_argument("--current-parser-version", default=CURRENT_PARSER_VERSION)
    p.add_argument("--avg-sec-per-request", type=float, default=DEFAULT_AVG_SEC_PER_REQ)
    p.add_argument("--output", default=str(DEFAULT_OUTPUT))
    return p


def main() -> int:
    args = _build_parser().parse_args()

    start_date = _parse_date(args.start_date)
    end_date = _parse_date(args.end_date)
    if start_date and end_date and end_date < start_date:
        raise SystemExit("error: end-date must be >= start-date")

    rows: list[dict[str, Any]]
    expected_race_count = 0
    source = {}

    if args.input_csv:
        csv_path = Path(args.input_csv)
        if not csv_path.exists():
            raise SystemExit(f"error: input CSV not found: {csv_path}")
        rows = _load_rows_from_csv(csv_path)
        source = {"type": "csv", "path": str(csv_path)}
    else:
        db_path = Path(args.input_db) if args.input_db else DEFAULT_DB_PATH
        if not db_path.exists():
            raise SystemExit(f"error: DB not found: {db_path}")
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        try:
            rows = _load_rows_from_db(conn)
            expected_ids = _expected_race_ids(conn, start_date, end_date)
            expected_race_count = len(expected_ids)
        finally:
            conn.close()
        source = {"type": "db", "path": str(db_path), "read_only": True}

    candidate_rows: list[dict[str, Any]] = []
    if args.candidate_csv:
        candidate_path = Path(args.candidate_csv)
        if not candidate_path.exists():
            raise SystemExit(f"error: candidate CSV not found: {candidate_path}")
        candidate_rows = _load_rows_from_csv(candidate_path)

    plan = _build_plan(
        rows=rows,
        policy=args.policy,
        target=args.target,
        start_date=start_date,
        end_date=end_date,
        candidate_rows=candidate_rows,
        expected_race_count=expected_race_count,
        avg_sec_per_req=float(args.avg_sec_per_request),
        stale_days=int(args.stale_days),
        current_parser_version=str(args.current_parser_version),
    )

    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "dry_run": True,
        "policy": args.policy,
        "target": args.target,
        "start_date": start_date,
        "end_date": end_date,
        "input_source": source,
        "candidate_source": str(args.candidate_csv) if args.candidate_csv else None,
        "safeguards": {
            "no_db_write": True,
            "no_production_write": True,
            "no_downgrade": True,
            "no_required_missing_overwrite": True,
            "no_error_page_overwrite": True,
            "record_hash_skip": True,
            "parser_version_reparse": True,
        },
        **plan,
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "output": str(out),
                "dry_run": True,
                "policy": args.policy,
                "target": args.target,
                "skip_count": payload.get("skip_count"),
                "repair_count": payload.get("repair_count"),
                "reparse_count": payload.get("reparse_count"),
                "refetch_count": payload.get("refetch_count"),
                "quarantine_count": payload.get("quarantine_count"),
                "no_downgrade_skip_count": payload.get("no_downgrade_skip_count"),
            },
            ensure_ascii=False,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
