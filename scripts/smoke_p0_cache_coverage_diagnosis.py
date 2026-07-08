#!/usr/bin/env python3
"""Smoke test for diagnose_p0_cache_coverage.py using temporary read-only fixtures."""

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
SCRIPT_PATH = ROOT_DIR / "scripts" / "diagnose_p0_cache_coverage.py"


def _write_main_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE races_ultimate (race_id TEXT PRIMARY KEY, data TEXT NOT NULL)")
    conn.execute("CREATE TABLE race_results_ultimate (race_id TEXT, data TEXT NOT NULL, created_at TEXT)")
    conn.execute("CREATE TABLE entries (race_id TEXT, horse_id TEXT, horse_name TEXT, horse_no TEXT, bracket TEXT, odds REAL, popularity INTEGER, weight REAL, jockey_name TEXT, trainer_name TEXT, created_at TEXT)")

    def race_row(race_id: str, date_txt: str, venue: str) -> str:
        return json.dumps({"race_id": race_id, "date": date_txt, "venue": venue, "race_name": "Smoke Cup", "track_type": "turf", "distance": 1600, "surface": "turf", "race_class": "Open"}, ensure_ascii=False)

    conn.execute("INSERT INTO races_ultimate VALUES (?, ?)", ("202601010101", race_row("202601010101", "20260101", "Tokyo")))
    conn.execute("INSERT INTO races_ultimate VALUES (?, ?)", ("202601010102", race_row("202601010102", "20260101", "Tokyo")))
    conn.execute("INSERT INTO races_ultimate VALUES (?, ?)", ("202601010201", race_row("202601010201", "20260101", "Tokyo")))
    conn.execute("INSERT INTO races_ultimate VALUES (?, ?)", ("202601010401", race_row("202601010401", "20260101", "Tokyo")))

    conn.execute(
        "INSERT INTO race_results_ultimate VALUES (?, ?, ?)",
        (
            "202601010101",
            json.dumps({"race_id": "202601010101", "horse_id": "2021100001", "horse_name": "Alpha", "finish_position": "1", "result_time": "1:34.5", "margin": "0.0", "race_date": "20260101", "venue": "Tokyo"}, ensure_ascii=False),
            "2026-07-09 00:00:00",
        ),
    )
    conn.commit()
    conn.close()


def _write_http_cache_db(path: Path) -> None:
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

    race_html = """
    <html><body>
    <table class='race_table_01'>
    <tr><th>着順</th><th>枠番</th><th>馬番</th><th>馬名</th><th>性齢</th><th>斤量</th><th>騎手</th><th>タイム</th><th>着差</th></tr>
    <tr><td>1</td><td>1</td><td>1</td><td><a href='/horse/result/2021100001/'>Alpha</a></td><td>牡3</td><td>56.0</td><td>J1</td><td>1:34.5</td><td>0.0</td></tr>
    </table>
    </body></html>
    """
    race_html_missing_rows = """
    <html><body>
    <table class='race_table_01'>
    <tr><th>着順</th><th>枠番</th><th>馬番</th><th>馬名</th><th>性齢</th><th>斤量</th><th>騎手</th><th>タイム</th><th>着差</th></tr>
    <tr><td></td><td>1</td><td>1</td><td><a href='/horse/result/2021100201/'>Beta</a></td><td>牡3</td><td>56.0</td><td>J2</td><td></td><td></td></tr>
    </table>
    </body></html>
    """
    horse_html = """
    <html><body><h1>Delta</h1><table class='blood_table'><tr><td><a>SireA</a></td></tr><tr><td><a>DamA</a></td><td><a>DamsireA</a></td></tr></table></body></html>
    """

    rows = [
        ("https://db.netkeiba.com/race/202601010101/", race_html),
        ("https://db.netkeiba.com/race/202601010201/", race_html_missing_rows),
        ("https://db.netkeiba.com/horse/result/2021100301/", horse_html),
    ]
    for url, html in rows:
        conn.execute(
            "INSERT INTO http_cache VALUES (?, ?, ?, ?, ?, ?, ?)",
            (url, url, 200, "{}", html.encode("utf-8"), time.time(), time.time() + 86400),
        )
    conn.commit()
    conn.close()


def _write_pedigree_cache_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE pedigree_cache (horse_id TEXT PRIMARY KEY, sire TEXT, dam TEXT, damsire TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("INSERT INTO pedigree_cache (horse_id, sire, dam, damsire) VALUES (?, ?, ?, ?)", ("2021100401", "SireA", "DamA", "DamsireA"))
    conn.commit()
    conn.close()


def _plan_fixture() -> dict[str, Any]:
    return {
        "p0_total_count": 9,
        "p0_action_breakdown": [
            {"action": "reparse-cache", "column": "finish_position", "count": 2},
            {"action": "refetch-required", "column": "(check)", "count": 1},
            {"action": "repair-from-existing-metadata", "column": "race_date", "count": 1},
            {"action": "schema-review", "column": "race_number", "count": 1},
            {"action": "manual-review", "column": "(check)", "count": 1},
        ],
        "sample_targets": [
            {"race_id": "202601010101", "horse_id": "2021100001", "column": "finish_position", "reason": "true-missing", "action": "reparse-cache", "priority": "P0", "source_hint": "race-result-cache", "recommended_next_action": "reparse-cache-first"},
            {"race_id": "202601010102", "horse_id": "2021100002", "column": "finish_position", "reason": "true-missing", "action": "reparse-cache", "priority": "P0", "source_hint": "race-result-cache", "recommended_next_action": "reparse-cache-first"},
            {"race_id": "202601010401", "horse_id": None, "column": "(check)", "reason": "consistency:race_without_horse_data", "action": "refetch-required", "priority": "P0", "source_hint": "race-level-missing-horse-rows", "recommended_next_action": "refetch-required"},
            {"race_id": "202601010301", "horse_id": "2021100301", "column": "horse_name", "reason": "true-missing", "action": "refetch-required", "priority": "P0", "source_hint": "horse-row-required-field-missing", "recommended_next_action": "refetch-required"},
            {"race_id": "202601010501", "horse_id": "2021100401", "column": "sire", "reason": "true-missing", "action": "reparse-cache", "priority": "P0", "source_hint": "pedigree-cache", "recommended_next_action": "reparse-cache-first"},
            {"race_id": "202601010201", "horse_id": "2021100201", "column": "race_date", "reason": "true-missing", "action": "repair-from-existing-metadata", "priority": "P0", "source_hint": "recoverable-from-race-metadata", "recommended_next_action": "repair-from-existing-metadata"},
            {"race_id": "202601010301", "horse_id": "2021100301", "column": "race_number", "reason": "true-missing", "action": "schema-review", "priority": "P0", "source_hint": "prefer-derived-or-schema-mapping", "recommended_next_action": "review-schema-alias-derived-rules"},
            {"race_id": None, "horse_id": None, "column": "(check)", "reason": "consistency:required_column_missing_rate_over_0pct", "action": "manual-review", "priority": "P1", "source_hint": "consistency-check", "recommended_next_action": "manual-review-required"},
            {"race_id": "202601010601", "horse_id": "2021100601", "column": "margin", "reason": "domain-allowed-missing", "action": "no-action-domain-allowed", "priority": "P0", "source_hint": "domain-allowed", "recommended_next_action": "no-action-monitor"},
        ],
    }


def _reparse_fixture() -> dict[str, Any]:
    return {
        "p0_total_count": 6,
        "cache_available_count": 4,
        "cache_missing_count": 2,
        "reparse_attempt_count": 4,
        "sample_diffs": [],
    }


def _run_script(audit_path: Path, p0_path: Path, reparse_path: Path, db_path: Path, cache_db: Path, ped_db: Path, output_path: Path) -> tuple[int, dict[str, Any]]:
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--input-audit",
            str(audit_path),
            "--input-p0-plan",
            str(p0_path),
            "--input-reparse-plan",
            str(reparse_path),
            "--db-path",
            str(db_path),
            "--cache-db",
            str(cache_db),
            "--pedigree-cache-db",
            str(ped_db),
            "--target",
            "all",
            "--output",
            str(output_path),
            "--max-targets",
            "120",
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


def main() -> int:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    result_file = REPORTS_DIR / "p0_cache_coverage_diagnosis_smoke_result.json"

    with tempfile.TemporaryDirectory(prefix="smoke_p0_cache_coverage_") as td:
        tmp = Path(td)
        db_path = tmp / "keiba_ultimate.db"
        cache_db = tmp / "fetch_cache.db"
        ped_db = tmp / "pedigree_cache.db"
        audit_path = tmp / "audit.json"
        p0_path = tmp / "p0_plan.json"
        reparse_path = tmp / "reparse_plan.json"
        out_json = tmp / "diagnosis.json"

        _write_main_db(db_path)
        _write_http_cache_db(cache_db)
        _write_pedigree_cache_db(ped_db)
        audit_path.write_text(json.dumps({
            "repair_reason_breakdown": [
                {"reason": "true-missing", "column": "finish_position", "required_level": "required_if_result", "count": 2, "priority": "P0", "example_keys": ["202601010101:2021100001", "202601010102:2021100002"]},
                {"reason": "consistency:race_without_horse_data", "column": "(check)", "required_level": "consistency", "count": 3, "priority": "P0", "example_keys": []},
                {"reason": "derived-field-candidate", "column": "race_number", "required_level": "required", "count": 1, "priority": "Schema review", "example_keys": ["202601010301:2021100301"]},
                {"reason": "domain-allowed-missing", "column": "margin", "required_level": "required_if_result", "count": 1, "priority": "Domain allowed", "example_keys": ["202601010601:2021100601"]},
            ],
            "column_missingness": [
                {"column": "finish_position", "true_missing_count": 2, "true_missing_example_keys": ["202601010101:2021100001", "202601010102:2021100002"]},
            ],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        p0_path.write_text(json.dumps(_plan_fixture(), ensure_ascii=False, indent=2), encoding="utf-8")
        reparse_path.write_text(json.dumps(_reparse_fixture(), ensure_ascii=False, indent=2), encoding="utf-8")

        db_before = db_path.stat().st_mtime_ns
        cache_before = cache_db.stat().st_mtime_ns
        ped_before = ped_db.stat().st_mtime_ns
        rc, payload = _run_script(audit_path, p0_path, reparse_path, db_path, cache_db, ped_db, out_json)
        db_after = db_path.stat().st_mtime_ns
        cache_after = cache_db.stat().st_mtime_ns
        ped_after = ped_db.stat().st_mtime_ns

    detail = payload.get("_detail") if isinstance(payload.get("_detail"), dict) else {}
    checks = {
        "reparse_is_sampled": bool(detail.get("is_reparse_plan_sampled")),
        "finish_position_cache_available_detected": int(detail.get("finish_position_cache_available_count") or 0) == 1,
        "finish_position_cache_missing_detected": int(detail.get("finish_position_cache_missing_count") or 0) >= 1,
        "race_without_horse_data_dedup_detected": int(detail.get("race_without_horse_data_cache_checked_count") or 0) >= 3,
        "race_without_horse_data_available_detected": int(detail.get("race_without_horse_data_cache_available_count") or 0) == 1,
        "race_without_horse_data_missing_detected": int(detail.get("race_without_horse_data_cache_missing_count") or 0) >= 2,
        "horse_cache_available_detected": int(detail.get("horse_detail_cache_available_count") or 0) == 1,
        "pedigree_cache_available_detected": int(detail.get("pedigree_cache_available_count") or 0) == 1,
        "http_access_zero": bool(detail.get("safeguards", {}).get("no_http_access")),
        "db_write_zero": bool(db_before == db_after and cache_before == cache_after and ped_before == ped_after),
    }
    success = bool(rc == 0 and all(checks.values()))
    output = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "success": success,
        "verdict": "pass" if success else "fail",
        "verdict_reason": "ok" if success else "p0-cache-coverage-diagnosis-smoke-failed",
        "checks": checks,
        "run_result": {"return_code": rc, **payload},
    }
    result_file.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"result_file": str(result_file), "success": success, "checks": checks}, ensure_ascii=False))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())