from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_RELATIVE_PATH = "supabase/migrations/20260718_scrape_uncertainty_review_ledger.sql"
MIGRATION_PATH = ROOT / MIGRATION_RELATIVE_PATH
REPORT_PATH = ROOT / "reports" / "phase3g_runtime_evidence_gate.json"

REPORT_SCHEMA = "phase3g-runtime-evidence-gate-report"
SCHEMA_VERSION = 1
DEFAULT_MAX_AGE_SECONDS = 900
MAX_MAX_AGE_SECONDS = 86_400
MAX_INPUT_BYTES = 64 * 1024
MAX_FUTURE_SKEW_SECONDS = 300

TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "evidence_mode",
        "environment",
        "synthetic",
        "l3_eligible",
        "success",
        "database_scope",
        "network_mode",
        "image",
        "tested_commit_sha",
        "migration_sha256",
        "observed_at",
        "catalog_checks",
        "behavioral_checks",
        "cleanup",
    }
)
CATALOG_CHECK_KEYS = (
    "migration_compiles",
    "request_table_present",
    "event_table_present",
    "rls_enabled",
    "no_browser_policies",
    "no_browser_table_grants",
    "service_role_rpc_signatures",
    "rpc_security_definer",
    "rpc_search_path_fixed",
    "immutable_event_trigger",
    "review_only_constraints",
    "no_execution_rpc",
)
BEHAVIORAL_CHECK_KEYS = (
    "idempotent_create",
    "self_approval_rejected",
    "cas_conflict_rejected",
    "concurrent_create_serialized",
    "concurrent_decision_serialized",
    "expiry_materialized",
    "immutable_event_mutation_rejected",
    "review_only_flags_enforced",
    "no_execution_rpc_observed",
)
CLEANUP_KEYS = ("attempted", "container_absent")

COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SYNTHETIC_IMAGE = "postgres:17.6-bookworm@sha256:f3bd19c606e442c3d7bdfa8002e03fe260a1023351e0ea4598032022b68dd6e3"
IMAGE_PATTERN = re.compile(
    r"^(?:postgres:[0-9]+(?:\.[0-9]+)?-[a-z0-9][a-z0-9._-]{0,40}@sha256:[0-9a-f]{64}|managed-postgres)$"
)
CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?:^|[\s\"'(])(?:[A-Za-z]:[\\/]|\\\\|/(?!/)|~[\\/]|file://)",
    re.IGNORECASE,
)
SECRET_VALUE_PATTERNS = (
    re.compile(r"postgres(?:ql)?://", re.IGNORECASE),
    re.compile(
        r"(?:password|passwd|pwd|secret|token|api[_-]?key|service[_-]?role[_-]?key|authorization)\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{12,}\b", re.IGNORECASE),
    re.compile(r"\bsb_secret_[A-Za-z0-9_-]{12,}\b", re.IGNORECASE),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)
PROHIBITED_KEY_FRAGMENTS = (
    "raw_row",
    "row_data",
    "database_url",
    "connection_string",
    "service_role_key",
)
PROHIBITED_KEYS = frozenset(
    {
        "rows",
        "records",
        "data",
        "dsn",
        "password",
        "secret",
        "token",
        "authorization",
        "cookie",
        "stdout",
        "stderr",
        "raw_error",
    }
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


def expected_migration_sha256() -> str | None:
    try:
        return hashlib.sha256(MIGRATION_PATH.read_bytes()).hexdigest()
    except OSError:
        return None


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) < 20 or len(value) > 40:
        return None
    if CONTROL_CHARACTER_PATTERN.search(value):
        return None
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _is_safe_image(value: Any) -> bool:
    return isinstance(value, str) and IMAGE_PATTERN.fullmatch(value) is not None


def _iter_items(value: Any) -> Iterable[tuple[str | None, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from _iter_items(child)
    elif isinstance(value, list):
        for child in value:
            yield None, child
            yield from _iter_items(child)


def _contains_prohibited_content(value: Any) -> bool:
    for key, child in _iter_items(value):
        if key is not None:
            lowered = key.lower()
            if lowered in PROHIBITED_KEYS or any(fragment in lowered for fragment in PROHIBITED_KEY_FRAGMENTS):
                return True
        if not isinstance(child, str):
            continue
        if CONTROL_CHARACTER_PATTERN.search(child):
            return True
        if child != MIGRATION_RELATIVE_PATH and ABSOLUTE_PATH_PATTERN.search(child):
            return True
        if any(pattern.search(child) for pattern in SECRET_VALUE_PATTERNS):
            return True
    return False


def _has_exact_keys(value: Any, expected: frozenset[str] | tuple[str, ...]) -> bool:
    return isinstance(value, dict) and set(value) == set(expected)


def _append_failure(failures: list[str], code: str) -> None:
    if code not in failures:
        failures.append(code)


def _safe_metadata(evidence: Any) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        return {
            "environment": None,
            "evidence_mode": None,
            "synthetic": None,
            "database_scope": None,
            "network_mode": None,
            "image": None,
            "tested_commit_sha": None,
            "migration_sha256": None,
            "observed_at": None,
        }
    return {
        "environment": evidence.get("environment") if evidence.get("environment") in {"ci-disposable", "staging"} else None,
        "evidence_mode": evidence.get("evidence_mode") if evidence.get("evidence_mode") in {"synthetic", "staging"} else None,
        "synthetic": evidence.get("synthetic") if type(evidence.get("synthetic")) is bool else None,
        "database_scope": evidence.get("database_scope") if evidence.get("database_scope") in {"disposable_docker", "staging"} else None,
        "network_mode": evidence.get("network_mode") if evidence.get("network_mode") in {"none", "staging_only"} else None,
        "image": evidence.get("image") if _is_safe_image(evidence.get("image")) else None,
        "tested_commit_sha": evidence.get("tested_commit_sha") if isinstance(evidence.get("tested_commit_sha"), str) and COMMIT_PATTERN.fullmatch(evidence["tested_commit_sha"]) else None,
        "migration_sha256": evidence.get("migration_sha256") if isinstance(evidence.get("migration_sha256"), str) and SHA256_PATTERN.fullmatch(evidence["migration_sha256"]) else None,
        "observed_at": evidence.get("observed_at") if _parse_timestamp(evidence.get("observed_at")) is not None else None,
    }


def _empty_checks() -> dict[str, bool]:
    checks = {
        "schema": False,
        "environment": False,
        "mode_boundary": False,
        "producer_success": False,
        "producer_l3_boundary": False,
        "image": False,
        "tested_commit": False,
        "expected_commit": False,
        "migration_sha256": False,
        "freshness": False,
        "safe_content": False,
    }
    checks.update({f"catalog.{key}": False for key in CATALOG_CHECK_KEYS})
    checks.update({f"behavioral.{key}": False for key in BEHAVIORAL_CHECK_KEYS})
    checks.update({f"cleanup.{key}": False for key in CLEANUP_KEYS})
    return checks


def build_report(
    evidence: Any,
    *,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    expected_commit: str | None = None,
    now: datetime | None = None,
    initial_failures: Iterable[str] = (),
) -> dict[str, Any]:
    failures = list(dict.fromkeys(initial_failures))
    checks = _empty_checks()
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    if not isinstance(evidence, dict):
        _append_failure(failures, "evidence-not-object")
    elif _contains_prohibited_content(evidence):
        _append_failure(failures, "prohibited-evidence-content")

    if isinstance(evidence, dict) and set(evidence) != TOP_LEVEL_KEYS:
        _append_failure(failures, "top-level-schema-mismatch")

    if isinstance(evidence, dict):
        checks["schema"] = type(evidence.get("schema_version")) is int and evidence.get("schema_version") == SCHEMA_VERSION
        if not checks["schema"]:
            _append_failure(failures, "schema-version-mismatch")

        mode = evidence.get("evidence_mode")
        environment = evidence.get("environment")
        checks["environment"] = (
            (mode == "synthetic" and environment == "ci-disposable")
            or (mode == "staging" and environment == "staging")
        )
        if not checks["environment"]:
            _append_failure(failures, "environment-mode-mismatch")

        synthetic = evidence.get("synthetic")
        database_scope = evidence.get("database_scope")
        network_mode = evidence.get("network_mode")
        checks["mode_boundary"] = (
            (mode == "synthetic" and synthetic is True and database_scope == "disposable_docker" and network_mode == "none")
            or (mode == "staging" and synthetic is False and database_scope == "staging" and network_mode == "staging_only")
        )
        if not checks["mode_boundary"]:
            _append_failure(failures, "evidence-mode-boundary-mismatch")

        checks["producer_success"] = evidence.get("success") is True
        if not checks["producer_success"]:
            _append_failure(failures, "producer-reported-failure")

        producer_l3 = evidence.get("l3_eligible")
        # Until evidence has an independently authenticated producer, it may
        # describe runtime compatibility but must never self-attest L3.
        checks["producer_l3_boundary"] = producer_l3 is False
        if not checks["producer_l3_boundary"]:
            _append_failure(failures, "producer-l3-boundary-mismatch")

        image = evidence.get("image")
        checks["image"] = (
            _is_safe_image(image)
            and (mode != "synthetic" or image == SYNTHETIC_IMAGE)
        )
        if not checks["image"]:
            _append_failure(failures, "invalid-runtime-image")

        tested_commit = evidence.get("tested_commit_sha")
        checks["tested_commit"] = isinstance(tested_commit, str) and COMMIT_PATTERN.fullmatch(tested_commit) is not None
        if not checks["tested_commit"]:
            _append_failure(failures, "invalid-tested-commit")

        if expected_commit is None:
            checks["expected_commit"] = False
            _append_failure(failures, "expected-commit-required")
        else:
            valid_expected = isinstance(expected_commit, str) and COMMIT_PATTERN.fullmatch(expected_commit) is not None
            checks["expected_commit"] = bool(valid_expected and tested_commit == expected_commit)
            if not valid_expected:
                _append_failure(failures, "invalid-expected-commit")
            elif tested_commit != expected_commit:
                _append_failure(failures, "tested-commit-mismatch")

        checks["migration_sha256"] = evidence.get("migration_sha256") == expected_migration_sha256()
        if not checks["migration_sha256"]:
            _append_failure(failures, "migration-sha256-mismatch")

        observed = _parse_timestamp(evidence.get("observed_at"))
        if observed is None:
            _append_failure(failures, "invalid-observed-at")
        else:
            age_seconds = (now_utc - observed).total_seconds()
            checks["freshness"] = -MAX_FUTURE_SKEW_SECONDS <= age_seconds <= max_age_seconds
            if age_seconds < -MAX_FUTURE_SKEW_SECONDS:
                _append_failure(failures, "observed-at-in-future")
            elif age_seconds > max_age_seconds:
                _append_failure(failures, "stale-evidence")

        catalog = evidence.get("catalog_checks")
        if not _has_exact_keys(catalog, CATALOG_CHECK_KEYS):
            _append_failure(failures, "catalog-check-schema-mismatch")
        else:
            for key in CATALOG_CHECK_KEYS:
                passed = type(catalog.get(key)) is bool and catalog[key] is True
                checks[f"catalog.{key}"] = passed
                if not passed:
                    _append_failure(failures, f"catalog-check-failed:{key}")

        behavioral = evidence.get("behavioral_checks")
        if not _has_exact_keys(behavioral, BEHAVIORAL_CHECK_KEYS):
            _append_failure(failures, "behavioral-check-schema-mismatch")
        else:
            for key in BEHAVIORAL_CHECK_KEYS:
                passed = type(behavioral.get(key)) is bool and behavioral[key] is True
                checks[f"behavioral.{key}"] = passed
                if not passed:
                    _append_failure(failures, f"behavioral-check-failed:{key}")

        cleanup = evidence.get("cleanup")
        if not _has_exact_keys(cleanup, CLEANUP_KEYS):
            _append_failure(failures, "cleanup-schema-mismatch")
        else:
            for key in CLEANUP_KEYS:
                passed = type(cleanup.get(key)) is bool and cleanup[key] is True
                checks[f"cleanup.{key}"] = passed
                if not passed:
                    _append_failure(failures, f"cleanup-check-failed:{key}")

    checks["safe_content"] = isinstance(evidence, dict) and not _contains_prohibited_content(evidence)
    success = not failures and all(checks.values())
    mode = evidence.get("evidence_mode") if isinstance(evidence, dict) else None
    # Contract validation is deliberately not an attestation mechanism.
    # Staging L3 remains disabled until a trusted producer/signature boundary
    # is designed and reviewed.
    l3_eligible = False
    if success and mode == "synthetic":
        reason = "synthetic-runtime-contract-compatible"
    elif success:
        reason = "staging-runtime-contract-compatible-not-l3-eligible"
    else:
        reason = failures[0] if failures else "runtime-evidence-invalid"

    return {
        "schema": REPORT_SCHEMA,
        "version": SCHEMA_VERSION,
        "success": success,
        "verdict": "pass" if success else "fail",
        "verdict_reason": reason,
        "l3_eligible": l3_eligible,
        "evidence": _safe_metadata(evidence),
        "checks": checks,
        "failure_codes": failures,
    }


def load_evidence(path: Path) -> tuple[Any, list[str]]:
    try:
        size = path.stat().st_size
    except OSError:
        return None, ["evidence-file-unavailable"]
    if size <= 0:
        return None, ["evidence-file-empty"]
    if size > MAX_INPUT_BYTES:
        return None, ["evidence-file-too-large"]
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="strict")
        parsed = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except DuplicateKeyError:
        return None, ["duplicate-json-key"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None, ["invalid-json"]
    return parsed, []


def write_report(report: dict[str, Any]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(REPORT_PATH)


def _positive_max_age(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("max age must be an integer") from exc
    if not 1 <= parsed <= MAX_MAX_AGE_SECONDS:
        raise argparse.ArgumentTypeError(f"max age must be between 1 and {MAX_MAX_AGE_SECONDS}")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a sanitized Phase 3G runtime-evidence bundle.")
    parser.add_argument("--evidence", required=True, type=Path, help="Path to the evidence JSON file.")
    parser.add_argument(
        "--max-age-seconds",
        type=_positive_max_age,
        default=DEFAULT_MAX_AGE_SECONDS,
        help="Maximum permitted evidence age (1..86400 seconds).",
    )
    parser.add_argument(
        "--expected-commit",
        help="Required 40-hex commit for every evidence mode.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    evidence, failures = load_evidence(args.evidence)
    report = build_report(
        evidence,
        max_age_seconds=args.max_age_seconds,
        expected_commit=args.expected_commit,
        initial_failures=failures,
    )
    write_report(report)
    print(
        json.dumps(
            {
                "success": report["success"],
                "verdict": report["verdict"],
                "verdict_reason": report["verdict_reason"],
                "l3_eligible": report["l3_eligible"],
                "report": str(REPORT_PATH.relative_to(ROOT)).replace("\\", "/"),
            },
            sort_keys=True,
        )
    )
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
