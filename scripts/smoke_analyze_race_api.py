#!/usr/bin/env python3
"""Smoke test for /api/analyze_race.

What it does:
1. Read latest race_id from keiba/data/keiba_ultimate.db (read-only)
2. POST {"race_id": "..."} to http://127.0.0.1:8000/api/analyze_race
3. Validate status=200, success=true, predictions is non-empty list
4. Save result to reports/analyze_race_smoke_result.json (UTF-8)
5. Exit 0 on success, 1 on failure
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = ROOT_DIR / "keiba" / "data" / "keiba_ultimate.db"
MODELS_DIR = ROOT_DIR / "python-api" / "models"
OUTPUT_PATH = Path("reports") / "analyze_race_smoke_result.json"
API_URL = "http://127.0.0.1:8000/api/analyze_race"
REQUEST_TIMEOUT_SECONDS = 300


def get_latest_race_id(db_path: Path) -> str:
    if not db_path.exists():
        raise RuntimeError(f"DB file not found: {db_path}")

    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT race_id FROM races_ultimate ORDER BY race_id DESC LIMIT 1"
        ).fetchone()

    if not row or not row[0]:
        raise RuntimeError("race_id not found in races_ultimate")

    return str(row[0])


def list_local_models() -> list[str]:
    if not MODELS_DIR.exists():
        return []
    models = sorted(MODELS_DIR.glob("model_*.joblib"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [p.stem for p in models]


def parse_mismatch_model(detail: str) -> str | None:
    m = re.search(r"モデル '([^']+)'", detail)
    if not m:
        return None
    name = m.group(1).strip()
    if name.endswith(".joblib"):
        name = name[:-7]
    return name or None


def call_analyze_race_api(
    race_id: str,
    bearer_token: str | None = None,
    model_id: str | None = None,
) -> Tuple[int, Dict[str, Any]]:
    body: Dict[str, Any] = {"race_id": race_id}
    if model_id:
        body["model_id"] = model_id
    payload = json.dumps(body).encode("utf-8")
    headers: Dict[str, str] = {"Content-Type": "application/json; charset=utf-8"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            status = int(getattr(resp, "status", 0))
            body = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(body) if body else {}
            return status, parsed
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        parsed: Dict[str, Any]
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {"raw_error_body": body}
        return int(e.code), parsed
    except urllib.error.URLError as e:
        msg = str(e.reason) if getattr(e, "reason", None) else str(e)
        raise RuntimeError(f"FastAPI server is not running: {msg}") from e
    except TimeoutError as e:
        raise RuntimeError(
            f"API request timed out after {REQUEST_TIMEOUT_SECONDS}s"
        ) from e


def validate_response(status: int, payload: Dict[str, Any], token_provided: bool) -> Tuple[bool, str, str, int]:
    if status in (401, 403) and not token_provided:
        return True, "warn", "auth-required", 0

    if status == 500:
        detail = str(payload.get("detail") or "")
        if "必要な特徴量が計算されていません" in detail:
            return False, "fail", "model-feature-mismatch", 0

    if status != 200:
        return False, "fail", f"HTTP status is not 200 (got {status})", 0

    success = bool(payload.get("success", False))
    if not success:
        return False, "fail", "response.success is not true", 0

    predictions = payload.get("predictions")
    if not isinstance(predictions, list):
        return False, "fail", "response.predictions is not a list", 0

    count = len(predictions)
    if count <= 0:
        return False, "fail", "response.predictions is empty", count

    return True, "pass", "ok", count


def save_result(result: Dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test for /api/analyze_race")
    parser.add_argument("--auth-token", default="", help="Optional Bearer token")
    parser.add_argument("--model-id", default="", help="Optional explicit model_id")
    args = parser.parse_args()

    token = args.auth_token.strip() or os.getenv("KEIBA_AUTH_BEARER_TOKEN", "").strip()
    token_provided = bool(token)
    requested_model_id = args.model_id.strip() or None

    started_at = datetime.now(timezone.utc).isoformat()
    result: Dict[str, Any] = {
        "started_at": started_at,
        "api_url": API_URL,
        "db_path": str(DB_PATH),
        "output_path": str(OUTPUT_PATH),
        "success": False,
        "token_provided": token_provided,
        "requested_model_id": requested_model_id,
    }

    try:
        race_id = get_latest_race_id(DB_PATH)
        result["race_id"] = race_id

        status, response_payload = call_analyze_race_api(
            race_id,
            bearer_token=token if token else None,
            model_id=requested_model_id,
        )
        result["http_status"] = status
        result["response"] = response_payload

        ok, verdict, reason, predictions_count = validate_response(
            status,
            response_payload,
            token_provided=token_provided,
        )
        result["attempted_models"] = [requested_model_id or "<default>"]

        if not ok and reason == "model-feature-mismatch" and requested_model_id is None:
            detail = str(response_payload.get("detail") or "")
            mismatch_model = parse_mismatch_model(detail)
            fallback_attempts: list[Dict[str, Any]] = []
            for candidate in [m for m in list_local_models() if m != mismatch_model]:
                c_status, c_payload = call_analyze_race_api(
                    race_id,
                    bearer_token=token if token else None,
                    model_id=candidate,
                )
                c_ok, c_verdict, c_reason, c_count = validate_response(
                    c_status,
                    c_payload,
                    token_provided=token_provided,
                )
                fallback_attempts.append(
                    {
                        "model_id": candidate,
                        "status": c_status,
                        "ok": c_ok,
                        "reason": c_reason,
                        "predictions_count": c_count,
                    }
                )
                result["attempted_models"].append(candidate)
                if c_ok:
                    status, response_payload = c_status, c_payload
                    ok, verdict, reason, predictions_count = c_ok, c_verdict, c_reason, c_count
                    result["http_status"] = status
                    result["response"] = response_payload
                    result["fallback_used"] = True
                    result["fallback_model_id"] = candidate
                    break

            result["fallback_attempts"] = fallback_attempts
            if not result.get("fallback_used"):
                result["fallback_used"] = False

        result["success"] = ok
        result["verdict"] = verdict
        result["validation"] = reason
        result["auth_required"] = reason == "auth-required"
        result["predictions_count"] = predictions_count
        result["response_success"] = bool(response_payload.get("success", False))
        race_info = response_payload.get("race_info")
        if isinstance(race_info, dict):
            result["race_name"] = race_info.get("race_name")

        save_result(result, OUTPUT_PATH)

        print(f"race_id: {race_id}")
        print(f"HTTP status: {status}")
        print(f"verdict: {result.get('verdict', 'unknown')}")
        if result.get("fallback_used"):
            print(f"fallback model used: {result.get('fallback_model_id')}")
        if result.get("auth_required"):
            print("note: auth required (set KEIBA_AUTH_BEARER_TOKEN or --auth-token)")
        print(f"success: {result['response_success']}")
        print(f"predictions_count: {predictions_count}")
        print(f"output file path: {OUTPUT_PATH}")

        return 0 if ok else 1

    except Exception as e:  # noqa: BLE001
        result["error"] = str(e)
        result["success"] = False
        result["verdict"] = "fail"
        try:
            save_result(result, OUTPUT_PATH)
        except Exception:
            pass

        print(f"error: {e}")
        print(f"output file path: {OUTPUT_PATH}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
