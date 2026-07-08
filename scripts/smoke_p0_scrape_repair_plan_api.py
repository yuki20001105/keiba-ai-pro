#!/usr/bin/env python3
"""Smoke test for /api/scrape/p0-repair-plan route (read-only preview only)."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request

DEFAULT_NEXT_URL = "http://localhost:3000"
OUTPUT_PATH = Path("reports") / "p0_scrape_repair_plan_api_smoke_result.json"
TIMEOUT_SECONDS = 30

SECRET_ENV_KEYS = [
    "KEIBA_AUTH_BEARER_TOKEN",
    "SUPABASE_SERVICE_ROLE_KEY",
    "NEXT_PUBLIC_SUPABASE_ANON_KEY",
    "SUPABASE_ANON_KEY",
    "NOTION_TOKEN",
    "OPENAI_API_KEY",
    "E2E_PASSWORD",
]
NOTION_TOKEN_PREFIX = "ntn" + "_"


def _http_json(method: str, url: str, payload: dict[str, Any] | None = None, token: str | None = None) -> tuple[int, str, dict[str, Any]]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url=url, method=method, headers=headers, data=body)

    try:
        with request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            status = int(getattr(resp, "status", 0))
            raw = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw) if raw else {}
            return status, raw, parsed if isinstance(parsed, dict) else {}
    except error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {}
        return int(e.code), raw, parsed if isinstance(parsed, dict) else {}


def _contains_secret(text: str, token: str) -> bool:
    lowered = text.lower()
    if NOTION_TOKEN_PREFIX in lowered:
        return True

    values = [token]
    for key in SECRET_ENV_KEYS:
        v = os.getenv(key, "").strip()
        if v:
            values.append(v)

    for v in values:
        if v and v in text:
            return True
    return False


def _save(result: dict[str, Any]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test for /api/scrape/p0-repair-plan")
    parser.add_argument("--next-url", default=DEFAULT_NEXT_URL)
    parser.add_argument("--auth-token", default="")
    args = parser.parse_args()

    token = args.auth_token.strip() or os.getenv("KEIBA_AUTH_BEARER_TOKEN", "").strip()
    base = args.next_url.rstrip("/")
    endpoint = f"{base}/api/scrape/p0-repair-plan"

    result: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "api_url": endpoint,
        "output_path": str(OUTPUT_PATH),
        "token_provided": bool(token),
        "checks": {},
        "success": False,
        "verdict": "fail",
    }

    try:
        unauth_status, unauth_body, _ = _http_json("POST", endpoint, payload={"target": "all"}, token=None)
        unauth_ok = unauth_status in (401, 403)
        result["checks"]["unauth_behavior"] = {
            "result": "pass" if unauth_ok else "warn",
            "reason": "auth-enforced" if unauth_ok else "auth-not-enforced",
            "status": unauth_status,
        }

        if not token:
            leaked = _contains_secret(unauth_body, "")
            result["checks"]["secret_not_exposed"] = {
                "result": "pass" if not leaked else "fail",
                "reason": "not-exposed" if not leaked else "secret-leaked",
            }
            result["checks"]["auth"] = {
                "result": "warn",
                "reason": "auth-required",
                "message": "KEIBA_AUTH_BEARER_TOKEN is not set",
            }

            check_results = [str(v.get("result")) for v in result["checks"].values()]
            has_fail = any(r == "fail" for r in check_results)
            has_warn = any(r == "warn" for r in check_results)
            result["success"] = not has_fail
            result["verdict"] = "fail" if has_fail else ("warn" if has_warn else "pass")
            _save(result)
            print(f"verdict: {result['verdict']}")
            print("reason: auth-required")
            print(f"output file path: {OUTPUT_PATH}")
            return 0 if result["success"] else 1

        status_ok, body_ok, json_ok = _http_json("POST", endpoint, payload={"target": "all"}, token=token)
        plan = json_ok.get("plan") if isinstance(json_ok.get("plan"), dict) else {}
        dry_ok = bool(json_ok.get("dry_run") is True and json_ok.get("read_only") is True)
        update_disabled = bool(json_ok.get("update_enabled") is False and str(json_ok.get("update_action")) == "not-implemented")
        has_p0_core = (
            isinstance(plan.get("p0_total_count"), int)
            and isinstance(plan.get("refetch_required_count"), int)
            and isinstance(plan.get("schema_review_count"), int)
            and isinstance(plan.get("manual_review_count"), int)
        )
        has_safeguards = isinstance(plan.get("safeguards"), dict)

        result["checks"]["p0_plan_response"] = {
            "result": "pass" if (status_ok == 200 and dry_ok and update_disabled and has_p0_core and has_safeguards) else "fail",
            "reason": "ok" if (status_ok == 200 and dry_ok and update_disabled and has_p0_core and has_safeguards) else "invalid-response",
            "status": status_ok,
        }

        status_path, _, json_path = _http_json(
            "POST",
            endpoint,
            payload={"target": "all", "inputAudit": "C:/tmp/audit.json"},
            token=token,
        )
        path_rejected = status_path == 400 and "forbidden input key" in str(json_path.get("error") or "")
        result["checks"]["path_input_rejected"] = {
            "result": "pass" if path_rejected else "fail",
            "reason": "rejected" if path_rejected else "not-rejected",
            "status": status_path,
        }

        status_put, body_put, json_put = _http_json("PUT", endpoint, payload={}, token=token)
        put_disabled = status_put == 501 and str(json_put.get("error") or "") == "not-implemented"
        result["checks"]["update_action_disabled"] = {
            "result": "pass" if put_disabled else "fail",
            "reason": "not-implemented" if put_disabled else "unexpected",
            "status": status_put,
        }

        leaked = _contains_secret("\n".join([unauth_body, body_ok, body_put]), token)
        result["checks"]["secret_not_exposed"] = {
            "result": "pass" if not leaked else "fail",
            "reason": "not-exposed" if not leaked else "secret-leaked",
        }

        check_results = [str(v.get("result")) for v in result["checks"].values()]
        has_fail = any(r == "fail" for r in check_results)
        has_warn = any(r == "warn" for r in check_results)
        result["success"] = not has_fail
        result["verdict"] = "fail" if has_fail else ("warn" if has_warn else "pass")

        _save(result)
        print(f"verdict: {result['verdict']}")
        print(f"success: {result['success']}")
        print(f"output file path: {OUTPUT_PATH}")
        return 0 if result["success"] else 1

    except Exception as e:  # noqa: BLE001
        result["error"] = str(e)
        result["success"] = False
        result["verdict"] = "fail"
        _save(result)
        print(f"error: {e}")
        print(f"output file path: {OUTPUT_PATH}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
