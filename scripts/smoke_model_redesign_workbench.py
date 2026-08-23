#!/usr/bin/env python3
"""Smoke test for /api/model-redesign/summary (read-only MVP).

Checks:
1. Unauthenticated summary request returns 401.
2. Missing Premium/Admin token is classified as auth-required warn.
3. Premium/Admin token request returns pass or warn(data/config-missing).
4. Optional non-premium token returns 403.
5. Arbitrary path-like inputs are rejected.
6. retrain dry-run preview is available for Premium/Admin.
7. retrain/action switch operations stay not-implemented/disabled.
8. Responses do not expose token/secret/env values.
9. .active_model.json is unchanged.
10. joblib/metadata/reports are not created/overwritten by this smoke.
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

ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT_DIR / "reports" / "model_redesign_workbench_smoke_result.json"
DEFAULT_NEXT_URL = "http://localhost:3000"
TIMEOUT_SECONDS = 120

ACTIVE_MODEL_PATH = ROOT_DIR / "python-api" / "models" / ".active_model.json"
MODELS_DIR = ROOT_DIR / "python-api" / "models"
REPORTS_DIR = ROOT_DIR / "reports"

PATH_KEYS = ["filePath", "reportPath", "modelPath", "path", "sourcePath"]
NOTION_TOKEN_PREFIX = ''.join(["nt", "n_"])


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


def _snapshot(path: Path, exclude: set[Path] | None = None) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    if not path.exists():
        return out

    excludes = exclude or set()
    for f in sorted([p for p in path.rglob("*") if p.is_file()]):
        if f in excludes:
            continue
        try:
            st = f.stat()
            out[str(f.relative_to(ROOT_DIR)).replace("\\", "/")] = (int(st.st_size), int(st.st_mtime_ns))
        except OSError:
            continue
    return out


def _single_file_snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "size": None, "mtime_ns": None}
    st = path.stat()
    return {"exists": True, "size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns)}


def _save(result: dict[str, Any]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def _contains_secret(text: str, secrets: list[str]) -> bool:
    lowered = text.lower()
    if NOTION_TOKEN_PREFIX in lowered:
        return True
    for value in secrets:
        if value and value in text:
            return True
    return False


def _check_path_forbidden(api_url: str, token: str) -> tuple[bool, list[dict[str, Any]], str]:
    details: list[dict[str, Any]] = []
    all_text = ""
    ok = True

    for key in PATH_KEYS:
        status, body, parsed = _http_json("GET", f"{api_url}?{key}=../../etc/passwd", token=token)
        all_text += body
        is_ok = status == 400 and str(parsed.get("code", "")) == "path-input-forbidden"
        details.append({"key": key, "status": status, "code": parsed.get("code"), "ok": is_ok})
        if not is_ok:
            ok = False

    return ok, details, all_text


def _check_dry_run_preview(api_url: str, token: str) -> tuple[bool, dict[str, Any], str]:
    status, body, parsed = _http_json("POST", api_url, token=token, payload={"action": "retrain_dry_run"})
    code = str(parsed.get("code", ""))
    preview = parsed.get("dry_run_preview") if isinstance(parsed.get("dry_run_preview"), dict) else {}

    required_keys = {
        "target",
        "model_type",
        "train_period",
        "validation_period",
        "feature_count",
        "selected_features",
        "removed_features",
        "expected_outputs",
        "estimated_runtime",
        "safety_checks",
    }
    has_required = required_keys.issubset(set(preview.keys())) if isinstance(preview, dict) else False
    ok = status == 200 and bool(parsed.get("success")) and code == "dry-run-preview" and has_required

    details = {
        "status": status,
        "code": code,
        "success": bool(parsed.get("success")),
        "has_required_fields": has_required,
    }
    return ok, details, body


def _check_not_implemented_actions(api_url: str, token: str) -> tuple[bool, list[dict[str, Any]], str]:
    actions = ["retrain", "active_model_switch"]
    details: list[dict[str, Any]] = []
    all_text = ""
    ok = True

    for action in actions:
        status, body, parsed = _http_json("POST", api_url, token=token, payload={"action": action})
        all_text += body
        code = str(parsed.get("code", ""))
        action_ok = code in {"not-implemented", "disabled"} and status in {405, 501}
        details.append({"action": action, "status": status, "code": code, "ok": action_ok})
        if not action_ok:
            ok = False

    return ok, details, all_text


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test for /api/model-redesign/summary")
    parser.add_argument("--next-url", default=DEFAULT_NEXT_URL)
    parser.add_argument("--auth-token", default="")
    parser.add_argument("--nonpremium-token", default="")
    args = parser.parse_args()

    premium_token = args.auth_token.strip() or os.getenv("KEIBA_AUTH_BEARER_TOKEN", "").strip()
    nonpremium_token = args.nonpremium_token.strip() or os.getenv("KEIBA_AUTH_BEARER_TOKEN_NONPREMIUM", "").strip()

    secrets = [
        premium_token,
        nonpremium_token,
        os.getenv("NOTION_TOKEN", "").strip(),
        os.getenv("NOTION_PARENT_PAGE_ID", "").strip(),
        os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY", "").strip(),
        os.getenv("SUPABASE_SERVICE_KEY", "").strip(),
    ]

    base = args.next_url.rstrip("/")
    api_url = f"{base}/api/model-redesign/summary"

    active_before = _single_file_snapshot(ACTIVE_MODEL_PATH)
    model_files_before = _snapshot(MODELS_DIR)
    reports_before = _snapshot(REPORTS_DIR, exclude={OUTPUT_PATH})

    result: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "api_url": api_url,
        "output_path": str(OUTPUT_PATH.relative_to(ROOT_DIR)).replace("\\", "/"),
        "token_provided": bool(premium_token),
        "nonpremium_token_provided": bool(nonpremium_token),
        "checks": {},
        "success": False,
        "verdict": "fail",
    }

    response_text = ""

    try:
        unauth_status, unauth_body, _unauth_json = _http_json("GET", api_url)
        response_text += unauth_body
        unauth_ok = unauth_status == 401
        result["checks"]["unauth_401"] = {
            "result": "pass" if unauth_ok else "fail",
            "reason": "unauthorized" if unauth_ok else "unexpected-status",
            "status": unauth_status,
        }

        if not premium_token:
            result["checks"]["auth"] = {
                "result": "warn",
                "reason": "auth-required",
                "message": "KEIBA_AUTH_BEARER_TOKEN is not set",
            }
        else:
            summary_status, summary_body, summary_json = _http_json("GET", api_url, token=premium_token)
            response_text += summary_body
            state = str(summary_json.get("state", ""))
            code = str(summary_json.get("code", ""))
            guard = summary_json.get("guard") if isinstance(summary_json.get("guard"), dict) else {}
            is_pass = summary_status == 200 and bool(summary_json.get("success")) and state == "pass"
            is_warn = summary_status == 200 and bool(summary_json.get("success")) and state == "warn" and code in {"data-missing", "config-missing"}
            summary_ok = is_pass or is_warn
            result["checks"]["summary"] = {
                "result": "pass" if summary_ok else "fail",
                "reason": "ready" if is_pass else ("data-missing" if is_warn else "summary-failed"),
                "status": summary_status,
                "state": state,
                "code": code,
            }

            guard_ok = (
                isinstance(guard, dict)
                and bool(guard.get("read_only_mode")) is True
                and str(guard.get("retrain_execution", "")) in {"not-implemented", "disabled"}
                and str(guard.get("active_model_switch", "")) in {"not-implemented", "disabled"}
                and bool(guard.get("production_write")) is False
            )
            result["checks"]["read_only_guard"] = {
                "result": "pass" if guard_ok else "fail",
                "reason": "guard-ok" if guard_ok else "guard-mismatch",
                "guard": {
                    "read_only_mode": guard.get("read_only_mode"),
                    "retrain_execution": guard.get("retrain_execution"),
                    "active_model_switch": guard.get("active_model_switch"),
                    "production_write": guard.get("production_write"),
                },
            }

            path_ok, path_details, path_text = _check_path_forbidden(api_url, premium_token)
            response_text += path_text
            result["checks"]["path_forbidden"] = {
                "result": "pass" if path_ok else "fail",
                "reason": "rejected" if path_ok else "not-rejected",
                "details": path_details,
            }

            dry_run_ok, dry_run_details, dry_run_text = _check_dry_run_preview(api_url, premium_token)
            response_text += dry_run_text
            result["checks"]["dry_run_preview"] = {
                "result": "pass" if dry_run_ok else "fail",
                "reason": "preview-ready" if dry_run_ok else "dry-run-preview-invalid",
                "details": dry_run_details,
            }

            action_ok, action_details, action_text = _check_not_implemented_actions(api_url, premium_token)
            response_text += action_text
            result["checks"]["action_not_implemented"] = {
                "result": "pass" if action_ok else "fail",
                "reason": "blocked" if action_ok else "unexpected-action-response",
                "details": action_details,
            }

        if nonpremium_token:
            np_status, np_body, np_json = _http_json("GET", api_url, token=nonpremium_token)
            response_text += np_body
            np_ok = np_status == 403 and not bool(np_json.get("success"))
            result["checks"]["nonpremium_403"] = {
                "result": "pass" if np_ok else "fail",
                "reason": "forbidden" if np_ok else "forbidden-not-enforced",
                "status": np_status,
            }
        else:
            result["checks"]["nonpremium_403"] = {
                "result": "warn",
                "reason": "nonpremium-token-missing",
            }

        leaked = _contains_secret(response_text, secrets)
        result["checks"]["secret_not_exposed"] = {
            "result": "pass" if not leaked else "fail",
            "reason": "not-exposed" if not leaked else "secret-exposed",
        }

        active_after = _single_file_snapshot(ACTIVE_MODEL_PATH)
        model_files_after = _snapshot(MODELS_DIR)
        reports_after = _snapshot(REPORTS_DIR, exclude={OUTPUT_PATH})

        active_unchanged = active_before == active_after
        models_unchanged = model_files_before == model_files_after
        reports_unchanged = reports_before == reports_after

        result["checks"]["active_model_unchanged"] = {
            "result": "pass" if active_unchanged else "fail",
            "reason": "unchanged" if active_unchanged else "changed",
        }
        result["checks"]["artifacts_unchanged"] = {
            "result": "pass" if (models_unchanged and reports_unchanged) else "fail",
            "reason": "unchanged" if (models_unchanged and reports_unchanged) else "changed",
            "details": {
                "model_files_unchanged": models_unchanged,
                "reports_unchanged_excluding_output": reports_unchanged,
            },
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
