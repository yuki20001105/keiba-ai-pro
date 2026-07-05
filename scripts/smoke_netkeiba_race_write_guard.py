from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, request


VALID_STATUSES = {"disabled", "blocked", "guarded-noop", "invalid"}


def _http_json(url: str, payload: dict[str, Any], token: str | None = None) -> tuple[int, dict[str, Any] | None, str | None]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = request.Request(url=url, method="POST", headers=headers, data=json.dumps(payload).encode("utf-8"))
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


def _is_contract_ok(status: int, body: dict[str, Any] | None) -> bool:
    if status not in (200, 400, 401, 403, 503):
        return False
    if not isinstance(body, dict):
        return False

    required = ["service", "status", "write_performed", "reason"]
    if not all(k in body for k in required):
        return False

    if body.get("service") != "netkeiba-race-write":
        return False

    if body.get("write_performed") is not False:
        return False

    st = body.get("status")
    if not isinstance(st, str) or st not in VALID_STATUSES:
        return False

    return True


def _classify(contract_ok: bool, body: dict[str, Any] | None) -> tuple[str, str]:
    if not contract_ok:
        return "fail", "contract-error"

    st = str(body.get("status") or "") if isinstance(body, dict) else ""
    if st == "disabled":
        return "pass", "write-disabled-default"
    if st in {"blocked", "guarded-noop", "invalid"}:
        return "warn", st
    return "fail", "invalid-status"


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test for netkeiba race write guard contract")
    parser.add_argument("--race-id", default="202406010101", help="12-digit race id")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"), help="YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--fastapi-url", default="http://127.0.0.1:8000", help="FastAPI base URL")
    parser.add_argument("--auth-token", default="", help="Optional Bearer token")
    args = parser.parse_args()

    endpoint = f"{args.fastapi_url}/api/netkeiba/race/write"
    payload = {
        "race_id": args.race_id,
        "date": args.date,
        "confirm_write": True,
        "dry_run": False,
        "user_id": "guard-smoke-user",
    }

    token = args.auth_token.strip() or None
    status, body, err = _http_json(endpoint, payload=payload, token=token)
    contract_ok = _is_contract_ok(status, body)
    verdict, reason = _classify(contract_ok, body)
    success = verdict != "fail"

    result = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "success": success,
        "contract_ok": contract_ok,
        "verdict": verdict,
        "verdict_reason": reason,
        "check": {
            "url": endpoint,
            "request": payload,
            "status": status,
            "ok": contract_ok,
            "error": err,
            "body": body,
        },
    }

    reports_dir = Path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_file = reports_dir / "netkeiba_race_write_guard_smoke_result.json"
    out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "result_file": str(out_file),
        "success": success,
        "verdict": verdict,
        "verdict_reason": reason,
        "status": status,
    }, ensure_ascii=False))

    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
