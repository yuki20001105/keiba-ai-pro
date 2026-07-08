from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT_DIR / "reports"
PLAN_SCRIPT = ROOT_DIR / "scripts" / "plan_p0_scrape_repair.py"


def _run_plan(audit_path: Path, refresh_path: Path, output_path: Path) -> tuple[int, dict[str, Any]]:
    proc = subprocess.run(
        [
            sys.executable,
            str(PLAN_SCRIPT),
            "--input-audit",
            str(audit_path),
            "--input-refresh-plan",
            str(refresh_path),
            "--target",
            "all",
            "--output",
            str(output_path),
        ],
        cwd=str(ROOT_DIR),
        text=True,
        capture_output=True,
    )

    payload: dict[str, Any] = {}
    out = (proc.stdout or "").strip()
    if out:
        try:
            payload = json.loads(out.splitlines()[-1])
        except Exception:
            payload = {"raw_stdout": out[-1000:]}

    if output_path.exists():
        try:
            detail = json.loads(output_path.read_text(encoding="utf-8"))
            if isinstance(detail, dict):
                payload["_detail"] = detail
        except Exception:
            pass

    return proc.returncode, payload


def _make_audit_fixture() -> dict[str, Any]:
    return {
        "repair_reason_breakdown": [
            {
                "reason": "true-missing",
                "column": "finish_position",
                "required_level": "required_if_result",
                "count": 2,
                "priority": "P0",
                "example_keys": ["202601010101:2021100001", "202601010101:2021100002"],
            },
            {
                "reason": "consistency:race_without_horse_data",
                "column": "(check)",
                "required_level": "consistency",
                "count": 3,
                "priority": "P0",
                "example_keys": ["202601010201", "202601010201", "202601010202"],
            },
            {
                "reason": "derived-field-candidate",
                "column": "race_number",
                "required_level": "required",
                "count": 1,
                "priority": "Schema review",
                "example_keys": ["202601010301:2021100301"],
            },
            {
                "reason": "domain-allowed-missing",
                "column": "margin",
                "required_level": "required_if_result",
                "count": 1,
                "priority": "Domain allowed",
                "example_keys": ["202601010401:2021100401"],
            },
        ]
    }


def _make_refresh_fixture() -> dict[str, Any]:
    return {
        "estimated_http_request_count": 4,
        "estimated_runtime": 8.0,
        "decisions": [
            {
                "key": "202601010101:2021100001",
                "action": "repair",
                "reason": "required-missing-or-invalid",
                "missing_fields": ["finish_position"],
                "parser_version": "2.0.0",
                "fetched_at": "2026-07-01 10:00:00",
            },
            {
                "key": "202601010101:2021100002",
                "action": "reparse-cache",
                "reason": "stale-parser-version",
                "missing_fields": ["finish_position"],
                "parser_version": "1.0.0",
                "fetched_at": "2026-07-01 10:00:00",
            },
            {
                "key": "202601010301:2021100301",
                "action": "schema-review",
                "reason": "schema-mismatch-or-derived-candidate",
                "missing_fields": ["race_number"],
                "parser_version": "2.0.0",
                "fetched_at": "2026-07-01 10:00:00",
            },
        ],
    }


def _has_sample_with(plan: dict[str, Any], *, reason: str, action: str, column: str) -> bool:
    for item in plan.get("sample_targets", []) if isinstance(plan.get("sample_targets"), list) else []:
        if not isinstance(item, dict):
            continue
        if str(item.get("reason")) == reason and str(item.get("action")) == action and str(item.get("column")) == column:
            return True
    return False


def main() -> int:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    result_file = REPORTS_DIR / "p0_scrape_repair_plan_smoke_result.json"
    run_id = int(time.time())

    with tempfile.TemporaryDirectory(prefix="smoke_p0_repair_plan_") as td:
        tmp = Path(td)
        audit_path = tmp / "audit.json"
        refresh_path = tmp / "refresh.json"
        plan_out = tmp / "p0_plan.json"

        audit_path.write_text(json.dumps(_make_audit_fixture(), ensure_ascii=False, indent=2), encoding="utf-8")
        refresh_path.write_text(json.dumps(_make_refresh_fixture(), ensure_ascii=False, indent=2), encoding="utf-8")

        rc, payload = _run_plan(audit_path=audit_path, refresh_path=refresh_path, output_path=plan_out)

    detail = payload.get("_detail") if isinstance(payload.get("_detail"), dict) else {}
    action_breakdown = detail.get("p0_action_breakdown") if isinstance(detail.get("p0_action_breakdown"), list) else []
    action_map = {f"{str(x.get('action'))}:{str(x.get('column'))}": int(x.get("count") or 0) for x in action_breakdown if isinstance(x, dict)}

    checks = {
        "finish_position_true_missing_is_repair_target": bool(
            _has_sample_with(detail, reason="true-missing", action="reparse-cache", column="finish_position")
            or _has_sample_with(detail, reason="true-missing", action="refetch-required", column="finish_position")
        ),
        "race_without_horse_data_refetch_required": bool(
            _has_sample_with(
                detail,
                reason="consistency:race_without_horse_data",
                action="refetch-required",
                column="(check)",
            )
        ),
        "race_number_derived_schema_review": bool(
            _has_sample_with(detail, reason="derived-field-candidate", action="schema-review", column="race_number")
        ),
        "race_number_derived_not_refetch": bool(action_map.get("refetch-required:race_number", 0) == 0),
        "winner_margin_blank_domain_allowed_no_action": bool(
            _has_sample_with(detail, reason="domain-allowed-missing", action="no-action-domain-allowed", column="margin")
        ),
        "race_without_horse_data_refetch_dedup_by_race_id": bool(int(detail.get("refetch_required_count") or 0) == 2),
    }

    success = bool(rc == 0 and all(checks.values()))
    output = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "success": success,
        "verdict": "pass" if success else "fail",
        "verdict_reason": "ok" if success else "p0-scrape-repair-plan-smoke-failed",
        "checks": checks,
        "plan_result": {"return_code": rc, **payload},
        "run_id": run_id,
    }
    result_file.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"result_file": str(result_file), "success": success, "checks": checks}, ensure_ascii=False))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
