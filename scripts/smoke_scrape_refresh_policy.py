from __future__ import annotations

import csv
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT_DIR / "reports"
PLAN_SCRIPT = ROOT_DIR / "scripts" / "plan_scrape_refresh.py"


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("rows are required")
    fields = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _run_plan(args: list[str]) -> tuple[int, dict[str, Any]]:
    proc = subprocess.run(
        [sys.executable, str(PLAN_SCRIPT), *args],
        cwd=str(ROOT_DIR),
        text=True,
        capture_output=True,
    )
    out = (proc.stdout or "").strip()
    payload: dict[str, Any] = {}
    if out:
        try:
            payload = json.loads(out.splitlines()[-1])
        except Exception:
            payload = {"raw_stdout": out[-1000:]}
    return proc.returncode, payload


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture_existing_rows() -> list[dict[str, Any]]:
    return [
        {
            "race_id": "202601010101",
            "race_date": "2026-01-01",
            "venue": "東京",
            "race_number": "1",
            "horse_id": "2021100001",
            "horse_name": "Alpha",
            "frame_number": "1",
            "horse_number": "1",
            "finish_position": "1",
            "result_time": "1:34.5",
            "margin": "0.0",
            "odds": "2.4",
            "popularity": "1",
            "horse_weight": "480",
            "jockey": "J1",
            "trainer": "T1",
            "sire": "S1",
            "dam": "D1",
            "broodmare_sire": "B1",
            "parser_version": "2.0.0",
            "fetched_at": "2026-07-01 10:00:00",
            "source_html_present": "true",
            "quality_score": "98",
            "record_hash": "hash-stable-1",
            "source_page_type": "result",
        },
        {
            "race_id": "202601010102",
            "race_date": "2026-01-01",
            "venue": "東京",
            "race_number": "2",
            "horse_id": "2021100002",
            "horse_name": "",
            "frame_number": "2",
            "horse_number": "2",
            "finish_position": "",
            "result_time": "",
            "margin": "",
            "odds": "3.1",
            "popularity": "2",
            "horse_weight": "470",
            "jockey": "J2",
            "trainer": "T2",
            "sire": "S2",
            "dam": "D2",
            "broodmare_sire": "B2",
            "parser_version": "2.0.0",
            "fetched_at": "2026-07-01 10:00:00",
            "source_html_present": "true",
            "quality_score": "70",
            "record_hash": "hash-repair-2",
            "source_page_type": "result",
        },
        {
            "race_id": "202601010103",
            "race_date": "2026-01-01",
            "venue": "東京",
            "race_number": "3",
            "horse_id": "2021100003",
            "horse_name": "Gamma",
            "frame_number": "3",
            "horse_number": "3",
            "finish_position": "3",
            "result_time": "1:35.0",
            "margin": "0.5",
            "odds": "10.2",
            "popularity": "8",
            "horse_weight": "460",
            "jockey": "J3",
            "trainer": "T3",
            "sire": "S3",
            "dam": "D3",
            "broodmare_sire": "B3",
            "parser_version": "1.0.0",
            "fetched_at": "2026-07-01 10:00:00",
            "source_html_present": "true",
            "quality_score": "90",
            "record_hash": "hash-old-parser-3",
            "source_page_type": "result",
        },
        {
            "race_id": "202601010104",
            "race_date": "2026-01-01",
            "venue": "東京",
            "race_number": "",
            "horse_id": "2021100004",
            "horse_name": "Delta",
            "frame_number": "4",
            "horse_number": "4",
            "finish_position": "4",
            "result_time": "1:35.3",
            "margin": "0.8",
            "odds": "12.0",
            "popularity": "9",
            "horse_weight": "458",
            "jockey": "J4",
            "trainer": "T4",
            "sire": "S4",
            "dam": "D4",
            "broodmare_sire": "B4",
            "parser_version": "2.0.0",
            "fetched_at": "2026-07-01 10:00:00",
            "source_html_present": "true",
            "quality_score": "95",
            "record_hash": "hash-schema-review-4",
            "source_page_type": "result",
        },
    ]


def _fixture_candidate_rows() -> list[dict[str, Any]]:
    return [
        {
            "race_id": "202601010101",
            "race_date": "2026-01-01",
            "venue": "",
            "race_number": "1",
            "horse_id": "2021100001",
            "horse_name": "Alpha",
            "frame_number": "",
            "horse_number": "1",
            "finish_position": "",
            "result_time": "",
            "margin": "",
            "odds": "",
            "popularity": "",
            "horse_weight": "",
            "jockey": "",
            "trainer": "",
            "sire": "",
            "dam": "",
            "broodmare_sire": "",
            "parser_version": "2.0.0",
            "fetched_at": "2026-07-02 10:00:00",
            "source_html_present": "false",
            "quality_score": "20",
            "record_hash": "hash-candidate-low-1",
            "source_page_type": "result",
            "http_status": "503",
            "is_error_page": "true",
        }
    ]


def _create_ro_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE races_ultimate (race_id TEXT, data TEXT, created_at TEXT)")
    conn.execute("CREATE TABLE race_results_ultimate (race_id TEXT, data TEXT, created_at TEXT)")
    race_data = json.dumps({"race_id": "202601010101", "date": "2026-01-01", "venue": "東京"}, ensure_ascii=False)
    result_data = json.dumps(
        {
            "race_id": "202601010101",
            "race_date": "2026-01-01",
            "venue": "東京",
            "race_number": 1,
            "horse_id": "2021100001",
            "horse_name": "Alpha",
            "bracket_number": 1,
            "horse_number": 1,
            "finish_position": 1,
            "finish_time": "1:34.5",
            "margin": "0.0",
            "parser_version": "2.0.0",
            "created_at": "2026-07-01 10:00:00",
            "source_html": "<html>ok</html>",
        },
        ensure_ascii=False,
    )
    conn.execute("INSERT INTO races_ultimate (race_id, data, created_at) VALUES (?, ?, ?)", ("202601010101", race_data, "2026-07-01 10:00:00"))
    conn.execute("INSERT INTO race_results_ultimate (race_id, data, created_at) VALUES (?, ?, ?)", ("202601010101", result_data, "2026-07-01 10:00:00"))
    conn.commit()
    conn.close()


def main() -> int:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    result_file = REPORTS_DIR / "scrape_refresh_policy_smoke_result.json"

    with tempfile.TemporaryDirectory(prefix="smoke_refresh_policy_") as td:
        d = Path(td)
        existing_csv = d / "existing.csv"
        candidate_csv = d / "candidate.csv"
        out_repair = d / "plan_repair.json"
        out_reparse = d / "plan_reparse.json"
        out_force = d / "plan_force.json"
        ro_db = d / "ro_fixture.db"

        _write_csv(existing_csv, _fixture_existing_rows())
        _write_csv(candidate_csv, _fixture_candidate_rows())
        _create_ro_db(ro_db)

        rc_repair, _ = _run_plan([
            "--input-csv",
            str(existing_csv),
            "--policy",
            "repair-missing",
            "--target",
            "all",
            "--output",
            str(out_repair),
        ])
        repair_plan = _load_json(out_repair)

        rc_reparse, _ = _run_plan([
            "--input-csv",
            str(existing_csv),
            "--policy",
            "reparse-cache",
            "--target",
            "all",
            "--output",
            str(out_reparse),
            "--current-parser-version",
            "2.0.0",
        ])
        reparse_plan = _load_json(out_reparse)

        mtime_before = ro_db.stat().st_mtime_ns
        rc_force, _ = _run_plan([
            "--input-csv",
            str(existing_csv),
            "--candidate-csv",
            str(candidate_csv),
            "--policy",
            "force-refresh",
            "--target",
            "all",
            "--output",
            str(out_force),
        ])
        force_plan = _load_json(out_force)

        # Run one planning command against a real sqlite file and verify no write side effect.
        rc_db, _ = _run_plan([
            "--input-db",
            str(ro_db),
            "--policy",
            "repair-missing",
            "--target",
            "all",
            "--output",
            str(d / "plan_db.json"),
        ])
        mtime_after = ro_db.stat().st_mtime_ns

    checks = {
        "complete_existing_skip": bool(rc_repair == 0 and int(repair_plan.get("skip_count", 0)) >= 1),
        "required_missing_repair": bool(rc_repair == 0 and int(repair_plan.get("repair_count", 0)) >= 1),
        "schema_issue_is_schema_review": bool(rc_repair == 0 and int(repair_plan.get("schema_review_count", 0)) >= 1),
        "schema_issue_not_refetch": bool(rc_repair == 0 and int(repair_plan.get("schema_review_count", 0)) >= 1 and int(repair_plan.get("refetch_count", 0)) == 0),
        "repair_plan_breakdown_present": bool(isinstance(repair_plan.get("repair_plan_breakdown"), list) and len(repair_plan.get("repair_plan_breakdown", [])) > 0),
        "priority_counts_present": bool(isinstance(repair_plan.get("priority_counts"), dict) and "repair_count_total" in repair_plan.get("priority_counts", {})),
        "domain_allowed_count_present": bool(isinstance(repair_plan.get("priority_counts"), dict) and "domain_allowed_missing_count" in repair_plan.get("priority_counts", {})),
        "stale_parser_reparse": bool(rc_reparse == 0 and int(reparse_plan.get("reparse_count", 0)) >= 1),
        "lower_quality_no_downgrade_skip": bool(rc_force == 0 and int(force_plan.get("no_downgrade_skip_count", 0)) >= 1),
        "dry_run_no_db_write": bool(rc_db == 0 and mtime_before == mtime_after),
    }

    success = all(checks.values())
    payload = {
        "timestamp": datetime_now_iso(),
        "success": success,
        "verdict": "pass" if success else "fail",
        "verdict_reason": "ok" if success else "scrape-refresh-policy-smoke-failed",
        "checks": checks,
    }
    result_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"result_file": str(result_file), "success": success, "checks": checks}, ensure_ascii=False))
    return 0 if success else 1


def datetime_now_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
