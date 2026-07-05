from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, request


VALID_STATUSES = {"disabled", "blocked", "guarded-noop", "guarded-stub", "invalid"}


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
    if st == "guarded-stub":
        return "pass", "staging-guarded-stub"
    if st in {"blocked", "guarded-noop", "invalid"}:
        return "warn", st
    return "fail", "invalid-status"


class _StubScrapeHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/scrape/ultimate":
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"success":false,"error":"not found"}')
            return

        content_len = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(content_len) if content_len > 0 else b"{}"
        try:
            req = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            req = {}

        race_id = str(req.get("race_id") or "202406010101")
        resp = {
            "success": True,
            "race_info": {
                "race_name": "stub race",
                "venue": "stub",
                "distance": 1600,
                "track_type": "芝",
                "weather": "晴",
                "field_condition": "良",
            },
            "results": [
                {
                    "finish_position": 1,
                    "bracket_number": 1,
                    "horse_number": 1,
                    "horse_name": f"stub-{race_id}",
                    "sex_age": "牡3",
                    "jockey_weight": 55.0,
                    "jockey_name": "stub jockey",
                    "finish_time": "1:34.5",
                    "odds": 2.5,
                    "popularity": 1,
                }
            ],
            "payouts": [
                {"type": "単勝", "numbers": "1", "amount": "250円"}
            ],
        }
        payload = json.dumps(resp, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return


def _start_stub_scrape(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), _StubScrapeHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


def _run_check(endpoint: str, payload: dict[str, Any], token: str | None) -> dict[str, Any]:
    status, body, err = _http_json(endpoint, payload=payload, token=token)
    contract_ok = _is_contract_ok(status, body)
    return {
        "status": status,
        "body": body,
        "error": err,
        "contract_ok": contract_ok,
        "write_performed": bool(body.get("write_performed")) if isinstance(body, dict) else None,
        "response_status": str(body.get("status") or "") if isinstance(body, dict) else "",
        "can_write": bool(body.get("can_write")) if isinstance(body, dict) else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test for netkeiba race write guard contract")
    parser.add_argument("--race-id", default="202406010101", help="12-digit race id")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"), help="YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--fastapi-url", default="http://127.0.0.1:8000", help="FastAPI base URL")
    parser.add_argument("--auth-token", default="", help="Optional Bearer token")
    parser.add_argument("--expect-enabled", action="store_true", help="Run enabled-guard matrix checks")
    parser.add_argument("--expect-production-block", action="store_true", help="Expect APP_ENV=production hard block branch")
    parser.add_argument("--expect-staging-lock-missing", action="store_true", help="Expect ALLOW_STAGING_WRITE=false block branch")
    parser.add_argument("--stub-scrape-port", type=int, default=8001, help="Port for local stub scrape service in enabled checks")
    args = parser.parse_args()

    endpoint = f"{args.fastapi_url}/api/netkeiba/race/write"
    token = args.auth_token.strip() or None

    if not args.expect_enabled and not args.expect_production_block and not args.expect_staging_lock_missing:
        payload = {
            "race_id": args.race_id,
            "date": args.date,
            "confirm_write": True,
            "dry_run": False,
            "user_id": "guard-smoke-user",
        }
        status, body, err = _http_json(endpoint, payload=payload, token=token)
        contract_ok = _is_contract_ok(status, body)
        verdict, reason = _classify(contract_ok, body)
        success = verdict != "fail"

        result = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "mode": "default-disabled",
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
        out_name = "netkeiba_race_write_guard_smoke_result.json"
    elif args.expect_production_block:
        payload = {
            "race_id": args.race_id,
            "date": args.date,
            "confirm_write": True,
            "dry_run": False,
            "payload_contract_approved": True,
            "user_id": "guard-smoke-user",
        }
        run = _run_check(endpoint, payload, token)
        body = run.get("body") if isinstance(run, dict) else None
        reason_text = str(body.get("reason") or "") if isinstance(body, dict) else ""
        status_ok = run.get("response_status") == "blocked"
        reason_ok = "production write is forbidden" in reason_text
        success = bool(run.get("contract_ok")) and status_ok and reason_ok and run.get("write_performed") is False

        result = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "mode": "expect-production-block",
            "success": success,
            "verdict": "pass" if success else "fail",
            "verdict_reason": "production-write-blocked" if success else "production-block-check-failed",
            "check": {
                "url": endpoint,
                "request": payload,
                "status": run.get("status"),
                "response_status": run.get("response_status"),
                "contract_ok": run.get("contract_ok"),
                "write_performed": run.get("write_performed"),
                "reason": reason_text,
                "error": run.get("error"),
                "body": body,
            },
        }
        out_name = "netkeiba_race_write_guard_production_smoke_result.json"
    elif args.expect_staging_lock_missing:
        payload = {
            "race_id": args.race_id,
            "date": args.date,
            "confirm_write": True,
            "dry_run": False,
            "payload_contract_approved": True,
            "user_id": "guard-smoke-user",
        }
        run = _run_check(endpoint, payload, token)
        body = run.get("body") if isinstance(run, dict) else None
        reason_text = str(body.get("reason") or "") if isinstance(body, dict) else ""
        status_ok = run.get("response_status") == "blocked"
        reason_ok = "ALLOW_STAGING_WRITE=true is required" in reason_text
        success = bool(run.get("contract_ok")) and status_ok and reason_ok and run.get("write_performed") is False

        result = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "mode": "expect-staging-lock-missing",
            "success": success,
            "verdict": "pass" if success else "fail",
            "verdict_reason": "staging-lock-missing-blocked" if success else "staging-lock-missing-check-failed",
            "check": {
                "url": endpoint,
                "request": payload,
                "status": run.get("status"),
                "response_status": run.get("response_status"),
                "contract_ok": run.get("contract_ok"),
                "write_performed": run.get("write_performed"),
                "reason": reason_text,
                "error": run.get("error"),
                "body": body,
            },
        }
        out_name = "netkeiba_race_write_guard_staging_lock_smoke_result.json"
    else:
        stub_server: ThreadingHTTPServer | None = None
        try:
            stub_server = _start_stub_scrape(args.stub_scrape_port)
        except Exception:
            stub_server = None

        cases = [
            {
                "name": "confirm-missing",
                "request": {
                    "race_id": args.race_id,
                    "date": args.date,
                    "confirm_write": False,
                    "dry_run": False,
                    "payload_contract_approved": True,
                    "user_id": "guard-smoke-user",
                },
                "expected_status": "blocked",
            },
            {
                "name": "dry-run-true",
                "request": {
                    "race_id": args.race_id,
                    "date": args.date,
                    "confirm_write": True,
                    "dry_run": True,
                    "payload_contract_approved": True,
                    "user_id": "guard-smoke-user",
                },
                "expected_status": "blocked",
            },
            {
                "name": "invalid-race-id",
                "request": {
                    "race_id": "abc",
                    "date": args.date,
                    "confirm_write": True,
                    "dry_run": False,
                    "payload_contract_approved": True,
                    "user_id": "guard-smoke-user",
                },
                "expected_status": "invalid",
            },
            {
                "name": "all-conditions-met",
                "request": {
                    "race_id": args.race_id,
                    "date": args.date,
                    "confirm_write": True,
                    "dry_run": False,
                    "payload_contract_approved": True,
                    "user_id": "guard-smoke-user",
                },
                "expected_status": "guarded-stub",
            },
        ]

        checks: list[dict[str, Any]] = []
        all_ok = True
        for c in cases:
            run = _run_check(endpoint, c["request"], token)
            status_ok = run["response_status"] == c["expected_status"]
            write_ok = run["write_performed"] is False
            contract_ok = bool(run["contract_ok"])
            case_ok = status_ok and write_ok and contract_ok
            all_ok = all_ok and case_ok
            checks.append(
                {
                    "name": c["name"],
                    "expected_status": c["expected_status"],
                    "actual_status": run["response_status"],
                    "write_performed": run["write_performed"],
                    "can_write": run["can_write"],
                    "contract_ok": run["contract_ok"],
                    "http_status": run["status"],
                    "error": run["error"],
                    "body": run["body"],
                    "ok": case_ok,
                }
            )

        guarded_case = next((c for c in checks if c["name"] == "all-conditions-met"), None)
        guarded_can_write = guarded_case.get("can_write") if isinstance(guarded_case, dict) else None
        guarded_flag_ok = guarded_can_write is True
        all_ok = all_ok and guarded_flag_ok

        result = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "mode": "expect-enabled",
            "success": all_ok,
            "verdict": "pass" if all_ok else "fail",
            "verdict_reason": "enabled-guard-verified" if all_ok else "enabled-guard-check-failed",
            "endpoint": endpoint,
            "stub_scrape": {
                "attempted_port": args.stub_scrape_port,
                "started": bool(stub_server is not None),
            },
            "checks": checks,
            "invariants": {
                "all_write_performed_false": all(c["write_performed"] is False for c in checks),
                "guarded_stub_can_write_true": guarded_flag_ok,
            },
        }
        out_name = "netkeiba_race_write_guard_enabled_smoke_result.json"

        if stub_server is not None:
            stub_server.shutdown()
            stub_server.server_close()

    reports_dir = Path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_file = reports_dir / out_name
    out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "result_file": str(out_file),
        "success": result.get("success"),
        "verdict": result.get("verdict"),
        "verdict_reason": result.get("verdict_reason"),
        "mode": result.get("mode"),
    }, ensure_ascii=False))

    return 0 if bool(result.get("success")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
