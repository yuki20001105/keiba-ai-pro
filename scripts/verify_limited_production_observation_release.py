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
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PHASE3N_VERIFIER_PATH = ROOT / "scripts/security/verify_phase3n_staging_evidence.py"
REPORT_PATH = ROOT / "reports/limited_production_observation_release_gate.json"
CONTRACT_ID = "limited-production-observation-v1"
CONTRACT_SCHEMA = "limited-production-observation-contract"
REPORT_SCHEMA = "limited-production-observation-release-gate"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
APPROVAL_RE = re.compile(
    r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(?:issues/\d+#issuecomment-\d+|pull/\d+)$"
)
MAX_INPUT_BYTES = 256 * 1024

CONTRACT_KEYS = frozenset(
    {
        "schema",
        "schema_version",
        "contract_id",
        "status",
        "approved_at",
        "approved_by",
        "approval_reference",
        "release_mode",
        "release_gate",
        "runtime_controls",
        "post_release_validation",
    }
)
RELEASE_GATE = {
    "trusted_system_attestation_required": True,
    "model_business_validation_required": False,
    "production_environment_approval_required": True,
    "exact_commit_required": True,
}
RUNTIME_CONTROLS = {
    "model_runtime_status": "observation",
    "automated_betting_enabled": False,
    "deployed_model_activation_enabled": False,
    "deployed_model_training_enabled": False,
    "observation_capture_required": True,
    "point_in_time_middle_odds_required": True,
    "rollback_ready_required": True,
}
POST_RELEASE_VALIDATION = {
    "contract_id": "model-acceptance-v1",
    "required_for_observation_release": False,
    "required_for_validated_status": True,
    "required_for_active_status": True,
}


class DuplicateKeyError(ValueError):
    pass


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError("duplicate JSON key")
        result[key] = value
    return result


def _load_json(path: Path, prefix: str) -> tuple[Any | None, list[str]]:
    try:
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_INPUT_BYTES:
            return None, [f"{prefix}-file-invalid"]
        payload = path.read_bytes()
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite")),
        )
        return value, []
    except DuplicateKeyError:
        return None, [f"{prefix}-duplicate-json-key"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        return None, [f"{prefix}-file-invalid"]


def _load_phase3n_verifier() -> ModuleType:
    spec = importlib.util.spec_from_file_location("limited_release_phase3n", PHASE3N_VERIFIER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("phase3n-verifier-unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _approved_time(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed <= datetime.now(timezone.utc)


def validate_contract(contract: Any) -> tuple[bool, tuple[str, ...]]:
    failures: list[str] = []
    if not isinstance(contract, dict) or frozenset(contract) != CONTRACT_KEYS:
        return False, ("contract-schema-invalid",)
    if contract.get("schema") != CONTRACT_SCHEMA or contract.get("schema_version") != 1:
        failures.append("contract-schema-version-invalid")
    if contract.get("contract_id") != CONTRACT_ID:
        failures.append("contract-id-invalid")
    if contract.get("status") != "approved":
        failures.append("contract-not-approved")
    if contract.get("approved_by") != "yuki20001105" or not _approved_time(contract.get("approved_at")):
        failures.append("contract-approval-invalid")
    if not isinstance(contract.get("approval_reference"), str) or APPROVAL_RE.fullmatch(
        contract["approval_reference"]
    ) is None:
        failures.append("contract-approval-reference-invalid")
    if contract.get("release_mode") != "limited-observation":
        failures.append("contract-release-mode-invalid")
    if contract.get("release_gate") != RELEASE_GATE:
        failures.append("contract-release-gate-invalid")
    if contract.get("runtime_controls") != RUNTIME_CONTROLS:
        failures.append("contract-runtime-controls-invalid")
    if contract.get("post_release_validation") != POST_RELEASE_VALIDATION:
        failures.append("contract-post-release-validation-invalid")
    return not failures, tuple(failures)


def build_report(
    contract: Any,
    attestation: Any,
    *,
    expected_commit: str,
    expected_run_id: int,
    expected_run_attempt: int,
    expected_repository: str,
    expected_repository_id: str,
    max_age_seconds: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    failures: list[str] = []
    contract_valid, contract_failures = validate_contract(contract)
    failures.extend(contract_failures)
    context_valid = bool(
        COMMIT_RE.fullmatch(expected_commit)
        and expected_run_id > 0
        and expected_run_attempt > 0
        and REPOSITORY_RE.fullmatch(expected_repository)
        and re.fullmatch(r"[1-9][0-9]*", expected_repository_id)
        and 1 <= max_age_seconds <= 86400
    )
    if not context_valid:
        failures.append("expected-context-invalid")
    attestation_valid = False
    if context_valid:
        try:
            verifier = _load_phase3n_verifier()
            attestation_valid, attestation_failures = verifier.validate_gate_report(
                attestation,
                expected_commit=expected_commit,
                expected_run_id=str(expected_run_id),
                expected_run_attempt=expected_run_attempt,
                expected_repository=expected_repository,
                expected_repository_id=expected_repository_id,
                now=now,
                max_age_seconds=max_age_seconds,
            )
            if not attestation_valid or attestation_failures:
                failures.append("trusted-system-attestation-invalid")
        except Exception:
            failures.append("trusted-system-attestation-invalid")
    ready = contract_valid and attestation_valid and not failures
    contract_digest = None
    if isinstance(contract, dict):
        contract_digest = hashlib.sha256(
            json.dumps(contract, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    return {
        "report_schema": REPORT_SCHEMA,
        "schema_version": 1,
        "success": ready,
        "verdict": "observation-release-ready" if ready else "fail",
        "verdict_reason": "trusted-system-release-with-observation-controls" if ready else failures[0],
        "release_mode": "limited-observation",
        "evaluated_commit_sha": expected_commit if COMMIT_RE.fullmatch(expected_commit) else None,
        "contract_id": CONTRACT_ID if contract_valid else None,
        "contract_sha256": contract_digest if contract_valid else None,
        "system_release_ready": ready,
        "observation_release_ready": ready,
        "model_business_validated": False,
        "full_production_ready": False,
        "automated_betting_allowed": False,
        "checks": {
            "contract": contract_valid,
            "trusted_system_attestation": attestation_valid,
            "exact_commit": context_valid,
            "observation_only": contract_valid,
            "automated_betting_disabled": contract_valid,
            "post_release_model_validation_required": contract_valid,
        },
        "failure_codes": list(dict.fromkeys(failures)),
    }


def _positive(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Authorize only a limited Production observation release.")
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--trusted-attestation", required=True, type=Path)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-run-id", required=True, type=_positive)
    parser.add_argument("--expected-run-attempt", required=True, type=_positive)
    parser.add_argument("--expected-repository", required=True)
    parser.add_argument("--expected-repository-id", required=True)
    parser.add_argument("--max-age-seconds", type=_positive, default=3600)
    parser.add_argument("--require-observation-ready", action="store_true")
    return parser.parse_args(argv)


def _write_report(report: dict[str, Any]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT_PATH.with_name(f".{REPORT_PATH.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, REPORT_PATH)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    contract, contract_failures = _load_json(args.contract, "contract")
    attestation, attestation_failures = _load_json(args.trusted_attestation, "attestation")
    if contract_failures or attestation_failures:
        report = {
            "report_schema": REPORT_SCHEMA,
            "schema_version": 1,
            "success": False,
            "verdict": "fail",
            "verdict_reason": (contract_failures + attestation_failures)[0],
            "release_mode": "limited-observation",
            "evaluated_commit_sha": None,
            "contract_id": None,
            "contract_sha256": None,
            "system_release_ready": False,
            "observation_release_ready": False,
            "model_business_validated": False,
            "full_production_ready": False,
            "automated_betting_allowed": False,
            "checks": {},
            "failure_codes": contract_failures + attestation_failures,
        }
    else:
        report = build_report(
            contract,
            attestation,
            expected_commit=args.expected_commit,
            expected_run_id=args.expected_run_id,
            expected_run_attempt=args.expected_run_attempt,
            expected_repository=args.expected_repository,
            expected_repository_id=args.expected_repository_id,
            max_age_seconds=args.max_age_seconds,
            now=datetime.now(timezone.utc),
        )
    if args.require_observation_ready and not report["observation_release_ready"]:
        if "observation-release-readiness-required" not in report["failure_codes"]:
            report["failure_codes"].append("observation-release-readiness-required")
    _write_report(report)
    print(json.dumps({key: report[key] for key in (
        "success", "verdict", "system_release_ready", "observation_release_ready",
        "model_business_validated", "full_production_ready", "automated_betting_allowed"
    )}, sort_keys=True))
    return 0 if report["success"] and (not args.require_observation_ready or report["observation_release_ready"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
