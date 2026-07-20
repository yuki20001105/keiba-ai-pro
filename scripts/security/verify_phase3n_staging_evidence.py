from __future__ import annotations

import argparse
import functools
import hashlib
import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "reports" / "phase3n_staging_evidence_gate.json"
PHASE3M_RUNNER = ROOT / "scripts" / "security" / "run_phase3m_supabase_bootstrap_gate.py"
PHASE3M_MANIFEST = ROOT / "supabase" / "bootstrap" / "v1" / "manifest.json"

EVIDENCE_SCHEMA = "phase3n-staging-evidence"
REPORT_SCHEMA = "phase3n-staging-evidence-gate-report"
SCHEMA_VERSION = 1
DEFAULT_MAX_AGE_SECONDS = 3600
MAX_EVIDENCE_BYTES = 128 * 1024
MAX_REPORT_BYTES = 128 * 1024
MAX_CLOCK_SKEW_SECONDS = 300

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
RUN_ID_RE = re.compile(r"^[1-9][0-9]{0,19}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
RESOURCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
SUPABASE_REF_RE = re.compile(r"^[a-z0-9]{20}$")
APPROVAL_ID_RE = re.compile(r"^gha-[0-9a-f]{64}$")
ACTOR_ID_RE = re.compile(r"^github-user:[1-9][0-9]{0,19}$")
EVIDENCE_ID_RE = re.compile(r"^phase3n-[0-9a-f]{64}$")

EVIDENCE_KEYS = frozenset(
    {
        "schema_version",
        "evidence_schema",
        "provenance",
        "provider_identities",
        "phase3m_bootstrap",
        "auth_rls_idor_smoke",
        "database_cache_integrity",
        "multi_instance_crash_recovery",
        "rollback_drill",
        "saga_checks",
        "staging_checks",
        "approvals",
    }
)
PROVENANCE_KEYS = frozenset(
    {
        "commit_sha",
        "repository",
        "repository_id",
        "workflow_ref",
        "run_id",
        "run_attempt",
        "environment",
        "evidence_id",
        "observed_at",
        "expires_at",
        "source_artifact_name",
        "source_artifact_id",
        "source_artifact_sha256",
    }
)
PROVIDER_KEYS = frozenset({"github", "vercel", "render", "supabase"})
GITHUB_PROVIDER_KEYS = frozenset({"provider", "repository_id", "environment_ids"})
VERCEL_PROVIDER_KEYS = frozenset(
    {"provider", "team_id", "project_id", "deployment_id", "commit_sha", "environment"}
)
RENDER_PROVIDER_KEYS = frozenset(
    {"provider", "service_id", "deployment_id", "commit_sha", "environment"}
)
SUPABASE_PROVIDER_KEYS = frozenset({"provider", "project_ref", "environment"})
APPROVAL_ENVIRONMENTS = (
    "staging-migration",
    "staging-execution-unlock",
    "production-release",
)
APPROVAL_PURPOSES = {
    "staging_migration": "staging-migration",
    "execution_unlock": "staging-execution-unlock",
    "production_release": "production-release",
}
APPROVAL_KEYS = frozenset(APPROVAL_PURPOSES)
APPROVAL_ENTRY_KEYS = frozenset(
    {"approval_id", "actor_id", "approved_at", "environment", "purpose"}
)
PHASE3M_KEYS = frozenset(
    {
        "expected_commit_sha",
        "chain_digest",
        "manifest_sha256",
        "schema_fingerprint",
        "migration_count",
        "history_count",
        "history_commit_match",
        "fingerprints_match",
    }
)
AUTH_CHECK_KEYS = frozenset(
    {
        "free_login",
        "premium_login",
        "admin_login",
        "anonymous_profile_denied",
        "free_profile_isolation",
        "premium_profile_isolation",
        "admin_profile_access",
        "foreign_profile_denied",
        "role_escalation_denied",
        "browser_privileged_rpc_denied",
        "private_bucket_write_denied",
    }
)
DB_CACHE_KEYS = frozenset(
    {
        "database_before_sha256",
        "database_after_sha256",
        "database_unchanged",
        "cache_before_sha256",
        "cache_after_sha256",
        "cache_unchanged",
        "captured_before_at",
        "captured_after_at",
    }
)
CRASH_RECOVERY_KEYS = frozenset(
    {
        "instance_count",
        "tested_operation_count",
        "crash_injected",
        "recovery_converged",
        "duplicate_effect_count",
        "stale_fence_rejection_count",
        "orphaned_operation_count",
        "non_synthetic",
    }
)
ROLLBACK_KEYS = frozenset(
    {
        "drill_id",
        "started_at",
        "completed_at",
        "rollback_completed",
        "state_restored",
        "pre_state_sha256",
        "post_state_sha256",
        "unexpected_effect_count",
        "non_synthetic",
    }
)
SAGA_CHECK_KEYS = frozenset(
    {
        "stable_operation_and_job_binding",
        "idempotent_reserve_and_consume",
        "atomic_sqlite_job_saga_outbox",
        "consume_before_worker_dispatch",
        "durable_recovery",
        "idempotent_compensation",
        "lease_and_fencing",
        "multi_instance_safety",
        "failure_injection_matrix",
    }
)
STAGING_CHECK_KEYS = frozenset(
    {
        "review_ledger_migration_applied",
        "bounded_external_http_validation",
        "zero_db_mutation_proven",
        "non_synthetic_crash_recovery",
        "trusted_evidence_producer",
    }
)
CHECK_KEYS = frozenset(
    {
        "evidence_schema",
        "expected_context",
        "temporal_bounds",
        "provider_identities",
        "phase3m_bootstrap",
        "auth_rls_idor_smoke",
        "database_cache_integrity",
        "multi_instance_crash_recovery",
        "rollback_drill",
        "saga_checks",
        "staging_checks",
        "approvals",
        "artifact_provenance",
        "sanitization",
    }
)
REPORT_KEYS = frozenset(
    {
        "report_schema",
        "schema_version",
        "success",
        "trusted",
        "verdict",
        "verdict_reason",
        "evaluated_commit_sha",
        "evaluated_run_id",
        "evaluated_run_attempt",
        "repository",
        "environment",
        "observed_at",
        "expires_at",
        "evidence_id",
        "provider_identities",
        "phase3m_bootstrap",
        "saga_checks",
        "staging_checks",
        "approvals",
        "artifact_provenance",
        "checks",
        "failure_codes",
        "saga_prerequisites_complete",
        "staging_prerequisites_complete",
        "approvals_complete",
        "l3_eligible",
        "production_ready",
    }
)

FORBIDDEN_KEY_PARTS = (
    "authorization",
    "cookie",
    "credential",
    "dsn",
    "password",
    "raw_row",
    "secret",
    "service_role_key",
    "token",
)
FORBIDDEN_VALUE_PATTERNS = (
    re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\b(?:sk|sb_secret|whsec)_[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?:^|[\s\"'=])(?:[A-Za-z]:[\\/]|\\\\)"),
    re.compile(r"^(?:/|~/|file://)"),
)


class DuplicateKeyError(ValueError):
    pass


def _without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _append(failures: list[str], code: str) -> None:
    if code not in failures:
        failures.append(code)


def load_json(path: Path, *, prefix: str = "evidence", max_bytes: int = MAX_EVIDENCE_BYTES) -> tuple[Any | None, list[str]]:
    try:
        stat = path.stat()
        if not path.is_file() or path.is_symlink():
            return None, [f"{prefix}-file-invalid"]
        if stat.st_size <= 0:
            return None, [f"{prefix}-file-empty"]
        if stat.st_size > max_bytes:
            return None, [f"{prefix}-file-too-large"]
        raw = path.read_bytes().decode("utf-8", errors="strict")
        return (
            json.loads(raw, object_pairs_hook=_without_duplicate_keys, parse_constant=_reject_constant),
            [],
        )
    except UnicodeDecodeError:
        return None, [f"{prefix}-invalid-utf8"]
    except DuplicateKeyError:
        return None, [f"{prefix}-duplicate-json-key"]
    except (json.JSONDecodeError, ValueError, RecursionError):
        return None, [f"{prefix}-invalid-json"]
    except OSError:
        return None, [f"{prefix}-file-unavailable"]


def _exact_dict(value: Any, keys: frozenset[str]) -> bool:
    return isinstance(value, dict) and frozenset(value) == keys


def _exact_true_map(value: Any, keys: frozenset[str]) -> bool:
    return _exact_dict(value, keys) and all(type(value[key]) is bool and value[key] is True for key in keys)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _time_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_id(value: Any) -> bool:
    return isinstance(value, str) and RESOURCE_ID_RE.fullmatch(value) is not None


def _is_sanitized(value: Any, *, depth: int = 0) -> bool:
    if depth > 12:
        return False
    if value is None or type(value) is bool or type(value) is int:
        return True
    if isinstance(value, str):
        if len(value) > 512 or any(ord(character) < 32 for character in value):
            return False
        return not any(pattern.search(value) for pattern in FORBIDDEN_VALUE_PATTERNS)
    if isinstance(value, list):
        return len(value) <= 128 and all(_is_sanitized(item, depth=depth + 1) for item in value)
    if isinstance(value, dict):
        if len(value) > 128:
            return False
        for key, item in value.items():
            if not isinstance(key, str) or any(part in key.lower() for part in FORBIDDEN_KEY_PARTS):
                return False
            if not _is_sanitized(item, depth=depth + 1):
                return False
        return True
    return False


def _load_phase3m_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("phase3n_phase3m_runner", PHASE3M_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("Phase3M runner unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@functools.lru_cache(maxsize=8)
def expected_phase3m(expected_commit: str) -> dict[str, Any]:
    runner = _load_phase3m_runner()
    manifest = runner.load_manifest(PHASE3M_MANIFEST, expected_commit=expected_commit)
    return {
        "expected_commit_sha": expected_commit,
        "chain_digest": manifest.chain_digest,
        "manifest_sha256": manifest.sha256,
        "migration_count": len(manifest.migrations),
    }


def _validate_provenance(
    value: Any,
    failures: list[str],
    *,
    expected_commit: str,
    expected_run_id: str,
    expected_run_attempt: int,
    expected_repository: str,
    expected_workflow_ref: str,
    expected_environment: str,
    now: datetime,
    max_age_seconds: int,
) -> tuple[bool, datetime | None, datetime | None]:
    if not _exact_dict(value, PROVENANCE_KEYS):
        _append(failures, "provenance-schema-invalid")
        return False, None, None
    valid = True
    expected_values = {
        "commit_sha": expected_commit,
        "repository": expected_repository,
        "run_id": expected_run_id,
        "run_attempt": expected_run_attempt,
        "workflow_ref": expected_workflow_ref,
        "environment": expected_environment,
    }
    for key, expected in expected_values.items():
        if value[key] != expected:
            _append(failures, f"provenance-{key.replace('_', '-')}-mismatch")
            valid = False
    if not RUN_ID_RE.fullmatch(str(value["repository_id"])):
        _append(failures, "provenance-repository-id-invalid")
        valid = False
    if not isinstance(value["evidence_id"], str) or EVIDENCE_ID_RE.fullmatch(value["evidence_id"]) is None:
        _append(failures, "provenance-evidence-id-invalid")
        valid = False
    if not _safe_id(value["source_artifact_name"]):
        _append(failures, "artifact-name-invalid")
        valid = False
    if not RUN_ID_RE.fullmatch(str(value["source_artifact_id"])):
        _append(failures, "artifact-id-invalid")
        valid = False
    if not isinstance(value["source_artifact_sha256"], str) or DIGEST_RE.fullmatch(value["source_artifact_sha256"]) is None:
        _append(failures, "artifact-digest-invalid")
        valid = False
    observed = _parse_time(value["observed_at"])
    expires = _parse_time(value["expires_at"])
    if observed is None or expires is None:
        _append(failures, "evidence-time-invalid")
        return False, observed, expires
    if observed > now + timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
        _append(failures, "evidence-observed-in-future")
        valid = False
    if now - observed > timedelta(seconds=max_age_seconds):
        _append(failures, "evidence-stale")
        valid = False
    if expires <= observed or expires - observed > timedelta(hours=24):
        _append(failures, "evidence-expiry-invalid")
        valid = False
    if now > expires:
        _append(failures, "evidence-expired")
        valid = False
    return valid, observed, expires


def _validate_providers(value: Any, failures: list[str], *, expected_commit: str, repository_id: str | None) -> bool:
    if not _exact_dict(value, PROVIDER_KEYS):
        _append(failures, "provider-identities-schema-invalid")
        return False
    valid = True
    github = value["github"]
    if not _exact_dict(github, GITHUB_PROVIDER_KEYS):
        _append(failures, "github-provider-schema-invalid")
        valid = False
    else:
        env_ids = github["environment_ids"]
        if github["provider"] != "github" or not RUN_ID_RE.fullmatch(str(github["repository_id"])):
            _append(failures, "github-provider-identity-invalid")
            valid = False
        if repository_id is not None and str(github["repository_id"]) != repository_id:
            _append(failures, "github-provider-repository-mismatch")
            valid = False
        if not _exact_dict(env_ids, frozenset(APPROVAL_ENVIRONMENTS)) or not all(
            RUN_ID_RE.fullmatch(str(env_ids[name])) for name in APPROVAL_ENVIRONMENTS
        ):
            _append(failures, "github-environment-identities-invalid")
            valid = False
        elif len({str(env_ids[name]) for name in APPROVAL_ENVIRONMENTS}) != 3:
            _append(failures, "github-environment-identities-not-distinct")
            valid = False

    vercel = value["vercel"]
    if not _exact_dict(vercel, VERCEL_PROVIDER_KEYS):
        _append(failures, "vercel-provider-schema-invalid")
        valid = False
    elif not (
        vercel["provider"] == "vercel"
        and vercel["environment"] == "staging"
        and vercel["commit_sha"] == expected_commit
        and all(_safe_id(vercel[key]) for key in ("team_id", "project_id", "deployment_id"))
    ):
        _append(failures, "vercel-provider-identity-invalid")
        valid = False

    render = value["render"]
    if not _exact_dict(render, RENDER_PROVIDER_KEYS):
        _append(failures, "render-provider-schema-invalid")
        valid = False
    elif not (
        render["provider"] == "render"
        and render["environment"] == "staging"
        and render["commit_sha"] == expected_commit
        and all(_safe_id(render[key]) for key in ("service_id", "deployment_id"))
    ):
        _append(failures, "render-provider-identity-invalid")
        valid = False

    supabase = value["supabase"]
    if not _exact_dict(supabase, SUPABASE_PROVIDER_KEYS):
        _append(failures, "supabase-provider-schema-invalid")
        valid = False
    elif not (
        supabase["provider"] == "supabase"
        and supabase["environment"] == "staging"
        and isinstance(supabase["project_ref"], str)
        and SUPABASE_REF_RE.fullmatch(supabase["project_ref"]) is not None
    ):
        _append(failures, "supabase-provider-identity-invalid")
        valid = False
    return valid


def _validate_phase3m(value: Any, failures: list[str], *, expected_commit: str) -> bool:
    if not _exact_dict(value, PHASE3M_KEYS):
        _append(failures, "phase3m-bootstrap-schema-invalid")
        return False
    try:
        canonical = expected_phase3m(expected_commit)
    except Exception:
        _append(failures, "phase3m-canonical-manifest-unavailable")
        return False
    valid = True
    for key in ("expected_commit_sha", "chain_digest", "manifest_sha256", "migration_count"):
        if value[key] != canonical[key]:
            _append(failures, f"phase3m-{key.replace('_', '-')}-mismatch")
            valid = False
    if not isinstance(value["schema_fingerprint"], str) or DIGEST_RE.fullmatch(value["schema_fingerprint"]) is None:
        _append(failures, "phase3m-schema-fingerprint-invalid")
        valid = False
    if type(value["history_count"]) is not int or value["history_count"] != canonical["migration_count"]:
        _append(failures, "phase3m-history-count-invalid")
        valid = False
    for key in ("history_commit_match", "fingerprints_match"):
        if type(value[key]) is not bool or value[key] is not True:
            _append(failures, f"phase3m-{key.replace('_', '-')}-invalid")
            valid = False
    return valid


def _validate_auth(value: Any, failures: list[str]) -> bool:
    valid = _exact_true_map(value, AUTH_CHECK_KEYS)
    if not valid:
        _append(failures, "auth-rls-idor-smoke-incomplete")
    return valid


def _validate_db_cache(value: Any, failures: list[str], *, observed: datetime | None) -> bool:
    if not _exact_dict(value, DB_CACHE_KEYS):
        _append(failures, "database-cache-integrity-schema-invalid")
        return False
    valid = True
    for prefix in ("database", "cache"):
        before = value[f"{prefix}_before_sha256"]
        after = value[f"{prefix}_after_sha256"]
        matched = value[f"{prefix}_unchanged"]
        if not isinstance(before, str) or DIGEST_RE.fullmatch(before) is None or before != after:
            _append(failures, f"{prefix}-digest-mismatch")
            valid = False
        if type(matched) is not bool or matched is not True:
            _append(failures, f"{prefix}-unchanged-not-proven")
            valid = False
    before_at = _parse_time(value["captured_before_at"])
    after_at = _parse_time(value["captured_after_at"])
    if before_at is None or after_at is None or before_at >= after_at or (observed is not None and after_at > observed):
        _append(failures, "database-cache-capture-order-invalid")
        valid = False
    return valid


def _validate_crash_recovery(value: Any, failures: list[str]) -> bool:
    if not _exact_dict(value, CRASH_RECOVERY_KEYS):
        _append(failures, "crash-recovery-schema-invalid")
        return False
    valid = (
        type(value["instance_count"]) is int
        and value["instance_count"] >= 2
        and type(value["tested_operation_count"]) is int
        and value["tested_operation_count"] >= 1
        and value["crash_injected"] is True
        and value["recovery_converged"] is True
        and type(value["duplicate_effect_count"]) is int
        and value["duplicate_effect_count"] == 0
        and type(value["stale_fence_rejection_count"]) is int
        and value["stale_fence_rejection_count"] >= 1
        and type(value["orphaned_operation_count"]) is int
        and value["orphaned_operation_count"] == 0
        and value["non_synthetic"] is True
    )
    if not valid:
        _append(failures, "multi-instance-crash-recovery-incomplete")
    return valid


def _validate_rollback(value: Any, failures: list[str]) -> bool:
    if not _exact_dict(value, ROLLBACK_KEYS):
        _append(failures, "rollback-drill-schema-invalid")
        return False
    started = _parse_time(value["started_at"])
    completed = _parse_time(value["completed_at"])
    valid = (
        _safe_id(value["drill_id"])
        and started is not None
        and completed is not None
        and started < completed
        and value["rollback_completed"] is True
        and value["state_restored"] is True
        and isinstance(value["pre_state_sha256"], str)
        and DIGEST_RE.fullmatch(value["pre_state_sha256"]) is not None
        and value["pre_state_sha256"] == value["post_state_sha256"]
        and type(value["unexpected_effect_count"]) is int
        and value["unexpected_effect_count"] == 0
        and value["non_synthetic"] is True
    )
    if not valid:
        _append(failures, "rollback-drill-incomplete")
    return valid


def _validate_approvals(
    value: Any,
    failures: list[str],
    *,
    observed: datetime | None,
    expires: datetime | None,
    now: datetime,
) -> bool:
    if not _exact_dict(value, APPROVAL_KEYS):
        _append(failures, "approvals-schema-invalid")
        return False
    valid = True
    ids: list[str] = []
    times: dict[str, datetime] = {}
    for key, environment in APPROVAL_PURPOSES.items():
        entry = value[key]
        if not _exact_dict(entry, APPROVAL_ENTRY_KEYS):
            _append(failures, f"approval-{key.replace('_', '-')}-schema-invalid")
            valid = False
            continue
        if not isinstance(entry["approval_id"], str) or APPROVAL_ID_RE.fullmatch(entry["approval_id"]) is None:
            _append(failures, f"approval-{key.replace('_', '-')}-id-invalid")
            valid = False
        else:
            ids.append(entry["approval_id"])
        if not isinstance(entry["actor_id"], str) or ACTOR_ID_RE.fullmatch(entry["actor_id"]) is None:
            _append(failures, f"approval-{key.replace('_', '-')}-actor-invalid")
            valid = False
        if entry["environment"] != environment or entry["purpose"] != environment:
            _append(failures, f"approval-{key.replace('_', '-')}-boundary-invalid")
            valid = False
        approved_at = _parse_time(entry["approved_at"])
        if approved_at is None:
            _append(failures, f"approval-{key.replace('_', '-')}-time-invalid")
            valid = False
        else:
            times[key] = approved_at
            if approved_at > now + timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
                _append(failures, f"approval-{key.replace('_', '-')}-future")
                valid = False
    if len(ids) != 3 or len(set(ids)) != 3:
        _append(failures, "approval-ids-not-distinct")
        valid = False
    if observed is not None and all(key in times for key in APPROVAL_KEYS):
        if not (
            times["staging_migration"]
            <= times["execution_unlock"]
            <= times["production_release"]
        ):
            _append(failures, "approval-order-invalid")
            valid = False
        if times["production_release"] < observed:
            _append(failures, "production-approval-before-observation")
            valid = False
    if expires is not None and any(value > expires for value in times.values()):
        _append(failures, "approval-after-evidence-expiry")
        valid = False
    return valid


def build_report(
    evidence: Any,
    *,
    expected_commit: str,
    expected_run_id: str,
    expected_run_attempt: int,
    expected_repository: str,
    expected_workflow_ref: str,
    expected_environment: str = "staging",
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    now: datetime | None = None,
    initial_failures: Iterable[str] = (),
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    failures = list(dict.fromkeys(initial_failures))
    checks = {key: False for key in CHECK_KEYS}

    context_valid = (
        isinstance(expected_commit, str)
        and COMMIT_RE.fullmatch(expected_commit) is not None
        and isinstance(expected_run_id, str)
        and RUN_ID_RE.fullmatch(expected_run_id) is not None
        and type(expected_run_attempt) is int
        and expected_run_attempt >= 1
        and isinstance(expected_repository, str)
        and REPOSITORY_RE.fullmatch(expected_repository) is not None
        and isinstance(expected_workflow_ref, str)
        and 1 <= len(expected_workflow_ref) <= 256
        and expected_environment == "staging"
        and type(max_age_seconds) is int
        and 1 <= max_age_seconds <= 86400
    )
    if not context_valid:
        _append(failures, "expected-context-invalid")

    if not _exact_dict(evidence, EVIDENCE_KEYS):
        _append(failures, "evidence-schema-invalid")
        evidence = {}
    else:
        checks["evidence_schema"] = (
            type(evidence["schema_version"]) is int
            and evidence["schema_version"] == SCHEMA_VERSION
            and evidence["evidence_schema"] == EVIDENCE_SCHEMA
        )
        if not checks["evidence_schema"]:
            _append(failures, "evidence-schema-version-invalid")

    provenance = evidence.get("provenance") if isinstance(evidence, dict) else None
    observed: datetime | None = None
    expires: datetime | None = None
    if context_valid:
        provenance_valid, observed, expires = _validate_provenance(
            provenance,
            failures,
            expected_commit=expected_commit,
            expected_run_id=expected_run_id,
            expected_run_attempt=expected_run_attempt,
            expected_repository=expected_repository,
            expected_workflow_ref=expected_workflow_ref,
            expected_environment=expected_environment,
            now=now,
            max_age_seconds=max_age_seconds,
        )
        checks["expected_context"] = provenance_valid
        checks["temporal_bounds"] = provenance_valid and observed is not None and expires is not None
        checks["artifact_provenance"] = provenance_valid

    repository_id = str(provenance.get("repository_id")) if isinstance(provenance, dict) else None
    checks["provider_identities"] = _validate_providers(
        evidence.get("provider_identities"), failures, expected_commit=expected_commit, repository_id=repository_id
    )
    checks["phase3m_bootstrap"] = _validate_phase3m(
        evidence.get("phase3m_bootstrap"), failures, expected_commit=expected_commit
    )
    checks["auth_rls_idor_smoke"] = _validate_auth(evidence.get("auth_rls_idor_smoke"), failures)
    checks["database_cache_integrity"] = _validate_db_cache(
        evidence.get("database_cache_integrity"), failures, observed=observed
    )
    checks["multi_instance_crash_recovery"] = _validate_crash_recovery(
        evidence.get("multi_instance_crash_recovery"), failures
    )
    checks["rollback_drill"] = _validate_rollback(evidence.get("rollback_drill"), failures)
    checks["saga_checks"] = _exact_true_map(evidence.get("saga_checks"), SAGA_CHECK_KEYS)
    if not checks["saga_checks"]:
        _append(failures, "saga-checks-incomplete")
    checks["staging_checks"] = _exact_true_map(evidence.get("staging_checks"), STAGING_CHECK_KEYS)
    if not checks["staging_checks"]:
        _append(failures, "staging-checks-incomplete")
    checks["approvals"] = _validate_approvals(
        evidence.get("approvals"), failures, observed=observed, expires=expires, now=now
    )
    checks["sanitization"] = _is_sanitized(evidence)
    if not checks["sanitization"]:
        _append(failures, "evidence-sanitization-failed")

    saga_complete = checks["saga_checks"] and checks["multi_instance_crash_recovery"]
    staging_complete = all(
        checks[key]
        for key in (
            "provider_identities",
            "phase3m_bootstrap",
            "auth_rls_idor_smoke",
            "database_cache_integrity",
            "multi_instance_crash_recovery",
            "rollback_drill",
            "staging_checks",
            "artifact_provenance",
        )
    )
    approvals_complete = checks["approvals"]
    success = not failures and all(checks.values()) and saga_complete and staging_complete and approvals_complete

    def projection(key: str, expected_keys: frozenset[str]) -> dict[str, Any]:
        value = evidence.get(key) if isinstance(evidence, dict) else None
        return dict(value) if _exact_dict(value, expected_keys) and _is_sanitized(value) else {}

    provider_projection = projection("provider_identities", PROVIDER_KEYS)
    phase3m_projection = projection("phase3m_bootstrap", PHASE3M_KEYS)
    saga_projection = projection("saga_checks", SAGA_CHECK_KEYS)
    staging_projection = projection("staging_checks", STAGING_CHECK_KEYS)
    approval_projection = projection("approvals", APPROVAL_KEYS)
    provenance_projection = provenance if _exact_dict(provenance, PROVENANCE_KEYS) and _is_sanitized(provenance) else {}

    return {
        "report_schema": REPORT_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "success": success,
        "trusted": success,
        "verdict": "trusted" if success else "fail",
        "verdict_reason": "trusted-staging-evidence" if success else (failures[0] if failures else "gate-incomplete"),
        "evaluated_commit_sha": expected_commit if COMMIT_RE.fullmatch(str(expected_commit)) else None,
        "evaluated_run_id": expected_run_id if RUN_ID_RE.fullmatch(str(expected_run_id)) else None,
        "evaluated_run_attempt": expected_run_attempt if type(expected_run_attempt) is int and expected_run_attempt >= 1 else None,
        "repository": expected_repository if REPOSITORY_RE.fullmatch(str(expected_repository)) else None,
        "environment": expected_environment if expected_environment == "staging" else None,
        "observed_at": provenance_projection.get("observed_at"),
        "expires_at": provenance_projection.get("expires_at"),
        "evidence_id": provenance_projection.get("evidence_id"),
        "provider_identities": provider_projection,
        "phase3m_bootstrap": phase3m_projection,
        "saga_checks": saga_projection,
        "staging_checks": staging_projection,
        "approvals": approval_projection,
        "artifact_provenance": {
            "name": provenance_projection.get("source_artifact_name"),
            "id": provenance_projection.get("source_artifact_id"),
            "sha256": provenance_projection.get("source_artifact_sha256"),
        },
        "checks": checks,
        "failure_codes": failures,
        "saga_prerequisites_complete": success and saga_complete,
        "staging_prerequisites_complete": success and staging_complete,
        "approvals_complete": success and approvals_complete,
        "l3_eligible": success,
        "production_ready": success,
    }


def validate_gate_report(
    report: Any,
    *,
    expected_commit: str,
    expected_run_id: str,
    expected_repository: str,
    expected_run_attempt: int | None = None,
    expected_repository_id: str | None = None,
    now: datetime | None = None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> tuple[bool, tuple[str, ...]]:
    failures: list[str] = []
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not _exact_dict(report, REPORT_KEYS):
        return False, ("report-schema-invalid",)
    if not (
        COMMIT_RE.fullmatch(str(expected_commit))
        and RUN_ID_RE.fullmatch(str(expected_run_id))
        and REPOSITORY_RE.fullmatch(str(expected_repository))
        and (
            expected_run_attempt is None
            or (type(expected_run_attempt) is int and expected_run_attempt >= 1)
        )
        and (
            expected_repository_id is None
            or RUN_ID_RE.fullmatch(str(expected_repository_id)) is not None
        )
        and type(max_age_seconds) is int
        and 1 <= max_age_seconds <= 86400
    ):
        return False, ("report-expected-context-invalid",)
    if report["report_schema"] != REPORT_SCHEMA or report["schema_version"] != SCHEMA_VERSION:
        _append(failures, "report-schema-version-invalid")
    if report["evaluated_commit_sha"] != expected_commit:
        _append(failures, "report-commit-mismatch")
    if report["evaluated_run_id"] != expected_run_id:
        _append(failures, "report-run-mismatch")
    if report["repository"] != expected_repository:
        _append(failures, "report-repository-mismatch")
    if not (
        report["success"] is True
        and report["trusted"] is True
        and report["verdict"] == "trusted"
        and report["verdict_reason"] == "trusted-staging-evidence"
        and report["environment"] == "staging"
        and report["failure_codes"] == []
    ):
        _append(failures, "report-not-trusted")
    if type(report["evaluated_run_attempt"]) is not int or report["evaluated_run_attempt"] < 1:
        _append(failures, "report-run-attempt-invalid")
    elif expected_run_attempt is not None and report["evaluated_run_attempt"] != expected_run_attempt:
        _append(failures, "report-run-attempt-mismatch")
    if not _exact_true_map(report["checks"], CHECK_KEYS):
        _append(failures, "report-checks-incomplete")
    for key in (
        "saga_prerequisites_complete",
        "staging_prerequisites_complete",
        "approvals_complete",
        "l3_eligible",
        "production_ready",
    ):
        if report[key] is not True:
            _append(failures, f"report-{key.replace('_', '-')}-false")
    observed = _parse_time(report["observed_at"])
    expires = _parse_time(report["expires_at"])
    if not (
        observed is not None
        and expires is not None
        and observed <= now + timedelta(seconds=MAX_CLOCK_SKEW_SECONDS)
        and now - observed <= timedelta(seconds=max_age_seconds)
        and expires > observed
        and expires - observed <= timedelta(hours=24)
        and now <= expires
    ):
        _append(failures, "report-temporal-boundary-invalid")
    if not isinstance(report["evidence_id"], str) or EVIDENCE_ID_RE.fullmatch(report["evidence_id"]) is None:
        _append(failures, "report-evidence-id-invalid")
    if not _exact_true_map(report["saga_checks"], SAGA_CHECK_KEYS):
        _append(failures, "report-saga-checks-invalid")
    if not _exact_true_map(report["staging_checks"], STAGING_CHECK_KEYS):
        _append(failures, "report-staging-checks-invalid")
    provider_repository_id: str | None = expected_repository_id
    if _exact_dict(report["provider_identities"], PROVIDER_KEYS):
        github = report["provider_identities"].get("github")
        if provider_repository_id is None and _exact_dict(github, GITHUB_PROVIDER_KEYS):
            provider_repository_id = str(github["repository_id"])
    provider_failures: list[str] = []
    if not _validate_providers(
        report["provider_identities"],
        provider_failures,
        expected_commit=expected_commit,
        repository_id=provider_repository_id,
    ):
        _append(failures, "report-provider-identities-invalid")
    phase3m_failures: list[str] = []
    if not _validate_phase3m(report["phase3m_bootstrap"], phase3m_failures, expected_commit=expected_commit):
        _append(failures, "report-phase3m-bootstrap-invalid")
    approval_failures: list[str] = []
    if not _validate_approvals(
        report["approvals"],
        approval_failures,
        observed=observed,
        expires=expires,
        now=now,
    ):
        _append(failures, "report-approvals-invalid")
    artifact = report["artifact_provenance"]
    if not (
        _exact_dict(artifact, frozenset({"name", "id", "sha256"}))
        and _safe_id(artifact["name"])
        and RUN_ID_RE.fullmatch(str(artifact["id"]))
        and isinstance(artifact["sha256"], str)
        and DIGEST_RE.fullmatch(artifact["sha256"]) is not None
    ):
        _append(failures, "report-artifact-provenance-invalid")
    if not _is_sanitized(report):
        _append(failures, "report-sanitization-failed")
    return not failures, tuple(failures)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _max_age(value: str) -> int:
    parsed = _positive_int(value)
    if parsed > 86400:
        raise argparse.ArgumentTypeError("max age must be at most 86400 seconds")
    return parsed


def _safe_report_path(path: Path) -> Path:
    absolute = path if path.is_absolute() else ROOT / path
    resolved = absolute.resolve(strict=False)
    reports = (ROOT / "reports").resolve()
    try:
        resolved.relative_to(reports)
    except ValueError as exc:
        raise ValueError("report-path-outside-reports") from exc
    if resolved.suffix != ".json" or resolved.is_symlink():
        raise ValueError("report-path-invalid")
    return resolved


def write_json_atomic(path: Path, payload: Any) -> None:
    if not _is_sanitized(payload):
        raise ValueError("report-sanitization-failed")
    serialized = json.dumps(payload, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2) + "\n"
    if len(serialized.encode("utf-8")) > MAX_REPORT_BYTES:
        raise ValueError("report-too-large")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(serialized, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify trusted, sanitized Phase3N staging evidence.")
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--expected-run-attempt", required=True, type=_positive_int)
    parser.add_argument("--expected-repository", required=True)
    parser.add_argument("--expected-workflow-ref", required=True)
    parser.add_argument("--expected-environment", default="staging", choices=("staging",))
    parser.add_argument("--max-age-seconds", type=_max_age, default=DEFAULT_MAX_AGE_SECONDS)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    evidence, load_failures = load_json(args.evidence)
    report = build_report(
        evidence,
        expected_commit=args.expected_commit,
        expected_run_id=args.expected_run_id,
        expected_run_attempt=args.expected_run_attempt,
        expected_repository=args.expected_repository,
        expected_workflow_ref=args.expected_workflow_ref,
        expected_environment=args.expected_environment,
        max_age_seconds=args.max_age_seconds,
        initial_failures=load_failures,
    )
    try:
        report_path = _safe_report_path(args.report)
        write_json_atomic(report_path, report)
    except (OSError, ValueError):
        print('{"success":false,"trusted":false,"verdict_reason":"report-write-failed"}')
        return 1
    print(
        json.dumps(
            {
                "l3_eligible": report["l3_eligible"],
                "production_ready": report["production_ready"],
                "report": report_path.relative_to(ROOT).as_posix(),
                "success": report["success"],
                "trusted": report["trusted"],
                "verdict_reason": report["verdict_reason"],
            },
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
        )
    )
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
