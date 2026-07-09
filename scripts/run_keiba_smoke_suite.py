from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT_DIR / "reports"


def _run_step(name: str, args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, *args],
        cwd=str(ROOT_DIR),
        text=True,
        capture_output=True,
    )
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    tail = "\n".join([x for x in [out, err] if x]).strip()
    if not tail:
        tail = "(no output)"
    return proc.returncode, tail


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _classify_preflight(report: dict[str, Any] | None, token_provided: bool) -> tuple[str, str]:
    if not isinstance(report, dict):
        return "fail", "preflight report missing or invalid"

    check = report.get("check") if isinstance(report.get("check"), dict) else {}
    status = check.get("status")
    if status in (401, 403) and not token_provided:
        return "warn", "auth-required"

    verdict = str(report.get("verdict") or "")
    reason = str(report.get("verdict_reason") or "")

    if verdict == "pass":
        return "pass", reason or "ready"
    if verdict == "warn":
        return "warn", reason or "non-ready"
    return "fail", reason or "contract-error"


def _classify_dry_run(report: dict[str, Any] | None, token_provided: bool) -> tuple[str, str]:
    if not isinstance(report, dict):
        return "fail", "dry-run report missing or invalid"

    check = report.get("check") if isinstance(report.get("check"), dict) else {}
    status = check.get("status")
    if status in (401, 403) and not token_provided:
        return "warn", "auth-required"

    verdict = str(report.get("verdict") or "")
    reason = str(report.get("verdict_reason") or "")

    if verdict == "pass":
        return "pass", reason or "ready"
    if verdict == "warn":
        return "warn", reason or "non-ready"
    return "fail", reason or "contract-error"


def _classify_payload_diff(report: dict[str, Any] | None) -> tuple[str, str]:
    if not isinstance(report, dict):
        return "fail", "payload-diff report missing or invalid"

    verdict = str(report.get("verdict") or "")
    reason = str(report.get("verdict_reason") or "")

    warn_reasons = {
        "missing-report",
        "stale-report",
        "dry-run-unavailable",
        "upstream-unavailable",
        "schema-mismatch",
    }

    if reason in warn_reasons:
        return "warn", reason
    if reason == "contract-error":
        return "fail", reason
    if reason == "unexpected-exception":
        return "fail", reason

    if verdict == "pass":
        return "pass", reason or "contracts-compatible"
    if verdict == "warn":
        return "warn", reason or "contract-diff-detected"
    return "fail", reason or "contract-error"


def _classify_write_guard(report: dict[str, Any] | None, token_provided: bool) -> tuple[str, str]:
    if not isinstance(report, dict):
        return "fail", "write-guard report missing or invalid"

    check = report.get("check") if isinstance(report.get("check"), dict) else {}
    status = check.get("status")
    if status in (401, 403) and not token_provided:
        return "warn", "auth-required"

    verdict = str(report.get("verdict") or "")
    reason = str(report.get("verdict_reason") or "")

    if verdict == "pass":
        return "pass", reason or "write-disabled-default"
    if verdict == "warn":
        return "warn", reason or "guarded"
    return "fail", reason or "contract-error"


def _classify_notion_report(report: dict[str, Any] | None, token_provided: bool) -> tuple[str, str]:
    if not isinstance(report, dict):
        return "fail", "notion-report missing or invalid"

    verdict = str(report.get("verdict") or "")
    if not token_provided and verdict == "warn":
        return "warn", "auth-required"

    if verdict == "pass":
        return "pass", "ok"
    if verdict == "warn":
        return "warn", "partial-verification"
    return "fail", "notion-report-smoke-failed"


def _classify_model_redesign_workbench(report: dict[str, Any] | None, token_provided: bool) -> tuple[str, str]:
    if not isinstance(report, dict):
        return "fail", "model-redesign-workbench missing or invalid"

    verdict = str(report.get("verdict") or "")
    if not token_provided and verdict == "warn":
        return "warn", "auth-required"

    if verdict == "pass":
        return "pass", "ok"
    if verdict == "warn":
        return "warn", "partial-verification"
    return "fail", "model-redesign-workbench-smoke-failed"


def _classify_fetch_summary_history(report: dict[str, Any] | None, token_provided: bool) -> tuple[str, str]:
    if not isinstance(report, dict):
        return "fail", "fetch-summary-history report missing or invalid"

    verdict = str(report.get("verdict") or "")
    if not token_provided and verdict == "warn":
        return "warn", "auth-required"

    if verdict == "pass":
        return "pass", "ok"
    if verdict == "warn":
        return "warn", "partial-verification"
    return "fail", "fetch-summary-history-smoke-failed"


def _classify_refresh_plan_api(report: dict[str, Any] | None, token_provided: bool) -> tuple[str, str]:
    if not isinstance(report, dict):
        return "fail", "refresh-plan-api report missing or invalid"

    verdict = str(report.get("verdict") or "")
    if not token_provided and verdict == "warn":
        return "warn", "auth-required"

    if verdict == "pass":
        return "pass", "ok"
    if verdict == "warn":
        return "warn", "partial-verification"
    return "fail", "refresh-plan-api-smoke-failed"


def _classify_p0_repair_plan_api(report: dict[str, Any] | None, token_provided: bool) -> tuple[str, str]:
    if not isinstance(report, dict):
        return "fail", "p0-repair-plan-api report missing or invalid"

    verdict = str(report.get("verdict") or "")
    if not token_provided and verdict == "warn":
        return "warn", "auth-required"

    if verdict == "pass":
        return "pass", "ok"
    if verdict == "warn":
        return "warn", "partial-verification"
    return "fail", "p0-repair-plan-api-smoke-failed"


def _classify_p0_reparse_cache(report: dict[str, Any] | None) -> tuple[str, str]:
    if not isinstance(report, dict):
        return "fail", "p0-reparse-cache report missing or invalid"

    verdict = str(report.get("verdict") or "")
    if verdict == "pass":
        return "pass", "ok"
    if verdict == "warn":
        return "warn", "partial-verification"
    return "fail", "p0-reparse-cache-smoke-failed"


def _classify_p0_cache_coverage_diagnosis(report: dict[str, Any] | None) -> tuple[str, str]:
    if not isinstance(report, dict):
        return "fail", "p0-cache-coverage-diagnosis report missing or invalid"

    verdict = str(report.get("verdict") or "")
    if verdict == "pass":
        return "pass", "ok"
    if verdict == "warn":
        return "warn", "partial-verification"
    return "fail", "p0-cache-coverage-diagnosis-smoke-failed"


def _classify_p0_targeted_refetch_plan(report: dict[str, Any] | None) -> tuple[str, str]:
    if not isinstance(report, dict):
        return "fail", "p0-targeted-refetch-plan report missing or invalid"

    verdict = str(report.get("verdict") or "")
    if verdict == "pass":
        return "pass", "ok"
    if verdict == "warn":
        return "warn", "partial-verification"
    return "fail", "p0-targeted-refetch-plan-smoke-failed"


def _classify_p0_targeted_refetch_live_validation(report: dict[str, Any] | None) -> tuple[str, str]:
    if not isinstance(report, dict):
        return "fail", "p0-targeted-refetch-live-validation report missing or invalid"

    verdict = str(report.get("verdict") or "")
    if verdict == "pass":
        return "pass", "ok"
    if verdict == "warn":
        return "warn", "partial-verification"
    return "fail", "p0-targeted-refetch-live-validation-smoke-failed"


def _classify_source_empty_result_cells_diagnosis(report: dict[str, Any] | None) -> tuple[str, str]:
    if not isinstance(report, dict):
        return "fail", "source-empty-result-cells-diagnosis report missing or invalid"

    verdict = str(report.get("verdict") or "")
    if verdict == "pass":
        return "pass", "ok"
    if verdict == "warn":
        return "warn", "partial-verification"
    return "fail", "source-empty-result-cells-diagnosis-smoke-failed"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run keiba smoke checks as an operational suite")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"), help="YYYYMMDD for race-list/preflight checks")
    parser.add_argument("--race-id", default="202406010101", help="12-digit race id for preflight check")
    parser.add_argument("--fastapi-url", default="http://127.0.0.1:8000", help="FastAPI base URL")
    parser.add_argument("--next-url", default="http://localhost:3000", help="Next.js base URL")
    parser.add_argument("--auth-token", default="", help="Optional Bearer token for protected endpoints")
    parser.add_argument(
        "--strict-preflight",
        action="store_true",
        help="Fail suite when preflight status is degraded/unavailable (default: warn only)",
    )
    parser.add_argument(
        "--verify-write-guard-enabled",
        action="store_true",
        help="Run additional write guard checks expecting NETKEIBA_RACE_WRITE_ENABLED=true",
    )
    parser.add_argument(
        "--verify-write-guard-flag-only",
        action="store_true",
        help="Run write guard check expecting NETKEIBA_RACE_WRITE_ENABLED=true only branch to be blocked",
    )
    parser.add_argument(
        "--verify-write-guard-sandbox-precheck",
        action="store_true",
        help="Run read-only sandbox precheck smoke (disabled by default)",
    )
    parser.add_argument(
        "--verify-write-guard-sandbox-write",
        action="store_true",
        help="Run explicit sandbox write smoke (disabled by default)",
    )
    parser.add_argument(
        "--verify-write-guard-sandbox-write-readback",
        action="store_true",
        help="Run explicit sandbox write + readback smoke (disabled by default)",
    )
    parser.add_argument(
        "--verify-write-guard-production-block",
        action="store_true",
        help="Run write guard check expecting APP_ENV=production hard block branch",
    )
    parser.add_argument(
        "--verify-write-guard-staging-lock-missing",
        action="store_true",
        help="Run write guard check expecting ALLOW_STAGING_WRITE=false block branch",
    )
    args = parser.parse_args()

    token_from_env = os.getenv("KEIBA_AUTH_BEARER_TOKEN", "").strip()
    effective_token = args.auth_token.strip() or token_from_env
    token_args: list[str] = []
    if effective_token:
        token_args = ["--auth-token", effective_token]

    token_provided = bool(effective_token)

    suite: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "mode": "strict-preflight" if args.strict_preflight else "contract-only-preflight",
        "steps": {},
    }

    analyze_rc, analyze_log = _run_step("analyze-race", [
        "scripts/smoke_analyze_race_api.py",
        *token_args,
    ])
    analyze_report = _read_json(REPORTS_DIR / "analyze_race_smoke_result.json")
    analyze_status = analyze_report.get("http_status") if isinstance(analyze_report, dict) else None
    analyze_verdict = str(analyze_report.get("verdict") or "") if isinstance(analyze_report, dict) else ""
    analyze_auth_required = bool(analyze_report.get("auth_required")) if isinstance(analyze_report, dict) else False
    if analyze_verdict == "pass":
        analyze_result = "pass"
        analyze_reason = "ok"
    elif analyze_auth_required and analyze_status in (401, 403) and not token_provided:
        analyze_result = "warn"
        analyze_reason = "auth-required"
    else:
        analyze_result = "fail"
        analyze_reason = str(analyze_report.get("validation") or "analyze-race-smoke-failed") if isinstance(analyze_report, dict) else "analyze-race-smoke-failed"

    suite["steps"]["analyze_race"] = {
        "return_code": analyze_rc,
        "result": analyze_result,
        "reason": analyze_reason,
        "note": "analyze_race API smoke",
        "log_tail": analyze_log[-2000:],
    }

    race_list_rc, race_list_log = _run_step("race-list", [
        "scripts/smoke_netkeiba_race_list_proxy.py",
        "--date", args.date,
        "--fastapi-url", args.fastapi_url,
        "--next-url", args.next_url,
        *token_args,
    ])
    race_list_report = _read_json(REPORTS_DIR / "netkeiba_race_list_proxy_smoke_result.json")
    race_list_result = "pass" if race_list_rc == 0 else "fail"
    race_list_reason = "ok" if race_list_rc == 0 else "request-failed"
    if isinstance(race_list_report, dict):
        checks = race_list_report.get("checks") if isinstance(race_list_report.get("checks"), dict) else {}
        fa_status = checks.get("fastapi", {}).get("status") if isinstance(checks.get("fastapi"), dict) else None
        nx_status = checks.get("next", {}).get("status") if isinstance(checks.get("next"), dict) else None
        if fa_status in (401, 403) and nx_status in (401, 403) and not token_provided:
            race_list_result = "warn"
            race_list_reason = "auth-required"

    suite["steps"]["race_list_proxy"] = {
        "return_code": race_list_rc,
        "result": race_list_result,
        "reason": race_list_reason,
        "note": "Next -> FastAPI race-list proxy smoke",
        "log_tail": race_list_log[-2000:],
    }

    preflight_args = [
        "scripts/smoke_netkeiba_race_preflight.py",
        "--race-id", args.race_id,
        "--date", args.date,
        "--fastapi-url", args.fastapi_url,
        *token_args,
    ]
    if args.strict_preflight:
        preflight_args.append("--fail-on-nonready")

    preflight_rc, preflight_log = _run_step("race-preflight", preflight_args)
    preflight_report = _read_json(REPORTS_DIR / "netkeiba_race_preflight_smoke_result.json")
    pf_result, pf_reason = _classify_preflight(preflight_report, token_provided=token_provided)
    suite["steps"]["race_preflight"] = {
        "return_code": preflight_rc,
        "result": pf_result,
        "reason": pf_reason,
        "note": "Contract check: ready=pass, degraded/unavailable=warn, contract-error=fail",
        "log_tail": preflight_log[-2000:],
    }

    dry_run_args = [
        "scripts/smoke_netkeiba_race_dry_run.py",
        "--race-id", args.race_id,
        "--date", args.date,
        "--fastapi-url", args.fastapi_url,
        *token_args,
    ]
    if args.strict_preflight:
        dry_run_args.append("--fail-on-nonready")

    dry_run_rc, dry_run_log = _run_step("race-dry-run", dry_run_args)
    dry_run_report = _read_json(REPORTS_DIR / "netkeiba_race_dry_run_smoke_result.json")
    dr_result, dr_reason = _classify_dry_run(dry_run_report, token_provided=token_provided)
    suite["steps"]["race_dry_run"] = {
        "return_code": dry_run_rc,
        "result": dr_result,
        "reason": dr_reason,
        "note": "Dry-run contract: ready=pass, degraded/unavailable/invalid=warn, contract-error=fail",
        "log_tail": dry_run_log[-2000:],
    }

    payload_diff_rc, payload_diff_log = _run_step("payload-diff", [
        "scripts/compare_netkeiba_race_payload_contract.py",
    ])
    payload_diff_report = _read_json(REPORTS_DIR / "netkeiba_race_payload_contract_diff.json")
    pd_result, pd_reason = _classify_payload_diff(payload_diff_report)
    suite["steps"]["payload_contract_diff"] = {
        "return_code": payload_diff_rc,
        "result": pd_result,
        "reason": pd_reason,
        "note": "Payload contract diff: pass/warn unless contract-error",
        "log_tail": payload_diff_log[-2000:],
    }

    write_guard_rc, write_guard_log = _run_step("write-guard", [
        "scripts/smoke_netkeiba_race_write_guard.py",
        "--race-id", args.race_id,
        "--date", args.date,
        "--fastapi-url", args.fastapi_url,
        *token_args,
    ])
    write_guard_report = _read_json(REPORTS_DIR / "netkeiba_race_write_guard_smoke_result.json")
    wg_result, wg_reason = _classify_write_guard(write_guard_report, token_provided=token_provided)
    suite["steps"]["race_write_guard"] = {
        "return_code": write_guard_rc,
        "result": wg_result,
        "reason": wg_reason,
        "note": "Write guard: default disabled is pass, contract-error is fail",
        "log_tail": write_guard_log[-2000:],
    }

    notion_report_rc, notion_report_log = _run_step("notion-report", [
        "scripts/smoke_notion_report_api.py",
        "--next-url", args.next_url,
        *token_args,
    ])
    notion_report_report = _read_json(REPORTS_DIR / "notion_report_smoke_result.json")
    nr_result, nr_reason = _classify_notion_report(notion_report_report, token_provided=token_provided)
    suite["steps"]["notion_report"] = {
        "return_code": notion_report_rc,
        "result": nr_result,
        "reason": nr_reason,
        "note": "Notion UI/API smoke: preview, send/config-missing, 403, no token exposure, path forbidden",
        "log_tail": notion_report_log[-2000:],
    }

    model_redesign_rc, model_redesign_log = _run_step("model-redesign-workbench", [
        "scripts/smoke_model_redesign_workbench.py",
        "--next-url", args.next_url,
        *token_args,
    ])
    model_redesign_report = _read_json(REPORTS_DIR / "model_redesign_workbench_smoke_result.json")
    mr_result, mr_reason = _classify_model_redesign_workbench(model_redesign_report, token_provided=token_provided)
    suite["steps"]["model_redesign_workbench"] = {
        "return_code": model_redesign_rc,
        "result": mr_result,
        "reason": mr_reason,
        "note": "Model redesign workbench smoke: auth, read-only guard, path forbidden, action blocked, no artifact mutation",
        "log_tail": model_redesign_log[-2000:],
    }

    fetch_summary_history_rc, fetch_summary_history_log = _run_step("fetch-summary-history", [
        "scripts/smoke_fetch_summary_history.py",
        "--next-url", args.next_url,
        *token_args,
    ])
    fetch_summary_history_report = _read_json(REPORTS_DIR / "fetch_summary_history_smoke_result.json")
    fsh_result, fsh_reason = _classify_fetch_summary_history(fetch_summary_history_report, token_provided=token_provided)
    suite["steps"]["fetch_summary_history"] = {
        "return_code": fetch_summary_history_rc,
        "result": fsh_result,
        "reason": fsh_reason,
        "note": "Fetch summary history smoke: auth behavior, limit, read-only, secret non-exposure, major fields",
        "log_tail": fetch_summary_history_log[-2000:],
    }

    refresh_plan_api_rc, refresh_plan_api_log = _run_step("refresh-plan-api", [
        "scripts/smoke_scrape_refresh_plan_api.py",
        "--next-url", args.next_url,
        *token_args,
    ])
    refresh_plan_api_report = _read_json(REPORTS_DIR / "scrape_refresh_plan_api_smoke_result.json")
    rpa_result, rpa_reason = _classify_refresh_plan_api(refresh_plan_api_report, token_provided=token_provided)
    suite["steps"]["scrape_refresh_plan_api"] = {
        "return_code": refresh_plan_api_rc,
        "result": rpa_result,
        "reason": rpa_reason,
        "note": "Refresh plan API smoke: dry-run only, path-input reject, update disabled, auth behavior",
        "log_tail": refresh_plan_api_log[-2000:],
    }

    p0_repair_plan_api_rc, p0_repair_plan_api_log = _run_step("p0-repair-plan-api", [
        "scripts/smoke_p0_scrape_repair_plan_api.py",
        "--next-url", args.next_url,
        *token_args,
    ])
    p0_repair_plan_api_report = _read_json(REPORTS_DIR / "p0_scrape_repair_plan_api_smoke_result.json")
    p0rpa_result, p0rpa_reason = _classify_p0_repair_plan_api(p0_repair_plan_api_report, token_provided=token_provided)
    suite["steps"]["p0_scrape_repair_plan_api"] = {
        "return_code": p0_repair_plan_api_rc,
        "result": p0rpa_result,
        "reason": p0rpa_reason,
        "note": "P0 repair plan API smoke: read-only preview, path-input reject, update disabled, auth behavior",
        "log_tail": p0_repair_plan_api_log[-2000:],
    }

    p0_reparse_cache_rc, p0_reparse_cache_log = _run_step("p0-reparse-cache", [
        "scripts/smoke_p0_reparse_cache.py",
    ])
    p0_reparse_cache_report = _read_json(REPORTS_DIR / "p0_reparse_cache_smoke_result.json")
    p0rc_result, p0rc_reason = _classify_p0_reparse_cache(p0_reparse_cache_report)
    suite["steps"]["p0_reparse_cache"] = {
        "return_code": p0_reparse_cache_rc,
        "result": p0rc_result,
        "reason": p0rc_reason,
        "note": "P0 reparse cache dry-run: read-only cached HTML comparison only",
        "log_tail": p0_reparse_cache_log[-2000:],
    }

    p0_cache_coverage_rc, p0_cache_coverage_log = _run_step("p0-cache-coverage-diagnosis", [
        "scripts/smoke_p0_cache_coverage_diagnosis.py",
    ])
    p0_cache_coverage_report = _read_json(REPORTS_DIR / "p0_cache_coverage_diagnosis_smoke_result.json")
    p0ccd_result, p0ccd_reason = _classify_p0_cache_coverage_diagnosis(p0_cache_coverage_report)
    suite["steps"]["p0_cache_coverage_diagnosis"] = {
        "return_code": p0_cache_coverage_rc,
        "result": p0ccd_result,
        "reason": p0ccd_reason,
        "note": "P0 cache coverage diagnosis: read-only cache presence and sampling-scope check",
        "log_tail": p0_cache_coverage_log[-2000:],
    }

    p0_targeted_refetch_rc, p0_targeted_refetch_log = _run_step("p0-targeted-refetch-plan", [
        "scripts/smoke_p0_targeted_refetch_plan.py",
    ])
    p0_targeted_refetch_report = _read_json(REPORTS_DIR / "p0_targeted_refetch_plan_smoke_result.json")
    p0tr_result, p0tr_reason = _classify_p0_targeted_refetch_plan(p0_targeted_refetch_report)
    suite["steps"]["p0_targeted_refetch_plan"] = {
        "return_code": p0_targeted_refetch_rc,
        "result": p0tr_result,
        "reason": p0tr_reason,
        "note": "P0 targeted refetch dry-run: read-only URL enumeration only",
        "log_tail": p0_targeted_refetch_log[-2000:],
    }

    p0_targeted_refetch_live_rc, p0_targeted_refetch_live_log = _run_step("p0-targeted-refetch-live-validation", [
        "scripts/smoke_p0_targeted_refetch_live_validation.py",
    ])
    p0_targeted_refetch_live_report = _read_json(REPORTS_DIR / "p0_targeted_refetch_live_validation_smoke_result.json")
    p0trlv_result, p0trlv_reason = _classify_p0_targeted_refetch_live_validation(p0_targeted_refetch_live_report)
    suite["steps"]["p0_targeted_refetch_live_validation"] = {
        "return_code": p0_targeted_refetch_live_rc,
        "result": p0trlv_result,
        "reason": p0trlv_reason,
        "note": "P0 targeted refetch small live validation: capped URL fetch validation with no DB writes",
        "log_tail": p0_targeted_refetch_live_log[-2000:],
    }

    source_empty_diag_rc, source_empty_diag_log = _run_step("source-empty-result-cells-diagnosis", [
        "scripts/smoke_source_empty_result_cells_diagnosis.py",
    ])
    source_empty_diag_report = _read_json(REPORTS_DIR / "source_empty_result_cells_diagnosis_smoke_result.json")
    sed_result, sed_reason = _classify_source_empty_result_cells_diagnosis(source_empty_diag_report)
    suite["steps"]["source_empty_result_cells_diagnosis"] = {
        "return_code": source_empty_diag_rc,
        "result": sed_result,
        "reason": sed_reason,
        "note": "Source-empty result cells diagnosis: read-only cache-based domain/source classification",
        "log_tail": source_empty_diag_log[-2000:],
    }

    missingness_rc, missingness_log = _run_step("scrape-missingness-audit", [
        "scripts/smoke_scrape_missingness_audit.py",
    ])
    missingness_report = _read_json(REPORTS_DIR / "scrape_missingness_audit_smoke_result.json")
    ms_verdict = str(missingness_report.get("verdict") or "") if isinstance(missingness_report, dict) else ""
    ms_success = bool(missingness_report.get("success")) if isinstance(missingness_report, dict) else False
    ms_result = "pass" if missingness_rc == 0 and ms_success and ms_verdict == "pass" else "fail"
    ms_reason = "ok" if ms_result == "pass" else "scrape-missingness-audit-smoke-failed"
    suite["steps"]["scrape_missingness_audit"] = {
        "return_code": missingness_rc,
        "result": ms_result,
        "reason": ms_reason,
        "note": "Read-only missingness fixture smoke: good data pass, required missing fail",
        "log_tail": missingness_log[-2000:],
    }

    refresh_policy_rc, refresh_policy_log = _run_step("scrape-refresh-policy", [
        "scripts/smoke_scrape_refresh_policy.py",
    ])
    refresh_policy_report = _read_json(REPORTS_DIR / "scrape_refresh_policy_smoke_result.json")
    rp_verdict = str(refresh_policy_report.get("verdict") or "") if isinstance(refresh_policy_report, dict) else ""
    rp_success = bool(refresh_policy_report.get("success")) if isinstance(refresh_policy_report, dict) else False
    rp_result = "pass" if refresh_policy_rc == 0 and rp_success and rp_verdict == "pass" else "fail"
    rp_reason = "ok" if rp_result == "pass" else "scrape-refresh-policy-smoke-failed"
    suite["steps"]["scrape_refresh_policy"] = {
        "return_code": refresh_policy_rc,
        "result": rp_result,
        "reason": rp_reason,
        "note": "Refresh safeguards smoke: skip/repair/reparse/no-downgrade and dry-run no-write",
        "log_tail": refresh_policy_log[-2000:],
    }

    p0_repair_plan_rc, p0_repair_plan_log = _run_step("p0-scrape-repair-plan", [
        "scripts/smoke_p0_scrape_repair_plan.py",
    ])
    p0_repair_plan_report = _read_json(REPORTS_DIR / "p0_scrape_repair_plan_smoke_result.json")
    p0_verdict = str(p0_repair_plan_report.get("verdict") or "") if isinstance(p0_repair_plan_report, dict) else ""
    p0_success = bool(p0_repair_plan_report.get("success")) if isinstance(p0_repair_plan_report, dict) else False
    p0_result = "pass" if p0_repair_plan_rc == 0 and p0_success and p0_verdict == "pass" else "fail"
    p0_reason = "ok" if p0_result == "pass" else "p0-scrape-repair-plan-smoke-failed"
    suite["steps"]["p0_scrape_repair_plan"] = {
        "return_code": p0_repair_plan_rc,
        "result": p0_result,
        "reason": p0_reason,
        "note": "P0-only read-only repair planning smoke: reason/action breakdown and refetch dedup",
        "log_tail": p0_repair_plan_log[-2000:],
    }

    fetch_pipeline_rc, fetch_pipeline_log = _run_step("fetch-pipeline", [
        "scripts/smoke_fetch_pipeline.py",
    ])
    fetch_pipeline_report = _read_json(REPORTS_DIR / "fetch_pipeline_smoke_result.json")
    fp_verdict = str(fetch_pipeline_report.get("verdict") or "") if isinstance(fetch_pipeline_report, dict) else ""
    fp_result = "pass" if fp_verdict == "pass" and fetch_pipeline_rc == 0 else "fail"
    fp_reason = "ok" if fp_result == "pass" else "fetch-pipeline-smoke-failed"
    suite["steps"]["fetch_pipeline"] = {
        "return_code": fetch_pipeline_rc,
        "result": fp_result,
        "reason": fp_reason,
        "note": "Stub/mock smoke: cache hit no-fetch, URL dedup collapse, retry-after backoff, dry-run no-access",
        "log_tail": fetch_pipeline_log[-2000:],
    }

    if args.verify_write_guard_enabled:
        write_guard_enabled_rc, write_guard_enabled_log = _run_step("write-guard-enabled", [
            "scripts/smoke_netkeiba_race_write_guard.py",
            "--expect-enabled",
            "--race-id", args.race_id,
            "--date", args.date,
            "--fastapi-url", args.fastapi_url,
            *token_args,
        ])
        write_guard_enabled_report = _read_json(REPORTS_DIR / "netkeiba_race_write_guard_enabled_smoke_result.json")
        wge_result, wge_reason = _classify_write_guard(write_guard_enabled_report, token_provided=token_provided)
        suite["steps"]["race_write_guard_enabled"] = {
            "return_code": write_guard_enabled_rc,
            "result": wge_result,
            "reason": wge_reason,
            "note": "Write guard enabled: all safety branches must keep write_performed=false",
            "log_tail": write_guard_enabled_log[-2000:],
        }

    if args.verify_write_guard_flag_only:
        write_guard_flag_only_rc, write_guard_flag_only_log = _run_step("write-guard-flag-only", [
            "scripts/smoke_netkeiba_race_write_guard.py",
            "--expect-flag-only",
            "--race-id", args.race_id,
            "--date", args.date,
            "--fastapi-url", args.fastapi_url,
            *token_args,
        ])
        write_guard_flag_only_report = _read_json(REPORTS_DIR / "netkeiba_race_write_guard_flag_only_smoke_result.json")
        wgfo_result, wgfo_reason = _classify_write_guard(write_guard_flag_only_report, token_provided=token_provided)
        suite["steps"]["race_write_guard_flag_only"] = {
            "return_code": write_guard_flag_only_rc,
            "result": wgfo_result,
            "reason": wgfo_reason,
            "note": "Write guard flag-only: NETKEIBA_RACE_WRITE_ENABLED only must stay blocked",
            "log_tail": write_guard_flag_only_log[-2000:],
        }

    if args.verify_write_guard_sandbox_precheck:
        write_guard_sandbox_precheck_rc, write_guard_sandbox_precheck_log = _run_step("write-guard-sandbox-precheck", [
            "scripts/smoke_netkeiba_race_write_guard.py",
            "--expect-sandbox-precheck",
            "--race-id", args.race_id,
            "--date", args.date,
            "--fastapi-url", args.fastapi_url,
            *token_args,
        ])
        write_guard_sandbox_precheck_report = _read_json(REPORTS_DIR / "netkeiba_race_write_guard_sandbox_precheck_smoke_result.json")
        wgsp_result, wgsp_reason = _classify_write_guard(write_guard_sandbox_precheck_report, token_provided=token_provided)
        suite["steps"]["race_write_guard_sandbox_precheck"] = {
            "return_code": write_guard_sandbox_precheck_rc,
            "result": wgsp_result,
            "reason": wgsp_reason,
            "note": "Read-only sandbox precheck smoke (run only with explicit opt-in)",
            "log_tail": write_guard_sandbox_precheck_log[-2000:],
        }

    if args.verify_write_guard_sandbox_write:
        write_guard_sandbox_rc, write_guard_sandbox_log = _run_step("write-guard-sandbox-write", [
            "scripts/smoke_netkeiba_race_write_guard.py",
            "--expect-sandbox-write",
            "--race-id", args.race_id,
            "--date", args.date,
            "--fastapi-url", args.fastapi_url,
            *token_args,
        ])
        write_guard_sandbox_report = _read_json(REPORTS_DIR / "netkeiba_race_write_guard_sandbox_write_smoke_result.json")
        wgs_result, wgs_reason = _classify_write_guard(write_guard_sandbox_report, token_provided=token_provided)
        suite["steps"]["race_write_guard_sandbox_write"] = {
            "return_code": write_guard_sandbox_rc,
            "result": wgs_result,
            "reason": wgs_reason,
            "note": "Explicit sandbox write smoke (run only with explicit opt-in)",
            "log_tail": write_guard_sandbox_log[-2000:],
        }

    if args.verify_write_guard_sandbox_write_readback:
        write_guard_sandbox_rb_rc, write_guard_sandbox_rb_log = _run_step("write-guard-sandbox-write-readback", [
            "scripts/smoke_netkeiba_race_write_guard.py",
            "--expect-sandbox-write-readback",
            "--race-id", args.race_id,
            "--date", args.date,
            "--fastapi-url", args.fastapi_url,
            *token_args,
        ])
        write_guard_sandbox_rb_report = _read_json(REPORTS_DIR / "netkeiba_race_write_guard_sandbox_write_readback_smoke_result.json")
        wgsrb_result, wgsrb_reason = _classify_write_guard(write_guard_sandbox_rb_report, token_provided=token_provided)
        suite["steps"]["race_write_guard_sandbox_write_readback"] = {
            "return_code": write_guard_sandbox_rb_rc,
            "result": wgsrb_result,
            "reason": wgsrb_reason,
            "note": "Explicit sandbox write + readback smoke (run only with explicit opt-in)",
            "log_tail": write_guard_sandbox_rb_log[-2000:],
        }

    if args.verify_write_guard_production_block:
        write_guard_prod_rc, write_guard_prod_log = _run_step("write-guard-production", [
            "scripts/smoke_netkeiba_race_write_guard.py",
            "--expect-production-block",
            "--race-id", args.race_id,
            "--date", args.date,
            "--fastapi-url", args.fastapi_url,
            *token_args,
        ])
        write_guard_prod_report = _read_json(REPORTS_DIR / "netkeiba_race_write_guard_production_smoke_result.json")
        wgp_result, wgp_reason = _classify_write_guard(write_guard_prod_report, token_provided=token_provided)
        suite["steps"]["race_write_guard_production"] = {
            "return_code": write_guard_prod_rc,
            "result": wgp_result,
            "reason": wgp_reason,
            "note": "Write guard production lock: APP_ENV=production must always block writes",
            "log_tail": write_guard_prod_log[-2000:],
        }

    if args.verify_write_guard_staging_lock_missing:
        write_guard_staging_lock_rc, write_guard_staging_lock_log = _run_step("write-guard-staging-lock", [
            "scripts/smoke_netkeiba_race_write_guard.py",
            "--expect-staging-lock-missing",
            "--race-id", args.race_id,
            "--date", args.date,
            "--fastapi-url", args.fastapi_url,
            *token_args,
        ])
        write_guard_staging_lock_report = _read_json(REPORTS_DIR / "netkeiba_race_write_guard_staging_lock_smoke_result.json")
        wgsl_result, wgsl_reason = _classify_write_guard(write_guard_staging_lock_report, token_provided=token_provided)
        suite["steps"]["race_write_guard_staging_lock"] = {
            "return_code": write_guard_staging_lock_rc,
            "result": wgsl_result,
            "reason": wgsl_reason,
            "note": "Write guard staging lock: ALLOW_STAGING_WRITE must block when false",
            "log_tail": write_guard_staging_lock_log[-2000:],
        }

    results = [str(v.get("result")) for v in suite["steps"].values()]
    has_fail = any(r == "fail" for r in results)
    has_warn = any(r == "warn" for r in results)
    suite["success"] = not has_fail
    suite["summary"] = "fail" if has_fail else ("warn" if has_warn else "pass")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_file = REPORTS_DIR / "keiba_smoke_suite_result.json"
    out_file.write_text(json.dumps(suite, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "result_file": str(out_file),
        "success": suite["success"],
        "summary": suite["summary"],
        "steps": {k: v.get("result") for k, v in suite["steps"].items()},
    }, ensure_ascii=False))

    return 0 if suite["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
