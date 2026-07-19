from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "reports" / "phase3j_saga_outbox_runtime_gate.json"
PHASE3H_VERIFIER = ROOT / "scripts" / "verify_phase3h_production_readiness.py"
PHASE3I_VERIFIER = ROOT / "scripts" / "verify_phase3i_saga_failure_injection.py"
DEFAULT_MIGRATION = ROOT / "supabase" / "migrations" / "20260720_scrape_execution_reservation.sql"
DEFAULT_STORE = ROOT / "python-api" / "scraping" / "cross_store_saga_store.py"
DEFAULT_RUNTIME_DIR = ROOT / "python-api" / "scraping"

REPORT_SCHEMA = "phase3j-saga-outbox-runtime-gate-report"
SCHEMA_VERSION = 1
DEFAULT_MAX_AGE_SECONDS = 900
MAX_MAX_AGE_SECONDS = 86_400
MAX_FUTURE_SKEW_SECONDS = 300
MAX_INPUT_BYTES = 128 * 1024
POSTGRES_IMAGE = "postgres:17.6-bookworm@sha256:f3bd19c606e442c3d7bdfa8002e03fe260a1023351e0ea4598032022b68dd6e3"
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
ABSOLUTE_PATH_PATTERN = re.compile(r"(?:^|[\s\"'(])(?:[A-Za-z]:[\\/]|\\\\|/(?!/)|~[\\/]|file://)", re.I)
SECRET_PATTERNS = (
    re.compile(r"(?:postgres(?:ql)?|https?)://", re.I),
    re.compile(r"(?:password|passwd|secret|token|api[_-]?key|authorization)\s*[:=]\s*\S+", re.I),
    re.compile(r"\b(?:gh[pousr]_|github_pat_|sb_secret_)[A-Za-z0-9_-]{12,}\b", re.I),
)

SCENARIO_KEYS = (
    "sqlite_prepare_rollback",
    "sqlite_crash_replay",
    "sqlite_claim_race",
    "sqlite_lease_fencing",
    "sqlite_stale_ack_rejected",
    "sqlite_ambiguous_remote_stops",
    "sqlite_compensation_replay",
    "sqlite_corruption_unavailable",
    "postgres_approved_review_only_denied",
    "postgres_reservation_replay",
    "postgres_consume_cas",
    "postgres_release_replay",
    "postgres_expiry_fencing",
)
INVARIANT_KEYS = (
    "atomic_prepare",
    "stable_operation_job_binding",
    "replay_idempotency",
    "single_claim_winner",
    "lease_fencing",
    "stale_ack_rejected",
    "ambiguous_remote_no_dispatch",
    "compensation_idempotent",
    "corruption_unavailable_fail_closed",
    "review_approval_not_execution_authority",
    "service_role_only_reservation",
    "zero_operational_effects",
)
OPERATIONAL_EFFECT_KEYS = ("worker_dispatch", "network_call", "thread_start", "operational_write")
RUNTIME_ASSETS = (
    "python-api/scraping/cross_store_saga_codec.py",
    "python-api/scraping/cross_store_saga_store.py",
    "python-api/scraping/cross_store_saga_ports.py",
    "python-api/scraping/cross_store_saga_runtime.py",
    "python-api/scraping/saga_runtime_config.py",
)
MIGRATION_RELATIVE_PATH = "supabase/migrations/20260720_scrape_execution_reservation.sql"
CONTRACT_KEYS = frozenset(
    {
        "schema_version", "contract_mode", "scenarios", "invariants", "operational_effects",
        "runtime_assets", "migration", "required_boundaries",
    }
)
BOUNDARY_KEYS = frozenset(
    {
        "database_scope", "environment", "evidence_mode", "external_migration_applied",
        "l3_eligible", "network_mode", "production_ready",
    }
)
EVIDENCE_KEYS = frozenset(
    {
        "schema_version", "evidence_mode", "environment", "database_scope", "network_mode",
        "image", "host_port_published", "external_credentials_used", "tested_commit_sha",
        "contract_sha256", "migration_sha256", "schema_sha256", "runtime_asset_sha256",
        "observed_at", "success", "production_ready", "l3_eligible",
        "external_migration_applied", "scenario_count", "scenario_checks", "invariant_checks",
        "operational_effect_count", "worker_dispatch_count", "operational_effects",
        "disposable_database_effect_count", "disposable_database_effects", "cleanup",
    }
)
CLEANUP_KEYS = frozenset({"attempted", "container_absent", "workspace_absent"})
DISPOSABLE_EFFECT_KEYS = frozenset({"sqlite_writes", "postgres_writes"})
GATE_REPORT_KEYS = frozenset(
    {
        "report_schema", "schema_version", "success", "verdict", "verdict_reason",
        "production_ready", "l3_eligible", "evaluated_commit_sha", "evidence",
        "phase3h_evidence_valid", "phase3i_evidence_valid", "checks", "failure_codes",
    }
)
PHASE3I_REPORT_KEYS = frozenset(
    {
        "report_schema", "schema_version", "success", "verdict", "verdict_reason",
        "production_ready", "l3_eligible", "evaluated_commit_sha", "evidence",
        "phase3h_evidence", "checks", "failure_codes",
    }
)
PHASE3I_CHECK_KEYS = frozenset(
    {
        "contract_schema", "evidence_schema", "synthetic_boundary", "non_executable_boundary",
        "not_ready_boundary", "producer_success", "expected_commit", "contract_sha256",
        "freshness", "scenario_count", "effect_count_zero", "scenario_checks",
        "invariant_checks", "cleanup", "safe_content", "phase3h_not_ready",
    }
)
PHASE3I_EVIDENCE_PROJECTION_KEYS = frozenset(
    {
        "tested_commit_sha", "contract_sha256", "observed_at", "effect_count",
        "scenario_count", "synthetic", "non_executable",
    }
)
PHASE3I_PHASE3H_PROJECTION_KEYS = frozenset(
    {
        "success", "verdict", "production_ready", "l3_eligible",
        "evaluated_commit_sha", "blocker_count",
    }
)


class DuplicateKeyError(ValueError):
    pass


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(key)
        result[key] = value
    return result


def _load_json(path: Path, prefix: str) -> tuple[Any | None, list[str]]:
    try:
        raw = path.read_bytes()
    except OSError:
        return None, [f"{prefix}-file-unavailable"]
    if not raw:
        return None, [f"{prefix}-file-empty"]
    if len(raw) > MAX_INPUT_BYTES:
        return None, [f"{prefix}-file-too-large"]
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None, [f"{prefix}-invalid-utf8"]
    try:
        return json.loads(
            text,
            object_pairs_hook=_no_duplicates,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        ), []
    except DuplicateKeyError:
        return None, [f"{prefix}-duplicate-json-key"]
    except (ValueError, json.JSONDecodeError):
        return None, [f"{prefix}-invalid-json"]


def load_evidence(path: Path) -> tuple[Any | None, list[str]]:
    return _load_json(path, "evidence")


def load_contract(path: Path) -> tuple[Any | None, list[str]]:
    return _load_json(path, "contract")


def load_phase3h_evidence(path: Path) -> tuple[Any | None, list[str]]:
    return _load_json(path, "phase3h-evidence")


def load_phase3i_evidence(path: Path) -> tuple[Any | None, list[str]]:
    return _load_json(path, "phase3i-evidence")


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{name} unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str | None:
    try:
        raw = path.read_bytes().replace(b"\r\n", b"\n")
    except OSError:
        return None
    return hashlib.sha256(raw).hexdigest()


def _schema_sha256(path: Path = DEFAULT_STORE) -> str | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeError):
        return None
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id in {"SCHEMA_SQL", "SAGA_SCHEMA_SQL"} for target in targets):
                try:
                    value = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    return None
                if isinstance(value, str) and value:
                    return hashlib.sha256(value.replace("\r\n", "\n").encode("utf-8")).hexdigest()
    return None


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or CONTROL_PATTERN.search(value):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _contains_prohibited_content(value: Any, key: str | None = None) -> bool:
    if isinstance(value, dict):
        return any(_contains_prohibited_content(child, str(child_key)) for child_key, child in value.items())
    if isinstance(value, list):
        return any(_contains_prohibited_content(child, key) for child in value)
    if not isinstance(value, str):
        return False
    if key and key.lower() in {"password", "secret", "token", "dsn", "authorization", "cookie", "raw_error"}:
        return True
    return bool(
        CONTROL_PATTERN.search(value)
        or ABSOLUTE_PATH_PATTERN.search(value)
        or any(pattern.search(value) for pattern in SECRET_PATTERNS)
    )


def _contract_valid(contract: Any) -> bool:
    if not isinstance(contract, dict) or frozenset(contract) != CONTRACT_KEYS:
        return False
    boundaries = contract.get("required_boundaries")
    return bool(
        contract.get("schema_version") == SCHEMA_VERSION
        and type(contract.get("schema_version")) is int
        and contract.get("contract_mode") == "disposable-runtime"
        and contract.get("scenarios") == list(SCENARIO_KEYS)
        and contract.get("invariants") == list(INVARIANT_KEYS)
        and contract.get("operational_effects") == list(OPERATIONAL_EFFECT_KEYS)
        and contract.get("runtime_assets") == list(RUNTIME_ASSETS)
        and contract.get("migration") == MIGRATION_RELATIVE_PATH
        and isinstance(boundaries, dict)
        and frozenset(boundaries) == BOUNDARY_KEYS
        and boundaries == {
            "database_scope": "temporary-sqlite-and-disposable-postgres",
            "environment": "ci-disposable",
            "evidence_mode": "disposable-runtime",
            "external_migration_applied": False,
            "l3_eligible": False,
            "network_mode": "container-none",
            "production_ready": False,
        }
    )


def _phase3h_valid(evidence: Any, expected_commit: str | None) -> bool:
    try:
        phase3i = _load_module(PHASE3I_VERIFIER, "phase3j_phase3i_verifier")
        return phase3i._phase3h_not_ready_valid(evidence, expected_commit) is True
    except Exception:
        return False


def _phase3i_valid(evidence: Any, expected_commit: str | None) -> bool:
    if not isinstance(evidence, dict) or frozenset(evidence) != PHASE3I_REPORT_KEYS:
        return False
    checks = evidence.get("checks")
    projection = evidence.get("evidence")
    phase3h_projection = evidence.get("phase3h_evidence")
    return bool(
        evidence.get("report_schema") == "phase3i-saga-failure-injection-gate-report"
        and evidence.get("schema_version") == 1
        and type(evidence.get("schema_version")) is int
        and evidence.get("success") is True
        and evidence.get("verdict") == "not-ready"
        and evidence.get("verdict_reason") == "synthetic-non-executable-saga-contract-compatible"
        and evidence.get("production_ready") is False
        and evidence.get("l3_eligible") is False
        and isinstance(expected_commit, str)
        and COMMIT_PATTERN.fullmatch(expected_commit) is not None
        and evidence.get("evaluated_commit_sha") == expected_commit
        and evidence.get("failure_codes") == []
        and isinstance(checks, dict)
        and frozenset(checks) == PHASE3I_CHECK_KEYS
        and all(checks.get(key) is True for key in PHASE3I_CHECK_KEYS)
        and isinstance(projection, dict)
        and frozenset(projection) == PHASE3I_EVIDENCE_PROJECTION_KEYS
        and projection.get("tested_commit_sha") == expected_commit
        and projection.get("effect_count") == 0
        and projection.get("synthetic") is True
        and projection.get("non_executable") is True
        and isinstance(phase3h_projection, dict)
        and frozenset(phase3h_projection) == PHASE3I_PHASE3H_PROJECTION_KEYS
        and phase3h_projection.get("success") is True
        and phase3h_projection.get("verdict") == "not-ready"
        and phase3h_projection.get("production_ready") is False
        and phase3h_projection.get("l3_eligible") is False
        and phase3h_projection.get("evaluated_commit_sha") == expected_commit
        and not _contains_prohibited_content(evidence)
    )


def _append_failure(failures: list[str], code: str) -> None:
    if code not in failures:
        failures.append(code)


def _safe_projection(evidence: Any) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        return {
            "tested_commit_sha": None, "contract_sha256": None, "migration_sha256": None,
            "schema_sha256": None, "scenario_count": None, "operational_effect_count": None,
            "worker_dispatch_count": None, "disposable_database_effect_count": None,
        }
    def safe_hash(key: str) -> str | None:
        value = evidence.get(key)
        return value if isinstance(value, str) and SHA256_PATTERN.fullmatch(value) else None
    commit = evidence.get("tested_commit_sha")
    return {
        "tested_commit_sha": commit if isinstance(commit, str) and COMMIT_PATTERN.fullmatch(commit) else None,
        "contract_sha256": safe_hash("contract_sha256"),
        "migration_sha256": safe_hash("migration_sha256"),
        "schema_sha256": safe_hash("schema_sha256"),
        "scenario_count": evidence.get("scenario_count") if type(evidence.get("scenario_count")) is int else None,
        "operational_effect_count": evidence.get("operational_effect_count") if type(evidence.get("operational_effect_count")) is int else None,
        "worker_dispatch_count": evidence.get("worker_dispatch_count") if type(evidence.get("worker_dispatch_count")) is int else None,
        "disposable_database_effect_count": evidence.get("disposable_database_effect_count") if type(evidence.get("disposable_database_effect_count")) is int else None,
    }


def build_report(
    evidence: Any,
    contract: Any,
    phase3h_evidence: Any,
    phase3i_evidence: Any,
    *,
    expected_commit: str | None,
    migration_path: Path = DEFAULT_MIGRATION,
    store_path: Path = DEFAULT_STORE,
    runtime_dir: Path = DEFAULT_RUNTIME_DIR,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    now: datetime | None = None,
    initial_failures: Iterable[str] = (),
) -> dict[str, Any]:
    failures = list(dict.fromkeys(initial_failures))
    checks = {
        "contract_schema": False,
        "evidence_schema": False,
        "disposable_boundary": False,
        "not_ready_boundary": False,
        "producer_success": False,
        "expected_commit": False,
        "contract_sha256": False,
        "migration_sha256": False,
        "schema_sha256": False,
        "runtime_asset_sha256": False,
        "freshness": False,
        "scenario_checks": False,
        "invariant_checks": False,
        "zero_operational_effects": False,
        "disposable_database_effects": False,
        "cleanup": False,
        "safe_content": False,
        "phase3h_not_ready": False,
        "phase3i_not_ready": False,
    }
    checks["contract_schema"] = _contract_valid(contract)
    if not checks["contract_schema"]:
        _append_failure(failures, "contract-schema-invalid")
    checks["phase3h_not_ready"] = _phase3h_valid(phase3h_evidence, expected_commit)
    if not checks["phase3h_not_ready"]:
        _append_failure(failures, "phase3h-not-ready-evidence-invalid")
    checks["phase3i_not_ready"] = _phase3i_valid(phase3i_evidence, expected_commit)
    if not checks["phase3i_not_ready"]:
        _append_failure(failures, "phase3i-not-ready-evidence-invalid")

    if not isinstance(evidence, dict):
        _append_failure(failures, "evidence-not-object")
    else:
        checks["evidence_schema"] = frozenset(evidence) == EVIDENCE_KEYS
        if not checks["evidence_schema"]:
            _append_failure(failures, "evidence-schema-invalid")
        if type(evidence.get("schema_version")) is not int or evidence.get("schema_version") != SCHEMA_VERSION:
            _append_failure(failures, "evidence-schema-version-invalid")
        checks["disposable_boundary"] = (
            evidence.get("evidence_mode") == "disposable-runtime"
            and evidence.get("environment") == "ci-disposable"
            and evidence.get("database_scope") == "temporary-sqlite-and-disposable-postgres"
            and evidence.get("network_mode") == "container-none"
            and evidence.get("image") == POSTGRES_IMAGE
            and evidence.get("host_port_published") is False
            and evidence.get("external_credentials_used") is False
            and evidence.get("external_migration_applied") is False
        )
        if not checks["disposable_boundary"]:
            _append_failure(failures, "disposable-boundary-invalid")
        checks["not_ready_boundary"] = evidence.get("production_ready") is False and evidence.get("l3_eligible") is False
        if not checks["not_ready_boundary"]:
            _append_failure(failures, "readiness-boundary-invalid")
        checks["producer_success"] = evidence.get("success") is True
        if not checks["producer_success"]:
            _append_failure(failures, "producer-reported-failure")
        expected_valid = isinstance(expected_commit, str) and COMMIT_PATTERN.fullmatch(expected_commit) is not None
        checks["expected_commit"] = bool(expected_valid and evidence.get("tested_commit_sha") == expected_commit)
        if not expected_valid:
            _append_failure(failures, "expected-commit-required")
        elif not checks["expected_commit"]:
            _append_failure(failures, "tested-commit-mismatch")

        expected_contract = _canonical_json_sha256(contract) if _contract_valid(contract) else None
        checks["contract_sha256"] = evidence.get("contract_sha256") == expected_contract
        if not checks["contract_sha256"]:
            _append_failure(failures, "contract-sha256-mismatch")
        expected_migration = _file_sha256(migration_path)
        checks["migration_sha256"] = expected_migration is not None and evidence.get("migration_sha256") == expected_migration
        if not checks["migration_sha256"]:
            _append_failure(failures, "migration-sha256-mismatch")
        expected_schema = _schema_sha256(store_path)
        checks["schema_sha256"] = expected_schema is not None and evidence.get("schema_sha256") == expected_schema
        if not checks["schema_sha256"]:
            _append_failure(failures, "schema-sha256-mismatch")
        expected_assets = {
            path: _file_sha256(runtime_dir / Path(path).name)
            for path in RUNTIME_ASSETS
        }
        checks["runtime_asset_sha256"] = (
            all(isinstance(value, str) for value in expected_assets.values())
            and evidence.get("runtime_asset_sha256") == expected_assets
        )
        if not checks["runtime_asset_sha256"]:
            _append_failure(failures, "runtime-asset-sha256-mismatch")

        observed = _parse_timestamp(evidence.get("observed_at"))
        if observed is None:
            _append_failure(failures, "invalid-observed-at")
        else:
            age = ((now or datetime.now(timezone.utc)).astimezone(timezone.utc) - observed).total_seconds()
            checks["freshness"] = -MAX_FUTURE_SKEW_SECONDS <= age <= max_age_seconds
            if not checks["freshness"]:
                _append_failure(failures, "stale-or-future-evidence")

        scenarios = evidence.get("scenario_checks")
        checks["scenario_checks"] = (
            evidence.get("scenario_count") == len(SCENARIO_KEYS)
            and type(evidence.get("scenario_count")) is int
            and isinstance(scenarios, dict)
            and frozenset(scenarios) == frozenset(SCENARIO_KEYS)
            and all(scenarios.get(key) is True for key in SCENARIO_KEYS)
        )
        if not checks["scenario_checks"]:
            _append_failure(failures, "scenario-checks-invalid")
        invariants = evidence.get("invariant_checks")
        checks["invariant_checks"] = (
            isinstance(invariants, dict)
            and frozenset(invariants) == frozenset(INVARIANT_KEYS)
            and all(invariants.get(key) is True for key in INVARIANT_KEYS)
        )
        if not checks["invariant_checks"]:
            _append_failure(failures, "invariant-checks-invalid")

        operational = evidence.get("operational_effects")
        checks["zero_operational_effects"] = (
            type(evidence.get("operational_effect_count")) is int
            and evidence.get("operational_effect_count") == 0
            and type(evidence.get("worker_dispatch_count")) is int
            and evidence.get("worker_dispatch_count") == 0
            and isinstance(operational, dict)
            and frozenset(operational) == frozenset(OPERATIONAL_EFFECT_KEYS)
            and all(type(operational.get(key)) is int and operational.get(key) == 0 for key in OPERATIONAL_EFFECT_KEYS)
        )
        if not checks["zero_operational_effects"]:
            _append_failure(failures, "operational-effect-observed")
        disposable = evidence.get("disposable_database_effects")
        checks["disposable_database_effects"] = (
            isinstance(disposable, dict)
            and frozenset(disposable) == DISPOSABLE_EFFECT_KEYS
            and all(type(disposable.get(key)) is int and disposable.get(key) > 0 for key in DISPOSABLE_EFFECT_KEYS)
            and type(evidence.get("disposable_database_effect_count")) is int
            and evidence.get("disposable_database_effect_count") == sum(disposable.values())
        )
        if not checks["disposable_database_effects"]:
            _append_failure(failures, "disposable-database-effects-invalid")
        cleanup = evidence.get("cleanup")
        checks["cleanup"] = (
            isinstance(cleanup, dict)
            and frozenset(cleanup) == CLEANUP_KEYS
            and all(cleanup.get(key) is True for key in CLEANUP_KEYS)
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
        "verdict_reason": "disposable-saga-outbox-runtime-compatible" if success else (failures[0] if failures else "phase3j-evidence-invalid"),
        "production_ready": False,
        "l3_eligible": False,
        "evaluated_commit_sha": expected_commit if isinstance(expected_commit, str) and COMMIT_PATTERN.fullmatch(expected_commit) else None,
        "evidence": _safe_projection(evidence),
        "phase3h_evidence_valid": checks["phase3h_not_ready"],
        "phase3i_evidence_valid": checks["phase3i_not_ready"],
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
    parser = argparse.ArgumentParser(description="Verify Phase 3J disposable saga/outbox runtime evidence.")
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--phase3h-evidence", required=True, type=Path)
    parser.add_argument("--phase3i-evidence", required=True, type=Path)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--migration", type=Path, default=DEFAULT_MIGRATION)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--runtime-dir", type=Path, default=DEFAULT_RUNTIME_DIR)
    parser.add_argument("--max-age-seconds", type=_positive_max_age, default=DEFAULT_MAX_AGE_SECONDS)
    return parser.parse_args(argv)


def write_report(report: dict[str, Any], path: Path = REPORT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = json.dumps(report, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2) + "\n"
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _checkout_head_failure(expected_commit: str, repository_root: Path = ROOT) -> str | None:
    candidate = expected_commit.lower()
    if COMMIT_PATTERN.fullmatch(candidate) is None:
        return "expected-commit-required"
    allowed = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "HOME", "TMP", "TEMP")
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository_root.resolve()), "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            shell=False,
            env=environment,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return "checkout-head-unavailable"
    actual = completed.stdout.strip().lower()
    if completed.returncode != 0 or COMMIT_PATTERN.fullmatch(actual) is None:
        return "checkout-head-unavailable"
    if actual != candidate:
        return "checkout-head-mismatch"
    return None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    evidence, evidence_failures = load_evidence(args.evidence)
    contract, contract_failures = load_contract(args.contract)
    phase3h, phase3h_failures = load_phase3h_evidence(args.phase3h_evidence)
    phase3i, phase3i_failures = load_phase3i_evidence(args.phase3i_evidence)
    checkout_failure = _checkout_head_failure(args.expected_commit)
    report = build_report(
        evidence,
        contract,
        phase3h,
        phase3i,
        expected_commit=args.expected_commit.lower(),
        migration_path=args.migration,
        store_path=args.store,
        runtime_dir=args.runtime_dir,
        max_age_seconds=args.max_age_seconds,
        initial_failures=[
            *evidence_failures,
            *contract_failures,
            *phase3h_failures,
            *phase3i_failures,
            *([checkout_failure] if checkout_failure is not None else []),
        ],
    )
    write_report(report)
    print(json.dumps({
        "success": report["success"], "verdict": report["verdict"],
        "production_ready": report["production_ready"], "l3_eligible": report["l3_eligible"],
        "report": str(REPORT_PATH.relative_to(ROOT)).replace("\\", "/"),
    }, sort_keys=True))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
