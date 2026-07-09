#!/usr/bin/env python3
"""Smoke test for plan_p0_targeted_refetch.py using temporary SQLite fixtures."""

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
PLAN_SCRIPT = ROOT_DIR / "scripts" / "plan_p0_targeted_refetch.py"


def _write_main_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE races_ultimate (race_id TEXT PRIMARY KEY, data TEXT NOT NULL)")
    conn.execute("CREATE TABLE race_results_ultimate (race_id TEXT, data TEXT NOT NULL, created_at TEXT)")
    conn.execute("CREATE TABLE entries (race_id TEXT, horse_id TEXT, horse_name TEXT, horse_no TEXT, bracket TEXT, odds REAL, popularity INTEGER, weight REAL, jockey_name TEXT, trainer_name TEXT, created_at TEXT)")

    def race_row(race_id: str) -> str:
        return json.dumps({"race_id": race_id, "date": "20260101", "venue": "東京"}, ensure_ascii=False)

    conn.execute("INSERT INTO races_ultimate VALUES (?, ?)", ("202601010900", race_row("202601010900")))
    conn.execute("INSERT INTO races_ultimate VALUES (?, ?)", ("202601010901", race_row("202601010901")))
    conn.execute("INSERT INTO races_ultimate VALUES (?, ?)", ("202601010902", race_row("202601010902")))
    conn.execute("INSERT INTO races_ultimate VALUES (?, ?)", ("202601010903", race_row("202601010903")))

    conn.execute(
        "INSERT INTO race_results_ultimate VALUES (?, ?, ?)",
        (
            "202601010900",
            json.dumps({"race_id": "202601010900", "horse_id": "2021100900", "horse_name": "Alpha", "frame_number": "1", "horse_number": "1", "finish_position": "1", "result_time": "1:34.5", "margin": "0.0"}, ensure_ascii=False),
            "2026-07-09 00:00:00",
        ),
    )
    conn.execute(
        "INSERT INTO race_results_ultimate VALUES (?, ?, ?)",
        (
            "202601010901",
            json.dumps({"race_id": "202601010901", "horse_id": "2021100901", "horse_name": "Beta", "frame_number": "2", "horse_number": "2", "finish_position": "", "result_time": "", "margin": ""}, ensure_ascii=False),
            "2026-07-09 00:00:00",
        ),
    )
    conn.execute(
        "INSERT INTO race_results_ultimate VALUES (?, ?, ?)",
        (
            "202601010902",
            json.dumps({"race_id": "202601010902", "horse_id": "2021100902", "horse_name": "", "frame_number": "", "horse_number": "", "finish_position": "", "result_time": "", "margin": ""}, ensure_ascii=False),
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
    cached_url = "https://db.netkeiba.com/race/202601010900/"
    conn.execute(
        "INSERT INTO http_cache VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cached_url, cached_url, 200, "{}", b"<html><body>cached</body></html>", time.time(), time.time() + 86400),
    )
    cached_missing_url = "https://db.netkeiba.com/race/202601010901/"
    conn.execute(
        "INSERT INTO http_cache VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cached_missing_url, cached_missing_url, 200, "{}", b"<html><body>cached missing finish position</body></html>", time.time(), time.time() + 86400),
    )
    conn.commit()
    conn.close()


def _write_pedigree_cache_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE pedigree_cache (horse_id TEXT PRIMARY KEY, sire TEXT, dam TEXT, damsire TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.commit()
    conn.close()


def _audit_fixture() -> dict[str, Any]:
    return {
        "column_missingness": [
            {"column": "finish_position", "true_missing_count": 2},
            {"column": "horse_name", "true_missing_count": 1},
            {"column": "frame_number", "true_missing_count": 1},
            {"column": "horse_number", "true_missing_count": 1},
        ],
        "repair_reason_breakdown": [
            {"reason": "consistency:race_without_horse_data", "column": "(check)", "count": 2},
        ],
    }


def _plan_fixture() -> dict[str, Any]:
    return {
        "p0_total_count": 35,
        "p0_action_breakdown": [
            {"action": "schema-review", "column": "race_number", "count": 8},
            {"action": "no-action-domain-allowed", "column": "margin", "count": 5},
            {"action": "reparse-cache", "column": "finish_position", "count": 2},
            {"action": "refetch-required", "column": "horse_name", "count": 1},
            {"action": "refetch-required", "column": "frame_number", "count": 1},
            {"action": "refetch-required", "column": "horse_number", "count": 1},
            {"action": "repair-from-existing-metadata", "column": "race_date", "count": 3},
            {"action": "repair-from-existing-metadata", "column": "venue", "count": 3},
        ],
        "sample_targets": [
            {"race_id": "202601010900", "horse_id": "2021100900", "column": "finish_position", "reason": "true-missing", "action": "reparse-cache", "priority": "P0", "source_hint": "race-result-cache", "recommended_next_action": "reparse-cache-first"},
            {"race_id": "202601010901", "horse_id": "2021100901", "column": "finish_position", "reason": "true-missing", "action": "reparse-cache", "priority": "P0", "source_hint": "race-result-cache", "recommended_next_action": "reparse-cache-first"},
            {"race_id": "202601010902", "horse_id": "2021100902", "column": "horse_name", "reason": "true-missing", "action": "refetch-required", "priority": "P1", "source_hint": "horse-row-required-field-missing", "recommended_next_action": "refetch-required"},
            {"race_id": "202601010902", "horse_id": "2021100902", "column": "frame_number", "reason": "true-missing", "action": "refetch-required", "priority": "P1", "source_hint": "horse-row-required-field-missing", "recommended_next_action": "refetch-required"},
            {"race_id": "202601010902", "horse_id": "2021100902", "column": "horse_number", "reason": "true-missing", "action": "refetch-required", "priority": "P1", "source_hint": "horse-row-required-field-missing", "recommended_next_action": "refetch-required"},
            {"race_id": None, "horse_id": None, "column": "(check)", "reason": "consistency:race_without_horse_data", "action": "refetch-required", "priority": "P0", "source_hint": "race-level-missing-horse-rows", "recommended_next_action": "refetch-required"},
            {"race_id": "202601011000", "horse_id": "2021101000", "column": "race_date", "reason": "true-missing", "action": "repair-from-existing-metadata", "priority": "P1", "source_hint": "recoverable-from-race-metadata", "recommended_next_action": "repair-from-existing-metadata"},
            {"race_id": "202601011001", "horse_id": "2021101001", "column": "race_number", "reason": "derived-field-candidate", "action": "schema-review", "priority": "Schema review", "source_hint": "schema/derived-candidate", "recommended_next_action": "review-schema-alias-derived-rules"},
            {"race_id": "202601011002", "horse_id": "2021101002", "column": "margin", "reason": "domain-allowed-missing", "action": "no-action-domain-allowed", "priority": "Domain allowed", "source_hint": "domain-allowed", "recommended_next_action": "no-action-monitor"},
        ],
    }


def _cache_diag_fixture() -> dict[str, Any]:
    return {
        "is_reparse_plan_sampled": True,
        "reparse_plan_total_count": 2,
        "p0_plan_total_count": 35,
        "sampled_target_count": 2,
        "full_target_count": 35,
    }


def _source_empty_diag_fixture() -> dict[str, Any]:
    return {
        "checked_count": 3,
        "domain_allowed_count": 2,
        "classification_breakdown": [
            {"classification": "domain-allowed-canceled", "count": 1},
            {"classification": "domain-allowed-excluded", "count": 1},
            {"classification": "source-result-missing", "count": 1},
        ],
    }


def _run_plan(audit_path: Path, p0_path: Path, cache_diag_path: Path, source_empty_diag_path: Path, db_path: Path, cache_db: Path, ped_db: Path, output_path: Path) -> tuple[int, dict[str, Any]]:
    proc = subprocess.run(
        [
            sys.executable,
            str(PLAN_SCRIPT),
            "--input-audit",
            str(audit_path),
            "--input-p0-plan",
            str(p0_path),
            "--input-cache-diagnosis",
            str(cache_diag_path),
            "--input-source-empty-diagnosis",
            str(source_empty_diag_path),
            "--db-path",
            str(db_path),
            "--cache-db",
            str(cache_db),
            "--pedigree-cache-db",
            str(ped_db),
            "--target",
            "all",
            "--max-targets",
            "10",
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


def main() -> int:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    result_file = REPORTS_DIR / "p0_targeted_refetch_plan_smoke_result.json"

    with tempfile.TemporaryDirectory(prefix="smoke_p0_targeted_refetch_") as td:
        tmp = Path(td)
        db_path = tmp / "keiba_ultimate.db"
        cache_db = tmp / "fetch_cache.db"
        ped_db = tmp / "pedigree_cache.db"
        audit_path = tmp / "audit.json"
        p0_path = tmp / "p0_plan.json"
        cache_diag_path = tmp / "cache_diag.json"
        source_empty_diag_path = tmp / "source_empty_diag.json"
        out_json = tmp / "refetch_plan.json"

        _write_main_db(db_path)
        _write_http_cache_db(cache_db)
        _write_pedigree_cache_db(ped_db)
        audit_path.write_text(json.dumps(_audit_fixture(), ensure_ascii=False, indent=2), encoding="utf-8")
        p0_path.write_text(json.dumps(_plan_fixture(), ensure_ascii=False, indent=2), encoding="utf-8")
        cache_diag_path.write_text(json.dumps(_cache_diag_fixture(), ensure_ascii=False, indent=2), encoding="utf-8")
        source_empty_diag_path.write_text(json.dumps(_source_empty_diag_fixture(), ensure_ascii=False, indent=2), encoding="utf-8")

        db_before = db_path.stat().st_mtime_ns
        cache_before = cache_db.stat().st_mtime_ns
        ped_before = ped_db.stat().st_mtime_ns
        rc, payload = _run_plan(audit_path, p0_path, cache_diag_path, source_empty_diag_path, db_path, cache_db, ped_db, out_json)
        db_after = db_path.stat().st_mtime_ns
        cache_after = cache_db.stat().st_mtime_ns
        ped_after = ped_db.stat().st_mtime_ns

    detail = payload.get("_detail") if isinstance(payload.get("_detail"), dict) else {}
    sample_urls = detail.get("sample_urls") if isinstance(detail.get("sample_urls"), dict) else {}
    result_samples = sample_urls.get("result_page") if isinstance(sample_urls.get("result_page"), list) else []
    race_samples = sample_urls.get("race_detail") if isinstance(sample_urls.get("race_detail"), list) else []
    classification_rows = detail.get("classification_breakdown") if isinstance(detail.get("classification_breakdown"), list) else []
    classification_map = {
        str(x.get("classification") or ""): int(x.get("count") or 0)
        for x in classification_rows
        if isinstance(x, dict)
    }

    checks = {
        "finish_position_result_page_url": any(item.get("url_type") == "result_page" and item.get("column") == "finish_position" for item in result_samples),
        "race_without_horse_data_race_id_dedup": int(detail.get("race_detail_url_count") or 0) == 1,
        "schema_review_excluded": int(detail.get("excluded_schema_review_count") or 0) > 0,
        "domain_allowed_excluded": int(detail.get("excluded_domain_allowed_count") or 0) > 0,
        "cache_exclusion_metric_present": int(detail.get("excluded_cache_available_count") or 0) >= 0,
        "source_empty_classified": int(classification_map.get("source-empty-result-cells", 0)) > 0,
        "source_empty_excluded_from_refetch": int(detail.get("excluded_source_empty_result_cells_count") or 0) > 0,
        "http_access_zero": bool(detail.get("safety_flags", {}).get("no_http_access")),
        "db_write_zero": bool(db_before == db_after and cache_before == cache_after and ped_before == ped_after),
        "race_detail_sample_present": bool(race_samples),
    }

    success = bool(rc == 0 and all(checks.values()))
    output = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "success": success,
        "verdict": "pass" if success else "fail",
        "verdict_reason": "ok" if success else "p0-targeted-refetch-plan-smoke-failed",
        "checks": checks,
        "run_result": {"return_code": rc, **payload},
    }
    result_file.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"result_file": str(result_file), "success": success, "checks": checks}, ensure_ascii=False))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())