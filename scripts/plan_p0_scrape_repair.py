#!/usr/bin/env python3
"""Build a read-only P0 scrape repair plan from existing audit and refresh-plan reports.

This script never writes to DB and never performs scraping.
It only classifies candidate targets into repair actions.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_AUDIT_INPUT = ROOT_DIR / "reports" / "scrape_missingness_audit.json"
DEFAULT_REFRESH_INPUT = ROOT_DIR / "reports" / "scrape_refresh_plan.json"
DEFAULT_SOURCE_EMPTY_DIAG_INPUT = ROOT_DIR / "reports" / "source_empty_result_cells_diagnosis.json"
DEFAULT_OUTPUT = ROOT_DIR / "reports" / "p0_scrape_repair_plan.json"
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

P0_SCOPE_COLUMNS = {
    "finish_position",
    "race_id",
    "horse_id",
    "race_date",
    "venue",
    "horse_name",
    "frame_number",
    "horse_number",
}

P0_SCOPE_CHECKS = {
    "race_without_horse_data",
    "horse_id_missing",
    "required_column_missing_rate_over_0pct",
}


@dataclass
class TargetRecord:
    race_id: str | None
    horse_id: str | None
    column: str
    reason: str
    priority: str
    source_hint: str
    action: str
    recommended_next_action: str


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


def _parse_example_key(raw: Any) -> tuple[str | None, str | None, str]:
    if isinstance(raw, dict):
        race_id = str(raw.get("race_id") or "").strip() or None
        horse_id = str(raw.get("horse_id") or "").strip() or None
        key_txt = str(raw.get("key") or f"{race_id or ''}:{horse_id or ''}").strip()
        return race_id, horse_id, key_txt

    txt = str(raw or "").strip()
    if not txt:
        return None, None, ""
    if ":" in txt:
        rid, hid = txt.split(":", 1)
        rid = rid.strip() or None
        hid = hid.strip() or None
        return rid, hid, txt
    return txt, None, txt


def _action_hint(action: str) -> str:
    if action == "reparse-cache":
        return "reparse-cache-first"
    if action == "refetch-required":
        return "refetch-required"
    if action == "source-empty-result-cells":
        return "source-review-domain-review"
    if action == "result-source-missing":
        return "result-source-missing-review"
    if action == "repair-from-existing-metadata":
        return "repair-from-existing-metadata"
    if action == "schema-review":
        return "review-schema-alias-derived-rules"
    if action == "manual-review":
        return "manual-review-required"
    if action == "no-action-domain-allowed":
        return "no-action-monitor"
    return "review"


def _infer_avg_sec_per_req(refresh: dict[str, Any]) -> float:
    req = float(refresh.get("estimated_http_request_count") or 0)
    runtime = float(refresh.get("estimated_runtime") or 0)
    if req > 0 and runtime > 0:
        return max(0.1, runtime / req)
    return DEFAULT_AVG_SEC_PER_REQ


def _is_p0_candidate(item: dict[str, Any]) -> bool:
    reason = str(item.get("reason") or "")
    col = str(item.get("column") or "")
    priority = str(item.get("priority") or "")
    if reason == "domain-allowed-missing":
        return col == "margin"
    if priority == "P0":
        return True
    if reason == "true-missing" and col in P0_SCOPE_COLUMNS:
        return True
    if reason.startswith("consistency:"):
        check_name = reason.split(":", 1)[1]
        return check_name in P0_SCOPE_CHECKS
    if reason in ("derived-field-candidate", "alias-candidate") and col == "race_number":
        return True
    if reason == "source-empty-result-cells" and col == "finish_position":
        return True
    return False


def _target_allowed(column: str, target: str) -> bool:
    return column in TARGET_COLUMNS.get(target, TARGET_COLUMNS["all"])


def _build_refresh_decision_map(refresh: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in refresh.get("decisions", []) if isinstance(refresh.get("decisions"), list) else []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        out[key] = item
    return out


def _choose_action(
    *,
    reason: str,
    column: str,
    key_txt: str,
    refresh_decisions: dict[str, dict[str, Any]],
) -> tuple[str, str]:
    if reason == "domain-allowed-missing":
        return "no-action-domain-allowed", "domain-allowed"

    if reason in ("derived-field-candidate", "alias-candidate"):
        return "schema-review", "schema/derived-candidate"

    if reason == "source-empty-result-cells":
        return "source-empty-result-cells", "result-row-empty-cells"

    if reason.startswith("consistency:"):
        check_name = reason.split(":", 1)[1]
        if check_name == "race_without_horse_data":
            return "refetch-required", "race-level-missing-horse-rows"
        return "manual-review", "consistency-check"

    dec = refresh_decisions.get(key_txt, {})
    dec_action = str(dec.get("action") or "")
    parser_version = str(dec.get("parser_version") or "").strip()
    fetched_at = str(dec.get("fetched_at") or "").strip()
    has_cache_hint = bool(parser_version or fetched_at)

    if column in ("race_date", "venue") and reason == "true-missing":
        return "repair-from-existing-metadata", "recoverable-from-race-metadata"

    if column == "race_number" and reason == "true-missing":
        return "schema-review", "prefer-derived-or-schema-mapping"

    if column in ("race_id", "horse_id") and reason == "true-missing":
        return "manual-review", "identifier-missing"

    if column == "finish_position" and reason == "true-missing":
        dec_reason = str(dec.get("reason") or "")
        if "source-empty-result-cells" in dec_reason:
            return "source-empty-result-cells", "from-refresh-decision-source-empty"
        if dec_action == "reparse-cache" or has_cache_hint:
            return "reparse-cache", "result-cache-reparse-priority"
        return "result-source-missing", "result-cache-missing-refetch"

    if column in ("result_time", "margin") and reason == "true-missing":
        dec_reason = str(dec.get("reason") or "")
        if "source-empty-result-cells" in dec_reason:
            return "source-empty-result-cells", "from-refresh-decision-source-empty"
        if dec_action == "reparse-cache" or has_cache_hint:
            return "reparse-cache", "result-cache-reparse-priority"
        return "result-source-missing", "result-cache-missing-refetch"

    if column in ("horse_name", "frame_number", "horse_number") and reason == "true-missing":
        if dec_action == "reparse-cache":
            return "reparse-cache", "candidate-parser-reparse"
        return "refetch-required", "horse-row-required-field-missing"

    if dec_action == "schema-review":
        return "schema-review", "from-refresh-decision"
    if dec_action == "reparse-cache":
        return "reparse-cache", "from-refresh-decision"
    if dec_action == "refetch":
        return "refetch-required", "from-refresh-decision"

    return "manual-review", "unmapped-case"


def _recommended_actions(records: list[TargetRecord], source_empty_diag: dict[str, Any] | None = None) -> list[str]:
    actions = {r.action for r in records}
    out: list[str] = []
    if any(r.column == "finish_position" and r.reason == "true-missing" for r in records):
        out.append("finish_position true missing は result page の reparse-cache を優先")
        out.append("cacheがなければ targeted refetch dry-run 候補")
    if any(r.action == "source-empty-result-cells" for r in records):
        out.append("result row があり finish/time/margin が空の場合は source review / domain review を優先")
        out.append("source-empty-result-cells は targeted refetch execution 候補から分離")
    if any(r.action == "result-source-missing" for r in records):
        out.append("result-source-missing は URL/page種別/対象キーの妥当性を先に確認")
        out.append("cache不在だけでは即時 refetch-required にしない")
    if any(r.reason == "consistency:race_without_horse_data" for r in records):
        out.append("race_without_horse_data は race_id単位で refetch候補")
    if any(r.column == "race_number" and r.action == "schema-review" for r in records):
        out.append("race_number derived は schema-review で対応し、HTTP refetchしない")
    if any(r.column == "margin" and r.action == "no-action-domain-allowed" for r in records):
        out.append("margin domain allowed は no-action")
    if "manual-review" in actions:
        out.append("identifier missing や consistency failure は manual-review で隔離確認")
    if "repair-from-existing-metadata" in actions:
        out.append("race_date/venue は races metadata から補完可能なら先に修復")

    if isinstance(source_empty_diag, dict):
        breakdown = source_empty_diag.get("classification_breakdown") if isinstance(source_empty_diag.get("classification_breakdown"), list) else []
        diag_counts = {str(x.get("classification") or ""): int(x.get("count") or 0) for x in breakdown if isinstance(x, dict)}
        domain_allowed_count = int(source_empty_diag.get("domain_allowed_count") or 0)
        if domain_allowed_count > 0:
            out.append("source-empty domain-allowed 系は repair/refetch 対象から除外")
        if diag_counts.get("source-result-missing", 0) > 0:
            out.append("source-result-missing は manual-review または alternate source 候補")
        if diag_counts.get("alternate-page-required", 0) > 0:
            out.append("alternate-page-required は URL生成規則の見直し候補")
        if diag_counts.get("wrong-target-row", 0) > 0:
            out.append("wrong-target-row は horse_id/horse_number 紐付け修正候補")
    return out


def _group_breakdown(records: list[TargetRecord], kind: str) -> list[dict[str, Any]]:
    ctr: Counter[tuple[str, str]] = Counter()
    for r in records:
        if kind == "reason":
            ctr[(r.reason, r.column)] += 1
        else:
            ctr[(r.action, r.column)] += 1

    out: list[dict[str, Any]] = []
    for (a, b), c in sorted(ctr.items(), key=lambda kv: kv[1], reverse=True):
        if kind == "reason":
            out.append({"reason": a, "column": b, "count": c})
        else:
            out.append({"action": a, "column": b, "count": c})
    return out


def _build_samples(records: list[TargetRecord], max_per_group: int = 10) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[TargetRecord]] = defaultdict(list)
    for r in records:
        grouped[(r.reason, r.action)].append(r)

    out: list[dict[str, Any]] = []
    for _, group in sorted(grouped.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        for r in group[:max_per_group]:
            out.append(
                {
                    "race_id": r.race_id,
                    "horse_id": r.horse_id,
                    "column": r.column,
                    "reason": r.reason,
                    "action": r.action,
                    "priority": r.priority,
                    "source_hint": r.source_hint,
                    "recommended_next_action": r.recommended_next_action,
                }
            )
    return out


def _build_plan(
    audit: dict[str, Any],
    refresh: dict[str, Any],
    target: str,
    source_empty_diag: dict[str, Any] | None = None,
) -> dict[str, Any]:
    refresh_decisions = _build_refresh_decision_map(refresh)
    breakdown = audit.get("repair_reason_breakdown") if isinstance(audit.get("repair_reason_breakdown"), list) else []

    records: list[TargetRecord] = []

    for item in breakdown:
        if not isinstance(item, dict):
            continue
        if not _is_p0_candidate(item):
            continue

        reason = str(item.get("reason") or "")
        column = str(item.get("column") or "")
        priority = str(item.get("priority") or "")
        if not _target_allowed(column, target):
            continue

        examples = list(item.get("example_keys") or [])
        if not examples:
            examples = [""]

        for ex in examples:
            rid, hid, key_txt = _parse_example_key(ex)
            action, source_hint = _choose_action(
                reason=reason,
                column=column,
                key_txt=key_txt,
                refresh_decisions=refresh_decisions,
            )
            records.append(
                TargetRecord(
                    race_id=rid,
                    horse_id=hid,
                    column=column,
                    reason=reason,
                    priority=priority,
                    source_hint=source_hint,
                    action=action,
                    recommended_next_action=_action_hint(action),
                )
            )

    p0_total_count = len(records)
    action_counter = Counter(r.action for r in records)

    refetch_units: set[str] = set()
    for r in records:
        if r.action != "refetch-required":
            continue
        if r.reason == "consistency:race_without_horse_data":
            unit = f"race:{r.race_id}" if r.race_id else "race:(unknown)"
        else:
            if r.race_id and r.horse_id:
                unit = f"row:{r.race_id}:{r.horse_id}"
            elif r.race_id:
                unit = f"race:{r.race_id}"
            else:
                unit = "row:(unknown)"
        refetch_units.add(unit)

    estimated_http_request_count = len(refetch_units)
    avg_sec_per_req = _infer_avg_sec_per_req(refresh)
    estimated_runtime_seconds = round(estimated_http_request_count * avg_sec_per_req, 2)

    verdict = "pass" if p0_total_count == 0 else "warn"

    return {
        "verdict": verdict,
        "target": target,
        "p0_total_count": p0_total_count,
        "p0_reason_breakdown": _group_breakdown(records, kind="reason"),
        "p0_action_breakdown": _group_breakdown(records, kind="action"),
        "refetch_required_count": len(refetch_units),
        "reparse_cache_count": int(action_counter.get("reparse-cache", 0)),
        "repair_from_metadata_count": int(action_counter.get("repair-from-existing-metadata", 0)),
        "schema_review_count": int(action_counter.get("schema-review", 0)),
        "manual_review_count": int(action_counter.get("manual-review", 0)),
        "no_action_count": int(action_counter.get("no-action-domain-allowed", 0)),
        "estimated_http_request_count": estimated_http_request_count,
        "estimated_runtime_seconds": estimated_runtime_seconds,
        "sample_targets": _build_samples(records, max_per_group=10),
        "recommended_next_actions": _recommended_actions(records, source_empty_diag),
        "safeguards": {
            "read_only": True,
            "no_db_write": True,
            "no_scrape_execute": True,
            "no_upsert": True,
            "no_force_refresh_execute": True,
        },
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Build read-only P0 scrape repair planning from report artifacts")
    p.add_argument("--input-audit", default=str(DEFAULT_AUDIT_INPUT), help="Path to scrape_missingness_audit.json")
    p.add_argument("--input-refresh-plan", default=str(DEFAULT_REFRESH_INPUT), help="Path to scrape_refresh_plan.json")
    p.add_argument("--input-source-empty-diagnosis", default=str(DEFAULT_SOURCE_EMPTY_DIAG_INPUT), help="Path to source_empty_result_cells_diagnosis.json")
    p.add_argument("--target", choices=["all", "race", "horse", "result", "pedigree", "odds"], default="all")
    p.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSON path")
    return p


def main() -> int:
    args = _build_parser().parse_args()

    audit = _load_json(Path(args.input_audit), label="input-audit")
    refresh = _load_json(Path(args.input_refresh_plan), label="input-refresh-plan")
    source_empty_diag_path = Path(args.input_source_empty_diagnosis)
    source_empty_diag = _load_json(source_empty_diag_path, label="input-source-empty-diagnosis") if source_empty_diag_path.exists() else None

    plan = _build_plan(audit=audit, refresh=refresh, target=str(args.target), source_empty_diag=source_empty_diag)
    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "input_audit": str(args.input_audit),
        "input_refresh_plan": str(args.input_refresh_plan),
        "input_source_empty_diagnosis": str(args.input_source_empty_diagnosis),
        **plan,
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "output": str(out),
                "verdict": payload.get("verdict"),
                "target": payload.get("target"),
                "p0_total_count": payload.get("p0_total_count"),
                "refetch_required_count": payload.get("refetch_required_count"),
                "reparse_cache_count": payload.get("reparse_cache_count"),
                "repair_from_metadata_count": payload.get("repair_from_metadata_count"),
                "schema_review_count": payload.get("schema_review_count"),
                "manual_review_count": payload.get("manual_review_count"),
                "no_action_count": payload.get("no_action_count"),
                "estimated_http_request_count": payload.get("estimated_http_request_count"),
                "estimated_runtime_seconds": payload.get("estimated_runtime_seconds"),
            },
            ensure_ascii=False,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
