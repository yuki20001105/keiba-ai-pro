#!/usr/bin/env python3
"""Smoke test for plan_p0_reparse_cache.py using temporary cache fixtures."""

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
PLAN_SCRIPT = ROOT_DIR / "scripts" / "plan_p0_reparse_cache.py"


def _write_http_cache_db(path: Path, rows: list[tuple[str, str]]) -> None:
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
    for url, html in rows:
        conn.execute(
            "INSERT INTO http_cache VALUES (?, ?, ?, ?, ?, ?, ?)",
            (url, url, 200, "{}", html.encode("utf-8"), time.time(), time.time() + 86400),
        )
    conn.commit()
    conn.close()


def _write_pedigree_cache_db(path: Path, rows: list[tuple[str, str, str, str]]) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE pedigree_cache (horse_id TEXT PRIMARY KEY, sire TEXT, dam TEXT, damsire TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    for horse_id, sire, dam, damsire in rows:
        conn.execute(
            "INSERT INTO pedigree_cache (horse_id, sire, dam, damsire) VALUES (?, ?, ?, ?)",
            (horse_id, sire, dam, damsire),
        )
    conn.commit()
    conn.close()


def _run_plan(plan_path: Path, cache_db: Path, ped_db: Path, output_path: Path) -> tuple[int, dict[str, Any]]:
    proc = subprocess.run(
        [
            sys.executable,
            str(PLAN_SCRIPT),
            "--input-p0-plan",
            str(plan_path),
            "--target",
            "all",
            "--max-targets",
            "50",
            "--cache-db",
            str(cache_db),
            "--pedigree-cache-db",
            str(ped_db),
            "--output",
            str(output_path),
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

    if output_path.exists():
        try:
            detail = json.loads(output_path.read_text(encoding="utf-8"))
            if isinstance(detail, dict):
                payload["_detail"] = detail
        except Exception:
            pass

    return proc.returncode, payload


def _plan_fixture() -> dict[str, Any]:
    return {
        "sample_targets": [
            {
                "race_id": "202601010101",
                "horse_id": "2021100001",
                "column": "finish_position",
                "reason": "true-missing",
                "action": "reparse-cache",
                "priority": "P0",
                "source_hint": "race-result-cache",
                "recommended_next_action": "reparse-cache-first",
            },
            {
                "race_id": "202601010102",
                "horse_id": "2021100002",
                "column": "finish_position",
                "reason": "true-missing",
                "action": "reparse-cache",
                "priority": "P0",
                "source_hint": "race-result-cache",
                "recommended_next_action": "reparse-cache-first",
            },
            {
                "race_id": "202601010201",
                "horse_id": None,
                "column": "(check)",
                "reason": "consistency:race_without_horse_data",
                "action": "refetch-required",
                "priority": "P0",
                "source_hint": "race-result-cache",
                "recommended_next_action": "refetch-required",
            },
            {
                "race_id": "202601010301",
                "horse_id": "2021100301",
                "column": "sire",
                "reason": "true-missing",
                "action": "reparse-cache",
                "priority": "P0",
                "source_hint": "pedigree-cache",
                "recommended_next_action": "reparse-cache-first",
            },
            {
                "race_id": "202601010401",
                "horse_id": "2021100401",
                "column": "finish_position",
                "reason": "true-missing",
                "action": "reparse-cache",
                "priority": "P0",
                "source_hint": "race-result-cache",
                "recommended_next_action": "reparse-cache-first",
            },
            {
                "race_id": "202601019999",
                "horse_id": "2021199999",
                "column": "finish_position",
                "reason": "true-missing",
                "action": "reparse-cache",
                "priority": "P0",
                "source_hint": "race-result-cache",
                "recommended_next_action": "reparse-cache-first",
            },
        ]
    }


def _finish_result_html(race_date: str, venue_text: str, finish_position: str, horse_id: str, horse_name: str, horse_num: str, margin: str, result_time: str) -> str:
    return f"""
<html><body>
<p class='smalltxt'>{race_date[:4]}年{int(race_date[4:6])}月{int(race_date[6:])}日 {venue_text} 1回1日目</p>
<table class='race_table_01'>
<tr><th>着順</th><th>枠番</th><th>馬番</th><th>馬名</th><th>性齢</th><th>斤量</th><th>騎手</th><th>タイム</th><th>着差</th></tr>
<tr><td>{finish_position}</td><td>1</td><td>{horse_num}</td><td><a href='/horse/result/{horse_id}/'>{horse_name}</a></td><td>牡3</td><td>56.0</td><td>J1</td><td>{result_time}</td><td>{margin}</td></tr>
</table>
</body></html>
"""


def _broken_html() -> str:
    return "<html><body>Cloudflare</body></html>"


def _low_quality_html(race_date: str, venue_text: str, horse_id: str, horse_name: str) -> str:
    return f"""
<html><body>
<p class='smalltxt'>{race_date[:4]}年{int(race_date[4:6])}月{int(race_date[6:])}日 {venue_text} 1回1日目</p>
<table class='race_table_01'>
<tr><th>着順</th><th>枠番</th><th>馬番</th><th>馬名</th><th>性齢</th><th>斤量</th><th>騎手</th><th>タイム</th><th>着差</th></tr>
<tr><td></td><td>2</td><td>2</td><td><a href='/horse/result/{horse_id}/'>{horse_name}</a></td><td>牡3</td><td>56.0</td><td>J2</td><td></td><td></td></tr>
</table>
</body></html>
"""


def _horse_html(horse_name: str, sire: str, dam: str, damsire: str) -> str:
    return f"""
<html><body>
<h1>{horse_name}</h1>
<table class='db_prof_table'><tr><th>通算成績</th><td>10戦2勝</td></tr></table>
<table class='blood_table'>
<tr><td><a>{sire}</a></td></tr>
<tr><td><a>{dam}</a></td><td><a>{damsire}</a></td></tr>
</table>
</body></html>
"""


def main() -> int:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    result_file = REPORTS_DIR / "p0_reparse_cache_smoke_result.json"

    with tempfile.TemporaryDirectory(prefix="smoke_p0_reparse_cache_") as td:
        tmp = Path(td)
        cache_db = tmp / "fetch_cache.db"
        ped_db = tmp / "pedigree_cache.db"
        plan_json = tmp / "p0_plan.json"
        out_json = tmp / "p0_reparse_plan.json"

        _write_http_cache_db(
            cache_db,
            [
                (
                    "https://db.netkeiba.com/race/202601010101/",
                    _finish_result_html("20260101", "東京", "1", "2021100001", "Alpha", "1", "0.0", "1:34.5"),
                ),
                (
                    "https://db.netkeiba.com/race/202601010102/",
                    _low_quality_html("20260101", "東京", "2021100002", "Beta"),
                ),
                (
                    "https://db.netkeiba.com/race/202601010201/",
                    _finish_result_html("20260101", "東京", "1", "2021100201", "Gamma", "1", "0.1", "1:34.6"),
                ),
                (
                    "https://db.netkeiba.com/race/202601010401/",
                    _broken_html(),
                ),
                (
                    "https://db.netkeiba.com/race/202601010301/",
                    _finish_result_html("20260101", "東京", "2", "2021100301", "Delta", "2", "0.2", "1:35.0"),
                ),
            ],
        )
        _write_pedigree_cache_db(
            ped_db,
            [
                ("2021100301", "SireA", "DamA", "DamsireA"),
            ],
        )
        plan_json.write_text(json.dumps(_plan_fixture(), ensure_ascii=False, indent=2), encoding="utf-8")

        cache_before = cache_db.stat().st_mtime_ns
        ped_before = ped_db.stat().st_mtime_ns
        rc, payload = _run_plan(plan_json, cache_db, ped_db, out_json)
        cache_after = cache_db.stat().st_mtime_ns
        ped_after = ped_db.stat().st_mtime_ns

    detail = payload.get("_detail") if isinstance(payload.get("_detail"), dict) else {}
    sample_diffs = detail.get("sample_diffs") if isinstance(detail.get("sample_diffs"), list) else []

    def _has(action: str, column: str | None = None) -> bool:
        for item in sample_diffs:
            if not isinstance(item, dict):
                continue
            if str(item.get("action")) != action:
                continue
            if column is not None and str(item.get("column")) != column:
                continue
            return True
        return False

    checks = {
        "would_fix_from_cache": _has("would-fix-from-cache", "finish_position"),
        "cache_missing": _has("cache-missing", "finish_position"),
        "reparse_failed": _has("reparse-failed", "finish_position"),
        "no_downgrade_skip": _has("no-downgrade-skip"),
        "http_request_zero": int(detail.get("estimated_http_request_count") or 0) == 0,
        "db_write_zero": bool(cache_before == cache_after and ped_before == ped_after),
        "safeguards_present": bool(isinstance(detail.get("safeguards"), dict) and detail["safeguards"].get("read_only") is True),
    }

    success = bool(rc == 0 and all(checks.values()))
    output = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "success": success,
        "verdict": "pass" if success else "fail",
        "verdict_reason": "ok" if success else "p0-reparse-cache-smoke-failed",
        "checks": checks,
        "plan_result": {"return_code": rc, **payload},
        "run_id": int(time.time()),
    }
    result_file.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"result_file": str(result_file), "success": success, "checks": checks}, ensure_ascii=False))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
