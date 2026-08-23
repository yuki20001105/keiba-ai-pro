#!/usr/bin/env python3
"""Smoke test for /api/notion-report.

Checks:
1. preview succeeds for Premium/Admin token
2. send is success or config-missing warn
3. non-premium token receives 403 (when provided)
4. token values are not exposed in response
5. arbitrary file path input is rejected
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUTPUT_PATH = Path("reports") / "notion_report_smoke_result.json"
DEFAULT_NEXT_URL = "http://localhost:3000"
TIMEOUT_SECONDS = 120

REPORT_TYPES = [
    "feature_analysis",
    "smoke_suite_summary",
    "production_readiness_summary",
    "model_evaluation_summary",
]
NOTION_TOKEN_PREFIX = ''.join(['nt', 'n_'])


def _http_json(
    method: str,
    url: str,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[int, str, dict[str, Any]]:
    data = None
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            status = int(getattr(resp, "status", 0))
            body = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(body) if body else {}
            return status, body, parsed if isinstance(parsed, dict) else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {}
        return int(e.code), body, parsed if isinstance(parsed, dict) else {}
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", e))
        raise RuntimeError(f"Next server is not reachable: {reason}") from e


def _contains_secret(text: str, secrets: list[str]) -> bool:
    lowered = text.lower()
    if NOTION_TOKEN_PREFIX in lowered:
        return True
    for value in secrets:
        if value and value in text:
            return True
    return False


def _save(result: dict[str, Any]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test for /api/notion-report")
    parser.add_argument("--next-url", default=DEFAULT_NEXT_URL)
    parser.add_argument("--auth-token", default="")
    parser.add_argument("--nonpremium-token", default="")
    args = parser.parse_args()

    premium_token = args.auth_token.strip() or os.getenv("KEIBA_AUTH_BEARER_TOKEN", "").strip()
    nonpremium_token = args.nonpremium_token.strip() or os.getenv("KEIBA_AUTH_BEARER_TOKEN_NONPREMIUM", "").strip()
    notion_token = os.getenv("NOTION_TOKEN", "").strip()
    notion_parent = os.getenv("NOTION_PARENT_PAGE_ID", "").strip()
    base = args.next_url.rstrip("/")
    api_url = f"{base}/api/notion-report"

    result: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "api_url": api_url,
        "output_path": str(OUTPUT_PATH),
        "token_provided": bool(premium_token),
        "nonpremium_token_provided": bool(nonpremium_token),
        "checks": {},
        "success": False,
        "verdict": "fail",
    }

    try:
        if not premium_token:
            result["checks"]["auth"] = {
                "result": "warn",
                "reason": "auth-required",
                "message": "KEIBA_AUTH_BEARER_TOKEN is not set",
            }
            result["success"] = True
            result["verdict"] = "warn"
            _save(result)
            print("verdict: warn")
            print("reason: auth-required")
            print(f"output file path: {OUTPUT_PATH}")
            return 0

        preview_results: list[dict[str, Any]] = []
        preview_ok = True
        all_preview_text = ""
        for report_type in REPORT_TYPES:
            status, body, parsed = _http_json(
                "POST",
                api_url,
                token=premium_token,
                payload={"action": "preview", "reportType": report_type},
            )
            all_preview_text += body
            ok = status == 200 and bool(parsed.get("success"))
            preview_results.append(
                {
                    "report_type": report_type,
                    "status": status,
                    "success": bool(parsed.get("success")),
                    "code": parsed.get("code"),
                    "state": parsed.get("state"),
                    "ok": ok,
                }
            )
            if not ok:
                preview_ok = False

        result["checks"]["preview"] = {
            "result": "pass" if preview_ok else "fail",
            "reason": "preview-ok" if preview_ok else "preview-failed",
            "details": preview_results,
        }

        send_status, send_body, send_json = _http_json(
            "POST",
            api_url,
            token=premium_token,
            payload={"action": "send", "reportType": "smoke_suite_summary"},
        )
        all_preview_text += send_body
        send_success = send_status == 200 and bool(send_json.get("success")) and str(send_json.get("code", "")) == "sent"
        send_warn = str(send_json.get("code", "")) == "config-missing" and str(send_json.get("state", "")) == "warn"
        send_ok = send_success or send_warn
        result["checks"]["send"] = {
            "result": "pass" if send_ok else "fail",
            "reason": "sent" if send_success else ("config-missing" if send_warn else "send-failed"),
            "status": send_status,
            "code": send_json.get("code"),
            "state": send_json.get("state"),
        }

        if nonpremium_token:
            np_status, _np_body, np_json = _http_json(
                "POST",
                api_url,
                token=nonpremium_token,
                payload={"action": "preview", "reportType": "feature_analysis"},
            )
            forbidden_ok = np_status == 403 and not bool(np_json.get("success"))
            result["checks"]["nonpremium_403"] = {
                "result": "pass" if forbidden_ok else "fail",
                "reason": "forbidden" if forbidden_ok else "forbidden-not-enforced",
                "status": np_status,
            }
        else:
            result["checks"]["nonpremium_403"] = {
                "result": "warn",
                "reason": "nonpremium-token-missing",
            }

        path_status, _path_body, path_json = _http_json(
            "POST",
            api_url,
            token=premium_token,
            payload={
                "action": "preview",
                "reportType": "feature_analysis",
                "filePath": "../../etc/passwd",
            },
        )
        path_forbidden_ok = path_status == 400 and str(path_json.get("code", "")) == "path-input-forbidden"
        result["checks"]["path_forbidden"] = {
            "result": "pass" if path_forbidden_ok else "fail",
            "reason": "rejected" if path_forbidden_ok else "not-rejected",
            "status": path_status,
            "code": path_json.get("code"),
        }

        leaked = _contains_secret(all_preview_text, [premium_token, nonpremium_token, notion_token, notion_parent])
        result["checks"]["token_not_exposed"] = {
            "result": "pass" if not leaked else "fail",
            "reason": "not-exposed" if not leaked else "token-leaked-in-response",
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
    sys.exit(main())
