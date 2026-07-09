#!/usr/bin/env python3
"""Smoke test for diagnose_source_empty_result_cells.py using fixture cache HTML only."""

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
SCRIPT_PATH = ROOT_DIR / "scripts" / "diagnose_source_empty_result_cells.py"


def _write_cache_db(path: Path, pages: dict[str, str]) -> None:
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
    now = time.time()
    for url, html in pages.items():
        conn.execute(
            "INSERT INTO http_cache VALUES (?, ?, ?, ?, ?, ?, ?)",
            (url, url, 200, "{}", html.encode("utf-8"), now, now + 86400),
        )
    conn.commit()
    conn.close()


def _live_validation_fixture() -> dict[str, Any]:
    base = {
        "http_status": 200,
        "parse_status": "parse_success",
        "action": "source-empty-result-cells",
    }
    rows = [
        {
            **base,
            "url": "https://db.netkeiba.com/race/202601010201/",
            "url_type": "result_page",
            "race_id": "202601010201",
            "horse_id": "H001",
            "horse_number": "1",
            "horse_name": "Alpha",
        },
        {
            **base,
            "url": "https://db.netkeiba.com/race/202601010202/",
            "url_type": "result_page",
            "race_id": "202601010202",
            "horse_id": "H002",
            "horse_number": "2",
            "horse_name": "Beta",
        },
        {
            **base,
            "url": "https://db.netkeiba.com/race/202601010203/",
            "url_type": "result_page",
            "race_id": "202601010203",
            "horse_id": "H003",
            "horse_number": "3",
            "horse_name": "Gamma",
        },
        {
            **base,
            "url": "https://db.netkeiba.com/race/202601010204/",
            "url_type": "result_page",
            "race_id": "202601010204",
            "horse_id": "H004",
            "horse_number": "4",
            "horse_name": "Delta",
        },
        {
            **base,
            "url": "https://db.netkeiba.com/race/202601010205/",
            "url_type": "result_page",
            "race_id": "202601010205",
            "horse_id": "H005",
            "horse_number": "5",
            "horse_name": "Epsilon",
        },
        {
            **base,
            "url": "https://db.netkeiba.com/race/202601010206/",
            "url_type": "result_page",
            "race_id": "202601010206",
            "horse_id": "H006",
            "horse_number": "6",
            "horse_name": "Zeta",
        },
    ]
    return {
        "sample_results": rows,
        "safety_flags": {
            "no_db_write": True,
            "no_upsert": True,
            "no_repair_execute": True,
            "no_bulk_refetch": True,
        },
    }


def _html_pages() -> dict[str, str]:
    def page(target_horse_id: str, target_horse_no: str, target_horse_name: str, target_finish: str, target_time: str, target_margin: str, remarks: str, other_finish: str = "1") -> str:
        return f"""
        <html><body>
        <p class='smalltxt'>2026年1月1日 東京 1回1日目</p>
        <div>レース結果 払戻</div>
        <table class='race_table_01'>
          <tr><th>着順</th><th>枠番</th><th>馬番</th><th>馬名</th><th>タイム</th><th>着差</th><th>備考</th></tr>
          <tr><td>{other_finish}</td><td>1</td><td>9</td><td><a href='/horse/result/H999/'>Other</a></td><td>1:34.0</td><td>0.0</td><td></td></tr>
          <tr><td>{target_finish}</td><td>1</td><td>{target_horse_no}</td><td><a href='/horse/result/{target_horse_id}/'>{target_horse_name}</a></td><td>{target_time}</td><td>{target_margin}</td><td>{remarks}</td></tr>
        </table>
        </body></html>
        """

    canceled = page("H001", "1", "Alpha", "", "", "", "取消")
    excluded = page("H002", "2", "Beta", "", "", "", "除外")
    dnf = page("H003", "3", "Gamma", "", "", "", "競走中止")
    manual = page("H004", "4", "Delta", "", "", "", "")

    wrong_target = """
    <html><body>
    <p class='smalltxt'>2026年1月1日 東京 1回1日目</p>
    <div>レース結果 払戻</div>
    <table class='race_table_01'>
      <tr><th>着順</th><th>枠番</th><th>馬番</th><th>馬名</th><th>タイム</th><th>着差</th><th>備考</th></tr>
      <tr><td>1</td><td>1</td><td>9</td><td><a href='/horse/result/H999/'>Other</a></td><td>1:34.0</td><td>0.0</td><td></td></tr>
      <tr><td></td><td>1</td><td>5</td><td><a href='/horse/result/HWRONG/'>Epsilon</a></td><td></td><td></td><td></td></tr>
    </table>
    </body></html>
    """

    return {
        "https://db.netkeiba.com/race/202601010201/": canceled,
        "https://db.netkeiba.com/race/202601010202/": excluded,
        "https://db.netkeiba.com/race/202601010203/": dnf,
        "https://db.netkeiba.com/race/202601010204/": manual,
        "https://db.netkeiba.com/race/202601010205/": wrong_target,
    }


def _run_script(input_live: Path, cache_db: Path, output_json: Path) -> tuple[int, dict[str, Any]]:
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--input-live-validation",
            str(input_live),
            "--cache-db",
            str(cache_db),
            "--max-samples",
            "20",
            "--output",
            str(output_json),
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

    if output_json.exists():
        try:
            detail = json.loads(output_json.read_text(encoding="utf-8"))
            if isinstance(detail, dict):
                payload["_detail"] = detail
        except Exception:
            pass

    return proc.returncode, payload


def _count_classification(detail: dict[str, Any], name: str) -> int:
    rows = detail.get("classification_breakdown") if isinstance(detail.get("classification_breakdown"), list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("classification") or "") == name:
            return int(row.get("count") or 0)
    return 0


def main() -> int:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    result_file = REPORTS_DIR / "source_empty_result_cells_diagnosis_smoke_result.json"

    with tempfile.TemporaryDirectory(prefix="smoke_source_empty_diag_") as td:
        tmp = Path(td)
        input_live = tmp / "live_validation.json"
        cache_db = tmp / "fetch_cache.db"
        output_json = tmp / "diagnosis.json"

        input_live.write_text(json.dumps(_live_validation_fixture(), ensure_ascii=False, indent=2), encoding="utf-8")
        _write_cache_db(cache_db, _html_pages())

        cache_before = cache_db.stat().st_mtime_ns
        rc, payload = _run_script(input_live, cache_db, output_json)
        cache_after = cache_db.stat().st_mtime_ns

    detail = payload.get("_detail") if isinstance(payload.get("_detail"), dict) else {}
    checks = {
        "checked_count_is_6": int(detail.get("checked_count") or 0) == 6,
        "canceled_classified": _count_classification(detail, "domain-allowed-canceled") >= 1,
        "excluded_classified": _count_classification(detail, "domain-allowed-excluded") >= 1,
        "dnf_classified": _count_classification(detail, "domain-allowed-did-not-finish") >= 1,
        "manual_review_classified": _count_classification(detail, "manual-review-required") >= 1,
        "wrong_target_row_classified": _count_classification(detail, "wrong-target-row") >= 1,
        "cache_missing_classified": _count_classification(detail, "cache-missing") >= 1,
        "cache_missing_not_alternate_page_required": _count_classification(detail, "alternate-page-required") == 0,
        "http_access_zero": bool(detail.get("safety_flags", {}).get("no_http_access")),
        "db_write_zero": bool(cache_before == cache_after),
        "sample_diagnostics_present": bool(detail.get("sample_diagnostics")),
    }

    success = bool(rc == 0 and all(checks.values()))
    output = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "success": success,
        "verdict": "pass" if success else "fail",
        "verdict_reason": "ok" if success else "source-empty-result-cells-diagnosis-smoke-failed",
        "checks": checks,
        "run_result": {"return_code": rc, **payload},
    }
    result_file.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"result_file": str(result_file), "success": success, "checks": checks}, ensure_ascii=False))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
