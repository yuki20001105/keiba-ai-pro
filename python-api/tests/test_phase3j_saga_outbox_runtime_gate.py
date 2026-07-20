from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_phase3j_saga_outbox_runtime.py"
CONTRACT_PATH = ROOT / "python-api" / "tests" / "fixtures" / "phase3j_saga_outbox_failure_matrix_v1.json"
PHASE3I_CONTRACT_PATH = ROOT / "python-api" / "tests" / "fixtures" / "phase3i_saga_failure_matrix_v1.json"
PHASE3I_EVIDENCE_PATH = ROOT / "python-api" / "tests" / "fixtures" / "phase3i_saga_failure_injection_synthetic_compatible.json"
MIGRATION = ROOT / "supabase" / "migrations" / "20260720_scrape_execution_reservation.sql"
STORE = ROOT / "python-api" / "scraping" / "cross_store_saga_store.py"
TESTED_COMMIT = subprocess.run(
    ["git", "-C", str(ROOT), "rev-parse", "--verify", "HEAD"],
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="strict",
    check=True,
    shell=False,
).stdout.strip().lower()
FIXED_NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)

SPEC = importlib.util.spec_from_file_location("phase3j_runtime_gate", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def _contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _phase3h() -> dict:
    phase3h_spec = importlib.util.spec_from_file_location(
        "phase3j_phase3h_test", ROOT / "scripts" / "verify_phase3h_production_readiness.py"
    )
    assert phase3h_spec is not None and phase3h_spec.loader is not None
    phase3h = importlib.util.module_from_spec(phase3h_spec)
    phase3h_spec.loader.exec_module(phase3h)
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
            "migration_sha256": "a" * 64,
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


def _phase3i() -> dict:
    spec = importlib.util.spec_from_file_location(
        "phase3j_phase3i_test", ROOT / "scripts" / "verify_phase3i_saga_failure_injection.py"
    )
    assert spec is not None and spec.loader is not None
    phase3i = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(phase3i)
    evidence = json.loads(PHASE3I_EVIDENCE_PATH.read_text(encoding="utf-8"))
    evidence["tested_commit_sha"] = TESTED_COMMIT
    evidence["observed_at"] = FIXED_NOW.isoformat().replace("+00:00", "Z")
    contract = json.loads(PHASE3I_CONTRACT_PATH.read_text(encoding="utf-8"))
    return phase3i.build_report(
        evidence,
        contract,
        _phase3h(),
        expected_commit=TESTED_COMMIT,
        now=FIXED_NOW,
    )


def _evidence() -> dict:
    contract = _contract()
    runtime_hashes = {path: gate._file_sha256(ROOT / path) for path in gate.RUNTIME_ASSETS}
    schema_hash = gate._schema_sha256(STORE)
    assert schema_hash is not None
    assert all(runtime_hashes.values())
    return {
        "schema_version": 1,
        "evidence_mode": "disposable-runtime",
        "environment": "ci-disposable",
        "database_scope": "temporary-sqlite-and-disposable-postgres",
        "network_mode": "container-none",
        "image": gate.POSTGRES_IMAGE,
        "host_port_published": False,
        "external_credentials_used": False,
        "tested_commit_sha": TESTED_COMMIT,
        "contract_sha256": gate._canonical_json_sha256(contract),
        "migration_sha256": gate._file_sha256(MIGRATION),
        "schema_sha256": schema_hash,
        "runtime_asset_sha256": runtime_hashes,
        "observed_at": FIXED_NOW.isoformat().replace("+00:00", "Z"),
        "success": True,
        "production_ready": False,
        "l3_eligible": False,
        "external_migration_applied": False,
        "scenario_count": len(gate.SCENARIO_KEYS),
        "scenario_checks": {key: True for key in gate.SCENARIO_KEYS},
        "invariant_checks": {key: True for key in gate.INVARIANT_KEYS},
        "operational_effect_count": 0,
        "worker_dispatch_count": 0,
        "operational_effects": {key: 0 for key in gate.OPERATIONAL_EFFECT_KEYS},
        "disposable_database_effect_count": 37,
        "disposable_database_effects": {"sqlite_writes": 20, "postgres_writes": 17},
        "cleanup": {"attempted": True, "container_absent": True, "workspace_absent": True},
    }


def _report(
    evidence: object | None = None,
    contract: object | None = None,
    phase3h: object | None = None,
    phase3i: object | None = None,
    **kwargs: object,
) -> dict:
    return gate.build_report(
        _evidence() if evidence is None else evidence,
        _contract() if contract is None else contract,
        _phase3h() if phase3h is None else phase3h,
        _phase3i() if phase3i is None else phase3i,
        expected_commit=kwargs.pop("expected_commit", TESTED_COMMIT),
        migration_path=kwargs.pop("migration_path", MIGRATION),
        store_path=kwargs.pop("store_path", STORE),
        now=kwargs.pop("now", FIXED_NOW),
        **kwargs,
    )


def test_compatible_disposable_runtime_is_successful_but_not_ready_or_l3() -> None:
    report = _report()
    assert frozenset(report) == gate.GATE_REPORT_KEYS
    assert report["success"] is True
    assert report["verdict"] == "not-ready"
    assert report["verdict_reason"] == "disposable-saga-outbox-runtime-compatible"
    assert report["production_ready"] is False
    assert report["l3_eligible"] is False
    assert report["phase3h_evidence_valid"] is True
    assert report["phase3i_evidence_valid"] is True
    assert report["failure_codes"] == []


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


@pytest.mark.parametrize("key", gate.OPERATIONAL_EFFECT_KEYS)
def test_any_operational_effect_is_release_blocking(key: str) -> None:
    evidence = _evidence()
    evidence["operational_effects"][key] = 1
    evidence["operational_effect_count"] = 1
    if key == "worker_dispatch":
        evidence["worker_dispatch_count"] = 1
    report = _report(evidence)
    assert report["success"] is False
    assert "operational-effect-observed" in report["failure_codes"]
    assert report["production_ready"] is False
    assert report["l3_eligible"] is False


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("host_port_published", True),
        ("external_credentials_used", True),
        ("external_migration_applied", True),
        ("production_ready", True),
        ("l3_eligible", True),
        ("network_mode", "bridge"),
        ("database_scope", "production"),
    ],
)
def test_runtime_cannot_promote_or_escape_disposable_boundary(key: str, value: object) -> None:
    evidence = _evidence()
    evidence[key] = value
    report = _report(evidence)
    assert report["success"] is False
    assert report["production_ready"] is False
    assert report["l3_eligible"] is False


def test_disposable_database_effects_are_separate_exact_and_nonzero() -> None:
    evidence = _evidence()
    evidence["disposable_database_effect_count"] = 0
    evidence["disposable_database_effects"] = {"sqlite_writes": 0, "postgres_writes": 0}
    assert "disposable-database-effects-invalid" in _report(evidence)["failure_codes"]
    evidence = _evidence()
    evidence["disposable_database_effect_count"] += 1
    assert "disposable-database-effects-invalid" in _report(evidence)["failure_codes"]


def test_hash_commit_freshness_and_cleanup_are_release_blocking() -> None:
    evidence = _evidence()
    evidence["tested_commit_sha"] = "2" * 40
    assert "tested-commit-mismatch" in _report(evidence)["failure_codes"]
    for key, failure in (
        ("contract_sha256", "contract-sha256-mismatch"),
        ("migration_sha256", "migration-sha256-mismatch"),
        ("schema_sha256", "schema-sha256-mismatch"),
    ):
        evidence = _evidence()
        evidence[key] = "0" * 64
        assert failure in _report(evidence)["failure_codes"]
    evidence = _evidence()
    evidence["runtime_asset_sha256"][gate.RUNTIME_ASSETS[0]] = "0" * 64
    assert "runtime-asset-sha256-mismatch" in _report(evidence)["failure_codes"]
    evidence = _evidence()
    evidence["observed_at"] = "2026-07-19T00:00:00Z"
    assert "stale-or-future-evidence" in _report(evidence, max_age_seconds=60)["failure_codes"]
    evidence = _evidence()
    evidence["cleanup"]["container_absent"] = False
    assert "cleanup-invalid" in _report(evidence)["failure_codes"]


def test_verifier_binds_expected_commit_to_actual_checkout_head() -> None:
    assert gate._checkout_head_failure(TESTED_COMMIT, ROOT) is None
    forged = "0" * 40 if TESTED_COMMIT != "0" * 40 else "1" * 40
    assert gate._checkout_head_failure(forged, ROOT) == "checkout-head-mismatch"


def test_unknown_fields_and_unsafe_content_fail_closed_without_leaking() -> None:
    evidence = _evidence()
    evidence["unexpected"] = True
    assert "evidence-schema-invalid" in _report(evidence)["failure_codes"]
    evidence = _evidence()
    evidence["scenario_checks"][gate.SCENARIO_KEYS[0]] = "postgresql://user:password@host/db"
    report = _report(evidence)
    rendered = json.dumps(report, sort_keys=True)
    assert report["success"] is False
    assert "postgresql://" not in rendered
    assert "password@host" not in rendered
    assert "scenario_checks" not in report


def test_same_run_phase3h_and_phase3i_evidence_are_both_release_blocking() -> None:
    phase3h = _phase3h()
    phase3h["evaluated_commit_sha"] = "2" * 40
    assert "phase3h-not-ready-evidence-invalid" in _report(phase3h=phase3h)["failure_codes"]
    phase3i = _phase3i()
    phase3i["evaluated_commit_sha"] = "2" * 40
    assert "phase3i-not-ready-evidence-invalid" in _report(phase3i=phase3i)["failure_codes"]
    phase3i = _phase3i()
    phase3i["checks"]["effect_count_zero"] = False
    assert "phase3i-not-ready-evidence-invalid" in _report(phase3i=phase3i)["failure_codes"]


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
    evidence = _evidence()
    evidence["observed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    evidence_path = tmp_path / "evidence.json"
    phase3h_path = tmp_path / "phase3h.json"
    phase3i_path = tmp_path / "phase3i.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    phase3h_path.write_text(json.dumps(_phase3h()), encoding="utf-8")
    phase3i_path.write_text(json.dumps(_phase3i()), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable, str(SCRIPT), "--evidence", str(evidence_path), "--contract", str(CONTRACT_PATH),
            "--phase3h-evidence", str(phase3h_path), "--phase3i-evidence", str(phase3i_path),
            "--expected-commit", TESTED_COMMIT, "--max-age-seconds", "86400",
        ],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(gate.REPORT_PATH.read_text(encoding="utf-8"))
    assert report["success"] is True
    assert report["verdict"] == "not-ready"
    assert report["production_ready"] is False
    assert report["l3_eligible"] is False
    assert list(gate.REPORT_PATH.parent.glob(f".{gate.REPORT_PATH.name}.*.tmp")) == []


def test_verifier_has_no_operational_clients() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in (
        "import requests", "import httpx", "import socket", "from supabase", "import supabase",
        "routers.scrape", "scraping.jobs",
    ):
        assert forbidden not in source


def test_ci_keeps_phase3j_release_blocking_topology_and_artifact_budget() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    all_jobs = workflow["jobs"]
    jobs = {
        name: job
        for name, job in all_jobs.items()
        if name != "dependency-security-release-blocking"
    }
    assert len(jobs) == 11
    assert len(all_jobs) == 12
    assert all_jobs["dependency-security-release-blocking"]["name"] == (
        "Dependency security (release-blocking)"
    )
    phase3j = jobs["phase3j-saga-outbox-runtime"]
    assert set(phase3j["needs"]) == {
        "phase3h-production-readiness",
        "phase3i-saga-failure-injection",
    }
    phase3j_steps = phase3j["steps"]
    assert any(
        step.get("uses") == "actions/download-artifact@v4"
        and step.get("with", {}).get("name") == "phase3h-production-readiness-json"
        for step in phase3j_steps
    )
    assert any(
        step.get("uses") == "actions/download-artifact@v4"
        and step.get("with", {}).get("name") == "phase3i-saga-failure-injection-json"
        for step in phase3j_steps
    )
    verifier_steps = [step for step in phase3j_steps if "verify_phase3j_saga_outbox_runtime.py" in step.get("run", "")]
    assert len(verifier_steps) == 1
    assert verifier_steps[0].get("if") == "always()"
    upload_names = [
        step.get("with", {}).get("name")
        for job in all_jobs.values()
        for step in job.get("steps", [])
        if step.get("uses") == "actions/upload-artifact@v4"
    ]
    assert upload_names == [
        "dependency-security-reports",
        "contract-smoke-aux-json",
        "contract-gate-json",
        "playwright-public-fixture-smoke",
        "security-scanner-reports",
        "phase3g-review-ledger-runtime-json",
        "phase3h-production-readiness-json",
        "phase3i-saga-failure-injection-json",
        "phase3j-saga-outbox-runtime-json",
        "phase3m-supabase-bootstrap-json",
    ]
