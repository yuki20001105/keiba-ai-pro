from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_phase3g_runtime_evidence.py"
FIXTURE = ROOT / "python-api" / "tests" / "fixtures" / "phase3g_runtime_evidence_synthetic_compatible.json"
REPORT = ROOT / "reports" / "phase3g_runtime_evidence_gate.json"

SPEC = importlib.util.spec_from_file_location("phase3g_runtime_evidence_gate", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)

FIXED_NOW = datetime(2026, 7, 20, 0, 5, tzinfo=timezone.utc)
TESTED_COMMIT = "1" * 40


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _report(payload: object, **kwargs: object) -> dict:
    options = {
        "max_age_seconds": 900,
        "now": FIXED_NOW,
        "expected_commit": TESTED_COMMIT,
    }
    options.update(kwargs)
    return gate.build_report(payload, **options)


def _write_payload(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _run_cli(path: Path, *extra: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--evidence", str(path), *extra],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert REPORT.exists(), f"missing report; stdout={proc.stdout!r} stderr={proc.stderr!r}"
    return proc, json.loads(REPORT.read_text(encoding="utf-8"))


def test_compatible_synthetic_runtime_contract_passes_without_l3_eligibility() -> None:
    report = _report(_fixture())

    assert report["success"] is True
    assert report["verdict"] == "pass"
    assert report["verdict_reason"] == "synthetic-runtime-contract-compatible"
    assert report["l3_eligible"] is False
    assert report["evidence"]["database_scope"] == "disposable_docker"
    assert report["evidence"]["network_mode"] == "none"
    assert all(report["checks"].values())
    assert report["failure_codes"] == []


def test_staging_runtime_evidence_cannot_self_attest_l3() -> None:
    payload = _fixture()
    payload.update(
        {
            "evidence_mode": "staging",
            "environment": "staging",
            "synthetic": False,
            "l3_eligible": True,
            "database_scope": "staging",
            "network_mode": "staging_only",
            "image": "managed-postgres",
        }
    )

    report = _report(payload, expected_commit=TESTED_COMMIT)

    assert report["success"] is False
    assert report["l3_eligible"] is False
    assert "producer-l3-boundary-mismatch" in report["failure_codes"]


def test_staging_runtime_evidence_without_expected_commit_fails_closed() -> None:
    payload = _fixture()
    payload.update(
        {
            "evidence_mode": "staging",
            "environment": "staging",
            "synthetic": False,
            "l3_eligible": True,
            "database_scope": "staging",
            "network_mode": "staging_only",
            "image": "managed-postgres",
        }
    )

    report = _report(payload, expected_commit=None)

    assert report["success"] is False
    assert report["l3_eligible"] is False
    assert "expected-commit-required" in report["failure_codes"]


def test_synthetic_runtime_evidence_without_expected_commit_fails_closed() -> None:
    report = _report(_fixture(), expected_commit=None)

    assert report["success"] is False
    assert report["l3_eligible"] is False
    assert "expected-commit-required" in report["failure_codes"]


@pytest.mark.parametrize(
    ("group", "check_name"),
    [
        *[("catalog_checks", name) for name in gate.CATALOG_CHECK_KEYS],
        *[("behavioral_checks", name) for name in gate.BEHAVIORAL_CHECK_KEYS],
    ],
)
def test_every_required_runtime_check_is_release_blocking(group: str, check_name: str) -> None:
    payload = _fixture()
    payload[group][check_name] = False

    report = _report(payload)

    assert report["success"] is False
    assert report["l3_eligible"] is False
    prefix = "catalog" if group == "catalog_checks" else "behavioral"
    assert report["checks"][f"{prefix}.{check_name}"] is False
    assert f"{prefix}-check-failed:{check_name}" in report["failure_codes"]


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.update({"unknown": True}),
        lambda value: value["catalog_checks"].update({"unknown": True}),
        lambda value: value["behavioral_checks"].update({"unknown": True}),
        lambda value: value["cleanup"].update({"unknown": True}),
    ],
)
def test_unknown_fields_fail_closed(mutator) -> None:
    payload = _fixture()
    mutator(payload)

    report = _report(payload)

    assert report["success"] is False
    assert report["l3_eligible"] is False
    assert any(code.endswith("schema-mismatch") for code in report["failure_codes"])


def test_production_environment_is_rejected() -> None:
    payload = _fixture()
    payload["environment"] = "production"

    report = _report(payload)

    assert report["success"] is False
    assert report["evidence"]["environment"] is None
    assert "environment-mode-mismatch" in report["failure_codes"]


def test_stale_and_far_future_evidence_are_rejected() -> None:
    stale = _fixture()
    stale["observed_at"] = (FIXED_NOW - timedelta(seconds=901)).isoformat()
    future = _fixture()
    future["observed_at"] = (FIXED_NOW + timedelta(seconds=301)).isoformat()

    stale_report = _report(stale)
    future_report = _report(future)

    assert stale_report["success"] is False
    assert "stale-evidence" in stale_report["failure_codes"]
    assert future_report["success"] is False
    assert "observed-at-in-future" in future_report["failure_codes"]


def test_migration_hash_must_match_the_repository_asset_exactly() -> None:
    assert gate.expected_migration_sha256() == _fixture()["migration_sha256"]

    wrong_hash = _fixture()
    wrong_hash["migration_sha256"] = "0" * 64

    hash_report = _report(wrong_hash)

    assert "migration-sha256-mismatch" in hash_report["failure_codes"]


def test_migration_hash_is_stable_across_checkout_line_endings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lf_path = tmp_path / "migration-lf.sql"
    crlf_path = tmp_path / "migration-crlf.sql"
    changed_path = tmp_path / "migration-changed.sql"
    lf_path.write_bytes(b"SELECT 1;\nSELECT 2;\n")
    crlf_path.write_bytes(b"SELECT 1;\r\nSELECT 2;\r\n")
    changed_path.write_bytes(b"SELECT 1;\nSELECT 3;\n")

    monkeypatch.setattr(gate, "MIGRATION_PATH", lf_path)
    lf_hash = gate.expected_migration_sha256()
    monkeypatch.setattr(gate, "MIGRATION_PATH", crlf_path)
    assert gate.expected_migration_sha256() == lf_hash
    monkeypatch.setattr(gate, "MIGRATION_PATH", changed_path)
    assert gate.expected_migration_sha256() != lf_hash


@pytest.mark.parametrize(
    "unsafe_value",
    [
        "postgresql://user:password@example.invalid/database",
        "password" + "=" + "runtime-" + "secret-" + "value",
        "C:\\Users\\operator\\evidence.json",
        "/etc/passwd",
        "file:///tmp/evidence.json",
        "contains\x00control",
    ],
)
def test_secrets_dsns_absolute_paths_and_control_characters_are_rejected_without_echo(unsafe_value: str) -> None:
    payload = _fixture()
    payload["unexpected"] = unsafe_value

    report = _report(payload)
    serialized = json.dumps(report, ensure_ascii=False)

    assert report["success"] is False
    assert "prohibited-evidence-content" in report["failure_codes"]
    assert unsafe_value not in serialized


def test_raw_rows_are_rejected_and_never_projected() -> None:
    payload = _fixture()
    payload["raw_rows"] = [{"review_id": "sensitive-row"}]

    report = _report(payload)

    assert report["success"] is False
    assert "prohibited-evidence-content" in report["failure_codes"]
    assert "sensitive-row" not in json.dumps(report)


def test_secret_in_known_metadata_field_is_not_projected_to_report() -> None:
    secret = "password=" + "Y" * 32
    payload = _fixture()
    payload["image"] = secret

    report = _report(payload)
    serialized = json.dumps(report)

    assert report["success"] is False
    assert report["evidence"]["image"] is None
    assert secret not in serialized


def test_boolean_runtime_checks_are_strict_and_do_not_accept_integer_one() -> None:
    payload = _fixture()
    payload["catalog_checks"]["rls_enabled"] = 1

    report = _report(payload)

    assert report["success"] is False
    assert report["checks"]["catalog.rls_enabled"] is False


def test_schema_version_does_not_accept_boolean_true_as_integer_one() -> None:
    payload = _fixture()
    payload["schema_version"] = True

    report = _report(payload)

    assert report["success"] is False
    assert "schema-version-mismatch" in report["failure_codes"]


def test_synthetic_mode_requires_disposable_offline_boundary() -> None:
    payload = _fixture()
    payload["network_mode"] = "staging_only"

    report = _report(payload)

    assert report["success"] is False
    assert "evidence-mode-boundary-mismatch" in report["failure_codes"]


@pytest.mark.parametrize("field", ["attempted", "container_absent"])
def test_cleanup_is_release_blocking(field: str) -> None:
    payload = _fixture()
    payload["cleanup"][field] = False

    report = _report(payload)

    assert report["success"] is False
    assert report["checks"][f"cleanup.{field}"] is False
    assert f"cleanup-check-failed:{field}" in report["failure_codes"]


def test_synthetic_producer_cannot_claim_l3_and_must_report_success() -> None:
    claims_l3 = _fixture()
    claims_l3["l3_eligible"] = True
    producer_failed = _fixture()
    producer_failed["success"] = False

    l3_report = _report(claims_l3)
    failure_report = _report(producer_failed)

    assert "producer-l3-boundary-mismatch" in l3_report["failure_codes"]
    assert "producer-reported-failure" in failure_report["failure_codes"]


def test_staging_contract_can_pass_without_claiming_l3() -> None:
    payload = _fixture()
    payload.update(
        {
            "evidence_mode": "staging",
            "environment": "staging",
            "synthetic": False,
            "l3_eligible": False,
            "database_scope": "staging",
            "network_mode": "staging_only",
            "image": "managed-postgres",
        }
    )

    report = _report(payload, expected_commit=TESTED_COMMIT)

    assert report["success"] is True
    assert report["l3_eligible"] is False
    assert report["verdict_reason"] == "staging-runtime-contract-compatible-not-l3-eligible"


def test_duplicate_json_keys_and_oversized_inputs_fail_before_contract_validation(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema":"first","schema":"second"}', encoding="utf-8")
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"{" + b" " * gate.MAX_INPUT_BYTES + b"}")

    duplicate_payload, duplicate_failures = gate.load_evidence(duplicate)
    oversized_payload, oversized_failures = gate.load_evidence(oversized)

    assert duplicate_payload is None
    assert duplicate_failures == ["duplicate-json-key"]
    assert oversized_payload is None
    assert oversized_failures == ["evidence-file-too-large"]


def test_cli_writes_a_sanitized_pass_report_for_fresh_synthetic_evidence(tmp_path: Path) -> None:
    payload = _fixture()
    payload["observed_at"] = datetime.now(timezone.utc).isoformat()
    evidence_path = _write_payload(tmp_path, payload)

    proc, report = _run_cli(evidence_path, "--expected-commit", TESTED_COMMIT)

    assert proc.returncode == 0, proc.stderr
    assert report["success"] is True
    assert report["verdict"] == "pass"
    assert report["l3_eligible"] is False
    summary = json.loads(proc.stdout.strip().splitlines()[-1])
    assert summary["report"] == "reports/phase3g_runtime_evidence_gate.json"


def test_cli_exits_nonzero_and_sanitizes_invalid_evidence(tmp_path: Path) -> None:
    secret = "password=" + "X" * 32
    payload = _fixture()
    payload["observed_at"] = datetime.now(timezone.utc).isoformat()
    payload["raw_rows"] = [{"value": secret}]
    evidence_path = _write_payload(tmp_path, payload)

    proc, report = _run_cli(evidence_path, "--expected-commit", TESTED_COMMIT)
    serialized = REPORT.read_text(encoding="utf-8")

    assert proc.returncode != 0
    assert report["success"] is False
    assert report["l3_eligible"] is False
    assert secret not in serialized
    assert "raw_rows" not in serialized


def test_staging_cli_requires_and_matches_expected_commit(tmp_path: Path) -> None:
    payload = _fixture()
    payload.update(
        {
            "evidence_mode": "staging",
            "environment": "staging",
            "synthetic": False,
            "l3_eligible": False,
            "database_scope": "staging",
            "network_mode": "staging_only",
            "image": "managed-postgres",
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    evidence_path = _write_payload(tmp_path, payload)

    missing, missing_report = _run_cli(evidence_path)
    mismatch, mismatch_report = _run_cli(evidence_path, "--expected-commit", "2" * 40)
    matched, matched_report = _run_cli(evidence_path, "--expected-commit", TESTED_COMMIT)

    assert missing.returncode != 0
    assert "expected-commit-required" in missing_report["failure_codes"]
    assert mismatch.returncode != 0
    assert "tested-commit-mismatch" in mismatch_report["failure_codes"]
    assert matched.returncode == 0
    assert matched_report["l3_eligible"] is False
    assert matched_report["verdict_reason"] == "staging-runtime-contract-compatible-not-l3-eligible"
