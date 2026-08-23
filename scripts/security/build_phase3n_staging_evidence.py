from __future__ import annotations

import argparse
import functools
import hashlib
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
VERIFIER_PATH = ROOT / "scripts" / "security" / "verify_phase3n_staging_evidence.py"
OBSERVATION_SCHEMA = "phase3n-staging-observation"
OBSERVATION_KEYS = frozenset(
    {
        "schema_version",
        "observation_schema",
        "observed_at",
        "expires_at",
        "provider_identities",
        "phase3m_bootstrap",
        "auth_rls_idor_smoke",
        "database_cache_integrity",
        "multi_instance_crash_recovery",
        "rollback_drill",
        "saga_checks",
        "staging_checks",
    }
)
OBSERVATION_PROVIDER_KEYS = frozenset({"vercel", "render", "supabase"})
MAX_APPROVAL_BYTES = 256 * 1024


@functools.lru_cache(maxsize=1)
def _load_verifier() -> ModuleType:
    spec = importlib.util.spec_from_file_location("phase3n_evidence_verifier", VERIFIER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("verifier-unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def validate_observation(
    observation: Any,
    *,
    expected_commit: str,
    now: datetime | None = None,
    max_age_seconds: int = 3600,
) -> tuple[bool, tuple[str, ...]]:
    gate = _load_verifier()
    failures: list[str] = []
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not gate._exact_dict(observation, OBSERVATION_KEYS):
        return False, ("observation-schema-invalid",)
    if (
        observation["schema_version"] != gate.SCHEMA_VERSION
        or observation["observation_schema"] != OBSERVATION_SCHEMA
    ):
        failures.append("observation-schema-version-invalid")
    observed = gate._parse_time(observation["observed_at"])
    expires = gate._parse_time(observation["expires_at"])
    if (
        observed is None
        or expires is None
        or observed > now + timedelta(seconds=gate.MAX_CLOCK_SKEW_SECONDS)
        or now - observed > timedelta(seconds=max_age_seconds)
        or expires <= observed
        or expires - observed > timedelta(hours=24)
        or now > expires
    ):
        failures.append("observation-temporal-boundary-invalid")

    providers = observation["provider_identities"]
    if not gate._exact_dict(providers, OBSERVATION_PROVIDER_KEYS):
        failures.append("observation-provider-identities-schema-invalid")
    else:
        projected = {
            "github": {
                "provider": "github",
                "repository_id": "1",
                "environment_ids": {
                    "staging-migration": "1",
                    "staging-execution-unlock": "2",
                    "production-release": "3",
                },
            },
            **providers,
        }
        gate._validate_providers(projected, failures, expected_commit=expected_commit, repository_id="1")
    gate._validate_phase3m(observation["phase3m_bootstrap"], failures, expected_commit=expected_commit)
    gate._validate_auth(observation["auth_rls_idor_smoke"], failures)
    gate._validate_db_cache(observation["database_cache_integrity"], failures, observed=observed)
    gate._validate_crash_recovery(observation["multi_instance_crash_recovery"], failures)
    gate._validate_rollback(observation["rollback_drill"], failures)
    if not gate._exact_true_map(observation["saga_checks"], gate.SAGA_CHECK_KEYS):
        failures.append("saga-checks-incomplete")
    if not gate._exact_true_map(observation["staging_checks"], gate.STAGING_CHECK_KEYS):
        failures.append("staging-checks-incomplete")
    if not gate._is_sanitized(observation):
        failures.append("observation-sanitization-failed")
    return not failures, tuple(dict.fromkeys(failures))


def _approved_environments(
    raw: Any,
    *,
    run_id: str,
    run_attempt: int,
    approval_times: dict[str, str],
) -> tuple[dict[str, Any], dict[str, str]]:
    gate = _load_verifier()
    if not isinstance(raw, list) or len(raw) > 100:
        raise ValueError("github-approval-history-invalid")
    if set(approval_times) != set(gate.APPROVAL_ENVIRONMENTS):
        raise ValueError("github-approval-boundary-times-invalid")
    parsed_times = {name: gate._parse_time(value) for name, value in approval_times.items()}
    if any(value is None for value in parsed_times.values()):
        raise ValueError("github-approval-boundary-times-invalid")
    selected: dict[str, tuple[dict[str, Any], str]] = {}
    environment_ids: dict[str, str] = {}
    for record in raw:
        if not isinstance(record, dict) or str(record.get("state", "")).lower() != "approved":
            continue
        user = record.get("user")
        environments = record.get("environments")
        if (
            not isinstance(user, dict)
            or not gate.RUN_ID_RE.fullmatch(str(user.get("id", "")))
            or not isinstance(environments, list)
        ):
            continue
        actor_id = f"github-user:{user['id']}"
        for environment in environments:
            if not isinstance(environment, dict):
                continue
            name = environment.get("name")
            environment_id = str(environment.get("id", ""))
            if name not in gate.APPROVAL_ENVIRONMENTS or gate.RUN_ID_RE.fullmatch(environment_id) is None:
                continue
            if name in selected:
                raise ValueError("github-environment-approval-ambiguous")
            approved_at = approval_times[name]
            approval_hash = hashlib.sha256(
                f"{run_id}:{run_attempt}:{environment_id}:{actor_id}:{approved_at}".encode("utf-8")
            ).hexdigest()
            entry = {
                "approval_id": f"gha-{approval_hash}",
                "actor_id": actor_id,
                "approved_at": approved_at,
                "environment": name,
                "purpose": name,
            }
            selected[name] = (entry, environment_id)
    missing = [name for name in gate.APPROVAL_ENVIRONMENTS if name not in selected]
    if missing:
        raise ValueError("github-environment-approval-missing")
    for name, (_entry, environment_id) in selected.items():
        environment_ids[name] = environment_id
    if len(set(environment_ids.values())) != 3:
        raise ValueError("github-environment-identities-not-distinct")
    approvals = {
        key: selected[environment][0] for key, environment in gate.APPROVAL_PURPOSES.items()
    }
    return approvals, environment_ids


def assemble_evidence(
    observation: Any,
    github_approvals: Any,
    *,
    expected_commit: str,
    run_id: str,
    run_attempt: int,
    repository: str,
    repository_id: str,
    workflow_ref: str,
    source_artifact_name: str,
    source_artifact_id: str,
    source_artifact_sha256: str,
    staging_migration_approved_at: str,
    execution_unlock_approved_at: str,
    production_release_approved_at: str,
    now: datetime | None = None,
    max_age_seconds: int = 3600,
) -> dict[str, Any]:
    gate = _load_verifier()
    valid, failures = validate_observation(
        observation, expected_commit=expected_commit, now=now, max_age_seconds=max_age_seconds
    )
    if not valid:
        raise ValueError(f"observation-invalid:{','.join(failures)}")
    approvals, environment_ids = _approved_environments(
        github_approvals,
        run_id=run_id,
        run_attempt=run_attempt,
        approval_times={
            "staging-migration": staging_migration_approved_at,
            "staging-execution-unlock": execution_unlock_approved_at,
            "production-release": production_release_approved_at,
        },
    )
    artifact_digest = source_artifact_sha256.removeprefix("sha256:").lower()
    identity_material = {
        "observation_sha256": hashlib.sha256(_canonical_json(observation)).hexdigest(),
        "commit_sha": expected_commit,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "repository": repository,
        "workflow_ref": workflow_ref,
        "approval_ids": sorted(entry["approval_id"] for entry in approvals.values()),
        "source_artifact_sha256": artifact_digest,
    }
    evidence_id = f"phase3n-{hashlib.sha256(_canonical_json(identity_material)).hexdigest()}"
    evidence = {
        "schema_version": gate.SCHEMA_VERSION,
        "evidence_schema": gate.EVIDENCE_SCHEMA,
        "provenance": {
            "commit_sha": expected_commit,
            "repository": repository,
            "repository_id": repository_id,
            "workflow_ref": workflow_ref,
            "run_id": run_id,
            "run_attempt": run_attempt,
            "environment": "staging",
            "evidence_id": evidence_id,
            "observed_at": observation["observed_at"],
            "expires_at": observation["expires_at"],
            "source_artifact_name": source_artifact_name,
            "source_artifact_id": source_artifact_id,
            "source_artifact_sha256": artifact_digest,
        },
        "provider_identities": {
            "github": {
                "provider": "github",
                "repository_id": repository_id,
                "environment_ids": environment_ids,
            },
            **observation["provider_identities"],
        },
        "phase3m_bootstrap": observation["phase3m_bootstrap"],
        "auth_rls_idor_smoke": observation["auth_rls_idor_smoke"],
        "database_cache_integrity": observation["database_cache_integrity"],
        "multi_instance_crash_recovery": observation["multi_instance_crash_recovery"],
        "rollback_drill": observation["rollback_drill"],
        "saga_checks": observation["saga_checks"],
        "staging_checks": observation["staging_checks"],
        "approvals": approvals,
    }
    report = gate.build_report(
        evidence,
        expected_commit=expected_commit,
        expected_run_id=run_id,
        expected_run_attempt=run_attempt,
        expected_repository=repository,
        expected_workflow_ref=workflow_ref,
        expected_environment="staging",
        max_age_seconds=max_age_seconds,
        now=now,
    )
    if not report["success"]:
        raise ValueError(f"assembled-evidence-invalid:{','.join(report['failure_codes'])}")
    return evidence


def _write(path: Path, payload: Any) -> None:
    gate = _load_verifier()
    if path.is_symlink() or not gate._is_sanitized(payload):
        raise ValueError("output-sanitization-failed")
    serialized = json.dumps(payload, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2) + "\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        temporary.write_text(serialized, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build canonical Phase3N staging evidence.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    canonicalize = subparsers.add_parser("canonicalize-observation")
    canonicalize.add_argument("--input", required=True, type=Path)
    canonicalize.add_argument("--expected-commit", required=True)
    canonicalize.add_argument("--max-age-seconds", type=int, default=3600)
    canonicalize.add_argument("--output", required=True, type=Path)

    assemble = subparsers.add_parser("assemble")
    assemble.add_argument("--observation", required=True, type=Path)
    assemble.add_argument("--github-approvals", required=True, type=Path)
    assemble.add_argument("--expected-commit", required=True)
    assemble.add_argument("--run-id", required=True)
    assemble.add_argument("--run-attempt", required=True, type=int)
    assemble.add_argument("--repository", required=True)
    assemble.add_argument("--repository-id", required=True)
    assemble.add_argument("--workflow-ref", required=True)
    assemble.add_argument("--source-artifact-name", required=True)
    assemble.add_argument("--source-artifact-id", required=True)
    assemble.add_argument("--source-artifact-sha256", required=True)
    assemble.add_argument("--staging-migration-approved-at", required=True)
    assemble.add_argument("--execution-unlock-approved-at", required=True)
    assemble.add_argument("--production-release-approved-at", required=True)
    assemble.add_argument("--max-age-seconds", type=int, default=3600)
    assemble.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    gate = _load_verifier()
    try:
        if args.command == "canonicalize-observation":
            observation, failures = gate.load_json(args.input, prefix="observation")
            if failures:
                raise ValueError(failures[0])
            valid, validation_failures = validate_observation(
                observation,
                expected_commit=args.expected_commit,
                max_age_seconds=args.max_age_seconds,
            )
            if not valid:
                raise ValueError(validation_failures[0])
            _write(args.output, observation)
        else:
            observation, observation_failures = gate.load_json(args.observation, prefix="observation")
            approvals, approval_failures = gate.load_json(
                args.github_approvals, prefix="github-approvals", max_bytes=MAX_APPROVAL_BYTES
            )
            if observation_failures or approval_failures:
                raise ValueError((observation_failures + approval_failures)[0])
            evidence = assemble_evidence(
                observation,
                approvals,
                expected_commit=args.expected_commit,
                run_id=args.run_id,
                run_attempt=args.run_attempt,
                repository=args.repository,
                repository_id=args.repository_id,
                workflow_ref=args.workflow_ref,
                source_artifact_name=args.source_artifact_name,
                source_artifact_id=args.source_artifact_id,
                source_artifact_sha256=args.source_artifact_sha256,
                staging_migration_approved_at=args.staging_migration_approved_at,
                execution_unlock_approved_at=args.execution_unlock_approved_at,
                production_release_approved_at=args.production_release_approved_at,
                max_age_seconds=args.max_age_seconds,
            )
            _write(args.output, evidence)
    except (OSError, RuntimeError, ValueError):
        print('{"success":false,"trusted":false,"verdict_reason":"phase3n-evidence-build-failed"}')
        return 1
    print('{"success":true,"trusted":true}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
