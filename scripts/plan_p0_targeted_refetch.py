#!/usr/bin/env python3
"""Build a read-only dry-run plan for P0 targeted refetch URLs.

The script only reads audit/plan reports and read-only SQLite caches. It never
performs HTTP access, database writes, upserts, or live scrape execution.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_AUDIT_INPUT = ROOT_DIR / "reports" / "scrape_missingness_audit.json"
DEFAULT_P0_PLAN_INPUT = ROOT_DIR / "reports" / "p0_scrape_repair_plan.json"
DEFAULT_CACHE_DIAG_INPUT = ROOT_DIR / "reports" / "p0_cache_coverage_diagnosis.json"
DEFAULT_OUTPUT = ROOT_DIR / "reports" / "p0_targeted_refetch_plan.json"
DEFAULT_DB_PATH = ROOT_DIR / "keiba" / "data" / "keiba_ultimate.db"
DEFAULT_CACHE_DB = ROOT_DIR / "keiba" / "data" / "fetch_cache.db"
DEFAULT_PEDIGREE_CACHE_DB = ROOT_DIR / "keiba" / "data" / "pedigree_cache.db"
DEFAULT_AVG_SEC_PER_REQ = 1.2

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

RESULT_COLUMNS = {"finish_position", "result_time", "margin"}
HORSE_COLUMNS = {"horse_name", "frame_number", "horse_number"}
PEDIGREE_COLUMNS = {"sire", "dam", "broodmare_sire"}


@dataclass
class Candidate:
    url: str
    url_type: str
    race_id: str | None
    horse_id: str | None
    reason: str
    column: str
    priority: str
    source: str
    recommended_next_action: str
    classification: str = "refetch-required"
    cache_status: str = "missing"
    raw_key: str = ""


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


def _is_missing(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip() == ""
    return False


def _pick(d: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in d and d.get(key) is not None:
            return d.get(key)
    return None


def _target_allowed(column: str, target: str) -> bool:
    return column in TARGET_COLUMNS.get(target, TARGET_COLUMNS["all"])


def _cache_lookup_http(conn: sqlite3.Connection, url: str) -> bool:
    normalized = _normalize_url(url)
    row = conn.execute("SELECT 1 FROM http_cache WHERE normalized_url = ?", (normalized,)).fetchone()
    if row:
        return True
    path = urlsplit(url).path or ""
    if "/race/" in path:
        race_id = path.split("/race/", 1)[1].strip("/")
        if race_id:
            row = conn.execute(
                "SELECT 1 FROM http_cache WHERE normalized_url LIKE ? OR final_url LIKE ? LIMIT 1",
                (f"%/race/{race_id}/%", f"%/race/{race_id}/%"),
            ).fetchone()
            return bool(row)
    if "/horse/result/" in path or "/horse/ped/" in path:
        horse_id = ""
        if "/horse/result/" in path:
            horse_id = path.split("/horse/result/", 1)[1].strip("/")
        else:
            horse_id = path.split("/horse/ped/", 1)[1].strip("/")
        if horse_id:
            row = conn.execute(
                "SELECT 1 FROM http_cache WHERE normalized_url LIKE ? OR final_url LIKE ? LIMIT 1",
                (f"%/horse/result/{horse_id}/%", f"%/horse/result/{horse_id}/%"),
            ).fetchone()
            if row:
                return True
            row = conn.execute(
                "SELECT 1 FROM http_cache WHERE normalized_url LIKE ? OR final_url LIKE ? LIMIT 1",
                (f"%/horse/ped/{horse_id}/%", f"%/horse/ped/{horse_id}/%"),
            ).fetchone()
            return bool(row)
    return False


def _cache_lookup_pedigree(conn: sqlite3.Connection | None, horse_id: str | None) -> bool:
    if conn is None or not horse_id:
        return False
    row = conn.execute("SELECT 1 FROM pedigree_cache WHERE horse_id = ?", (horse_id,)).fetchone()
    return bool(row)


def _make_url(url_type: str, race_id: str | None, horse_id: str | None) -> str | None:
    if url_type in {"result_page", "race_detail"}:
        return f"https://db.netkeiba.com/race/{race_id}/" if race_id else None
    if url_type == "horse_detail":
        return f"https://db.netkeiba.com/horse/result/{horse_id}/" if horse_id else None
    if url_type == "pedigree":
        return f"https://db.netkeiba.com/horse/ped/{horse_id}/" if horse_id else None
    return None


def _classify_url_type(column: str, reason: str) -> str:
    if reason == "consistency:race_without_horse_data":
        return "race_detail"
    if column in PEDIGREE_COLUMNS:
        return "pedigree"
    if column in HORSE_COLUMNS:
        return "result_page"
    if column in RESULT_COLUMNS:
        return "result_page"
    return "unknown"


def _read_audit_counts(audit: dict[str, Any]) -> dict[str, int]:
    column_missingness = audit.get("column_missingness") if isinstance(audit.get("column_missingness"), list) else []
    out = {
        "audit_p0_true_missing_count": 0,
        "audit_finish_position_true_missing_count": 0,
        "audit_race_without_horse_data_count": 0,
    }
    for item in column_missingness:
        if not isinstance(item, dict):
            continue
        col = str(item.get("column") or "")
        true_missing = int(item.get("true_missing_count") or 0)
        out["audit_p0_true_missing_count"] += true_missing
        if col == "finish_position":
            out["audit_finish_position_true_missing_count"] = true_missing
    breakdown = audit.get("repair_reason_breakdown") if isinstance(audit.get("repair_reason_breakdown"), list) else []
    for item in breakdown:
        if not isinstance(item, dict):
            continue
        if str(item.get("reason") or "") == "consistency:race_without_horse_data":
            out["audit_race_without_horse_data_count"] = int(item.get("count") or 0)
    return out


def _build_exclusion_counts(plan: dict[str, Any]) -> dict[str, int]:
    breakdown = plan.get("p0_action_breakdown") if isinstance(plan.get("p0_action_breakdown"), list) else []
    out = {"schema": 0, "domain": 0, "metadata": 0}
    for item in breakdown:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "")
        count = int(item.get("count") or 0)
        if action == "schema-review":
            out["schema"] += count
        elif action == "no-action-domain-allowed":
            out["domain"] += count
        elif action == "repair-from-existing-metadata":
            out["metadata"] += count
    return out


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


def _load_result_missing_candidates(conn: sqlite3.Connection, target: str) -> list[Candidate]:
    out: list[Candidate] = []
    for race_id, data_txt in conn.execute("SELECT race_id, data FROM race_results_ultimate"):
        try:
            payload = json.loads(data_txt or "{}")
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}

        rid = str(_pick(payload, "race_id") or race_id or "").strip() or None
        hid = str(_pick(payload, "horse_id") or "").strip() or None

        missing_specs: list[tuple[str, str, str]] = []
        miss_finish = _is_missing(_pick(payload, "finish_position", "finish"))
        miss_time = _is_missing(_pick(payload, "result_time", "finish_time", "time"))
        miss_margin = _is_missing(_pick(payload, "margin"))
        has_row_identity = bool(hid or _pick(payload, "horse_number", "horse_no"))
        has_row_context = bool(_pick(payload, "horse_name") or _pick(payload, "frame_number", "bracket_number", "bracket") or _pick(payload, "horse_number", "horse_no"))

        if miss_finish:
            missing_specs.append(("finish_position", "true-missing", "P0"))
        if _is_missing(_pick(payload, "horse_name")):
            missing_specs.append(("horse_name", "true-missing", "P1"))
        if _is_missing(_pick(payload, "frame_number", "bracket_number", "bracket")):
            missing_specs.append(("frame_number", "true-missing", "P1"))
        if _is_missing(_pick(payload, "horse_number", "horse_no")):
            missing_specs.append(("horse_number", "true-missing", "P1"))

        for column, reason, priority in missing_specs:
            if not _target_allowed(column, target):
                continue
            url_type = _classify_url_type(column, reason)
            if url_type == "unknown" or not rid:
                continue
            url = _make_url(url_type, rid, hid)
            if not url:
                continue

            classification = "refetch-required"
            reason_out = reason
            next_action = "targeted refetch dry-run"

            if column in RESULT_COLUMNS and miss_finish and miss_time and miss_margin and has_row_identity and has_row_context:
                classification = "source-empty-result-cells"
                reason_out = "source-empty-result-cells"
                next_action = "source review / domain review"
            elif column in RESULT_COLUMNS and not has_row_identity:
                classification = "result-source-missing"
                reason_out = "result-source-missing"
                next_action = "manual review target row mapping"
            elif column in RESULT_COLUMNS and not rid:
                classification = "manual-review"
                reason_out = "manual-review"
                next_action = "manual review race_id / source key"

            out.append(
                Candidate(
                    url=url,
                    url_type=url_type,
                    race_id=rid,
                    horse_id=hid,
                    reason=reason_out,
                    column=column,
                    priority=priority,
                    source="db",
                    recommended_next_action=next_action,
                    classification=classification,
                    raw_key=f"{rid}:{hid or ''}:{column}",
                )
            )

    return out


def _load_race_without_horse_candidates(conn: sqlite3.Connection, target: str) -> list[Candidate]:
    out: list[Candidate] = []
    if not _target_allowed("(check)", target) and target != "all":
        return out
    for race_id in _load_race_without_horse_ids(conn):
        url = _make_url("race_detail", race_id, None)
        if not url:
            continue
        out.append(
            Candidate(
                url=url,
                url_type="race_detail",
                race_id=race_id,
                horse_id=None,
                reason="consistency:race_without_horse_data",
                column="(check)",
                priority="P0",
                source="db",
                recommended_next_action="targeted refetch dry-run",
                raw_key=race_id,
            )
        )
    return out


def _load_pedigree_candidates(conn: sqlite3.Connection | None, target: str) -> list[Candidate]:
    out: list[Candidate] = []
    if target not in ("all", "pedigree"):
        return out
    if conn is None:
        return out
    try:
        rows = conn.execute("SELECT horse_id, sire, dam, damsire FROM horse_details")
    except sqlite3.Error:
        return out
    for horse_id, sire, dam, damsire in rows:
        if not any(_is_missing(v) for v in (sire, dam, damsire)):
            continue
        url = _make_url("pedigree", None, str(horse_id))
        if not url:
            continue
        out.append(
            Candidate(
                url=url,
                url_type="pedigree",
                race_id=None,
                horse_id=str(horse_id),
                reason="true-missing",
                column="sire",
                priority="P1",
                source="db",
                recommended_next_action="targeted refetch dry-run",
                raw_key=str(horse_id),
            )
        )
    return out


def _maybe_cache_status(candidate: Candidate, cache_conn: sqlite3.Connection, ped_conn: sqlite3.Connection | None) -> str:
    if candidate.url_type == "pedigree":
        return "available" if _cache_lookup_pedigree(ped_conn, candidate.horse_id) else "missing"
    return "available" if _cache_lookup_http(cache_conn, candidate.url) else "missing"


def _best_sample_slot(candidate: Candidate) -> str:
    if candidate.url_type == "result_page":
        return "result_page"
    if candidate.url_type == "race_detail":
        return "race_detail"
    if candidate.url_type == "pedigree":
        return "pedigree"
    return "horse_detail"


def _sample_item(candidate: Candidate) -> dict[str, Any]:
    return {
        "url": candidate.url,
        "url_type": candidate.url_type,
        "race_id": candidate.race_id,
        "horse_id": candidate.horse_id,
        "reason": candidate.reason,
        "column": candidate.column,
        "priority": candidate.priority,
        "source": candidate.source,
        "recommended_next_action": candidate.recommended_next_action,
    }


def _recommended_next_actions(unique_url_count: int, counts: dict[str, int], target: str, cache_available_rate: float) -> list[str]:
    out: list[str] = []
    if unique_url_count <= 10:
        out.append("unique_url_count が小さいので targeted refetch small live validation を次に検討")
    else:
        out.append("unique_url_count が大きいので date/race_id 分割で段階実行を検討")

    if counts.get("finish_position", 0) > 0:
        out.append("finish_position 中心なので result page refetch を優先")
    if counts.get("source_empty_result_cells", 0) > 0:
        out.append("source-empty-result-cells は refetch から分離し source/domain review を優先")
    if counts.get("result_source_missing", 0) > 0:
        out.append("result-source-missing は URL/row key の manual-review を先に実施")
    if counts.get("race_without_horse_data", 0) > 0:
        out.append("race_without_horse_data は race detail/result page のどちらが必要かを再確認")
    if counts.get("pedigree", 0) > 0:
        out.append("horse/pedigree 中心なら horse/pedigree page を別 tier で扱う")
    if cache_available_rate > 0.0:
        out.append("cache_available 対象は refetch に混ぜず reparse 側へ残す")
    if target == "race":
        out.append("race target は race detail URL のみを使い、schema-review 系は除外済み")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan a read-only P0 targeted refetch dry-run")
    parser.add_argument("--input-audit", default=str(DEFAULT_AUDIT_INPUT), help="Path to scrape_missingness_audit.json")
    parser.add_argument("--input-p0-plan", default=str(DEFAULT_P0_PLAN_INPUT), help="Path to p0_scrape_repair_plan.json")
    parser.add_argument("--input-cache-diagnosis", default=str(DEFAULT_CACHE_DIAG_INPUT), help="Path to p0_cache_coverage_diagnosis.json")
    parser.add_argument("--target", choices=["all", "race", "horse", "result", "pedigree", "odds"], default="all")
    parser.add_argument("--max-targets", type=int, default=10, help="Maximum sample URLs per URL type")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSON path")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="Read-only main SQLite DB path")
    parser.add_argument("--cache-db", default=str(DEFAULT_CACHE_DB), help="Read-only fetch cache DB path")
    parser.add_argument("--pedigree-cache-db", default=str(DEFAULT_PEDIGREE_CACHE_DB), help="Read-only pedigree cache DB path")
    args = parser.parse_args()

    audit = _load_json(Path(args.input_audit), label="input-audit")
    p0_plan = _load_json(Path(args.input_p0_plan), label="input-p0-plan")
    cache_diag = _load_json(Path(args.input_cache_diagnosis), label="input-cache-diagnosis")

    audit_counts = _read_audit_counts(audit)
    exclusion_counts = _build_exclusion_counts(p0_plan)
    p0_plan_total_count = int(p0_plan.get("p0_total_count") or 0)
    p0_total_count = int(audit_counts["audit_p0_true_missing_count"] or p0_plan_total_count)
    cache_diag_sampled = bool(cache_diag.get("is_reparse_plan_sampled")) if isinstance(cache_diag, dict) else False

    db_conn = _open_ro_db(Path(args.db_path))
    cache_conn = _open_ro_db(Path(args.cache_db))
    ped_conn = _open_ro_db(Path(args.pedigree_cache_db)) if Path(args.pedigree_cache_db).exists() else None

    try:
        raw_candidates: list[Candidate] = []
        raw_candidates.extend(_load_result_missing_candidates(db_conn, args.target))
        raw_candidates.extend(_load_race_without_horse_candidates(db_conn, args.target))
        raw_candidates.extend(_load_pedigree_candidates(ped_conn, args.target))

        candidate_counts: Counter[str] = Counter()
        raw_by_url: dict[str, Candidate] = {}
        raw_refetch_candidate_count = 0
        reparse_candidate_count = 0
        excluded_cache_available_count = 0
        classification_breakdown: Counter[str] = Counter()
        excluded_source_empty_result_cells_count = 0
        excluded_manual_review_count = 0
        excluded_result_source_missing_count = 0

        for candidate in raw_candidates:
            classification_breakdown[candidate.classification] += 1
            if candidate.classification == "source-empty-result-cells":
                excluded_source_empty_result_cells_count += 1
                continue
            if candidate.classification == "manual-review":
                excluded_manual_review_count += 1
                continue
            if candidate.classification == "result-source-missing":
                excluded_result_source_missing_count += 1
                continue

            candidate.cache_status = _maybe_cache_status(candidate, cache_conn, ped_conn)
            if candidate.cache_status == "available":
                excluded_cache_available_count += 1
                reparse_candidate_count += 1
                continue
            raw_refetch_candidate_count += 1
            candidate_counts[candidate.url_type] += 1
            existing = raw_by_url.get(candidate.url)
            if existing is None:
                raw_by_url[candidate.url] = candidate
                continue
            priority_order = {"P0": 0, "P1": 1, "P2": 2}
            if priority_order.get(candidate.priority, 9) < priority_order.get(existing.priority, 9):
                raw_by_url[candidate.url] = candidate

        unique_candidates = list(raw_by_url.values())
        unique_url_count = len(unique_candidates)
        url_type_counts = Counter(c.url_type for c in unique_candidates)
        result_page_url_count = int(url_type_counts.get("result_page", 0))
        race_detail_url_count = int(url_type_counts.get("race_detail", 0))
        horse_detail_url_count = int(url_type_counts.get("horse_detail", 0))
        pedigree_url_count = int(url_type_counts.get("pedigree", 0))

        sample_urls: dict[str, list[dict[str, Any]]] = {
            "result_page": [],
            "race_detail": [],
            "horse_detail": [],
            "pedigree": [],
        }
        for candidate in sorted(unique_candidates, key=lambda c: (c.url_type, c.url, c.race_id or "", c.horse_id or "")):
            slot = _best_sample_slot(candidate)
            if len(sample_urls[slot]) < int(args.max_targets):
                sample_urls[slot].append(_sample_item(candidate))

        estimated_http_request_count = unique_url_count
        estimated_runtime_seconds = round(float(unique_url_count) * DEFAULT_AVG_SEC_PER_REQ, 2)
        cache_available_rate = (excluded_cache_available_count / len(raw_candidates)) if raw_candidates else 0.0

        report = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "input_audit": str(args.input_audit),
            "input_p0_plan": str(args.input_p0_plan),
            "input_cache_diagnosis": str(args.input_cache_diagnosis),
            "target": args.target,
            "verdict": "warn" if unique_url_count > 0 else "pass",
            "verdict_reason": "targeted-refetch-dry-run",
            "p0_total_count": p0_total_count,
            "p0_plan_total_count": p0_plan_total_count,
            "audit_p0_true_missing_count": audit_counts["audit_p0_true_missing_count"],
            "refetch_candidate_count": raw_refetch_candidate_count,
            "unique_url_count": unique_url_count,
            "race_result_url_count": result_page_url_count,
            "race_detail_url_count": race_detail_url_count,
            "horse_detail_url_count": horse_detail_url_count,
            "pedigree_url_count": pedigree_url_count,
            "excluded_schema_review_count": exclusion_counts["schema"],
            "excluded_domain_allowed_count": exclusion_counts["domain"],
            "excluded_metadata_repair_count": exclusion_counts["metadata"],
            "excluded_cache_available_count": excluded_cache_available_count,
            "excluded_source_empty_result_cells_count": excluded_source_empty_result_cells_count,
            "excluded_manual_review_count": excluded_manual_review_count,
            "excluded_result_source_missing_count": excluded_result_source_missing_count,
            "reparse_candidate_count": reparse_candidate_count,
            "classification_breakdown": [
                {"classification": k, "count": int(v)} for k, v in sorted(classification_breakdown.items(), key=lambda kv: kv[1], reverse=True)
            ],
            "estimated_http_request_count": estimated_http_request_count,
            "estimated_runtime_seconds": estimated_runtime_seconds,
            "rate_limit_policy": "sequential read-only URL enumeration; one request per URL only after explicit execution approval; no parallelism",
            "safety_flags": {
                "read_only": True,
                "no_db_write": True,
                "no_http_access": True,
                "no_scrape_execute": True,
                "no_upsert": True,
                "no_force_refresh_execute": True,
            },
            "sample_urls": sample_urls,
            "recommended_next_actions": _recommended_next_actions(unique_url_count, {
                "finish_position": sum(1 for c in unique_candidates if c.column == "finish_position"),
                "source_empty_result_cells": excluded_source_empty_result_cells_count,
                "result_source_missing": excluded_result_source_missing_count,
                "race_without_horse_data": sum(1 for c in unique_candidates if c.reason == "consistency:race_without_horse_data"),
                "pedigree": pedigree_url_count,
            }, args.target, cache_available_rate),
        }

        if cache_diag_sampled:
            report["cache_diagnosis_note"] = "p0_cache_coverage_diagnosis indicates the reparse plan is sampled"

        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        print(
            json.dumps(
                {
                    "output": str(out),
                    "verdict": report["verdict"],
                    "p0_total_count": report["p0_total_count"],
                    "refetch_candidate_count": report["refetch_candidate_count"],
                    "unique_url_count": report["unique_url_count"],
                    "race_result_url_count": report["race_result_url_count"],
                    "race_detail_url_count": report["race_detail_url_count"],
                    "horse_detail_url_count": report["horse_detail_url_count"],
                    "pedigree_url_count": report["pedigree_url_count"],
                    "estimated_http_request_count": report["estimated_http_request_count"],
                    "estimated_runtime_seconds": report["estimated_runtime_seconds"],
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