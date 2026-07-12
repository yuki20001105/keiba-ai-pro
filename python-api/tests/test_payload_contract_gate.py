from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "reports" / "netkeiba_race_payload_contract_diff.json"
SCRIPT_PATH = ROOT / "scripts" / "compare_netkeiba_race_payload_contract.py"
FIXTURES = ROOT / "python-api" / "tests" / "fixtures"


def _run_compare_path(
    fixture_path: Path,
    *,
    skip_staleness_check: bool,
    stale_seconds: int,
) -> tuple[int, dict]:
    cmd = [
        sys.executable,
        str(SCRIPT_PATH),
        "--dry-run-report",
        str(fixture_path),
        "--stale-seconds",
        str(stale_seconds),
    ]
    if skip_staleness_check:
        cmd.append("--skip-staleness-check")

    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert REPORT_PATH.exists(), f"report not found: {REPORT_PATH}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    payload = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    return proc.returncode, payload


def _run_compare(fixture_name: str, *, skip_staleness_check: bool = True, stale_seconds: int = 86400) -> tuple[int, dict]:
    return _run_compare_path(
        FIXTURES / fixture_name,
        skip_staleness_check=skip_staleness_check,
        stale_seconds=stale_seconds,
    )


def _is_release_gate_pass(report: dict) -> bool:
    return bool(report.get("success")) and report.get("verdict") == "pass"


def test_compatible_fixture_is_pass() -> None:
    code, report = _run_compare("contract_dry_run_compatible.json")
    assert code == 0
    assert report.get("verdict") == "pass"
    assert report.get("verdict_reason") == "contracts-compatible"
    assert report["input"]["staleness_check_skipped"] is True
    assert _is_release_gate_pass(report) is True


def test_mismatch_fixture_is_warn_and_rejected_by_gate() -> None:
    code, report = _run_compare("contract_dry_run_mismatch.json")
    assert code == 0
    assert report.get("verdict") == "warn"
    assert report.get("verdict_reason") == "schema-mismatch"
    assert report["input"]["staleness_check_skipped"] is True
    assert _is_release_gate_pass(report) is False


def test_contract_error_fixture_is_fail_and_rejected_by_gate() -> None:
    code, report = _run_compare("contract_dry_run_error.json")
    assert code != 0
    assert report.get("verdict") == "fail"
    assert report.get("verdict_reason") == "contract-error"
    assert report["input"]["staleness_check_skipped"] is True
    assert _is_release_gate_pass(report) is False


def test_stale_fixture_without_skip_is_warn_and_rejected_by_gate(tmp_path: Path) -> None:
    base_fixture = FIXTURES / "contract_dry_run_compatible.json"
    stale_fixture = tmp_path / "contract_dry_run_stale.json"

    payload = json.loads(base_fixture.read_text(encoding="utf-8"))
    payload["timestamp"] = "1970-01-01T00:00:00+00:00"
    stale_fixture.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    code, report = _run_compare_path(
        stale_fixture,
        skip_staleness_check=False,
        stale_seconds=1,
    )

    assert code == 0
    assert report.get("verdict") == "warn"
    assert report.get("verdict_reason") == "stale-report"
    assert report["input"]["staleness_check_skipped"] is False
    assert _is_release_gate_pass(report) is False
