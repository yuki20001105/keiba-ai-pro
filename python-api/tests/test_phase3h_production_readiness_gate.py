from __future__ import annotations

import copy
import ast
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_phase3h_production_readiness.py"
MANIFEST = ROOT / "python-api" / "tests" / "fixtures" / "phase3h_production_readiness_current_not_ready.json"
PHASE3G_FIXTURE = ROOT / "python-api" / "tests" / "fixtures" / "phase3g_runtime_evidence_synthetic_compatible.json"
REPORT = ROOT / "reports" / "phase3h_production_readiness_gate.json"
TESTED_COMMIT = "1" * 40
FIXED_NOW = datetime(2026, 7, 20, 0, 5, tzinfo=timezone.utc)

SPEC = importlib.util.spec_from_file_location("phase3h_production_readiness", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _phase3g() -> dict:
    payload = json.loads(PHASE3G_FIXTURE.read_text(encoding="utf-8"))
    payload["tested_commit_sha"] = TESTED_COMMIT
    payload["observed_at"] = FIXED_NOW.isoformat()
    return payload


def _report(manifest: object | None = None, evidence: object | None = None, **kwargs: object) -> dict:
    return gate.build_report(
        _manifest() if manifest is None else manifest,
        _phase3g() if evidence is None else evidence,
        expected_commit=kwargs.pop("expected_commit", TESTED_COMMIT),
        max_age_seconds=kwargs.pop("max_age_seconds", 900),
        now=kwargs.pop("now", FIXED_NOW),
        **kwargs,
    )


def test_current_repository_state_is_a_successful_not_ready_decision() -> None:
    report = _report()

    assert report["success"] is True
    assert report["verdict"] == "not-ready"
    assert report["verdict_reason"] == "production-readiness-prerequisites-incomplete"
    assert report["production_ready"] is False
    assert report["l3_eligible"] is False
    assert report["readiness_required"] is False
    assert report["evaluated_commit_sha"] == TESTED_COMMIT
    assert report["failure_codes"] == []
    assert report["blockers"] == list(gate.ALL_BLOCKERS)
    assert len(report["blockers"]) == 18
    assert all(report["checks"].values())
    assert report["phase3g_evidence"]["synthetic"] is True
    assert report["phase3g_evidence"]["l3_eligible"] is False


def test_production_promotion_policy_rejects_current_not_ready_state() -> None:
    report = _report(require_ready=True)

    assert report["success"] is False
    assert report["verdict"] == "fail"
    assert report["verdict_reason"] == "production-readiness-required"
    assert report["readiness_required"] is True
    assert report["production_ready"] is False
    assert report["l3_eligible"] is False
    assert report["blockers"] == list(gate.ALL_BLOCKERS)
    assert report["failure_codes"] == ["production-readiness-required"]
    assert report["checks"]["production_promotion_policy"] is False


def test_trusted_attestation_is_the_only_ready_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gate, "_trusted_attestation_valid", lambda *_args, **_kwargs: True)

    report = _report(
        evidence=None,
        trusted_attestation={"sanitized": True},
        expected_attestation_run_id=123456,
        expected_attestation_run_attempt=2,
        expected_repository="owner/repository",
        expected_repository_id="987654",
        require_ready=True,
    )

    assert report["success"] is True
    assert report["verdict"] == "ready"
    assert report["verdict_reason"] == "trusted-staging-attestation-valid"
    assert report["production_ready"] is True
    assert report["l3_eligible"] is True
    assert report["readiness_required"] is True
    assert report["blockers"] == []
    assert report["failure_codes"] == []
    assert all(report["checks"].values())
    assert report["checks"]["trusted_staging_attestation"] is True
    assert "phase3g_runtime_evidence" not in report["checks"]


def test_invalid_trusted_attestation_fails_instead_of_falling_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gate, "_trusted_attestation_valid", lambda *_args, **_kwargs: False)

    report = _report(
        evidence=None,
        trusted_attestation={"production_ready": True},
        expected_attestation_run_id=123456,
        expected_attestation_run_attempt=2,
        expected_repository="owner/repository",
        expected_repository_id="987654",
    )

    assert report["success"] is False
    assert report["verdict"] == "fail"
    assert report["verdict_reason"] == "trusted-attestation-invalid"
    assert report["production_ready"] is False
    assert report["l3_eligible"] is False
    assert report["blockers"] == []
    assert report["failure_codes"] == ["trusted-attestation-invalid"]


def test_phase3n_validator_receives_strict_correlated_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeVerifier:
        @staticmethod
        def validate_gate_report(report: object, **kwargs: object) -> tuple[bool, tuple[str, ...]]:
            captured["report"] = report
            captured.update(kwargs)
            return True, ()

    monkeypatch.setattr(gate, "_load_phase3n_verifier", lambda: FakeVerifier)
    attestation = {"report_schema": "phase3n-staging-evidence-gate-report"}

    assert gate._trusted_attestation_valid(
        attestation,
        expected_commit=TESTED_COMMIT,
        expected_run_id=123456,
        expected_run_attempt=2,
        expected_repository="owner/repository",
        expected_repository_id="987654",
        max_age_seconds=900,
        now=FIXED_NOW,
    ) is True
    assert captured == {
        "report": attestation,
        "expected_commit": TESTED_COMMIT,
        "expected_run_id": "123456",
        "expected_run_attempt": 2,
        "expected_repository": "owner/repository",
        "expected_repository_id": "987654",
        "max_age_seconds": 900,
        "now": FIXED_NOW,
    }


@pytest.mark.parametrize(
    ("run_id", "repository"),
    [(None, "owner/repository"), (0, "owner/repository"), (1, None), (1, "invalid")],
)
def test_phase3n_context_validation_fails_before_loading_verifier(
    monkeypatch: pytest.MonkeyPatch,
    run_id: int | None,
    repository: str | None,
) -> None:
    def unexpected() -> object:
        raise AssertionError("verifier must not load for invalid context")

    monkeypatch.setattr(gate, "_load_phase3n_verifier", unexpected)
    assert gate._trusted_attestation_valid(
        {},
        expected_commit=TESTED_COMMIT,
        expected_run_id=run_id,
        expected_run_attempt=1,
        expected_repository=repository,
        expected_repository_id="987654",
        max_age_seconds=900,
        now=FIXED_NOW,
    ) is False


@pytest.mark.parametrize(
    ("run_attempt", "repository_id"),
    [(None, "987654"), (0, "987654"), (1, None), (1, "invalid")],
)
def test_phase3n_attempt_and_repository_id_fail_before_loading_verifier(
    monkeypatch: pytest.MonkeyPatch,
    run_attempt: int | None,
    repository_id: str | None,
) -> None:
    def unexpected() -> object:
        raise AssertionError("verifier must not load for invalid context")

    monkeypatch.setattr(gate, "_load_phase3n_verifier", unexpected)
    assert gate._trusted_attestation_valid(
        {},
        expected_commit=TESTED_COMMIT,
        expected_run_id=123456,
        expected_run_attempt=run_attempt,
        expected_repository="owner/repository",
        expected_repository_id=repository_id,
        max_age_seconds=900,
        now=FIXED_NOW,
    ) is False


@pytest.mark.parametrize("top_level_key", sorted(gate.MANIFEST_KEYS))
def test_missing_manifest_field_fails_closed(top_level_key: str) -> None:
    payload = _manifest()
    del payload[top_level_key]
    report = _report(payload)

    assert report["success"] is False
    assert report["production_ready"] is False
    assert report["l3_eligible"] is False
    assert "manifest-schema-invalid" in report["failure_codes"]


def test_extra_manifest_field_fails_closed() -> None:
    payload = _manifest()
    payload["operator_note"] = "ready"
    assert "manifest-schema-invalid" in _report(payload)["failure_codes"]


@pytest.mark.parametrize("claim", ["production_ready_claimed", "l3_claimed"])
def test_manifest_cannot_self_assert_readiness(claim: str) -> None:
    payload = _manifest()
    payload[claim] = True
    report = _report(payload)

    assert report["success"] is False
    assert "manifest-self-asserted-readiness" in report["failure_codes"]
    assert report["blockers"] == []


@pytest.mark.parametrize(
    "group,key",
    [
        ("cross_store_saga", "durable_recovery"),
        ("staging_evidence", "trusted_evidence_producer"),
        ("approvals", "production_release"),
    ],
)
def test_manifest_cannot_self_assert_a_completed_prerequisite(group: str, key: str) -> None:
    payload = _manifest()
    payload[group][key] = True
    report = _report(payload)

    assert report["success"] is False
    assert "manifest-self-asserted-prerequisite" in report["failure_codes"]


@pytest.mark.parametrize("bad_value", [None, 0, 1, "false", [], {}])
def test_manifest_boolean_values_are_exact_booleans(bad_value: object) -> None:
    payload = _manifest()
    payload["cross_store_saga"]["lease_and_fencing"] = bad_value
    assert "manifest-saga-value-invalid" in _report(payload)["failure_codes"]


def test_nested_schema_is_exact() -> None:
    missing = _manifest()
    del missing["approvals"]["execution_unlock"]
    extra = _manifest()
    extra["staging_evidence"]["repository_review"] = False

    assert "manifest-approvals-schema-invalid" in _report(missing)["failure_codes"]
    assert "manifest-staging-schema-invalid" in _report(extra)["failure_codes"]


def test_expected_commit_is_mandatory_and_correlated() -> None:
    missing = _report(expected_commit=None)
    mismatch = _report(expected_commit="2" * 40)

    assert "expected-commit-required" in missing["failure_codes"]
    assert "phase3g-runtime-evidence-invalid" in mismatch["failure_codes"]
    assert mismatch["production_ready"] is False


def test_stale_phase3g_evidence_fails_closed() -> None:
    evidence = _phase3g()
    evidence["observed_at"] = (FIXED_NOW - timedelta(seconds=901)).isoformat()
    report = _report(evidence=evidence)

    assert "phase3g-runtime-evidence-invalid" in report["failure_codes"]
    assert report["success"] is False


@pytest.mark.parametrize(
    "key,value",
    [
        ("synthetic", False),
        ("l3_eligible", True),
        ("environment", "staging"),
        ("database_scope", "staging"),
        ("network_mode", "staging_only"),
    ],
)
def test_phase3g_boundary_cannot_be_promoted(key: str, value: object) -> None:
    evidence = _phase3g()
    evidence[key] = value
    report = _report(evidence=evidence)

    assert report["success"] is False
    assert "phase3g-runtime-boundary-invalid" in report["failure_codes"]
    assert report["l3_eligible"] is False


def test_phase3g_failed_cleanup_is_rejected() -> None:
    evidence = _phase3g()
    evidence["cleanup"]["container_absent"] = False
    assert "phase3g-runtime-evidence-invalid" in _report(evidence=evidence)["failure_codes"]


def test_report_projection_does_not_copy_manifest_or_raw_rows() -> None:
    report = _report()
    serialized = json.dumps(report, ensure_ascii=True, allow_nan=False)

    assert "cross_store_saga" not in report
    assert "staging_evidence" not in report
    assert "approvals" not in report
    assert "postgresql://" not in serialized
    assert "C:\\\\Users" not in serialized
    assert "/tmp/" not in serialized
    assert "raw_rows" not in serialized


def _write_bytes(tmp_path: Path, content: bytes, name: str = "manifest.json") -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


@pytest.mark.parametrize(
    "content,expected",
    [
        (b'{"schema_version":1,"schema_version":1}', "manifest-duplicate-json-key"),
        (b'{"schema_version":NaN}', "manifest-invalid-json"),
        (b"{", "manifest-invalid-json"),
        (b"[" * 2000 + b"]" * 2000, "manifest-invalid-json"),
        (b"\xff\xfe", "manifest-invalid-utf8"),
        (b"", "manifest-file-empty"),
    ],
)
def test_manifest_loader_rejects_unsafe_json(tmp_path: Path, content: bytes, expected: str) -> None:
    loaded, failures = gate.load_manifest(_write_bytes(tmp_path, content))

    assert loaded is None
    assert failures == [expected]


def test_manifest_loader_rejects_oversize_and_missing_files(tmp_path: Path) -> None:
    oversized = _write_bytes(tmp_path, b" " * (gate.MAX_MANIFEST_BYTES + 1))
    assert gate.load_manifest(oversized)[1] == ["manifest-file-too-large"]
    assert gate.load_manifest(tmp_path / "missing.json")[1] == ["manifest-file-unavailable"]


def test_cli_writes_only_a_sanitized_not_ready_report(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    evidence = tmp_path / "phase3g.json"
    manifest.write_text(json.dumps(_manifest()), encoding="utf-8")
    phase3g = _phase3g()
    phase3g["observed_at"] = datetime.now(timezone.utc).isoformat()
    evidence.write_text(json.dumps(phase3g), encoding="utf-8")

    process = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--manifest",
            str(manifest),
            "--phase3g-evidence",
            str(evidence),
            "--expected-commit",
            TESTED_COMMIT,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(REPORT.read_text(encoding="utf-8"))

    assert process.returncode == 0, process.stderr
    assert report["success"] is True
    assert report["verdict"] == "not-ready"
    assert report["production_ready"] is False
    assert report["l3_eligible"] is False
    assert report["evaluated_commit_sha"] == TESTED_COMMIT


def test_cli_fails_when_phase3g_file_is_missing(tmp_path: Path) -> None:
    process = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--manifest",
            str(MANIFEST),
            "--phase3g-evidence",
            str(tmp_path / "missing.json"),
            "--expected-commit",
            TESTED_COMMIT,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(REPORT.read_text(encoding="utf-8"))

    assert process.returncode != 0
    assert "phase3g-evidence-file-invalid" in report["failure_codes"]
    assert report["production_ready"] is False


def test_cli_require_ready_blocks_production_promotion(tmp_path: Path) -> None:
    evidence = tmp_path / "phase3g.json"
    payload = _phase3g()
    payload["observed_at"] = datetime.now(timezone.utc).isoformat()
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    process = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--manifest",
            str(MANIFEST),
            "--phase3g-evidence",
            str(evidence),
            "--expected-commit",
            TESTED_COMMIT,
            "--require-ready",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(REPORT.read_text(encoding="utf-8"))

    assert process.returncode != 0
    assert report["success"] is False
    assert report["verdict_reason"] == "production-readiness-required"
    assert report["readiness_required"] is True


def test_cli_loader_failure_still_writes_sanitized_report(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable() -> object:
        raise RuntimeError("sensitive loader detail")

    monkeypatch.setattr(gate, "_load_phase3g_verifier", unavailable)
    result = gate.main(
        [
            "--manifest",
            str(MANIFEST),
            "--phase3g-evidence",
            str(PHASE3G_FIXTURE),
            "--expected-commit",
            TESTED_COMMIT,
        ]
    )
    report_text = REPORT.read_text(encoding="utf-8")
    report = json.loads(report_text)

    assert result != 0
    assert "phase3g-evidence-loader-unavailable" in report["failure_codes"]
    assert "sensitive loader detail" not in report_text


def test_report_write_is_atomic_on_replace_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    previous = REPORT.read_bytes() if REPORT.exists() else None
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text('{"previous":true}\n', encoding="utf-8")

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(gate.os, "replace", fail_replace)
    try:
        with pytest.raises(OSError, match="simulated replace failure"):
            gate._write_report_atomic({"success": False})
        assert REPORT.read_text(encoding="utf-8") == '{"previous":true}\n'
        assert not list(REPORT.parent.glob(f".{REPORT.name}.*.tmp"))
    finally:
        if previous is None:
            REPORT.unlink(missing_ok=True)
        else:
            REPORT.write_bytes(previous)


def test_verifier_source_is_offline_and_has_no_effectful_client_imports() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])

    forbidden = {
        "boto3",
        "docker",
        "httpx",
        "psycopg",
        "requests",
        "socket",
        "sqlalchemy",
        "subprocess",
        "supabase",
        "urllib",
    }
    assert imported_roots.isdisjoint(forbidden)
