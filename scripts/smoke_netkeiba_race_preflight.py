from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, request


VALID_STATUSES = {"ready", "degraded", "unavailable"}


def _http_json(url: str, token: str | None = None) -> tuple[int, dict[str, Any] | None, str | None]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = request.Request(url=url, method="GET", headers=headers)
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


def _is_contract_ok(status: int, obj: dict[str, Any] | None) -> bool:
    if status not in (200, 400, 401, 403, 503):
        return False

    payload = obj
    if isinstance(obj, dict) and isinstance(obj.get("detail"), dict):
        payload = obj["detail"]

    if not isinstance(payload, dict):
        return False

    required = ["service", "race_id", "can_scrape", "can_write", "write_performed"]
    if not all(k in payload for k in required):
        return False

    if payload.get("service") != "netkeiba-race":
        return False

    if payload.get("can_write") is not False or payload.get("write_performed") is not False:
        return False

    st = payload.get("status")
    if st is not None and st not in VALID_STATUSES:
        return False

    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test for netkeiba race preflight contract")
    parser.add_argument("--race-id", default="202406010101", help="12-digit race id")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"), help="YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--fastapi-url", default="http://127.0.0.1:8000", help="FastAPI base URL")
    parser.add_argument("--auth-token", default="", help="Optional Bearer token")
    args = parser.parse_args()

    token = args.auth_token.strip() or None
    endpoint = f"{args.fastapi_url}/api/netkeiba/race/preflight?race_id={args.race_id}&date={args.date}"

    status, payload, err = _http_json(endpoint, token=token)
    ok = _is_contract_ok(status, payload)

    result = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "success": ok,
        "check": {
            "url": endpoint,
            "status": status,
            "ok": ok,
            "error": err,
            "body": payload,
        },
    }

    reports_dir = Path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_file = reports_dir / "netkeiba_race_preflight_smoke_result.json"
    out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "result_file": str(out_file),
        "success": ok,
        "status": status,
    }, ensure_ascii=False))

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
