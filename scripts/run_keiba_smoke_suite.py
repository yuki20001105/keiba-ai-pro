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
