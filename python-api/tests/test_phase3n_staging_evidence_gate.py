from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[2]
VERIFIER_PATH = ROOT / "scripts" / "security" / "verify_phase3n_staging_evidence.py"
BUILDER_PATH = ROOT / "scripts" / "security" / "build_phase3n_staging_evidence.py"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "staging-evidence.yml"
FIXED_NOW = datetime(2026, 7, 20, 10, 30, tzinfo=timezone.utc)
OBSERVED = FIXED_NOW - timedelta(minutes=10)
EXPIRES = FIXED_NOW + timedelta(minutes=50)


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(VERIFIER_PATH, "test_phase3n_verifier")
builder = _load(BUILDER_PATH, "test_phase3n_builder")


def _git_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


COMMIT = _git_head()
RUN_ID = "29730000001"
RUN_ATTEMPT = 1
REPOSITORY = "yuki20001105/keiba-ai-pro"
REPOSITORY_ID = "123456789"
WORKFLOW_REF = (
    f"{REPOSITORY}/.github/workflows/staging-evidence.yml@"
    "refs/heads/security/phase3n-trusted-producer-v1"
)


def _time(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _phase3m(applied_commit: str = COMMIT) -> dict[str, Any]:
    expected = gate.expected_phase3m_binding(applied_commit, COMMIT)
    return {
        **expected,
        "schema_fingerprint": "d" * 64,
        "history_count": expected["migration_count"],
        "fingerprints_match": True,
    }


def _observation() -> dict[str, Any]:
    return {
        "schema_version": gate.SCHEMA_VERSION,
        "observation_schema": builder.OBSERVATION_SCHEMA,
        "observed_at": _time(OBSERVED),
        "expires_at": _time(EXPIRES),
        "provider_identities": {
            "vercel": {
                "provider": "vercel",
                "team_id": "team-staging",
                "project_id": "project-staging",
                "deployment_id": "deployment-vercel-staging",
                "commit_sha": COMMIT,
                "environment": "staging",
            },
            "render": {
                "provider": "render",
                "service_id": "service-staging",
                "deployment_id": "deployment-render-staging",
                "commit_sha": COMMIT,
                "environment": "staging",
            },
            "supabase": {
                "provider": "supabase",
                "project_ref": "xitrnivjskfepateedms",
                "environment": "staging",
            },
        },
        "phase3m_bootstrap": _phase3m(),
        "auth_rls_idor_smoke": {key: True for key in gate.AUTH_CHECK_KEYS},
        "database_cache_integrity": {
            "database_before_sha256": "a" * 64,
            "database_after_sha256": "a" * 64,
            "database_unchanged": True,
            "cache_before_sha256": "b" * 64,
            "cache_after_sha256": "b" * 64,
            "cache_unchanged": True,
            "captured_before_at": _time(OBSERVED - timedelta(minutes=5)),
            "captured_after_at": _time(OBSERVED - timedelta(minutes=1)),
        },
        "multi_instance_crash_recovery": {
            "instance_count": 2,
            "tested_operation_count": 3,
            "crash_injected": True,
            "recovery_converged": True,
            "duplicate_effect_count": 0,
            "stale_fence_rejection_count": 2,
            "orphaned_operation_count": 0,
            "non_synthetic": True,
        },
        "rollback_drill": {
            "drill_id": "rollback-staging-001",
            "started_at": _time(OBSERVED - timedelta(minutes=4)),
            "completed_at": _time(OBSERVED - timedelta(minutes=2)),
            "rollback_completed": True,
            "state_restored": True,
            "pre_state_sha256": "c" * 64,
            "post_state_sha256": "c" * 64,
            "unexpected_effect_count": 0,
            "non_synthetic": True,
        },
        "saga_checks": {key: True for key in gate.SAGA_CHECK_KEYS},
        "staging_checks": {key: True for key in gate.STAGING_CHECK_KEYS},
    }


def _approval_history() -> list[dict[str, Any]]:
    return [
        {
            "state": "approved",
            "comment": "not projected",
            "user": {"id": 42, "login": "not-projected"},
            "environments": [{"id": 100 + index, "name": name}],
        }
        for index, name in enumerate(gate.APPROVAL_ENVIRONMENTS, start=1)
    ]


def _evidence() -> dict[str, Any]:
    return builder.assemble_evidence(
        _observation(),
        _approval_history(),
        expected_commit=COMMIT,
        run_id=RUN_ID,
        run_attempt=RUN_ATTEMPT,
        repository=REPOSITORY,
        repository_id=REPOSITORY_ID,
        workflow_ref=WORKFLOW_REF,
        source_artifact_name="phase3n-staging-observation-29730000001-1",
        source_artifact_id="700000001",
        source_artifact_sha256="e" * 64,
        staging_migration_approved_at=_time(OBSERVED - timedelta(minutes=20)),
        execution_unlock_approved_at=_time(OBSERVED - timedelta(minutes=10)),
        production_release_approved_at=_time(OBSERVED + timedelta(minutes=5)),
        now=FIXED_NOW,
    )


def _report(evidence: Any | None = None, **overrides: Any) -> dict[str, Any]:
    arguments = {
        "expected_commit": COMMIT,
        "expected_run_id": RUN_ID,
        "expected_run_attempt": RUN_ATTEMPT,
        "expected_repository": REPOSITORY,
        "expected_workflow_ref": WORKFLOW_REF,
        "expected_environment": "staging",
        "max_age_seconds": 3600,
        "now": FIXED_NOW,
    }
    arguments.update(overrides)
    return gate.build_report(_evidence() if evidence is None else evidence, **arguments)


def test_valid_trusted_evidence_has_one_success_boundary() -> None:
    report = _report()

    assert report["success"] is report["trusted"] is True
    assert report["verdict"] == "trusted"
    assert report["verdict_reason"] == "trusted-staging-evidence"
    assert report["failure_codes"] == []
    assert all(report["checks"].values())
    assert report["saga_prerequisites_complete"] is True
    assert report["staging_prerequisites_complete"] is True
    assert report["approvals_complete"] is True
    assert report["l3_eligible"] is report["production_ready"] is True


def test_ancestor_bootstrap_with_identical_manifest_is_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    applied_commit = "a" * 40
    canonical = {
        **gate.expected_phase3m(COMMIT),
        "applied_commit_sha": applied_commit,
        "history_commit_match": True,
        "applied_commit_is_ancestor": True,
        "candidate_manifest_equivalent": True,
    }
    value = {
        **canonical,
        "schema_fingerprint": "d" * 64,
        "history_count": canonical["migration_count"],
        "fingerprints_match": True,
    }
    monkeypatch.setattr(
        gate,
        "expected_phase3m_binding",
        lambda applied, candidate: canonical,
    )
    failures: list[str] = []

    assert gate._validate_phase3m(value, failures, expected_commit=COMMIT) is True
    assert failures == []


def test_v1_observation_and_evidence_fail_closed() -> None:
    observation = _observation()
    observation["schema_version"] = 1
    valid, failures = builder.validate_observation(
        observation,
        expected_commit=COMMIT,
        now=FIXED_NOW,
    )
    assert valid is False
    assert "observation-schema-version-invalid" in failures

    evidence = _evidence()
    evidence["schema_version"] = 1
    report = _report(evidence)
    assert report["trusted"] is False
    assert "evidence-schema-version-invalid" in report["failure_codes"]


def test_phase3m_binding_compares_ordered_migration_identity_and_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    applied_commit = "a" * 40
    candidate_commit = "b" * 40

    def manifest(file_sha256: str) -> SimpleNamespace:
        entry = SimpleNamespace(
            version="20260701",
            path="supabase/migrations/20260701_example.sql",
            source="phase3m-bootstrap",
            sha256=file_sha256,
        )
        return SimpleNamespace(
            chain_digest="c" * 64,
            sha256="d" * 64,
            migrations=(entry,),
        )

    class FakeRunner:
        @staticmethod
        def load_manifest(_path: Path, *, expected_commit: str) -> SimpleNamespace:
            return manifest("e" * 64 if expected_commit == applied_commit else "f" * 64)

    monkeypatch.setattr(gate, "_load_phase3m_runner", lambda: FakeRunner())
    monkeypatch.setattr(
        gate,
        "subprocess",
        SimpleNamespace(run=lambda *args, **kwargs: SimpleNamespace(returncode=0)),
    )
    gate.expected_phase3m_binding.cache_clear()
    try:
        binding = gate.expected_phase3m_binding(applied_commit, candidate_commit)
    finally:
        gate.expected_phase3m_binding.cache_clear()

    assert binding["applied_commit_is_ancestor"] is True
    assert binding["candidate_manifest_equivalent"] is False


def test_phase3m_binding_rejects_nonancestor_even_when_manifest_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    commit = "a" * 40
    manifest = SimpleNamespace(
        chain_digest="c" * 64,
        sha256="d" * 64,
        migrations=(
            SimpleNamespace(
                version="20260701",
                path="supabase/migrations/20260701_example.sql",
                source="phase3m-bootstrap",
                sha256="e" * 64,
            ),
        ),
    )

    class FakeRunner:
        @staticmethod
        def load_manifest(_path: Path, *, expected_commit: str) -> SimpleNamespace:
            return manifest

    monkeypatch.setattr(gate, "_load_phase3m_runner", lambda: FakeRunner())
    monkeypatch.setattr(
        gate,
        "subprocess",
        SimpleNamespace(run=lambda *args, **kwargs: SimpleNamespace(returncode=1)),
    )
    gate.expected_phase3m_binding.cache_clear()
    try:
        binding = gate.expected_phase3m_binding(commit, "b" * 40)
    finally:
        gate.expected_phase3m_binding.cache_clear()

    assert binding["applied_commit_is_ancestor"] is False
    assert binding["candidate_manifest_equivalent"] is True


@pytest.mark.parametrize("key", sorted(gate.EVIDENCE_KEYS))
def test_missing_top_level_evidence_field_fails_closed(key: str) -> None:
    evidence = _evidence()
    del evidence[key]
    report = _report(evidence)

    assert report["success"] is report["trusted"] is False
    assert "evidence-schema-invalid" in report["failure_codes"]
    assert report["production_ready"] is False


def test_extra_evidence_field_and_input_readiness_claim_fail_closed() -> None:
    evidence = _evidence()
    evidence["trusted"] = True

    report = _report(evidence)
    assert report["success"] is False
    assert "evidence-schema-invalid" in report["failure_codes"]


@pytest.mark.parametrize(
    ("field", "value", "failure"),
    [
        ("commit_sha", "f" * 40, "provenance-commit-sha-mismatch"),
        ("run_id", "29730000002", "provenance-run-id-mismatch"),
        ("run_attempt", 2, "provenance-run-attempt-mismatch"),
        ("repository", "other/repository", "provenance-repository-mismatch"),
        ("workflow_ref", "other/workflow", "provenance-workflow-ref-mismatch"),
        ("environment", "production", "provenance-environment-mismatch"),
    ],
)
def test_provenance_context_mismatch_fails_closed(field: str, value: Any, failure: str) -> None:
    evidence = _evidence()
    evidence["provenance"][field] = value

    report = _report(evidence)
    assert failure in report["failure_codes"]
    assert report["trusted"] is False


def test_stale_expired_and_future_evidence_fail_closed() -> None:
    stale = _evidence()
    stale["provenance"]["observed_at"] = _time(FIXED_NOW - timedelta(hours=2))
    expired = _evidence()
    expired["provenance"]["expires_at"] = _time(FIXED_NOW - timedelta(seconds=1))
    future = _evidence()
    future["provenance"]["observed_at"] = _time(FIXED_NOW + timedelta(minutes=6))

    assert "evidence-stale" in _report(stale)["failure_codes"]
    assert "evidence-expired" in _report(expired)["failure_codes"]
    assert "evidence-observed-in-future" in _report(future)["failure_codes"]


@pytest.mark.parametrize("provider", ["vercel", "render"])
def test_deployment_provider_commit_must_match(provider: str) -> None:
    evidence = _evidence()
    evidence["provider_identities"][provider]["commit_sha"] = "f" * 40

    report = _report(evidence)
    assert f"{provider}-provider-identity-invalid" in report["failure_codes"]


def test_provider_resources_and_github_environments_must_be_isolated() -> None:
    production = _evidence()
    production["provider_identities"]["vercel"]["environment"] = "production"
    duplicate_environment = _evidence()
    duplicate_environment["provider_identities"]["github"]["environment_ids"]["production-release"] = "102"

    assert "vercel-provider-identity-invalid" in _report(production)["failure_codes"]
    assert "github-environment-identities-not-distinct" in _report(duplicate_environment)["failure_codes"]


@pytest.mark.parametrize(
    ("field", "value", "failure"),
    [
        ("chain_digest", "0" * 64, "phase3m-chain-digest-mismatch"),
        ("manifest_sha256", "0" * 64, "phase3m-manifest-sha256-mismatch"),
        ("history_count", 10, "phase3m-history-count-invalid"),
        ("history_commit_match", False, "phase3m-history-commit-match-mismatch"),
        ("history_commit_match", 1, "phase3m-history-commit-match-mismatch"),
        ("applied_commit_is_ancestor", False, "phase3m-applied-commit-is-ancestor-mismatch"),
        ("applied_commit_is_ancestor", 1, "phase3m-applied-commit-is-ancestor-mismatch"),
        ("candidate_manifest_equivalent", False, "phase3m-candidate-manifest-equivalent-mismatch"),
        ("candidate_manifest_equivalent", 1, "phase3m-candidate-manifest-equivalent-mismatch"),
        ("fingerprints_match", False, "phase3m-fingerprints-match-invalid"),
    ],
)
def test_phase3m_fingerprints_and_history_are_commit_bound(field: str, value: Any, failure: str) -> None:
    evidence = _evidence()
    evidence["phase3m_bootstrap"][field] = value

    assert failure in _report(evidence)["failure_codes"]


def test_phase3m_unknown_applied_commit_fails_closed() -> None:
    evidence = _evidence()
    evidence["phase3m_bootstrap"]["applied_commit_sha"] = "f" * 40

    report = _report(evidence)
    assert "phase3m-bootstrap-equivalence-unavailable" in report["failure_codes"]
    assert report["trusted"] is False


@pytest.mark.parametrize("check", sorted(gate.AUTH_CHECK_KEYS))
def test_each_auth_rls_idor_smoke_check_is_required(check: str) -> None:
    evidence = _evidence()
    evidence["auth_rls_idor_smoke"][check] = False

    report = _report(evidence)
    assert "auth-rls-idor-smoke-incomplete" in report["failure_codes"]


def test_database_and_cache_before_after_digests_must_match() -> None:
    database = _evidence()
    database["database_cache_integrity"]["database_after_sha256"] = "f" * 64
    cache = _evidence()
    cache["database_cache_integrity"]["cache_unchanged"] = False

    assert "database-digest-mismatch" in _report(database)["failure_codes"]
    assert "cache-unchanged-not-proven" in _report(cache)["failure_codes"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("instance_count", 1),
        ("crash_injected", False),
        ("recovery_converged", False),
        ("duplicate_effect_count", 1),
        ("stale_fence_rejection_count", 0),
        ("orphaned_operation_count", 1),
        ("non_synthetic", False),
    ],
)
def test_multi_instance_crash_recovery_is_non_synthetic_and_convergent(field: str, value: Any) -> None:
    evidence = _evidence()
    evidence["multi_instance_crash_recovery"][field] = value

    assert "multi-instance-crash-recovery-incomplete" in _report(evidence)["failure_codes"]


def test_rollback_drill_requires_restored_digest_and_no_unexpected_effect() -> None:
    evidence = _evidence()
    evidence["rollback_drill"]["post_state_sha256"] = "f" * 64
    evidence["rollback_drill"]["unexpected_effect_count"] = 1

    assert "rollback-drill-incomplete" in _report(evidence)["failure_codes"]


@pytest.mark.parametrize("check", sorted(gate.SAGA_CHECK_KEYS))
def test_each_saga_check_is_release_blocking(check: str) -> None:
    evidence = _evidence()
    evidence["saga_checks"][check] = False

    report = _report(evidence)
    assert "saga-checks-incomplete" in report["failure_codes"]
    assert report["l3_eligible"] is False


@pytest.mark.parametrize("check", sorted(gate.STAGING_CHECK_KEYS))
def test_each_staging_check_is_release_blocking(check: str) -> None:
    evidence = _evidence()
    evidence["staging_checks"][check] = False

    report = _report(evidence)
    assert "staging-checks-incomplete" in report["failure_codes"]


def test_three_approval_ids_are_distinct_but_actor_may_be_same() -> None:
    evidence = _evidence()
    actors = {entry["actor_id"] for entry in evidence["approvals"].values()}
    ids = {entry["approval_id"] for entry in evidence["approvals"].values()}

    assert actors == {"github-user:42"}
    assert len(ids) == 3
    assert _report(evidence)["trusted"] is True

    evidence["approvals"]["production_release"]["approval_id"] = evidence["approvals"]["execution_unlock"][
        "approval_id"
    ]
    assert "approval-ids-not-distinct" in _report(evidence)["failure_codes"]


def test_approval_environment_purpose_and_chronology_are_strict() -> None:
    wrong_boundary = _evidence()
    wrong_boundary["approvals"]["execution_unlock"]["environment"] = "production-release"
    early_production = _evidence()
    early_production["approvals"]["production_release"]["approved_at"] = _time(OBSERVED - timedelta(seconds=1))

    assert "approval-execution-unlock-boundary-invalid" in _report(wrong_boundary)["failure_codes"]
    assert "production-approval-before-observation" in _report(early_production)["failure_codes"]


@pytest.mark.parametrize(
    "unsafe_value",
    [
        "postgresql" + "://" + "user:example@host.invalid/db",
        "Bearer" + " " + "example-value",
        "C:" + "\\private\\evidence.json",
        "/var/private/evidence.json",
        "file" + "://" + "/tmp/evidence.json",
    ],
)
def test_secrets_dsns_and_absolute_paths_are_never_accepted(unsafe_value: str) -> None:
    evidence = _evidence()
    evidence["provider_identities"]["vercel"]["team_id"] = unsafe_value

    report = _report(evidence)
    assert "evidence-sanitization-failed" in report["failure_codes"]
    assert unsafe_value not in json.dumps(report)


def test_loader_rejects_duplicate_keys_and_oversize(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * (gate.MAX_EVIDENCE_BYTES + 1))

    assert gate.load_json(duplicate)[1] == ["evidence-duplicate-json-key"]
    assert gate.load_json(oversized)[1] == ["evidence-file-too-large"]


def test_builder_projects_only_stable_github_approval_identity() -> None:
    evidence = _evidence()
    serialized = json.dumps(evidence, sort_keys=True)

    assert "not projected" not in serialized
    assert "not-projected" not in serialized
    assert all(set(entry) == gate.APPROVAL_ENTRY_KEYS for entry in evidence["approvals"].values())


def test_builder_fails_when_any_environment_approval_is_missing() -> None:
    approvals = _approval_history()[:-1]

    with pytest.raises(ValueError, match="github-environment-approval-missing"):
        builder.assemble_evidence(
            _observation(),
            approvals,
            expected_commit=COMMIT,
            run_id=RUN_ID,
            run_attempt=RUN_ATTEMPT,
            repository=REPOSITORY,
            repository_id=REPOSITORY_ID,
            workflow_ref=WORKFLOW_REF,
            source_artifact_name="phase3n-staging-observation-29730000001-1",
            source_artifact_id="700000001",
            source_artifact_sha256="e" * 64,
            staging_migration_approved_at=_time(OBSERVED - timedelta(minutes=20)),
            execution_unlock_approved_at=_time(OBSERVED - timedelta(minutes=10)),
            production_release_approved_at=_time(OBSERVED + timedelta(minutes=5)),
            now=FIXED_NOW,
        )


def test_public_report_validator_revalidates_trusted_boundary() -> None:
    report = _report()
    valid, failures = gate.validate_gate_report(
        report,
        expected_commit=COMMIT,
        expected_run_id=RUN_ID,
        expected_repository=REPOSITORY,
        now=FIXED_NOW,
    )
    assert valid is True and failures == ()

    tampered = copy.deepcopy(report)
    tampered["trusted"] = False
    valid, failures = gate.validate_gate_report(
        tampered,
        expected_commit=COMMIT,
        expected_run_id=RUN_ID,
        expected_repository=REPOSITORY,
        now=FIXED_NOW,
    )
    assert valid is False and "report-not-trusted" in failures


def test_public_report_validator_rejects_extra_key_stale_and_commit_mismatch() -> None:
    extra = _report()
    extra["operator_note"] = "approved"
    stale = _report()
    wrong_commit = _report()

    assert gate.validate_gate_report(
        extra,
        expected_commit=COMMIT,
        expected_run_id=RUN_ID,
        expected_repository=REPOSITORY,
        now=FIXED_NOW,
    )[1] == ("report-schema-invalid",)
    assert "report-temporal-boundary-invalid" in gate.validate_gate_report(
        stale,
        expected_commit=COMMIT,
        expected_run_id=RUN_ID,
        expected_repository=REPOSITORY,
        now=FIXED_NOW + timedelta(hours=2),
    )[1]
    assert "report-commit-mismatch" in gate.validate_gate_report(
        wrong_commit,
        expected_commit="f" * 40,
        expected_run_id=RUN_ID,
        expected_repository=REPOSITORY,
        now=FIXED_NOW,
    )[1]


def test_public_report_validator_rechecks_provider_phase3m_and_approvals() -> None:
    provider = _report()
    provider["provider_identities"]["render"]["commit_sha"] = "f" * 40
    phase3m = _report()
    phase3m["phase3m_bootstrap"]["chain_digest"] = "f" * 64
    approval = _report()
    approval["approvals"]["production_release"]["approval_id"] = approval["approvals"]["execution_unlock"][
        "approval_id"
    ]

    common = {
        "expected_commit": COMMIT,
        "expected_run_id": RUN_ID,
        "expected_run_attempt": RUN_ATTEMPT,
        "expected_repository": REPOSITORY,
        "expected_repository_id": REPOSITORY_ID,
        "now": FIXED_NOW,
    }
    assert "report-provider-identities-invalid" in gate.validate_gate_report(provider, **common)[1]
    assert "report-phase3m-bootstrap-invalid" in gate.validate_gate_report(phase3m, **common)[1]
    assert "report-approvals-invalid" in gate.validate_gate_report(approval, **common)[1]


def test_public_report_validator_correlates_attempt_repository_id_and_time_order() -> None:
    report = _report()
    common = {
        "expected_commit": COMMIT,
        "expected_run_id": RUN_ID,
        "expected_run_attempt": RUN_ATTEMPT,
        "expected_repository": REPOSITORY,
        "expected_repository_id": REPOSITORY_ID,
        "now": FIXED_NOW,
    }
    assert gate.validate_gate_report(report, **common) == (True, ())

    assert "report-run-attempt-mismatch" in gate.validate_gate_report(
        report, **{**common, "expected_run_attempt": RUN_ATTEMPT + 1}
    )[1]
    assert "report-provider-identities-invalid" in gate.validate_gate_report(
        report, **{**common, "expected_repository_id": "999999999"}
    )[1]

    future = copy.deepcopy(report)
    future["observed_at"] = _time(FIXED_NOW + timedelta(minutes=10))
    future["expires_at"] = _time(FIXED_NOW + timedelta(minutes=20))
    assert "report-temporal-boundary-invalid" in gate.validate_gate_report(future, **common)[1]

    reversed_approvals = copy.deepcopy(report)
    reversed_approvals["approvals"]["staging_migration"]["approved_at"] = _time(
        FIXED_NOW + timedelta(minutes=3)
    )
    reversed_approvals["approvals"]["execution_unlock"]["approved_at"] = _time(
        FIXED_NOW + timedelta(minutes=2)
    )
    assert "report-approvals-invalid" in gate.validate_gate_report(reversed_approvals, **common)[1]


def test_workflow_has_three_distinct_environment_gates_and_minimal_permissions() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow and "push:" not in workflow
    assert "trusted_producer_sha:" in workflow
    assert "refs/heads/security/phase3n-trusted-producer-v1" in workflow
    assert '[[ "$GITHUB_SHA" == "$TRUSTED_PRODUCER_SHA" ]]' in workflow
    assert 'git diff --quiet "$TRUSTED_PRODUCER_SHA" "$EXPECTED_COMMIT"' in workflow
    for environment in gate.APPROVAL_ENVIRONMENTS:
        assert f"name: {environment}" in workflow
    assert "persist-credentials: false" in workflow
    assert workflow.count("fetch-depth: 0") >= 3
    assert "permissions:\n  contents: read" in workflow
    assert "attestations: write" in workflow and "id-token: write" in workflow
    assert "actions/attest-build-provenance@e8998f949152b193b063cb0ec769d69d929409be" in workflow
    assert "uses: actions/checkout@v4" not in workflow
    assert "uses: actions/upload-artifact@v4" not in workflow
    assert "uses: actions/download-artifact@v4" not in workflow
    assert "PHASE3N_STAGING_OBSERVATION_B64: ${{ secrets.PHASE3N_STAGING_OBSERVATION_B64 }}" in workflow
    assert "--expected-commit \"$EXPECTED_COMMIT\"" in workflow
    assert "--expected-run-id \"$GITHUB_RUN_ID\"" in workflow
    assert "--expected-run-attempt \"$GITHUB_RUN_ATTEMPT\"" in workflow
    assert "if-no-files-found: error" in workflow
    assert "phase3n-staging-evidence-json" in workflow
    lowered = workflow.lower()
    assert "vercel deploy" not in lowered
    assert "render deploy" not in lowered
    assert "supabase db push" not in lowered
