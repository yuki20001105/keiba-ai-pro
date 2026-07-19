from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "reports" / "phase3i_saga_failure_injection_gate.json"
PHASE3H_VERIFIER_PATH = ROOT / "scripts" / "verify_phase3h_production_readiness.py"

REPORT_SCHEMA = "phase3i-saga-failure-injection-gate-report"
SCHEMA_VERSION = 1
DEFAULT_MAX_AGE_SECONDS = 900
MAX_MAX_AGE_SECONDS = 86_400
MAX_FUTURE_SKEW_SECONDS = 300
MAX_EVIDENCE_BYTES = 64 * 1024
MAX_CONTRACT_BYTES = 32 * 1024
MAX_PHASE3H_BYTES = 64 * 1024

COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?:^|[\s\"'(])(?:[A-Za-z]:[\\/]|\\\\|/(?!/)|~[\\/]|file://)",
    re.IGNORECASE,
)
SECRET_VALUE_PATTERNS = (
    re.compile(r"(?:postgres(?:ql)?|https?)://", re.IGNORECASE),
    re.compile(
        r"(?:password|passwd|pwd|secret|token|api[_-]?key|service[_-]?role[_-]?key|authorization)\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{12,}\b", re.IGNORECASE),
    re.compile(r"\bsb_secret_[A-Za-z0-9_-]{12,}\b", re.IGNORECASE),
)
PROHIBITED_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "data",
        "dsn",
        "password",
        "raw_error",
        "records",
        "rows",
        "secret",
        "stderr",
        "stdout",
        "token",
    }
)

SCENARIO_KEYS = (
    "failure_before_prepare",
    "failure_after_prepare",
    "reservation_rejected",
    "reservation_expired",
    "consume_rejected",
    "consume_ambiguous",
    "failure_after_consume_before_outbox_ack",
    "dispatcher_crash_before_claim_commit",
    "dispatcher_crash_after_claim",
    "stale_fencing_token",
    "duplicate_dispatcher_replay",
    "compensation_interrupted_then_replayed",
    "recovery_replayed_twice",
    "concurrent_recovery",
)
INVARIANT_KEYS = (
    "stable_operation_job_binding",
    "idempotent_prepare",
    "consume_before_dispatch",
    "deterministic_recovery",
    "idempotent_compensation",
    "lease_fencing",
    "replay_deduplication",
    "worker_dispatch_prohibited",
    "zero_external_effects",
)
FORBIDDEN_EFFECT_KEYS = (
    "consume_rpc",
    "external_database_write",
    "lock_release",
    "network_call",
    "reservation_rpc",
    "scrape_dispatch",
)
TERMINAL_STATE_KEYS = ("succeeded", "compensated", "failed_terminal", "manual_intervention")

CONTRACT_KEYS = frozenset(
    {
        "schema_version",
        "contract_mode",
        "scenarios",
        "invariants",
        "terminal_states",
        "forbidden_effects",
    }
)
EVIDENCE_KEYS = frozenset(
    {
        "schema_version",
        "evidence_mode",
        "environment",
        "database_scope",
        "network_mode",
        "synthetic",
        "non_executable",
        "success",
        "production_ready",
        "l3_eligible",
        "tested_commit_sha",
        "contract_sha256",
        "observed_at",
        "scenario_count",
        "effect_count",
        "scenario_checks",
        "invariant_checks",
        "cleanup",
    }
)
CLEANUP_KEYS = ("attempted", "workspace_absent")
PHASE3H_REPORT_KEYS = frozenset(
    {
        "report_schema",
        "schema_version",
        "success",
        "verdict",
        "verdict_reason",
        "production_ready",
        "l3_eligible",
        "readiness_required",
        "evaluated_commit_sha",
        "phase3g_evidence",
        "blockers",
        "checks",
        "failure_codes",
    }
)
PHASE3H_CHECK_KEYS = frozenset(
    {
        "manifest_schema",
        "expected_commit",
        "phase3g_runtime_evidence",
        "self_asserted_readiness_rejected",
        "production_promotion_policy",
    }
)
PHASE3H_PHASE3G_KEYS = frozenset(
    {"success", "tested_commit_sha", "migration_sha256", "synthetic", "l3_eligible"}
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
        parsed = json.loads(
            path.read_bytes().decode("utf-8", errors="strict"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except UnicodeDecodeError:
        return None, [f"{prefix}-invalid-utf8"]
    except DuplicateKeyError:
        return None, [f"{prefix}-duplicate-json-key"]
    except (OSError, json.JSONDecodeError, ValueError, RecursionError):
        return None, [f"{prefix}-invalid-json"]
    return parsed, []


def load_evidence(path: Path) -> tuple[Any | None, list[str]]:
    return _load_json(path, max_bytes=MAX_EVIDENCE_BYTES, prefix="evidence")


def load_contract(path: Path) -> tuple[Any | None, list[str]]:
    return _load_json(path, max_bytes=MAX_CONTRACT_BYTES, prefix="contract")


def load_phase3h_evidence(path: Path) -> tuple[Any | None, list[str]]:
    return _load_json(path, max_bytes=MAX_PHASE3H_BYTES, prefix="phase3h-evidence")


def _load_phase3h_verifier() -> ModuleType:
    spec = importlib.util.spec_from_file_location("phase3i_phase3h_verifier", PHASE3H_VERIFIER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Phase3H verifier is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def expected_contract_sha256(contract: Any) -> str | None:
    return _canonical_sha256(contract) if _contract_schema_valid(contract) else None


def _contract_schema_valid(contract: Any) -> bool:
    return bool(
        isinstance(contract, dict)
        and frozenset(contract) == CONTRACT_KEYS
        and type(contract.get("schema_version")) is int
        and contract.get("schema_version") == SCHEMA_VERSION
        and contract.get("contract_mode") == "synthetic_non_executable"
        and contract.get("scenarios") == list(SCENARIO_KEYS)
        and contract.get("invariants") == list(INVARIANT_KEYS)
        and contract.get("terminal_states") == list(TERMINAL_STATE_KEYS)
        and contract.get("forbidden_effects") == list(FORBIDDEN_EFFECT_KEYS)
    )


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not 20 <= len(value) <= 40:
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


def _iter_items(value: Any):
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
        if key is not None and key.lower() in PROHIBITED_KEYS:
            return True
        if not isinstance(child, str):
            continue
        if CONTROL_CHARACTER_PATTERN.search(child) or ABSOLUTE_PATH_PATTERN.search(child):
            return True
        if any(pattern.search(child) for pattern in SECRET_VALUE_PATTERNS):
            return True
    return False


def _safe_evidence_projection(evidence: Any) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        return {
            "tested_commit_sha": None,
            "contract_sha256": None,
            "observed_at": None,
            "effect_count": None,
            "scenario_count": None,
            "synthetic": None,
            "non_executable": None,
        }
    commit = evidence.get("tested_commit_sha")
    contract_hash = evidence.get("contract_sha256")
    return {
        "tested_commit_sha": commit
        if isinstance(commit, str) and COMMIT_PATTERN.fullmatch(commit)
        else None,
        "contract_sha256": contract_hash
        if isinstance(contract_hash, str) and SHA256_PATTERN.fullmatch(contract_hash)
        else None,
        "observed_at": evidence.get("observed_at")
        if _parse_timestamp(evidence.get("observed_at")) is not None
        else None,
        "effect_count": evidence.get("effect_count")
        if type(evidence.get("effect_count")) is int
        else None,
        "scenario_count": evidence.get("scenario_count")
        if type(evidence.get("scenario_count")) is int
        else None,
        "synthetic": evidence.get("synthetic")
        if type(evidence.get("synthetic")) is bool
        else None,
        "non_executable": evidence.get("non_executable")
        if type(evidence.get("non_executable")) is bool
        else None,
    }


def _safe_phase3h_projection(evidence: Any) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        return {
            "success": False,
            "verdict": None,
            "production_ready": False,
            "l3_eligible": False,
            "evaluated_commit_sha": None,
            "blocker_count": None,
        }
    commit = evidence.get("evaluated_commit_sha")
    blockers = evidence.get("blockers")
    return {
        "success": evidence.get("success") is True,
        "verdict": evidence.get("verdict") if evidence.get("verdict") in {"not-ready", "fail"} else None,
        "production_ready": False,
        "l3_eligible": False,
        "evaluated_commit_sha": commit
        if isinstance(commit, str) and COMMIT_PATTERN.fullmatch(commit)
        else None,
        "blocker_count": len(blockers) if isinstance(blockers, list) else None,
    }


def _phase3h_not_ready_valid(evidence: Any, expected_commit: str | None) -> bool:
    if not isinstance(evidence, dict) or frozenset(evidence) != PHASE3H_REPORT_KEYS:
        return False
    try:
        phase3h = _load_phase3h_verifier()
        expected_blockers = list(phase3h.ALL_BLOCKERS)
        expected_schema = phase3h.REPORT_SCHEMA
        expected_version = phase3h.SCHEMA_VERSION
    except Exception:
        return False
    checks = evidence.get("checks")
    phase3g = evidence.get("phase3g_evidence")
    return bool(
        evidence.get("report_schema") == expected_schema
        and type(evidence.get("schema_version")) is int
        and evidence.get("schema_version") == expected_version
        and evidence.get("success") is True
        and evidence.get("verdict") == "not-ready"
        and evidence.get("verdict_reason") == "production-readiness-prerequisites-incomplete"
        and evidence.get("production_ready") is False
        and evidence.get("l3_eligible") is False
        and evidence.get("readiness_required") is False
        and isinstance(expected_commit, str)
        and COMMIT_PATTERN.fullmatch(expected_commit) is not None
        and evidence.get("evaluated_commit_sha") == expected_commit
        and evidence.get("blockers") == expected_blockers
        and evidence.get("failure_codes") == []
        and isinstance(checks, dict)
        and frozenset(checks) == PHASE3H_CHECK_KEYS
        and all(checks[key] is True for key in PHASE3H_CHECK_KEYS)
        and isinstance(phase3g, dict)
        and frozenset(phase3g) == PHASE3H_PHASE3G_KEYS
        and phase3g.get("success") is True
        and phase3g.get("tested_commit_sha") == expected_commit
        and isinstance(phase3g.get("migration_sha256"), str)
        and SHA256_PATTERN.fullmatch(phase3g["migration_sha256"]) is not None
        and phase3g.get("synthetic") is True
        and phase3g.get("l3_eligible") is False
        and not _contains_prohibited_content(evidence)
    )


def build_report(
    evidence: Any,
    contract: Any,
    phase3h_evidence: Any,
    *,
    expected_commit: str | None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    now: datetime | None = None,
    initial_failures: Iterable[str] = (),
) -> dict[str, Any]:
    failures = list(dict.fromkeys(initial_failures))
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    checks = {
        "contract_schema": False,
        "evidence_schema": False,
        "synthetic_boundary": False,
        "non_executable_boundary": False,
        "not_ready_boundary": False,
        "producer_success": False,
        "expected_commit": False,
        "contract_sha256": False,
        "freshness": False,
        "scenario_count": False,
        "effect_count_zero": False,
        "scenario_checks": False,
        "invariant_checks": False,
        "cleanup": False,
        "safe_content": False,
        "phase3h_not_ready": False,
    }

    checks["contract_schema"] = _contract_schema_valid(contract)
    if not checks["contract_schema"]:
        _append_failure(failures, "contract-schema-invalid")

    checks["phase3h_not_ready"] = _phase3h_not_ready_valid(phase3h_evidence, expected_commit)
    if not checks["phase3h_not_ready"]:
        _append_failure(failures, "phase3h-not-ready-evidence-invalid")

    if not isinstance(evidence, dict):
        _append_failure(failures, "evidence-not-object")
    else:
        checks["evidence_schema"] = frozenset(evidence) == EVIDENCE_KEYS
        if not checks["evidence_schema"]:
            _append_failure(failures, "evidence-schema-invalid")

        if type(evidence.get("schema_version")) is not int or evidence.get("schema_version") != SCHEMA_VERSION:
            _append_failure(failures, "evidence-schema-version-invalid")

        checks["synthetic_boundary"] = (
            evidence.get("evidence_mode") == "synthetic"
            and evidence.get("environment") == "ci-disposable"
            and evidence.get("database_scope") == "temporary-sqlite-model"
            and evidence.get("network_mode") == "none"
            and evidence.get("synthetic") is True
        )
        if not checks["synthetic_boundary"]:
            _append_failure(failures, "synthetic-boundary-invalid")

        checks["non_executable_boundary"] = evidence.get("non_executable") is True
        if not checks["non_executable_boundary"]:
            _append_failure(failures, "non-executable-boundary-invalid")

        checks["not_ready_boundary"] = (
            evidence.get("production_ready") is False
            and evidence.get("l3_eligible") is False
        )
        if not checks["not_ready_boundary"]:
            _append_failure(failures, "readiness-boundary-invalid")

        checks["producer_success"] = evidence.get("success") is True
        if not checks["producer_success"]:
            _append_failure(failures, "producer-reported-failure")

        tested_commit = evidence.get("tested_commit_sha")
        expected_valid = isinstance(expected_commit, str) and COMMIT_PATTERN.fullmatch(expected_commit) is not None
        checks["expected_commit"] = bool(expected_valid and tested_commit == expected_commit)
        if not expected_valid:
            _append_failure(failures, "expected-commit-required")
        elif tested_commit != expected_commit:
            _append_failure(failures, "tested-commit-mismatch")

        expected_hash = expected_contract_sha256(contract)
        checks["contract_sha256"] = bool(
            expected_hash is not None and evidence.get("contract_sha256") == expected_hash
        )
        if not checks["contract_sha256"]:
            _append_failure(failures, "contract-sha256-mismatch")

        observed = _parse_timestamp(evidence.get("observed_at"))
        if observed is None:
            _append_failure(failures, "invalid-observed-at")
        else:
            age = (now_utc - observed).total_seconds()
            checks["freshness"] = -MAX_FUTURE_SKEW_SECONDS <= age <= max_age_seconds
            if age < -MAX_FUTURE_SKEW_SECONDS:
                _append_failure(failures, "observed-at-in-future")
            elif age > max_age_seconds:
                _append_failure(failures, "stale-evidence")

        checks["scenario_count"] = (
            type(evidence.get("scenario_count")) is int
            and evidence.get("scenario_count") == len(SCENARIO_KEYS)
        )
        if not checks["scenario_count"]:
            _append_failure(failures, "scenario-count-invalid")

        checks["effect_count_zero"] = (
            type(evidence.get("effect_count")) is int and evidence.get("effect_count") == 0
        )
        if not checks["effect_count_zero"]:
            _append_failure(failures, "external-effect-observed")

        scenario_checks = evidence.get("scenario_checks")
        checks["scenario_checks"] = bool(
            isinstance(scenario_checks, dict)
            and frozenset(scenario_checks) == frozenset(SCENARIO_KEYS)
            and all(type(scenario_checks[key]) is bool and scenario_checks[key] is True for key in SCENARIO_KEYS)
        )
        if not checks["scenario_checks"]:
            _append_failure(failures, "scenario-checks-invalid")

        invariant_checks = evidence.get("invariant_checks")
        checks["invariant_checks"] = bool(
            isinstance(invariant_checks, dict)
            and frozenset(invariant_checks) == frozenset(INVARIANT_KEYS)
            and all(type(invariant_checks[key]) is bool and invariant_checks[key] is True for key in INVARIANT_KEYS)
        )
        if not checks["invariant_checks"]:
            _append_failure(failures, "invariant-checks-invalid")

        cleanup = evidence.get("cleanup")
        checks["cleanup"] = bool(
            isinstance(cleanup, dict)
            and frozenset(cleanup) == frozenset(CLEANUP_KEYS)
            and cleanup.get("attempted") is True
            and cleanup.get("workspace_absent") is True
        )
        if not checks["cleanup"]:
            _append_failure(failures, "cleanup-invalid")

    checks["safe_content"] = isinstance(evidence, dict) and not _contains_prohibited_content(evidence)
    if not checks["safe_content"]:
        _append_failure(failures, "prohibited-evidence-content")

    success = not failures and all(checks.values())
    return {
        "report_schema": REPORT_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "success": success,
        "verdict": "not-ready" if success else "fail",
        "verdict_reason": (
            "synthetic-non-executable-saga-contract-compatible"
            if success
            else failures[0] if failures else "phase3i-evidence-invalid"
        ),
        "production_ready": False,
        "l3_eligible": False,
        "evaluated_commit_sha": expected_commit
        if isinstance(expected_commit, str) and COMMIT_PATTERN.fullmatch(expected_commit)
        else None,
        "evidence": _safe_evidence_projection(evidence),
        "phase3h_evidence": _safe_phase3h_projection(phase3h_evidence),
        "checks": checks,
        "failure_codes": failures,
    }


def _positive_max_age(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("max age must be an integer") from exc
    if not 1 <= parsed <= MAX_MAX_AGE_SECONDS:
        raise argparse.ArgumentTypeError(f"max age must be between 1 and {MAX_MAX_AGE_SECONDS}")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate sanitized Phase 3I synthetic non-executable saga evidence."
    )
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--phase3h-evidence", required=True, type=Path)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--max-age-seconds", type=_positive_max_age, default=DEFAULT_MAX_AGE_SECONDS)
    return parser.parse_args(argv)


def write_report(report: dict[str, Any]) -> None:
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
    evidence, evidence_failures = load_evidence(args.evidence)
    contract, contract_failures = load_contract(args.contract)
    phase3h_evidence, phase3h_failures = load_phase3h_evidence(args.phase3h_evidence)
    report = build_report(
        evidence,
        contract,
        phase3h_evidence,
        expected_commit=args.expected_commit,
        max_age_seconds=args.max_age_seconds,
        now=datetime.now(timezone.utc),
        initial_failures=[*evidence_failures, *contract_failures, *phase3h_failures],
    )
    write_report(report)
    print(
        json.dumps(
            {
                "l3_eligible": report["l3_eligible"],
                "production_ready": report["production_ready"],
                "report": str(REPORT_PATH.relative_to(ROOT)).replace("\\", "/"),
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
