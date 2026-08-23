from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, request


VALID_STATUSES = {"ready", "degraded", "unavailable", "invalid"}


def _http_json(url: str, payload: dict[str, Any], token: str | None = None) -> tuple[int, dict[str, Any] | None, str | None]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url=url, method="POST", headers=headers, data=data)
    try:
        with request.urlopen(req, timeout=20) as resp:
            status = resp.getcode()
            body = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(body) if body else {}
            return status, parsed, None
    except error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        parsed = None
        try:
            parsed = json.loads(body) if body else {}
        except Exception:
            parsed = {"raw": body}
        return e.code, parsed, str(e)
    except Exception as e:
        return 0, None, str(e)


def _extract_payload(obj: dict[str, Any] | None) -> dict[str, Any] | None:
    if isinstance(obj, dict) and isinstance(obj.get("detail"), dict):
        return obj["detail"]
    if isinstance(obj, dict):
        return obj
    return None


def _is_contract_ok(status: int, obj: dict[str, Any] | None) -> bool:
    if status not in (200, 400, 401, 403, 503):
        return False

    payload = _extract_payload(obj)
    if not isinstance(payload, dict):
        return False

    required = ["service", "race_id", "can_scrape", "can_write", "write_performed", "dry_run", "status"]
    if not all(k in payload for k in required):
        return False

    if payload.get("service") != "netkeiba-race":
        return False

    if payload.get("dry_run") is not True:
        return False

    if payload.get("can_write") is not False or payload.get("write_performed") is not False:
        return False

    st = payload.get("status")
    if not isinstance(st, str) or st not in VALID_STATUSES:
        return False

    if st == "ready":
        preview = payload.get("preview")
        if not isinstance(preview, dict):
            return False
        tables = preview.get("tables")
        if not isinstance(tables, list):
            return False

    return True


def _classify_verdict(contract_ok: bool, dry_run_status: str | None, fail_on_nonready: bool) -> tuple[str, str]:
    if not contract_ok:
        return "fail", "contract-error"

    if dry_run_status == "ready":
        return "pass", "ready"

    if dry_run_status in {"degraded", "unavailable", "invalid"}:
        if fail_on_nonready:
            return "fail", f"nonready-{dry_run_status}"
        return "warn", str(dry_run_status)

    return "fail", "invalid-status"


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test for netkeiba race dry-run contract")
    parser.add_argument("--race-id", default="202406010101", help="12-digit race id")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"), help="YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--user-id", default="dry-run-user", help="Optional user id for preview payload building")
    parser.add_argument("--fastapi-url", default="http://127.0.0.1:8000", help="FastAPI base URL")
    parser.add_argument("--auth-token", default="", help="Optional Bearer token")
    parser.add_argument(
        "--fail-on-nonready",
        action="store_true",
        help="Treat degraded/unavailable/invalid as fail (default: warn and pass for CI contract mode)",
    )
    args = parser.parse_args()

    token = args.auth_token.strip() or None
    endpoint = f"{args.fastapi_url}/api/netkeiba/race/dry-run"
    body = {"race_id": args.race_id, "date": args.date, "user_id": args.user_id}

    status, payload, err = _http_json(endpoint, payload=body, token=token)
    contract_ok = _is_contract_ok(status, payload)
    view = _extract_payload(payload)
    dry_run_status = view.get("status") if isinstance(view, dict) else None
    verdict, reason = _classify_verdict(contract_ok, dry_run_status, args.fail_on_nonready)
    ok = verdict != "fail"

    result = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "mode": "strict" if args.fail_on_nonready else "contract-only",
        "success": ok,
        "contract_ok": contract_ok,
        "verdict": verdict,
        "verdict_reason": reason,
        "dry_run_status": dry_run_status,
        "check": {
            "url": endpoint,
            "request": body,
            "status": status,
            "ok": contract_ok,
            "error": err,
            "body": payload,
        },
    }

    reports_dir = Path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_file = reports_dir / "netkeiba_race_dry_run_smoke_result.json"
    out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "result_file": str(out_file),
        "success": ok,
        "contract_ok": contract_ok,
        "verdict": verdict,
        "verdict_reason": reason,
        "dry_run_status": dry_run_status,
        "status": status,
    }, ensure_ascii=False))

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
