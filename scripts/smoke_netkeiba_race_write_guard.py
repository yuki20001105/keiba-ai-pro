from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, request


VALID_STATUSES = {"disabled", "blocked", "guarded-noop", "guarded-stub", "invalid", "stopped", "sandbox-written"}
_TABLE_WHITELIST = {"races", "race_results", "race_payouts"}
_ROW_LIMITS = {
    "races": 1,
    "race_results": 30,
    "race_payouts": 100,
}


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


def _http_get_json(url: str, token: str | None = None) -> tuple[int, dict[str, Any] | None, str | None]:
    headers: dict[str, str] = {}
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

    st = body.get("status")
    if not isinstance(st, str) or st not in VALID_STATUSES:
        return False

    write_performed = body.get("write_performed")
    if st == "sandbox-written":
        if write_performed is not True:
            return False
    else:
        if write_performed is not False:
            return False

    return True


def _classify(contract_ok: bool, body: dict[str, Any] | None) -> tuple[str, str]:
    if not contract_ok:
        return "fail", "contract-error"

    st = str(body.get("status") or "") if isinstance(body, dict) else ""
    if st == "disabled":
        return "pass", "write-disabled-default"
    if st == "sandbox-written":
        return "pass", "sandbox-write-performed"
    if st == "guarded-stub":
        return "pass", "staging-guarded-stub"
    if st == "stopped":
        return "warn", "sandbox-write-stopped"
    if st in {"blocked", "guarded-noop", "invalid"}:
        return "warn", st
    return "fail", "invalid-status"


def _is_sandbox_precheck_contract_ok(status: int, body: dict[str, Any] | None) -> bool:
    if status not in (200, 401, 403, 503):
        return False
    if not isinstance(body, dict):
        return False

    required = ["service", "status", "target_mode", "write_performed", "tables", "reason"]
    if not all(k in body for k in required):
        return False

    if body.get("service") != "netkeiba-race-sandbox-precheck":
        return False
    if body.get("target_mode") != "sandbox":
        return False
    if body.get("write_performed") is not False:
        return False

    st = str(body.get("status") or "")
    if st not in {"ready", "stopped", "warn", "unavailable"}:
        return False

    tables = body.get("tables")
    if not isinstance(tables, dict):
        return False

    expected = {
        "sandbox_netkeiba_races",
        "sandbox_netkeiba_race_results",
        "sandbox_netkeiba_race_payouts",
    }
    for name in expected:
        t = tables.get(name)
        if not isinstance(t, dict):
            return False
        for k in ["exists", "schema_compatible", "missing_columns", "type_mismatches", "row_limit_supported", "references_base_tables"]:
            if k not in t:
                return False
        if not isinstance(t.get("missing_columns"), list):
            return False
        if not isinstance(t.get("type_mismatches"), list):
            return False
        if not isinstance(t.get("references_base_tables"), list):
            return False

    return True


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
        too_many_results = race_id.endswith("9999")
        results_count = 31 if too_many_results else 1

        results = []
        for i in range(results_count):
            horse_no = i + 1
            results.append(
                {
                    "finish_position": horse_no,
                    "bracket_number": min(8, ((horse_no - 1) % 8) + 1),
                    "horse_number": horse_no,
                    "horse_name": f"stub-{race_id}-{horse_no}",
                    "sex_age": "牡3",
                    "jockey_weight": 55.0,
                    "jockey_name": "stub jockey",
                    "finish_time": "1:34.5",
                    "odds": 2.5,
                    "popularity": horse_no,
                }
            )

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
            "results": results,
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


def _is_writer_stub_contract_ok(body: dict[str, Any] | None) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not isinstance(body, dict):
        return False, ["body is not an object"]

    if body.get("status") != "guarded-stub":
        errors.append("status is not guarded-stub")

    if body.get("write_performed") is not False:
        errors.append("write_performed must be false")

    idempotency_key = str(body.get("idempotency_key") or "")
    if not idempotency_key.startswith("netkeiba_race:"):
        errors.append("idempotency_key prefix is invalid")

    payload_hash = str(body.get("payload_hash") or "")
    if len(payload_hash) != 64:
        errors.append("payload_hash length is invalid")

    dry_preview = body.get("dry_run_preview") if isinstance(body.get("dry_run_preview"), dict) else {}
    target_tables = dry_preview.get("target_tables") if isinstance(dry_preview.get("target_tables"), list) else []
    if not target_tables:
        errors.append("dry_run_preview.target_tables is missing")
    else:
        if any(t not in _TABLE_WHITELIST for t in target_tables):
            errors.append("target_tables includes non-whitelisted table")

    row_limits = dry_preview.get("row_limits") if isinstance(dry_preview.get("row_limits"), dict) else {}
    for table, limit in _ROW_LIMITS.items():
        if int(row_limits.get(table, -1)) != limit:
            errors.append(f"row_limits mismatch for {table}")

    writer_stub = body.get("writer_stub") if isinstance(body.get("writer_stub"), dict) else {}
    writer = writer_stub.get("writer") if isinstance(writer_stub.get("writer"), dict) else {}
    if writer.get("implementation") != "no-op":
        errors.append("writer implementation must be no-op")

    audit_preview = body.get("audit_payload_preview") if isinstance(body.get("audit_payload_preview"), dict) else {}
    required_audit_fields = {
        "race_id",
        "requested_at",
        "app_env",
        "dry_run",
        "confirm_write",
        "target_tables",
        "records_count",
        "payload_hash",
        "write_performed",
        "reason",
    }
    if not required_audit_fields.issubset(set(audit_preview.keys())):
        errors.append("audit_payload_preview missing required fields")

    return len(errors) == 0, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test for netkeiba race write guard contract")
    parser.add_argument("--race-id", default="202406010101", help="12-digit race id")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"), help="YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--fastapi-url", default="http://127.0.0.1:8000", help="FastAPI base URL")
    parser.add_argument("--auth-token", default="", help="Optional Bearer token")
    parser.add_argument("--expect-enabled", action="store_true", help="Run enabled-guard matrix checks")
    parser.add_argument("--expect-flag-only", action="store_true", help="Expect NETKEIBA_RACE_WRITE_ENABLED=true only branch to be blocked")
    parser.add_argument("--expect-sandbox-precheck", action="store_true", help="Run read-only sandbox precheck contract check")
    parser.add_argument("--expect-sandbox-write", action="store_true", help="Run explicit sandbox write check")
    parser.add_argument("--expect-production-block", action="store_true", help="Expect APP_ENV=production hard block branch")
    parser.add_argument("--expect-staging-lock-missing", action="store_true", help="Expect ALLOW_STAGING_WRITE=false block branch")
    parser.add_argument("--limit-test-race-id", default="202406019999", help="Race id used for row-limit exceeded case in enabled checks")
    parser.add_argument("--stub-scrape-port", type=int, default=8001, help="Port for local stub scrape service in enabled checks")
    args = parser.parse_args()

    mode_count = sum(1 for x in [args.expect_enabled, args.expect_flag_only, args.expect_sandbox_precheck, args.expect_sandbox_write, args.expect_production_block, args.expect_staging_lock_missing] if x)
    if mode_count > 1:
        print(json.dumps({
            "success": False,
            "error": "choose only one mode: --expect-enabled, --expect-flag-only, --expect-sandbox-precheck, --expect-sandbox-write, --expect-production-block, --expect-staging-lock-missing",
        }, ensure_ascii=False))
        return 2

    endpoint = f"{args.fastapi_url}/api/netkeiba/race/write"
    precheck_endpoint = f"{args.fastapi_url}/api/netkeiba/race/sandbox/precheck"
    token = args.auth_token.strip() or None

    if not args.expect_enabled and not args.expect_flag_only and not args.expect_sandbox_precheck and not args.expect_sandbox_write and not args.expect_production_block and not args.expect_staging_lock_missing:
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
    elif args.expect_sandbox_precheck:
        status, body, err = _http_get_json(precheck_endpoint, token=token)
        contract_ok = _is_sandbox_precheck_contract_ok(status, body)
        response_status = str(body.get("status") or "") if isinstance(body, dict) else ""

        verdict = "pass" if response_status == "ready" else ("warn" if response_status in {"stopped", "warn", "unavailable"} else "fail")
        if response_status == "ready":
            verdict_reason = "sandbox-precheck-ready"
        elif response_status == "stopped":
            verdict_reason = "sandbox-precheck-stopped"
        elif response_status == "warn":
            verdict_reason = "sandbox-precheck-warn"
        elif response_status == "unavailable":
            verdict_reason = "sandbox-precheck-unavailable"
        else:
            verdict_reason = "sandbox-precheck-check-failed"

        success = contract_ok and verdict != "fail"
        result = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "mode": "expect-sandbox-precheck",
            "success": success,
            "verdict": verdict,
            "verdict_reason": verdict_reason,
            "check": {
                "url": precheck_endpoint,
                "status": status,
                "response_status": response_status,
                "contract_ok": contract_ok,
                "write_performed": body.get("write_performed") if isinstance(body, dict) else None,
                "error": err,
                "body": body,
            },
        }
        out_name = "netkeiba_race_write_guard_sandbox_precheck_smoke_result.json"
    elif args.expect_sandbox_write:
        payload = {
            "race_id": args.race_id,
            "date": args.date,
            "confirm_write": True,
            "dry_run": False,
            "payload_contract_approved": True,
            "sandbox_write": True,
            "target_mode": "sandbox",
            "idempotency_key": f"smoke-{args.race_id}",
            "user_id": "guard-smoke-user",
        }
        run = _run_check(endpoint, payload, token)
        body = run.get("body") if isinstance(run, dict) else None
        response_status = str(run.get("response_status") or "")
        write_performed = run.get("write_performed")
        dry_run_status = str(body.get("dry_run_status") or "") if isinstance(body, dict) else ""

        sandbox_written_ok = response_status == "sandbox-written" and write_performed is True
        stopped_ok = response_status == "stopped" and write_performed is False
        precondition_unavailable_ok = response_status == "blocked" and dry_run_status == "unavailable" and write_performed is False
        status_ok = sandbox_written_ok or stopped_ok or precondition_unavailable_ok

        contract_ok = bool(run.get("contract_ok"))
        success = contract_ok and status_ok
        verdict = "pass" if sandbox_written_ok else ("warn" if (stopped_ok or precondition_unavailable_ok) else "fail")
        verdict_reason = (
            "sandbox-write-performed"
            if sandbox_written_ok
            else ("sandbox-write-stopped" if stopped_ok else ("sandbox-precondition-unavailable" if precondition_unavailable_ok else "sandbox-write-check-failed"))
        )

        contract_details = {
            "target_mode": str(body.get("target_mode") or "") if isinstance(body, dict) else "",
            "idempotency_key_present": bool(body.get("idempotency_key")) if isinstance(body, dict) else False,
            "audit_payload_present": isinstance(body.get("audit_payload") if isinstance(body, dict) else None, dict)
            or isinstance(body.get("audit_payload_preview") if isinstance(body, dict) else None, dict),
            "records_written_present": isinstance(body.get("records_written") if isinstance(body, dict) else None, dict),
        }

        result = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "mode": "expect-sandbox-write",
            "success": success,
            "verdict": verdict,
            "verdict_reason": verdict_reason,
            "check": {
                "url": endpoint,
                "request": payload,
                "status": run.get("status"),
                "response_status": response_status,
                "contract_ok": contract_ok,
                "write_performed": write_performed,
                "error": run.get("error"),
                "body": body,
            },
            "contract_details": contract_details,
        }
        out_name = "netkeiba_race_write_guard_sandbox_write_smoke_result.json"
    elif args.expect_flag_only:
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
            "mode": "expect-flag-only",
            "success": success,
            "verdict": "pass" if success else "fail",
            "verdict_reason": "flag-only-blocked" if success else "flag-only-check-failed",
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
        out_name = "netkeiba_race_write_guard_flag_only_smoke_result.json"
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
            {
                "name": "row-limit-exceeded",
                "request": {
                    "race_id": args.limit_test_race_id,
                    "date": args.date,
                    "confirm_write": True,
                    "dry_run": False,
                    "payload_contract_approved": True,
                    "user_id": "guard-smoke-user",
                },
                "expected_status": "blocked",
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
        guarded_body = guarded_case.get("body") if isinstance(guarded_case, dict) else None
        guarded_contract_ok, guarded_contract_errors = _is_writer_stub_contract_ok(guarded_body if isinstance(guarded_body, dict) else None)
        all_ok = all_ok and guarded_flag_ok
        all_ok = all_ok and guarded_contract_ok

        limit_case = next((c for c in checks if c["name"] == "row-limit-exceeded"), None)
        limit_body = limit_case.get("body") if isinstance(limit_case, dict) else None
        preview_validation = limit_body.get("preview_validation") if isinstance(limit_body, dict) else None
        preview_issues = preview_validation.get("issues") if isinstance(preview_validation, dict) else []
        has_limit_issue = any("records_count exceeds limit" in str(x) for x in preview_issues) if isinstance(preview_issues, list) else False
        row_limit_block_ok = isinstance(limit_case, dict) and limit_case.get("actual_status") == "blocked" and has_limit_issue
        all_ok = all_ok and row_limit_block_ok

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
                "guarded_stub_contract_ok": guarded_contract_ok,
                "row_limit_block_ok": row_limit_block_ok,
                "guarded_stub_contract_errors": guarded_contract_errors,
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
