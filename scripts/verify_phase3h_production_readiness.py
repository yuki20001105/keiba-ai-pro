from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
PHASE3G_VERIFIER_PATH = ROOT / "scripts" / "verify_phase3g_runtime_evidence.py"
REPORT_PATH = ROOT / "reports" / "phase3h_production_readiness_gate.json"

REPORT_SCHEMA = "phase3h-production-readiness-gate-report"
SCHEMA_VERSION = 1
DEFAULT_MAX_AGE_SECONDS = 900
MAX_MANIFEST_BYTES = 32 * 1024
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")

MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "decision_mode",
        "production_ready_claimed",
        "l3_claimed",
        "cross_store_saga",
        "staging_evidence",
        "approvals",
    }
)
SAGA_BLOCKERS = {
    "stable_operation_and_job_binding": "saga-operation-job-binding-unproven",
    "idempotent_reserve_and_consume": "saga-reserve-consume-unproven",
    "atomic_sqlite_job_saga_outbox": "saga-atomic-sqlite-prepare-unproven",
    "consume_before_worker_dispatch": "saga-consume-before-dispatch-unproven",
    "durable_recovery": "saga-durable-recovery-unproven",
    "idempotent_compensation": "saga-compensation-unproven",
    "lease_and_fencing": "saga-lease-fencing-unproven",
    "multi_instance_safety": "saga-multi-instance-safety-unproven",
    "failure_injection_matrix": "saga-failure-matrix-unproven",
}
STAGING_BLOCKERS = {
    "review_ledger_migration_applied": "staging-review-ledger-migration-unapplied",
    "bounded_external_http_validation": "staging-external-http-evidence-missing",
    "zero_db_mutation_proven": "staging-zero-db-mutation-evidence-missing",
    "non_synthetic_crash_recovery": "staging-crash-recovery-evidence-missing",
    "trusted_evidence_producer": "trusted-attestation-producer-missing",
}
APPROVAL_BLOCKERS = {
    "staging_migration": "approval-staging-migration-missing",
    "execution_unlock": "approval-execution-unlock-missing",
    "production_release": "approval-production-release-missing",
}
ALL_BLOCKERS = tuple(
    sorted(
        ["phase3g-runtime-synthetic-only"]
        + list(SAGA_BLOCKERS.values())
        + list(STAGING_BLOCKERS.values())
        + list(APPROVAL_BLOCKERS.values())
    )
)


class DuplicateKeyError(ValueError):
    pass


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _append_failure(failures: list[str], code: str) -> None:
    if code not in failures:
        failures.append(code)


def _load_json(path: Path, *, max_bytes: int, prefix: str) -> tuple[Any | None, list[str]]:
    try:
        stat = path.stat()
    except OSError:
        return None, [f"{prefix}-file-unavailable"]
    if not path.is_file():
        return None, [f"{prefix}-file-unavailable"]
    if stat.st_size <= 0:
        return None, [f"{prefix}-file-empty"]
    if stat.st_size > max_bytes:
        return None, [f"{prefix}-file-too-large"]
    try:
        text = path.read_bytes().decode("utf-8", errors="strict")
        return (
            json.loads(
                text,
                object_pairs_hook=_object_without_duplicates,
                parse_constant=_reject_json_constant,
            ),
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


def load_manifest(path: Path) -> tuple[Any | None, list[str]]:
    return _load_json(path, max_bytes=MAX_MANIFEST_BYTES, prefix="manifest")


def _load_phase3g_verifier() -> ModuleType:
    spec = importlib.util.spec_from_file_location("phase3h_phase3g_verifier", PHASE3G_VERIFIER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Phase3G verifier is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validate_boolean_group(
    value: Any,
    expected: dict[str, str],
    failures: list[str],
    group_name: str,
) -> bool:
    if not isinstance(value, dict) or frozenset(value) != frozenset(expected):
        _append_failure(failures, f"manifest-{group_name}-schema-invalid")
        return False
    valid = True
    for key in expected:
        if type(value[key]) is not bool:
            _append_failure(failures, f"manifest-{group_name}-value-invalid")
            valid = False
        elif value[key] is True:
            _append_failure(failures, "manifest-self-asserted-prerequisite")
            valid = False
    return valid


def _validate_manifest(manifest: Any, failures: list[str]) -> bool:
    if not isinstance(manifest, dict) or frozenset(manifest) != MANIFEST_KEYS:
        _append_failure(failures, "manifest-schema-invalid")
        return False
    valid = True
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != SCHEMA_VERSION:
        _append_failure(failures, "manifest-schema-version-invalid")
        valid = False
    if manifest["decision_mode"] != "repository_current_state":
        _append_failure(failures, "manifest-decision-mode-invalid")
        valid = False
    for key in ("production_ready_claimed", "l3_claimed"):
        if type(manifest[key]) is not bool:
            _append_failure(failures, f"manifest-{key.replace('_', '-')}-invalid")
            valid = False
        elif manifest[key] is True:
            _append_failure(failures, "manifest-self-asserted-readiness")
            valid = False
    valid = _validate_boolean_group(manifest["cross_store_saga"], SAGA_BLOCKERS, failures, "saga") and valid
    valid = _validate_boolean_group(manifest["staging_evidence"], STAGING_BLOCKERS, failures, "staging") and valid
    valid = _validate_boolean_group(manifest["approvals"], APPROVAL_BLOCKERS, failures, "approvals") and valid
    return valid


def _safe_phase3g_projection(evidence: Any) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        return {
            "success": False,
            "tested_commit_sha": None,
            "migration_sha256": None,
            "synthetic": None,
            "l3_eligible": False,
        }
    commit = evidence.get("tested_commit_sha")
    migration_hash = evidence.get("migration_sha256")
    return {
        "success": evidence.get("success") is True,
        "tested_commit_sha": commit if isinstance(commit, str) and COMMIT_PATTERN.fullmatch(commit) else None,
        "migration_sha256": migration_hash
        if isinstance(migration_hash, str) and re.fullmatch(r"[0-9a-f]{64}", migration_hash)
        else None,
        "synthetic": evidence.get("synthetic") if type(evidence.get("synthetic")) is bool else None,
        "l3_eligible": False,
    }


def build_report(
    manifest: Any,
    phase3g_evidence: Any,
    *,
    expected_commit: str | None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    now: datetime | None = None,
    initial_failures: Iterable[str] = (),
    require_ready: bool = False,
) -> dict[str, Any]:
    failures = list(dict.fromkeys(initial_failures))
    manifest_valid = _validate_manifest(manifest, failures)

    expected_commit_valid = isinstance(expected_commit, str) and COMMIT_PATTERN.fullmatch(expected_commit) is not None
    if not expected_commit_valid:
        _append_failure(failures, "expected-commit-required")

    phase3g_report: dict[str, Any] | None = None
    try:
        phase3g = _load_phase3g_verifier()
        phase3g_report = phase3g.build_report(
            phase3g_evidence,
            max_age_seconds=max_age_seconds,
            expected_commit=expected_commit,
            now=now,
        )
    except Exception:
        _append_failure(failures, "phase3g-verifier-unavailable")

    phase3g_valid = bool(phase3g_report and phase3g_report.get("success") is True)
    if not phase3g_valid:
        _append_failure(failures, "phase3g-runtime-evidence-invalid")
    if not (
        isinstance(phase3g_evidence, dict)
        and phase3g_evidence.get("evidence_mode") == "synthetic"
        and phase3g_evidence.get("environment") == "ci-disposable"
        and phase3g_evidence.get("database_scope") == "disposable_docker"
        and phase3g_evidence.get("network_mode") == "none"
        and phase3g_evidence.get("synthetic") is True
        and phase3g_evidence.get("l3_eligible") is False
    ):
        _append_failure(failures, "phase3g-runtime-boundary-invalid")
        phase3g_valid = False

    assessment_valid = not failures
    blockers = list(ALL_BLOCKERS) if assessment_valid else []
    if require_ready and assessment_valid:
        _append_failure(failures, "production-readiness-required")

    success = not failures
    checks = {
        "manifest_schema": manifest_valid,
        "expected_commit": expected_commit_valid,
        "phase3g_runtime_evidence": phase3g_valid,
        "self_asserted_readiness_rejected": manifest_valid,
        "production_promotion_policy": not require_ready,
    }
    return {
        "report_schema": REPORT_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "success": success,
        "verdict": "not-ready" if success else "fail",
        "verdict_reason": "production-readiness-prerequisites-incomplete" if success else failures[0],
        "production_ready": False,
        "l3_eligible": False,
        "readiness_required": require_ready,
        "evaluated_commit_sha": expected_commit if expected_commit_valid else None,
        "phase3g_evidence": _safe_phase3g_projection(phase3g_evidence),
        "blockers": blockers,
        "checks": checks,
        "failure_codes": failures,
    }


def _positive_max_age(value: str) -> int:
    parsed = int(value)
    if parsed < 1 or parsed > 86400:
        raise argparse.ArgumentTypeError("max age must be between 1 and 86400 seconds")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Derive the fail-closed Phase 3H production readiness decision.")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--phase3g-evidence", required=True, type=Path)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--max-age-seconds", type=_positive_max_age, default=DEFAULT_MAX_AGE_SECONDS)
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Fail the process when the validated decision is not production-ready (promotion policy).",
    )
    return parser.parse_args(argv)


def _write_report_atomic(report: dict[str, Any]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT_PATH.with_name(f".{REPORT_PATH.name}.{os.getpid()}.tmp")
    payload = json.dumps(report, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2) + "\n"
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, REPORT_PATH)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    manifest, manifest_failures = load_manifest(args.manifest)

    initial_failures = list(manifest_failures)
    phase3g_evidence: Any = None
    try:
        phase3g = _load_phase3g_verifier()
        phase3g_evidence, phase3g_failures = phase3g.load_evidence(args.phase3g_evidence)
        if phase3g_failures:
            initial_failures.append("phase3g-evidence-file-invalid")
    except Exception:
        initial_failures.append("phase3g-evidence-loader-unavailable")

    report = build_report(
        manifest,
        phase3g_evidence,
        expected_commit=args.expected_commit,
        max_age_seconds=args.max_age_seconds,
        now=datetime.now(timezone.utc),
        initial_failures=initial_failures,
        require_ready=args.require_ready,
    )
    _write_report_atomic(report)
    print(
        json.dumps(
            {
                "l3_eligible": report["l3_eligible"],
                "production_ready": report["production_ready"],
                "report": str(REPORT_PATH.relative_to(ROOT)),
                "success": report["success"],
                "verdict": report["verdict"],
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
