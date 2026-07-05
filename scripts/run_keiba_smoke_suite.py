from __future__ import annotations

import argparse
import json
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


def _classify_preflight(report: dict[str, Any] | None) -> tuple[str, str]:
    if not isinstance(report, dict):
        return "fail", "preflight report missing or invalid"

    verdict = str(report.get("verdict") or "")
    reason = str(report.get("verdict_reason") or "")

    if verdict == "pass":
        return "pass", reason or "ready"
    if verdict == "warn":
        return "warn", reason or "non-ready"
    return "fail", reason or "contract-error"


def _classify_dry_run(report: dict[str, Any] | None) -> tuple[str, str]:
    if not isinstance(report, dict):
        return "fail", "dry-run report missing or invalid"

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

    if verdict == "pass":
        return "pass", reason or "contracts-compatible"
    if verdict == "warn":
        return "warn", reason or "contract-diff-detected"
    return "fail", reason or "contract-error"


def _classify_write_guard(report: dict[str, Any] | None) -> tuple[str, str]:
    if not isinstance(report, dict):
        return "fail", "write-guard report missing or invalid"

    verdict = str(report.get("verdict") or "")
    reason = str(report.get("verdict_reason") or "")

    if verdict == "pass":
        return "pass", reason or "write-disabled-default"
    if verdict == "warn":
        return "warn", reason or "guarded"
    return "fail", reason or "contract-error"


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
        "--verify-write-guard-sandbox-write",
        action="store_true",
        help="Run explicit sandbox write smoke (disabled by default)",
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

    token_args: list[str] = []
    if args.auth_token.strip():
        token_args = ["--auth-token", args.auth_token.strip()]

    suite: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "mode": "strict-preflight" if args.strict_preflight else "contract-only-preflight",
        "steps": {},
    }

    analyze_rc, analyze_log = _run_step("analyze-race", [
        "scripts/smoke_analyze_race_api.py",
    ])
    suite["steps"]["analyze_race"] = {
        "return_code": analyze_rc,
        "result": "pass" if analyze_rc == 0 else "fail",
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
    suite["steps"]["race_list_proxy"] = {
        "return_code": race_list_rc,
        "result": "pass" if race_list_rc == 0 else "fail",
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
    pf_result, pf_reason = _classify_preflight(preflight_report)
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
    dr_result, dr_reason = _classify_dry_run(dry_run_report)
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
    wg_result, wg_reason = _classify_write_guard(write_guard_report)
    suite["steps"]["race_write_guard"] = {
        "return_code": write_guard_rc,
        "result": wg_result,
        "reason": wg_reason,
        "note": "Write guard: default disabled is pass, contract-error is fail",
        "log_tail": write_guard_log[-2000:],
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
        wge_result, wge_reason = _classify_write_guard(write_guard_enabled_report)
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
        wgfo_result, wgfo_reason = _classify_write_guard(write_guard_flag_only_report)
        suite["steps"]["race_write_guard_flag_only"] = {
            "return_code": write_guard_flag_only_rc,
            "result": wgfo_result,
            "reason": wgfo_reason,
            "note": "Write guard flag-only: NETKEIBA_RACE_WRITE_ENABLED only must stay blocked",
            "log_tail": write_guard_flag_only_log[-2000:],
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
        wgs_result, wgs_reason = _classify_write_guard(write_guard_sandbox_report)
        suite["steps"]["race_write_guard_sandbox_write"] = {
            "return_code": write_guard_sandbox_rc,
            "result": wgs_result,
            "reason": wgs_reason,
            "note": "Explicit sandbox write smoke (run only with explicit opt-in)",
            "log_tail": write_guard_sandbox_log[-2000:],
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
        wgp_result, wgp_reason = _classify_write_guard(write_guard_prod_report)
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
        wgsl_result, wgsl_reason = _classify_write_guard(write_guard_staging_lock_report)
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
