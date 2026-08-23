#!/usr/bin/env python3
"""Smoke test for validate_p0_targeted_refetch_live.py with fixture fetch responses."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT_DIR / "reports"
SCRIPT_PATH = ROOT_DIR / "scripts" / "validate_p0_targeted_refetch_live.py"


def _write_cache_db(path: Path, cached_url: str) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE http_cache (
            normalized_url TEXT PRIMARY KEY,
            final_url TEXT NOT NULL,
            status INTEGER NOT NULL,
            headers_json TEXT NOT NULL,
            body BLOB NOT NULL,
            fetched_at REAL NOT NULL,
            expires_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO http_cache VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cached_url, cached_url, 200, "{}", b"<html><body>cached</body></html>", time.time(), time.time() + 86400),
    )
    conn.commit()
    conn.close()


def _plan_fixture() -> dict[str, Any]:
    base_rows = [
        {
            "url": "https://db.netkeiba.com/race/202601010101/",
            "url_type": "result_page",
            "race_id": "202601010101",
            "horse_id": "2021100001",
            "reason": "true-missing",
            "column": "finish_position",
            "priority": "P0",
            "source": "plan",
            "recommended_next_action": "targeted refetch live validation",
        },
        {
            "url": "https://db.netkeiba.com/race/202601010102/",
            "url_type": "result_page",
            "race_id": "202601010102",
            "horse_id": "2021100002",
            "reason": "true-missing",
            "column": "finish_position",
            "priority": "P0",
            "source": "plan",
            "recommended_next_action": "targeted refetch live validation",
        },
        {
            "url": "https://db.netkeiba.com/race/202601010103/",
            "url_type": "race_detail",
            "race_id": "202601010103",
            "horse_id": None,
            "reason": "consistency:race_without_horse_data",
            "column": "(check)",
            "priority": "P0",
            "source": "plan",
            "recommended_next_action": "targeted refetch live validation",
        },
        {
            "url": "https://db.netkeiba.com/race/202601010104/",
            "url_type": "result_page",
            "race_id": "202601010104",
            "horse_id": "2021100004",
            "reason": "derived-field-candidate",
            "column": "race_number",
            "priority": "P0",
            "source": "plan",
            "recommended_next_action": "schema-review",
        },
        {
            "url": "https://db.netkeiba.com/race/202601010105/",
            "url_type": "result_page",
            "race_id": "202601010105",
            "horse_id": "2021100005",
            "reason": "domain-allowed-missing",
            "column": "margin",
            "priority": "P0",
            "source": "plan",
            "recommended_next_action": "no-action",
        },
        {
            "url": "https://db.netkeiba.com/race/202601010106/",
            "url_type": "result_page",
            "race_id": "202601010106",
            "horse_id": "2021100006",
            "reason": "true-missing",
            "column": "race_date",
            "priority": "P1",
            "source": "plan",
            "recommended_next_action": "metadata-repair",
        },
        {
            "url": "https://db.netkeiba.com/race/202601010107/",
            "url_type": "result_page",
            "race_id": "202601010107",
            "horse_id": "2021100007",
            "reason": "true-missing",
            "column": "finish_position",
            "priority": "P0",
            "source": "plan",
            "recommended_next_action": "targeted refetch live validation",
        },
        {
            "url": "https://db.netkeiba.com/race/202601010108/",
            "url_type": "result_page",
            "race_id": "202601010108",
            "horse_id": "2021100008",
            "reason": "true-missing",
            "column": "finish_position",
            "priority": "P0",
            "source": "plan",
            "recommended_next_action": "targeted refetch live validation",
        },
        {
            "url": "https://db.netkeiba.com/race/202601010109/",
            "url_type": "result_page",
            "race_id": "202601010109",
            "horse_id": "2021100009",
            "reason": "true-missing",
            "column": "finish_position",
            "priority": "P0",
            "source": "plan",
            "recommended_next_action": "targeted refetch live validation",
        },
        {
            "url": "https://db.netkeiba.com/race/202601010110/",
            "url_type": "result_page",
            "race_id": "202601010110",
            "horse_id": "2021100010",
            "reason": "true-missing",
            "column": "finish_position",
            "priority": "P0",
            "source": "plan",
            "recommended_next_action": "targeted refetch live validation",
        },
        {
            "url": "https://db.netkeiba.com/race/202601010111/",
            "url_type": "result_page",
            "race_id": "202601010111",
            "horse_id": "2021100011",
            "reason": "true-missing",
            "column": "finish_position",
            "priority": "P0",
            "source": "plan",
            "recommended_next_action": "targeted refetch live validation",
        },
        {
            "url": "https://db.netkeiba.com/race/202601010112/",
            "url_type": "result_page",
            "race_id": "202601010112",
            "horse_id": "2021100012",
            "reason": "true-missing",
            "column": "finish_position",
            "priority": "P0",
            "source": "plan",
            "recommended_next_action": "targeted refetch live validation",
        },
    ]
    return {
        "unique_url_count": 100,
        "sample_urls": {
            "result_page": base_rows,
            "race_detail": [],
            "horse_detail": [],
            "pedigree": [],
        },
    }


def _fixture_fetch_responses() -> dict[str, Any]:
    race_ok = """
    <html><body>
    <p class='smalltxt'>2026年1月1日 東京 1回1日目</p>
    <table class='race_table_01'>
    <tr><th>着順</th><th>枠番</th><th>馬番</th><th>馬名</th><th>性齢</th><th>斤量</th><th>騎手</th><th>タイム</th><th>着差</th></tr>
    <tr><td>1</td><td>1</td><td>1</td><td><a href='/horse/result/2021100001/'>Alpha</a></td><td>牡3</td><td>56.0</td><td>J1</td><td>1:34.5</td><td>0.0</td></tr>
    </table>
    </body></html>
    """
    race_short = "<html>short</html>"
    return {
        "responses": {
            "https://db.netkeiba.com/race/202601010101/": {"status": 200, "body": race_ok, "source": "network", "attempts": 1},
            "https://db.netkeiba.com/race/202601010102/": {"status": 200, "body": race_short, "source": "network", "attempts": 1},
            "https://db.netkeiba.com/race/202601010103/": {"status": 503, "body": "", "source": "network", "attempts": 2, "backoff_observed": True},
            "*": {"status": 200, "body": race_ok, "source": "network", "attempts": 1},
        }
    }


def _run_script(plan_path: Path, cache_db: Path, fixture_path: Path, out_path: Path) -> tuple[int, dict[str, Any]]:
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--input-refetch-plan",
            str(plan_path),
            "--target",
            "all",
            "--url-type",
            "all",
            "--max-urls",
            "12",
            "--cache-db",
            str(cache_db),
            "--fixture-json",
            str(fixture_path),
            "--output",
            str(out_path),
        ],
        cwd=str(ROOT_DIR),
        text=True,
        capture_output=True,
    )

    payload: dict[str, Any] = {}
    out = (proc.stdout or "").strip()
    if out:
        try:
            payload = json.loads(out.splitlines()[-1])
        except Exception:
            payload = {"raw_stdout": out[-1000:]}
    if out_path.exists():
        try:
            detail = json.loads(out_path.read_text(encoding="utf-8"))
            if isinstance(detail, dict):
                payload["_detail"] = detail
        except Exception:
            pass
    return proc.returncode, payload


def main() -> int:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    result_file = REPORTS_DIR / "p0_targeted_refetch_live_validation_smoke_result.json"

    with tempfile.TemporaryDirectory(prefix="smoke_p0_refetch_live_") as td:
        tmp = Path(td)
        plan_path = tmp / "plan.json"
        cache_db = tmp / "fetch_cache.db"
        fixture_path = tmp / "fixture.json"
        out_path = tmp / "out.json"

        plan_path.write_text(json.dumps(_plan_fixture(), ensure_ascii=False, indent=2), encoding="utf-8")
        fixture_path.write_text(json.dumps(_fixture_fetch_responses(), ensure_ascii=False, indent=2), encoding="utf-8")
        _write_cache_db(cache_db, "https://db.netkeiba.com/race/202601010107/")

        cache_before = cache_db.stat().st_mtime_ns
        rc, payload = _run_script(plan_path, cache_db, fixture_path, out_path)
        cache_after = cache_db.stat().st_mtime_ns

    detail = payload.get("_detail") if isinstance(payload.get("_detail"), dict) else {}
    sample_results = detail.get("sample_results") if isinstance(detail.get("sample_results"), list) else []

    checks = {
        "max_urls_capped_to_10": int(detail.get("max_urls_applied") or 0) == 10,
        "schema_review_excluded": int(detail.get("excluded_schema_review_count") or 0) > 0,
        "domain_allowed_excluded": int(detail.get("excluded_domain_allowed_count") or 0) > 0,
        "metadata_repair_excluded": int(detail.get("excluded_metadata_repair_count") or 0) > 0,
        "cache_available_excluded": int(detail.get("excluded_cache_available_count") or 0) > 0,
        "http_error_detected": int(detail.get("http_error_count") or 0) > 0,
        "parse_failed_detected": int(detail.get("parse_failed_count") or 0) > 0,
        "would_fix_detected": int(detail.get("would_fix_count") or 0) > 0,
        "safety_flags_present": bool(detail.get("safety_flags", {}).get("no_db_write")) and bool(detail.get("safety_flags", {}).get("no_upsert")),
        "db_write_zero": bool(cache_before == cache_after),
        "sample_results_present": bool(sample_results),
    }

    success = bool(rc == 0 and all(checks.values()))
    output = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "success": success,
        "verdict": "pass" if success else "fail",
        "verdict_reason": "ok" if success else "p0-targeted-refetch-live-validation-smoke-failed",
        "checks": checks,
        "run_result": {"return_code": rc, **payload},
    }
    result_file.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"result_file": str(result_file), "success": success, "checks": checks}, ensure_ascii=False))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())