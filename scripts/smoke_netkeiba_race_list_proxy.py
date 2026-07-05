from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, request


def _http_json(url: str, method: str = "GET", payload: dict[str, Any] | None = None, token: str | None = None) -> tuple[int, dict[str, Any] | None, str | None]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    req = request.Request(url=url, method=method, headers=headers, data=data)
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


def _check_shape(obj: dict[str, Any] | None) -> bool:
    if not isinstance(obj, dict):
        return False
    if "raceIds" in obj and "count" in obj:
        return True
    if "races" in obj and isinstance(obj.get("races"), list):
        return True
    return False


def _check_clear_error_contract(status: int, obj: dict[str, Any] | None) -> bool:
    if status not in (502, 503):
        return False
    if not isinstance(obj, dict):
        return False
    # Accept either { error: ... } or { success: false, error: ... }
    if isinstance(obj.get("error"), str) and obj.get("error"):
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test for netkeiba race-list proxy path")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"), help="YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--fastapi-url", default="http://127.0.0.1:8000", help="FastAPI base URL")
    parser.add_argument("--next-url", default="http://localhost:3000", help="Next.js base URL")
    parser.add_argument("--auth-token", default="", help="Optional Bearer token")
    args = parser.parse_args()

    date_compact = args.date.replace("-", "")
    token = args.auth_token.strip() or None

    fastapi_endpoint = f"{args.fastapi_url}/api/netkeiba/race-list?date={date_compact}"
    next_endpoint = f"{args.next_url}/api/netkeiba/race-list"

    fa_status, fa_json, fa_err = _http_json(fastapi_endpoint, method="GET", token=token)
    nx_status, nx_json, nx_err = _http_json(next_endpoint, method="POST", payload={"date": date_compact}, token=token)

    fa_ok = (fa_status == 200 and _check_shape(fa_json)) or _check_clear_error_contract(fa_status, fa_json)
    nx_ok = (nx_status == 200 and _check_shape(nx_json)) or _check_clear_error_contract(nx_status, nx_json)

    result = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "date": date_compact,
        "success": False,
        "checks": {
            "fastapi": {
                "url": fastapi_endpoint,
                "status": fa_status,
                "ok": fa_ok,
                "error": fa_err,
                "body": fa_json,
            },
            "next": {
                "url": next_endpoint,
                "status": nx_status,
                "ok": nx_ok,
                "error": nx_err,
                "body": nx_json,
            },
        },
    }

    result["success"] = bool(result["checks"]["fastapi"]["ok"] and result["checks"]["next"]["ok"])

    reports_dir = Path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_file = reports_dir / "netkeiba_race_list_proxy_smoke_result.json"
    out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "result_file": str(out_file),
        "success": result["success"],
        "fastapi_status": fa_status,
        "next_status": nx_status,
    }, ensure_ascii=False))

    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
