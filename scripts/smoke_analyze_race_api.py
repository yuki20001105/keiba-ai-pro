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

import json
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = ROOT_DIR / "keiba" / "data" / "keiba_ultimate.db"
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


def call_analyze_race_api(race_id: str) -> Tuple[int, Dict[str, Any]]:
    payload = json.dumps({"race_id": race_id}).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
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


def validate_response(status: int, payload: Dict[str, Any]) -> Tuple[bool, str, int]:
    if status != 200:
        return False, f"HTTP status is not 200 (got {status})", 0

    success = bool(payload.get("success", False))
    if not success:
        return False, "response.success is not true", 0

    predictions = payload.get("predictions")
    if not isinstance(predictions, list):
        return False, "response.predictions is not a list", 0

    count = len(predictions)
    if count <= 0:
        return False, "response.predictions is empty", count

    return True, "ok", count


def save_result(result: Dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    started_at = datetime.now(timezone.utc).isoformat()
    result: Dict[str, Any] = {
        "started_at": started_at,
        "api_url": API_URL,
        "db_path": str(DB_PATH),
        "output_path": str(OUTPUT_PATH),
        "success": False,
    }

    try:
        race_id = get_latest_race_id(DB_PATH)
        result["race_id"] = race_id

        status, response_payload = call_analyze_race_api(race_id)
        result["http_status"] = status
        result["response"] = response_payload

        ok, reason, predictions_count = validate_response(status, response_payload)
        result["success"] = ok
        result["validation"] = reason
        result["predictions_count"] = predictions_count
        result["response_success"] = bool(response_payload.get("success", False))
        race_info = response_payload.get("race_info")
        if isinstance(race_info, dict):
            result["race_name"] = race_info.get("race_name")

        save_result(result, OUTPUT_PATH)

        print(f"race_id: {race_id}")
        print(f"HTTP status: {status}")
        print(f"success: {result['response_success']}")
        print(f"predictions_count: {predictions_count}")
        print(f"output file path: {OUTPUT_PATH}")

        return 0 if ok else 1

    except Exception as e:  # noqa: BLE001
        result["error"] = str(e)
        result["success"] = False
        try:
            save_result(result, OUTPUT_PATH)
        except Exception:
            pass

        print(f"error: {e}")
        print(f"output file path: {OUTPUT_PATH}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
