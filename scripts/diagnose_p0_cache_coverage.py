#!/usr/bin/env python3
"""Read-only diagnosis for P0 cache coverage and sampling scope.

The script only reads existing reports and read-only SQLite caches. It does not
perform HTTP access, database writes, or scrape execution.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_AUDIT_INPUT = ROOT_DIR / "reports" / "scrape_missingness_audit.json"
DEFAULT_P0_PLAN_INPUT = ROOT_DIR / "reports" / "p0_scrape_repair_plan.json"
DEFAULT_REPARSE_PLAN_INPUT = ROOT_DIR / "reports" / "p0_reparse_cache_plan.json"
DEFAULT_OUTPUT = ROOT_DIR / "reports" / "p0_cache_coverage_diagnosis.json"
DEFAULT_DB_PATH = ROOT_DIR / "keiba" / "data" / "keiba_ultimate.db"
DEFAULT_CACHE_DB = ROOT_DIR / "keiba" / "data" / "fetch_cache.db"
DEFAULT_PEDIGREE_CACHE_DB = ROOT_DIR / "keiba" / "data" / "pedigree_cache.db"

RESULT_COLUMNS = {"finish_position", "result_time", "margin"}
RACE_COLUMNS = {"race_date", "venue", "race_number"}
HORSE_COLUMNS = {"horse_name", "frame_number", "horse_number"}
PEDIGREE_COLUMNS = {"sire", "dam", "broodmare_sire"}

TARGET_COLUMNS: dict[str, set[str]] = {
    "all": {
        "race_id",
        "race_date",
        "venue",
        "race_number",
        "horse_id",
        "horse_name",
        "frame_number",
        "horse_number",
        "finish_position",
        "result_time",
        "margin",
        "odds",
        "popularity",
        "sire",
        "dam",
        "broodmare_sire",
        "(check)",
    },
    "race": {"race_id", "race_date", "venue", "race_number", "(check)"},
    "horse": {"horse_id", "horse_name", "frame_number", "horse_number", "(check)"},
    "result": {"finish_position", "result_time", "margin", "(check)"},
    "pedigree": {"sire", "dam", "broodmare_sire", "(check)"},
    "odds": {"odds", "popularity", "(check)"},
}

CLASSIFICATIONS = [
    "likely_cache_missing",
    "likely_parser_or_mapper_issue",
    "likely_schema_or_mapping_issue",
    "likely_metadata_repair",
    "likely_targeted_refetch_needed",
    "manual_review_required",
]


@dataclass
class DiagnosisTarget:
    race_id: str | None
    horse_id: str | None
    column: str
    reason: str
    current_action: str
    page_kind: str
    cache_status: str
    cache_key_or_url_hint: str
    classification: str
    recommended_next_action: str
    source_hint: str = ""


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"error: {label} not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"error: failed to parse {label}: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"error: invalid {label} JSON object: {path}")
    return data


def _open_ro_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"error: database not found: {path}")
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    path = parts.path or "/"
    query = parts.query or ""
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}{('?' + query) if query else ''}"


def _read_body(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        for enc in ("utf-8", "euc-jp", "cp932", "shift_jis"):
            try:
                return raw.decode(enc)
            except Exception:
                continue
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def _load_breakdown_map(report: dict[str, Any], key: str) -> dict[tuple[str, str], int]:
    out: dict[tuple[str, str], int] = {}
    items = report.get(key) if isinstance(report.get(key), list) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        reason = str(item.get("reason") or "")
        column = str(item.get("column") or "")
        out[(reason, column)] = int(item.get("count") or 0)
    return out


def _plan_target_allowed(column: str, target: str) -> bool:
    return column in TARGET_COLUMNS.get(target, TARGET_COLUMNS["all"])


def _page_kind_for_record(column: str, reason: str) -> str:
    if column in RESULT_COLUMNS:
        return "result_page"
    if column in RACE_COLUMNS:
        return "race_detail"
    if column in HORSE_COLUMNS:
        return "horse_detail"
    if column in PEDIGREE_COLUMNS:
        return "pedigree"
    if reason == "consistency:race_without_horse_data":
        return "race_detail"
    return "unknown"


def _race_url(race_id: str | None) -> str:
    return f"https://db.netkeiba.com/race/{race_id}/" if race_id else ""


def _horse_result_url(horse_id: str | None) -> str:
    return f"https://db.netkeiba.com/horse/result/{horse_id}/" if horse_id else ""


def _horse_ped_url(horse_id: str | None) -> str:
    return f"https://db.netkeiba.com/horse/ped/{horse_id}/" if horse_id else ""


def _cache_hint(page_kind: str, race_id: str | None, horse_id: str | None) -> str:
    if page_kind in {"result_page", "race_detail"}:
        return _race_url(race_id)
    if page_kind == "horse_detail":
        return _horse_result_url(horse_id)
    if page_kind == "pedigree":
        return f"pedigree_cache:{horse_id or ''}".rstrip(":")
    return race_id or horse_id or ""


def _fetch_http_cache_entry(conn: sqlite3.Connection, candidates: list[str]) -> tuple[str | None, str | None, str | None]:
    for candidate in candidates:
        normalized = _normalize_url(candidate)
        row = conn.execute(
            "SELECT normalized_url, final_url, body FROM http_cache WHERE normalized_url = ?",
            (normalized,),
        ).fetchone()
        if row:
            return str(row[0]), str(row[1] or ""), _read_body(row[2])

    for candidate in candidates:
        parts = urlsplit(candidate.strip())
        path = parts.path or ""
        if "/race/" in path:
            race_id = path.split("/race/", 1)[1].strip("/")
            if race_id:
                row = conn.execute(
                    "SELECT normalized_url, final_url, body FROM http_cache WHERE normalized_url LIKE ? OR final_url LIKE ? ORDER BY normalized_url LIMIT 1",
                    (f"%/race/{race_id}/%", f"%/race/{race_id}/%"),
                ).fetchone()
                if row:
                    return str(row[0]), str(row[1] or ""), _read_body(row[2])
        if "/horse/result/" in path or "/horse/ped/" in path:
            horse_id = ""
            if "/horse/result/" in path:
                horse_id = path.split("/horse/result/", 1)[1].strip("/")
            elif "/horse/ped/" in path:
                horse_id = path.split("/horse/ped/", 1)[1].strip("/")
            if horse_id:
                for pattern in (f"%/horse/result/{horse_id}/%", f"%/horse/ped/{horse_id}/%"):
                    row = conn.execute(
                        "SELECT normalized_url, final_url, body FROM http_cache WHERE normalized_url LIKE ? OR final_url LIKE ? ORDER BY normalized_url LIMIT 1",
                        (pattern, pattern),
                    ).fetchone()
                    if row:
                        return str(row[0]), str(row[1] or ""), _read_body(row[2])
    return None, None, None


def _fetch_pedigree_cache(conn: sqlite3.Connection | None, horse_id: str | None) -> tuple[str | None, dict[str, Any] | None]:
    if conn is None or not horse_id:
        return None, None
    try:
        row = conn.execute(
            "SELECT sire, dam, damsire FROM pedigree_cache WHERE horse_id = ?",
            (horse_id,),
        ).fetchone()
    except sqlite3.Error:
        return None, None
    if not row:
        return None, None
    return f"pedigree_cache:{horse_id}", {"sire": row[0], "dam": row[1], "broodmare_sire": row[2]}


def _parse_race_cache(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="race_table_01")
    if not table:
        return {"page_ok": False, "horse_row_count": 0, "finish_position_count": 0}

    horse_row_count = 0
    finish_position_count = 0
    for row in table.find_all("tr")[1:]:
        cols = row.find_all("td")
        if not cols:
            continue
        horse_row_count += 1
        if cols[0].get_text(strip=True):
            finish_position_count += 1

    return {"page_ok": True, "horse_row_count": horse_row_count, "finish_position_count": finish_position_count}


def _build_p0_records(plan: dict[str, Any], target: str, max_targets: int) -> list[DiagnosisTarget]:
    records: list[DiagnosisTarget] = []
    raw = plan.get("sample_targets") if isinstance(plan.get("sample_targets"), list) else []
    for item in raw:
        if not isinstance(item, dict):
            continue
        reason = str(item.get("reason") or "")
        action = str(item.get("action") or "")
        column = str(item.get("column") or "")
        if not _plan_target_allowed(column, target):
            continue
        if action == "no-action-domain-allowed":
            continue
        if reason.startswith("consistency:") and reason != "consistency:race_without_horse_data" and action != "manual-review":
            continue
        if action not in {"reparse-cache", "refetch-required", "repair-from-existing-metadata", "schema-review", "manual-review"}:
            continue

        race_id = str(item.get("race_id") or "").strip() or None
        horse_id = str(item.get("horse_id") or "").strip() or None
        page_kind = _page_kind_for_record(column, reason)
        records.append(
            DiagnosisTarget(
                race_id=race_id,
                horse_id=horse_id,
                column=column,
                reason=reason,
                current_action=action,
                page_kind=page_kind,
                cache_status="unknown",
                cache_key_or_url_hint=_cache_hint(page_kind, race_id, horse_id),
                classification="",
                recommended_next_action="",
                source_hint=str(item.get("source_hint") or ""),
            )
        )
        if len(records) >= max_targets:
            break
    return records


def _build_reparse_targets(plan: dict[str, Any], target: str, max_targets: int) -> list[DiagnosisTarget]:
    records: list[DiagnosisTarget] = []
    raw = plan.get("sample_targets") if isinstance(plan.get("sample_targets"), list) else []
    for item in raw:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "")
        reason = str(item.get("reason") or "")
        column = str(item.get("column") or "")
        if action not in {"reparse-cache", "refetch-required", "repair-from-existing-metadata"}:
            continue
        if not _plan_target_allowed(column, target):
            continue
        records.append(
            DiagnosisTarget(
                race_id=str(item.get("race_id") or "").strip() or None,
                horse_id=str(item.get("horse_id") or "").strip() or None,
                column=column,
                reason=reason,
                current_action=action,
                page_kind=_page_kind_for_record(column, reason),
                cache_status="unknown",
                cache_key_or_url_hint="",
                classification="",
                recommended_next_action="",
                source_hint=str(item.get("source_hint") or ""),
            )
        )
        if len(records) >= max_targets:
            break
    return records


def _load_race_without_horse_ids(conn: sqlite3.Connection) -> list[str]:
    try:
        rows = conn.execute(
            """
            SELECT r.race_id
            FROM races_ultimate r
            WHERE NOT EXISTS (
                SELECT 1 FROM race_results_ultimate rr WHERE rr.race_id = r.race_id
            )
            ORDER BY r.race_id
            """
        ).fetchall()
        return [str(row[0]) for row in rows if row and row[0]]
    except sqlite3.Error:
        pass

    try:
        rows = conn.execute(
            """
            SELECT r.race_id
            FROM races_ultimate r
            WHERE NOT EXISTS (
                SELECT 1 FROM entries e WHERE e.race_id = r.race_id
            )
            ORDER BY r.race_id
            """
        ).fetchall()
        return [str(row[0]) for row in rows if row and row[0]]
    except sqlite3.Error:
        return []


def _read_audit_counts(audit: dict[str, Any]) -> dict[str, int]:
    breakdown = _load_breakdown_map(audit, "repair_reason_breakdown")
    column_missingness = audit.get("column_missingness") if isinstance(audit.get("column_missingness"), list) else []

    def _missing_count(column: str) -> int:
        for item in column_missingness:
            if not isinstance(item, dict):
                continue
            if str(item.get("column") or "") == column:
                return int(item.get("true_missing_count") or 0)
        return 0

    true_missing_total = sum(count for (reason, _column), count in breakdown.items() if reason == "true-missing")
    return {
        "audit_p0_true_missing_count": true_missing_total,
        "audit_finish_position_true_missing_count": _missing_count("finish_position"),
        "audit_race_without_horse_data_count": breakdown.get(("consistency:race_without_horse_data", "(check)"), 0),
    }


def _count_plan_breakdown(plan: dict[str, Any], action: str, column: str) -> int:
    breakdown = plan.get("p0_action_breakdown") if isinstance(plan.get("p0_action_breakdown"), list) else []
    for item in breakdown:
        if not isinstance(item, dict):
            continue
        if str(item.get("action") or "") == action and str(item.get("column") or "") == column:
            return int(item.get("count") or 0)
    return 0


def _add_sample(samples: dict[str, list[dict[str, Any]]], record: DiagnosisTarget, limit: int = 10) -> None:
    if record.classification not in samples:
        return
    if len(samples[record.classification]) >= limit:
        return
    samples[record.classification].append(
        {
            "race_id": record.race_id,
            "horse_id": record.horse_id,
            "column": record.column,
            "reason": record.reason,
            "current_action": record.current_action,
            "cache_status": record.cache_status,
            "cache_key_or_url_hint": record.cache_key_or_url_hint,
            "recommended_next_action": record.recommended_next_action,
        }
    )


def _classify_record(record: DiagnosisTarget, cache_available: bool, parse_ok: bool) -> str:
    if record.current_action == "schema-review" or record.column == "race_number":
        return "likely_schema_or_mapping_issue"
    if record.current_action == "repair-from-existing-metadata":
        return "likely_metadata_repair"
    if record.current_action == "manual-review":
        return "manual_review_required"
    if record.reason == "consistency:race_without_horse_data":
        return "likely_parser_or_mapper_issue" if cache_available else "likely_targeted_refetch_needed"
    if record.column in RESULT_COLUMNS:
        return "likely_parser_or_mapper_issue" if cache_available and parse_ok else "likely_targeted_refetch_needed"
    if record.column in HORSE_COLUMNS:
        return "likely_parser_or_mapper_issue" if cache_available else "likely_targeted_refetch_needed"
    if record.column in PEDIGREE_COLUMNS:
        return "likely_parser_or_mapper_issue" if cache_available else "likely_targeted_refetch_needed"
    if record.reason.startswith("consistency:"):
        return "manual_review_required"
    return "likely_targeted_refetch_needed"


def _recommended_action(classification: str) -> str:
    if classification == "likely_parser_or_mapper_issue":
        return "parser/mapper review"
    if classification == "likely_schema_or_mapping_issue":
        return "schema review"
    if classification == "likely_metadata_repair":
        return "metadata repair dry-run"
    if classification == "likely_targeted_refetch_needed":
        return "targeted refetch dry-run"
    return "manual review"


def _recommended_next_actions_from_report(report: dict[str, Any]) -> list[str]:
    out: list[str] = []
    finish_rate = float(report.get("finish_position_cache_available_rate") or 0.0)
    finish_missing = int(report.get("finish_position_cache_missing_count") or 0)
    parser_count = int(report.get("likely_parser_or_mapper_issue") or 0)
    schema_count = int(report.get("likely_schema_or_mapping_issue") or 0)
    metadata_count = int(report.get("likely_metadata_repair") or 0)
    refetch_count = int(report.get("likely_targeted_refetch_needed") or 0)
    manual_count = int(report.get("manual_review_required") or 0)
    race_missing = int(report.get("race_without_horse_data_cache_missing_count") or 0)
    race_rate = float(report.get("race_without_horse_data_cache_available_rate") or 0.0)

    if finish_missing > 0:
        if finish_rate < 0.25:
            out.append("finish_position cache coverage is low; prioritize targeted refetch dry-run next")
        else:
            out.append("finish_position cache exists often; prioritize parser/mapper review next")
    if race_missing > 0:
        if race_rate < 0.25:
            out.append("race_without_horse_data cache coverage is low; prioritize targeted refetch dry-run next")
        else:
            out.append("race_without_horse_data has cache coverage; review parser/mapper and DB save path next")
    if metadata_count > 0:
        out.append("metadata repair rows are present; add a metadata repair dry-run next")
    if schema_count > 0:
        out.append("schema-review rows are present; audit mapper aliases and derived rules next")
    if parser_count > 0:
        out.append("cache-backed rows still look parser/mapper sensitive; inspect extraction rules next")
    if refetch_count > 0:
        out.append("targeted refetch dry-run remains relevant for cache-missing rows")
    if manual_count > 0:
        out.append("manual review rows are present; isolate consistency failures separately")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose P0 cache coverage using read-only reports and caches")
    parser.add_argument("--input-audit", default=str(DEFAULT_AUDIT_INPUT), help="Path to scrape_missingness_audit.json")
    parser.add_argument("--input-p0-plan", default=str(DEFAULT_P0_PLAN_INPUT), help="Path to p0_scrape_repair_plan.json")
    parser.add_argument("--input-reparse-plan", default=str(DEFAULT_REPARSE_PLAN_INPUT), help="Path to p0_reparse_cache_plan.json")
    parser.add_argument("--target", choices=["all", "race", "horse", "result", "pedigree", "odds"], default="all")
    parser.add_argument("--max-targets", type=int, default=120, help="Maximum targets to inspect from the p0 plan subset")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSON path")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="Read-only main SQLite DB path")
    parser.add_argument("--cache-db", default=str(DEFAULT_CACHE_DB), help="Read-only fetch cache DB path")
    parser.add_argument("--pedigree-cache-db", default=str(DEFAULT_PEDIGREE_CACHE_DB), help="Read-only pedigree cache DB path")
    args = parser.parse_args()

    audit = _load_json(Path(args.input_audit), label="input-audit")
    p0_plan = _load_json(Path(args.input_p0_plan), label="input-p0-plan")
    reparse_plan = _load_json(Path(args.input_reparse_plan), label="input-reparse-plan")

    audit_counts = _read_audit_counts(audit)
    p0_plan_total_count = int(p0_plan.get("p0_total_count") or 0)
    reparse_plan_total_count = int(reparse_plan.get("p0_total_count") or 0)
    max_targets_applied = int(args.max_targets)

    p0_finish_position_count = _count_plan_breakdown(p0_plan, "reparse-cache", "finish_position")
    p0_race_without_horse_data_count = _count_plan_breakdown(p0_plan, "refetch-required", "(check)")
    p0_race_without_horse_data_dedup_count = p0_race_without_horse_data_count
    p0_repair_metadata_count = _count_plan_breakdown(p0_plan, "repair-from-existing-metadata", "race_date") + _count_plan_breakdown(p0_plan, "repair-from-existing-metadata", "venue")
    p0_schema_review_count = _count_plan_breakdown(p0_plan, "schema-review", "race_number")
    p0_manual_review_count = _count_plan_breakdown(p0_plan, "manual-review", "(check)")

    reparse_subset = _build_reparse_targets(p0_plan, args.target, max_targets_applied)
    sampled_target_count = len(reparse_subset)
    full_target_count = p0_plan_total_count
    is_reparse_plan_sampled = sampled_target_count < full_target_count
    if sampled_target_count >= full_target_count:
        sampling_reason = "reparse plan covers the full P0 plan subset for this target"
    else:
        sampling_reason = "reparse plan inspects only the reparse/refetch/metadata subset from p0_plan.sample_targets"

    coverage_targets = _build_p0_records(p0_plan, args.target, max_targets_applied)

    db_conn = _open_ro_db(Path(args.db_path))
    cache_conn = _open_ro_db(Path(args.cache_db))
    ped_conn = _open_ro_db(Path(args.pedigree_cache_db)) if Path(args.pedigree_cache_db).exists() else None
    try:
        if args.target in ("all", "race"):
            race_without_horse_ids = _load_race_without_horse_ids(db_conn)
            p0_race_without_horse_data_dedup_count = len(race_without_horse_ids)
            coverage_targets = [
                r
                for r in coverage_targets
                if not (r.reason == "consistency:race_without_horse_data" and r.race_id is None)
            ]
            existing = {r.race_id for r in coverage_targets if r.reason == "consistency:race_without_horse_data"}
            for race_id in race_without_horse_ids:
                if race_id in existing:
                    continue
                coverage_targets.append(
                    DiagnosisTarget(
                        race_id=race_id,
                        horse_id=None,
                        column="(check)",
                        reason="consistency:race_without_horse_data",
                        current_action="refetch-required",
                        page_kind="race_detail",
                        cache_status="unknown",
                        cache_key_or_url_hint=_race_url(race_id),
                        classification="",
                        recommended_next_action="",
                        source_hint="race-level-missing-horse-rows",
                    )
                )

        total_p0_targets = len(coverage_targets)
        cache_available_count = 0
        cache_missing_count = 0
        result_page_cache_available_count = 0
        race_detail_cache_available_count = 0
        horse_detail_cache_available_count = 0
        pedigree_cache_available_count = 0

        classification_counts: Counter[str] = Counter()
        sample_targets: dict[str, list[dict[str, Any]]] = {name: [] for name in CLASSIFICATIONS}

        for record in coverage_targets:
            cache_status = "missing"
            parse_ok = False
            cache_key_or_url_hint = record.cache_key_or_url_hint

            if record.page_kind == "pedigree":
                cache_key, ped_payload = _fetch_pedigree_cache(ped_conn, record.horse_id)
                if ped_payload is not None:
                    cache_status = "available"
                    pedigree_cache_available_count += 1
                    parse_ok = all((ped_payload.get(k) not in (None, "", [])) for k in ("sire", "dam", "broodmare_sire"))
                    cache_key_or_url_hint = cache_key or cache_key_or_url_hint
            else:
                candidate_urls = []
                if record.page_kind in {"result_page", "race_detail"}:
                    candidate_urls.append(_race_url(record.race_id))
                elif record.page_kind == "horse_detail":
                    candidate_urls.extend([_horse_result_url(record.horse_id), _horse_ped_url(record.horse_id)])

                matched_url, final_url, html = _fetch_http_cache_entry(cache_conn, candidate_urls)
                if html is not None:
                    cache_status = "available"
                    cache_key_or_url_hint = matched_url or final_url or cache_key_or_url_hint
                    if record.page_kind == "result_page":
                        parsed = _parse_race_cache(html)
                        parse_ok = bool(parsed.get("page_ok")) and int(parsed.get("finish_position_count") or 0) > 0
                        result_page_cache_available_count += 1
                    elif record.page_kind == "race_detail":
                        parsed = _parse_race_cache(html)
                        parse_ok = bool(parsed.get("page_ok"))
                        race_detail_cache_available_count += 1
                    elif record.page_kind == "horse_detail":
                        soup = BeautifulSoup(html, "html.parser")
                        parse_ok = bool(soup.get_text(" ", strip=True))
                        horse_detail_cache_available_count += 1

            if cache_status == "available":
                cache_available_count += 1
            else:
                cache_missing_count += 1

            record.cache_status = cache_status
            record.cache_key_or_url_hint = cache_key_or_url_hint
            record.classification = _classify_record(record, cache_available=(cache_status == "available"), parse_ok=parse_ok)
            record.recommended_next_action = _recommended_action(record.classification)
            classification_counts[record.classification] += 1
            _add_sample(sample_targets, record)

        cache_checked_count = total_p0_targets
        cache_available_rate = (cache_available_count / cache_checked_count) if cache_checked_count else 0.0
        cache_missing_rate = (cache_missing_count / cache_checked_count) if cache_checked_count else 0.0

        finish_targets = [r for r in coverage_targets if r.column == "finish_position" and r.reason == "true-missing"]
        finish_cache_available_count = sum(1 for r in finish_targets if r.cache_status == "available")
        finish_cache_missing_count = sum(1 for r in finish_targets if r.cache_status != "available")
        finish_cache_checked_count = len(finish_targets)

        race_targets = [r for r in coverage_targets if r.reason == "consistency:race_without_horse_data"]
        race_cache_available_count = sum(1 for r in race_targets if r.cache_status == "available")
        race_cache_missing_count = sum(1 for r in race_targets if r.cache_status != "available")
        race_cache_checked_count = len(race_targets)
        targeted_refetch_candidate_count = race_cache_missing_count

        report = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "input_audit": str(args.input_audit),
            "input_p0_plan": str(args.input_p0_plan),
            "input_reparse_plan": str(args.input_reparse_plan),
            "target": args.target,
            "max_targets": max_targets_applied,
            "verdict": "warn" if any(v > 0 for k, v in classification_counts.items() if k != "manual_review_required") else "pass",
            "verdict_reason": "cache-coverage-diagnosis",
            "audit_p0_true_missing_count": audit_counts["audit_p0_true_missing_count"],
            "audit_finish_position_true_missing_count": audit_counts["audit_finish_position_true_missing_count"],
            "audit_race_without_horse_data_count": audit_counts["audit_race_without_horse_data_count"],
            "p0_plan_total_count": p0_plan_total_count,
            "p0_finish_position_count": p0_finish_position_count,
            "p0_race_without_horse_data_count": p0_race_without_horse_data_count,
            "p0_race_without_horse_data_dedup_count": p0_race_without_horse_data_dedup_count,
            "p0_metadata_repair_count": p0_repair_metadata_count,
            "p0_schema_review_count": p0_schema_review_count,
            "p0_manual_review_count": p0_manual_review_count,
            "reparse_plan_total_count": reparse_plan_total_count,
            "sampled_target_count": sampled_target_count,
            "full_target_count": full_target_count,
            "is_reparse_plan_sampled": is_reparse_plan_sampled,
            "sampling_reason": sampling_reason,
            "max_targets_applied": max_targets_applied,
            "total_p0_targets": total_p0_targets,
            "cache_checked_count": cache_checked_count,
            "cache_available_count": cache_available_count,
            "cache_missing_count": cache_missing_count,
            "cache_available_rate": cache_available_rate,
            "cache_missing_rate": cache_missing_rate,
            "result_page_cache_available_count": result_page_cache_available_count,
            "race_detail_cache_available_count": race_detail_cache_available_count,
            "horse_detail_cache_available_count": horse_detail_cache_available_count,
            "pedigree_cache_available_count": pedigree_cache_available_count,
            "finish_position_cache_checked_count": finish_cache_checked_count,
            "finish_position_cache_available_count": finish_cache_available_count,
            "finish_position_cache_missing_count": finish_cache_missing_count,
            "finish_position_cache_available_rate": (finish_cache_available_count / finish_cache_checked_count) if finish_cache_checked_count else 0.0,
            "finish_position_cache_missing_rate": (finish_cache_missing_count / finish_cache_checked_count) if finish_cache_checked_count else 0.0,
            "race_without_horse_data_cache_checked_count": race_cache_checked_count,
            "race_without_horse_data_cache_available_count": race_cache_available_count,
            "race_without_horse_data_cache_missing_count": race_cache_missing_count,
            "race_without_horse_data_cache_available_rate": (race_cache_available_count / race_cache_checked_count) if race_cache_checked_count else 0.0,
            "targeted_refetch_candidate_count": targeted_refetch_candidate_count,
            "classification_counts": dict(classification_counts),
            "sample_targets": sample_targets,
            "recommended_next_actions": _recommended_next_actions_from_report({
                "finish_position_cache_available_rate": (finish_cache_available_count / finish_cache_checked_count) if finish_cache_checked_count else 0.0,
                "finish_position_cache_missing_count": finish_cache_missing_count,
                "likely_parser_or_mapper_issue": classification_counts.get("likely_parser_or_mapper_issue", 0),
                "likely_schema_or_mapping_issue": classification_counts.get("likely_schema_or_mapping_issue", 0),
                "likely_metadata_repair": classification_counts.get("likely_metadata_repair", 0),
                "likely_targeted_refetch_needed": classification_counts.get("likely_targeted_refetch_needed", 0),
                "manual_review_required": classification_counts.get("manual_review_required", 0),
                "race_without_horse_data_cache_missing_count": race_cache_missing_count,
                "race_without_horse_data_cache_available_rate": (race_cache_available_count / race_cache_checked_count) if race_cache_checked_count else 0.0,
            }),
            "safeguards": {
                "read_only": True,
                "no_db_write": True,
                "no_http_access": True,
                "no_scrape_execute": True,
                "no_upsert": True,
                "no_force_refresh_execute": True,
            },
        }

        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        print(
            json.dumps(
                {
                    "output": str(out),
                    "verdict": report["verdict"],
                    "total_p0_targets": report["total_p0_targets"],
                    "cache_checked_count": report["cache_checked_count"],
                    "cache_available_count": report["cache_available_count"],
                    "cache_missing_count": report["cache_missing_count"],
                    "finish_position_cache_available_count": report["finish_position_cache_available_count"],
                    "finish_position_cache_missing_count": report["finish_position_cache_missing_count"],
                    "race_without_horse_data_cache_available_count": report["race_without_horse_data_cache_available_count"],
                    "race_without_horse_data_cache_missing_count": report["race_without_horse_data_cache_missing_count"],
                    "targeted_refetch_candidate_count": report["targeted_refetch_candidate_count"],
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        db_conn.close()
        cache_conn.close()
        if ped_conn is not None:
            ped_conn.close()


if __name__ == "__main__":
    raise SystemExit(main())