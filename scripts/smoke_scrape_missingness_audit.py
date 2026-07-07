from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT_DIR / "reports"
AUDIT_SCRIPT = ROOT_DIR / "scripts" / "audit_scrape_missingness.py"


def _run_audit(csv_path: Path, output_path: Path) -> tuple[int, dict[str, Any]]:
    proc = subprocess.run(
        [
            sys.executable,
            str(AUDIT_SCRIPT),
            "--input-csv",
            str(csv_path),
            "--target",
            "all",
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


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("rows required")

    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                columns.append(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _base_rows() -> list[dict[str, Any]]:
    return [
        {
            "race_id": "202601010101",
            "race_date": "2026-01-01",
            "venue": "東京",
            "race_number": "1",
            "horse_id": "2021100001",
            "horse_name": "A",
            "frame_number": "1",
            "horse_number": "1",
            "finish_position": "1",
            "result_time": "1:34.5",
            "margin": "",
            "odds": "2.5",
            "popularity": "1",
            "horse_weight": "480",
            "jockey": "J1",
            "trainer": "T1",
            "sire": "S1",
            "dam": "D1",
            "broodmare_sire": "B1",
            "race_type": "芝",
            "distance": "1600",
            "surface": "芝",
            "class": "1勝",
            "source_page_type": "result",
        },
        {
            "race_id": "202601010101",
            "race_date": "2026-01-01",
            "venue": "東京",
            "race_number": "1",
            "horse_id": "2021100002",
            "horse_name": "B",
            "frame_number": "1",
            "horse_number": "2",
            "finish_position": "2",
            "result_time": "1:34.7",
            "margin": "0.2",
            "odds": "4.0",
            "popularity": "2",
            "horse_weight": "470",
            "jockey": "J2",
            "trainer": "T2",
            "sire": "S2",
            "dam": "D2",
            "broodmare_sire": "B2",
            "race_type": "芝",
            "distance": "1600",
            "surface": "芝",
            "class": "1勝",
            "source_page_type": "result",
            "race_no": "1",
        },
    ]


def main() -> int:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    ts = int(time.time())
    result_file = REPORTS_DIR / "scrape_missingness_audit_smoke_result.json"

    with tempfile.TemporaryDirectory(prefix="smoke_missingness_") as td:
        tmp_dir = Path(td)

        good_csv = tmp_dir / "good.csv"
        bad_csv = tmp_dir / "bad.csv"
        good_out = tmp_dir / "good_result.json"
        bad_out = tmp_dir / "bad_result.json"

        good_rows = _base_rows()
        bad_rows = _base_rows()
        bad_rows[0]["race_id"] = ""

        _write_csv(good_csv, good_rows)
        _write_csv(bad_csv, bad_rows)

        good_rc, good_payload = _run_audit(good_csv, good_out)
        bad_rc, bad_payload = _run_audit(bad_csv, bad_out)

    def _col(payload: dict[str, Any], name: str) -> dict[str, Any]:
        for item in payload.get("column_missingness", []) if isinstance(payload, dict) else []:
            if str(item.get("column")) == name:
                return item
        return {}

    good_detail = good_payload.get("_detail") if isinstance(good_payload.get("_detail"), dict) else {}
    race_num_col = _col(good_detail, "race_number")
    margin_col = _col(good_detail, "margin")

    checks = {
        "good_fixture_pass": bool(good_rc == 0 and str(good_payload.get("verdict")) in ("pass", "warn")),
        "race_number_alias_or_derived_not_fail": bool(int(race_num_col.get("true_missing_count") or 0) == 0),
        "winner_margin_allowed_missing": bool(int(margin_col.get("domain_allowed_missing_count") or 0) >= 1),
        "bad_fixture_fail": bool(bad_rc != 0 and str(bad_payload.get("verdict")) == "fail"),
    }

    success = all(checks.values())
    output = {
        "timestamp": datetime_now_iso(),
        "success": success,
        "verdict": "pass" if success else "fail",
        "verdict_reason": "ok" if success else "missingness-audit-smoke-failed",
        "checks": checks,
        "good_result": {"return_code": good_rc, **good_payload},
        "bad_result": {"return_code": bad_rc, **bad_payload},
        "run_id": ts,
    }

    result_file.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"result_file": str(result_file), "success": success, "checks": checks}, ensure_ascii=False))

    return 0 if success else 1


def datetime_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


if __name__ == "__main__":
    raise SystemExit(main())
