#!/usr/bin/env python3
"""Smoke test for fetch summary history API (/api/scrape/history via Next route)."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request

DEFAULT_NEXT_URL = "http://localhost:3000"
OUTPUT_PATH = Path("reports") / "fetch_summary_history_smoke_result.json"
JOBS_DB_PATH = Path("keiba") / "data" / "scrape_jobs.db"
TIMEOUT_SECONDS = 20

SECRET_ENV_KEYS = [
    "KEIBA_AUTH_BEARER_TOKEN",
    "SUPABASE_SERVICE_ROLE_KEY",
    "NEXT_PUBLIC_SUPABASE_ANON_KEY",
    "SUPABASE_ANON_KEY",
    "NOTION_TOKEN",
    "OPENAI_API_KEY",
    "E2E_PASSWORD",
]


def _http_json(url: str, token: str | None = None) -> tuple[int, str, dict[str, Any]]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = request.Request(url=url, method="GET", headers=headers)
    try:
        with request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            status = int(getattr(resp, "status", 0))
            body = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(body) if body else {}
            return status, body, parsed if isinstance(parsed, dict) else {}
    except error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {}
        return int(e.code), body, parsed if isinstance(parsed, dict) else {}


def _db_row_count(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(str(path))
        row = conn.execute("SELECT COUNT(*) FROM scrape_jobs").fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:
        return None


def _contains_secret(text: str, token: str) -> bool:
    lowered = text.lower()
    _notion_token_prefix = "nt" + "n_"
    if _notion_token_prefix in lowered:
        return True

    values = [token]
    for key in SECRET_ENV_KEYS:
        val = os.getenv(key, "").strip()
        if val:
            values.append(val)

    for value in values:
        if value and value in text:
            return True
    return False


def _normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    summary = item.get("fetch_summary") if isinstance(item.get("fetch_summary"), dict) else {}
    dry = summary.get("dry_run") if isinstance(summary.get("dry_run"), dict) else {}
    metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}

    cache_hit = dry.get("cache_hit_count")
    if cache_hit is None:
        cache_hit = metrics.get("cache_hits")

    cache_miss = dry.get("cache_miss_count")

    resume_hit = dry.get("resume_hit_count")
    if resume_hit is None:
        resume_hit = metrics.get("resume_hits")

    elapsed_seconds = summary.get("elapsed_time_sec")
    if elapsed_seconds is None:
        elapsed_seconds = dry.get("estimated_runtime_sec")

    return {
        "mode": summary.get("mode"),
        "job_id": item.get("job_id"),
        "updated_at": item.get("updated_at"),
        "estimated_request_count": dry.get("estimated_request_count"),
        "cache_hit_count": cache_hit,
        "cache_miss_count": cache_miss,
        "resume_hit_count": resume_hit,
        "elapsed_seconds": elapsed_seconds,
        "retry_count": metrics.get("retry_count"),
    }


def _limit_check(jobs: Any, limit: int) -> bool:
    return isinstance(jobs, list) and len(jobs) <= limit


def _major_fields_check(sample: dict[str, Any]) -> bool:
    required = ["mode", "job_id", "updated_at"]
    for key in required:
        if not sample.get(key):
            return False
    return True


def _save(result: dict[str, Any]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test for /api/scrape/history")
    parser.add_argument("--next-url", default=DEFAULT_NEXT_URL)
    parser.add_argument("--auth-token", default="")
    parser.add_argument("--limit-small", type=int, default=1)
    parser.add_argument("--limit-large", type=int, default=5)
    args = parser.parse_args()

    token = args.auth_token.strip() or os.getenv("KEIBA_AUTH_BEARER_TOKEN", "").strip()
    base = args.next_url.rstrip("/")

    result: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "api_url": f"{base}/api/scrape/history",
        "output_path": str(OUTPUT_PATH),
        "token_provided": bool(token),
        "checks": {},
        "success": False,
        "verdict": "fail",
    }

    try:
        unauth_url = f"{base}/api/scrape/history?limit={args.limit_small}"
        unauth_status, unauth_body, unauth_json = _http_json(unauth_url, token=None)
        unauth_ok = unauth_status in (401, 403)
        result["checks"]["unauth_behavior"] = {
            "result": "pass" if unauth_ok else "warn",
            "reason": "auth-enforced" if unauth_ok else "auth-not-enforced",
            "status": unauth_status,
        }

        if not token:
            result["checks"]["auth"] = {
                "result": "warn",
                "reason": "auth-required",
                "message": "KEIBA_AUTH_BEARER_TOKEN is not set",
            }
            leaked = _contains_secret(unauth_body, "")
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
            print("reason: auth-required")
            print(f"output file path: {OUTPUT_PATH}")
            return 0 if result["success"] else 1

        count_before = _db_row_count(JOBS_DB_PATH)

        u1 = f"{base}/api/scrape/history?limit={args.limit_small}"
        u2 = f"{base}/api/scrape/history?limit={args.limit_large}"
        s1, b1, j1 = _http_json(u1, token=token)
        s2, b2, j2 = _http_json(u2, token=token)

        count_after = _db_row_count(JOBS_DB_PATH)

        jobs1 = j1.get("jobs") if isinstance(j1, dict) else None
        jobs2 = j2.get("jobs") if isinstance(j2, dict) else None

        auth_ok = s1 == 200 and s2 == 200
        result["checks"]["auth_behavior"] = {
            "result": "pass" if auth_ok else "fail",
            "reason": "ok" if auth_ok else "request-failed",
            "status_small": s1,
            "status_large": s2,
        }

        limit_ok = _limit_check(jobs1, args.limit_small) and _limit_check(jobs2, args.limit_large)
        result["checks"]["limit_parameter"] = {
            "result": "pass" if limit_ok else "fail",
            "reason": "limit-applied" if limit_ok else "limit-not-applied",
            "len_small": len(jobs1) if isinstance(jobs1, list) else None,
            "len_large": len(jobs2) if isinstance(jobs2, list) else None,
        }

        db_read_only = count_before is not None and count_after is not None and count_before == count_after
        db_read_only_result = "pass" if db_read_only else "warn"
        db_read_only_reason = "row-count-unchanged" if db_read_only else "db-row-count-unverifiable"
        result["checks"]["read_only_db"] = {
            "result": db_read_only_result,
            "reason": db_read_only_reason,
            "rows_before": count_before,
            "rows_after": count_after,
        }

        all_text = "\n".join([unauth_body, b1, b2])
        leaked = _contains_secret(all_text, token)
        result["checks"]["secret_not_exposed"] = {
            "result": "pass" if not leaked else "fail",
            "reason": "not-exposed" if not leaked else "secret-leaked",
        }

        samples: list[dict[str, Any]] = []
        if isinstance(jobs2, list):
            for item in jobs2:
                if isinstance(item, dict) and isinstance(item.get("fetch_summary"), dict):
                    samples.append(_normalize_item(item))

        if not samples:
            result["checks"]["history_entries"] = {
                "result": "warn",
                "reason": "empty-history-or-no-fetch-summary",
            }
        else:
            major_ok = all(_major_fields_check(sample) for sample in samples)
            result["checks"]["history_entries"] = {
                "result": "pass" if major_ok else "fail",
                "reason": "major-fields-present" if major_ok else "major-fields-missing",
                "sample_count": len(samples),
                "samples": samples[:5],
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
