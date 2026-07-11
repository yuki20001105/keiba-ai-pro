from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "reports" / "netkeiba_race_payload_contract_diff.json"
SCRIPT_PATH = ROOT / "scripts" / "compare_netkeiba_race_payload_contract.py"
FIXTURES = ROOT / "python-api" / "tests" / "fixtures"


def _run_compare(fixture_name: str) -> tuple[int, dict]:
    fixture_path = FIXTURES / fixture_name
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--dry-run-report",
            str(fixture_path),
            "--stale-seconds",
            "86400",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert REPORT_PATH.exists(), f"report not found: {REPORT_PATH}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    payload = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    return proc.returncode, payload


def _is_release_gate_pass(report: dict) -> bool:
    return bool(report.get("success")) and report.get("verdict") == "pass"


def test_compatible_fixture_is_pass() -> None:
    code, report = _run_compare("contract_dry_run_compatible.json")
    assert code == 0
    assert report.get("verdict") == "pass"
    assert report.get("verdict_reason") == "contracts-compatible"
    assert _is_release_gate_pass(report) is True


def test_mismatch_fixture_is_warn_and_rejected_by_gate() -> None:
    code, report = _run_compare("contract_dry_run_mismatch.json")
    assert code == 0
    assert report.get("verdict") == "warn"
    assert report.get("verdict_reason") == "schema-mismatch"
    assert _is_release_gate_pass(report) is False


def test_contract_error_fixture_is_fail_and_rejected_by_gate() -> None:
    code, report = _run_compare("contract_dry_run_error.json")
    assert code != 0
    assert report.get("verdict") == "fail"
    assert report.get("verdict_reason") == "contract-error"
    assert _is_release_gate_pass(report) is False
