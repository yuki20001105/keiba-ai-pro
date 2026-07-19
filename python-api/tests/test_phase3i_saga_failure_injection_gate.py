from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_phase3i_saga_failure_injection.py"
EVIDENCE = ROOT / "python-api" / "tests" / "fixtures" / "phase3i_saga_failure_injection_synthetic_compatible.json"
CONTRACT = ROOT / "python-api" / "tests" / "fixtures" / "phase3i_saga_failure_matrix_v1.json"
REPORT = ROOT / "reports" / "phase3i_saga_failure_injection_gate.json"
TESTED_COMMIT = "1" * 40
FIXED_NOW = datetime(2026, 7, 20, 0, 5, tzinfo=timezone.utc)

SPEC = importlib.util.spec_from_file_location("phase3i_saga_failure_injection_gate", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def _evidence() -> dict:
    return json.loads(EVIDENCE.read_text(encoding="utf-8"))


def _contract() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def _phase3h() -> dict:
    phase3h = gate._load_phase3h_verifier()
    return {
        "report_schema": phase3h.REPORT_SCHEMA,
        "schema_version": phase3h.SCHEMA_VERSION,
        "success": True,
        "verdict": "not-ready",
        "verdict_reason": "production-readiness-prerequisites-incomplete",
        "production_ready": False,
        "l3_eligible": False,
        "readiness_required": False,
        "evaluated_commit_sha": TESTED_COMMIT,
        "phase3g_evidence": {
            "success": True,
            "tested_commit_sha": TESTED_COMMIT,
            "migration_sha256": "6abdb1ab1fa8bee0a50834c25080682fc00f1275e6cb2959b77e1ab2c9f9e2af",
            "synthetic": True,
            "l3_eligible": False,
        },
        "blockers": list(phase3h.ALL_BLOCKERS),
        "checks": {
            "manifest_schema": True,
            "expected_commit": True,
            "phase3g_runtime_evidence": True,
            "self_asserted_readiness_rejected": True,
            "production_promotion_policy": True,
        },
        "failure_codes": [],
    }


def _report(
    evidence: object | None = None,
    contract: object | None = None,
    phase3h: object | None = None,
    **kwargs: object,
) -> dict:
    return gate.build_report(
        _evidence() if evidence is None else evidence,
        _contract() if contract is None else contract,
        _phase3h() if phase3h is None else phase3h,
        expected_commit=kwargs.pop("expected_commit", TESTED_COMMIT),
        now=kwargs.pop("now", FIXED_NOW),
        **kwargs,
    )


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=True, allow_nan=False), encoding="utf-8")
    return path


def test_compatible_synthetic_contract_is_successful_but_not_ready_or_l3() -> None:
    report = _report()
    assert report["success"] is True
    assert report["verdict"] == "not-ready"
    assert report["verdict_reason"] == "synthetic-non-executable-saga-contract-compatible"
    assert report["production_ready"] is False
    assert report["l3_eligible"] is False
    assert report["checks"]["effect_count_zero"] is True
    assert report["checks"]["phase3h_not_ready"] is True
    assert report["failure_codes"] == []


@pytest.mark.parametrize("value", [1, -1, True, False, None, "0"])
def test_effect_count_must_be_exact_integer_zero(value: object) -> None:
    evidence = _evidence()
    evidence["effect_count"] = value
    report = _report(evidence)
    assert report["success"] is False
    assert "external-effect-observed" in report["failure_codes"]
    assert report["production_ready"] is False
    assert report["l3_eligible"] is False


@pytest.mark.parametrize("key", gate.SCENARIO_KEYS)
def test_every_scenario_is_release_blocking(key: str) -> None:
    evidence = _evidence()
    evidence["scenario_checks"][key] = False
    assert "scenario-checks-invalid" in _report(evidence)["failure_codes"]


@pytest.mark.parametrize("key", gate.INVARIANT_KEYS)
def test_every_invariant_is_release_blocking(key: str) -> None:
    evidence = _evidence()
    evidence["invariant_checks"][key] = False
    assert "invariant-checks-invalid" in _report(evidence)["failure_codes"]


def test_unknown_or_missing_schema_fields_fail_closed() -> None:
    evidence = _evidence()
    evidence["unexpected"] = True
    assert "evidence-schema-invalid" in _report(evidence)["failure_codes"]
    contract = _contract()
    contract.pop("forbidden_effects")
    assert "contract-schema-invalid" in _report(contract=contract)["failure_codes"]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("synthetic", False),
        ("non_executable", False),
        ("production_ready", True),
        ("l3_eligible", True),
        ("network_mode", "staging_only"),
        ("database_scope", "staging"),
    ],
)
def test_evidence_cannot_promote_its_runtime_boundary(key: str, value: object) -> None:
    evidence = _evidence()
    evidence[key] = value
    report = _report(evidence)
    assert report["success"] is False
    assert report["production_ready"] is False
    assert report["l3_eligible"] is False


def test_expected_commit_hash_freshness_and_cleanup_are_release_blocking() -> None:
    assert "expected-commit-required" in _report(expected_commit=None)["failure_codes"]

    evidence = _evidence()
    evidence["tested_commit_sha"] = "2" * 40
    assert "tested-commit-mismatch" in _report(evidence)["failure_codes"]

    evidence = _evidence()
    evidence["contract_sha256"] = "0" * 64
    assert "contract-sha256-mismatch" in _report(evidence)["failure_codes"]

    evidence = _evidence()
    evidence["observed_at"] = "2026-07-19T00:00:00Z"
    assert "stale-evidence" in _report(evidence, max_age_seconds=900)["failure_codes"]

    evidence = _evidence()
    evidence["cleanup"]["workspace_absent"] = False
    assert "cleanup-invalid" in _report(evidence)["failure_codes"]


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("success",), False),
        (("verdict",), "pass"),
        (("production_ready",), True),
        (("l3_eligible",), True),
        (("readiness_required",), True),
        (("evaluated_commit_sha",), "2" * 40),
        (("failure_codes",), ["hidden-failure"]),
        (("blockers",), []),
        (("checks", "phase3g_runtime_evidence"), False),
        (("phase3g_evidence", "synthetic"), False),
    ],
)
def test_phase3h_same_run_not_ready_evidence_is_exact(path: tuple[str, ...], value: object) -> None:
    phase3h = _phase3h()
    target = phase3h
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    report = _report(phase3h=phase3h)
    assert report["success"] is False
    assert "phase3h-not-ready-evidence-invalid" in report["failure_codes"]


def test_report_is_sanitized_and_does_not_copy_raw_evidence() -> None:
    evidence = _evidence()
    evidence["scenario_checks"]["failure_before_prepare"] = "postgresql://user:password@host/db"
    report = _report(evidence)
    rendered = json.dumps(report, sort_keys=True)
    assert report["success"] is False
    assert "postgresql://" not in rendered
    assert "password@host" not in rendered
    assert "scenario_checks" not in report


@pytest.mark.parametrize(
    ("content", "failure"),
    [
        (b'{"a":1,"a":2}', "evidence-duplicate-json-key"),
        (b'{"a":NaN}', "evidence-invalid-json"),
        (b"\xff", "evidence-invalid-utf8"),
        (b"", "evidence-file-empty"),
    ],
)
def test_loader_rejects_unsafe_json(tmp_path: Path, content: bytes, failure: str) -> None:
    path = tmp_path / "evidence.json"
    path.write_bytes(content)
    _, failures = gate.load_evidence(path)
    assert failure in failures


def test_cli_writes_atomic_sanitized_not_ready_report(tmp_path: Path) -> None:
    payload = _evidence()
    payload["observed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    evidence = _write(tmp_path / "evidence.json", payload)
    contract = _write(tmp_path / "contract.json", _contract())
    phase3h = _write(tmp_path / "phase3h.json", _phase3h())
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--evidence",
            str(evidence),
            "--contract",
            str(contract),
            "--phase3h-evidence",
            str(phase3h),
            "--expected-commit",
            TESTED_COMMIT,
            "--max-age-seconds",
            "86400",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["success"] is True
    assert report["verdict"] == "not-ready"
    assert report["production_ready"] is False
    assert report["l3_eligible"] is False
    assert list(REPORT.parent.glob(f".{REPORT.name}.*.tmp")) == []


def test_verifier_source_has_no_network_or_production_runtime_clients() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in (
        "import requests",
        "import httpx",
        "import socket",
        "from supabase",
        "import supabase",
        "routers.scrape",
        "scraping.jobs",
    ):
        assert forbidden not in source
