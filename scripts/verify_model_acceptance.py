from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "reports" / "model_acceptance_gate.json"

CONTRACT_SCHEMA = "model-acceptance-contract"
EVIDENCE_SCHEMA = "model-acceptance-evidence"
REPORT_SCHEMA = "model-acceptance-gate-report"
SCHEMA_VERSION = 1
DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
MAX_INPUT_BYTES = 64 * 1024

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:#-]{2,255}$")

CONTRACT_KEYS = frozenset(
    {
        "schema",
        "schema_version",
        "contract_id",
        "status",
        "approved_at",
        "approved_by",
        "approval_reference",
        "evaluation_policy",
        "thresholds",
    }
)
POLICY_KEYS = frozenset({"holdout_kind", "future_field_leakage_allowed"})
THRESHOLD_KEYS = {
    "auc": "gte",
    "brier_score": "lte",
    "expected_calibration_error": "lte",
    "roi_percent": "gte",
    "max_drawdown_percent": "lte",
    "bet_count": "gte",
    "sample_count": "gte",
    "p95_latency_ms": "lte",
    "data_freshness_minutes": "lte",
    "observation_period_days": "gte",
    "baseline_roi_delta_percent": "gte",
}
COUNT_METRICS = frozenset({"bet_count", "sample_count"})
THRESHOLD_ENTRY_KEYS = frozenset({"operator", "value"})
EVIDENCE_KEYS = frozenset(
    {
        "schema",
        "schema_version",
        "candidate_commit_sha",
        "model_id",
        "model_artifact_sha256",
        "model_feature_columns_sha256",
        "observations_sha256",
        "contract_id",
        "contract_sha256",
        "observed_at",
        "evaluation",
        "metrics",
    }
)
EVALUATION_KEYS = frozenset(
    {"holdout_kind", "future_field_leakage_detected", "started_at", "ended_at"}
)
REPORT_KEYS = frozenset(
    {
        "report_schema",
        "schema_version",
        "success",
        "verdict",
        "verdict_reason",
        "accepted",
        "acceptance_required",
        "evaluated_commit_sha",
        "contract",
        "evidence",
        "blockers",
        "checks",
        "failure_codes",
    }
)
REPORT_CONTRACT_KEYS = frozenset({"contract_id", "sha256", "status"})
REPORT_EVIDENCE_KEYS = frozenset(
    {
        "model_id",
        "model_artifact_sha256",
        "model_feature_columns_sha256",
        "observations_sha256",
        "observed_at",
    }
)
REPORT_CHECK_KEYS = frozenset(
    {
        "contract_schema",
        "contract_approved",
        "evidence_schema_and_binding",
        "metrics_against_thresholds",
        "promotion_policy",
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


def _append(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def load_json(path: Path, *, prefix: str) -> tuple[Any | None, list[str]]:
    try:
        stat = path.stat()
    except OSError:
        return None, [f"{prefix}-file-unavailable"]
    if not path.is_file():
        return None, [f"{prefix}-file-unavailable"]
    if stat.st_size <= 0:
        return None, [f"{prefix}-file-empty"]
    if stat.st_size > MAX_INPUT_BYTES:
        return None, [f"{prefix}-file-too-large"]
    try:
        return (
            json.loads(
                path.read_bytes().decode("utf-8", errors="strict"),
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


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _finite_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(float(value))


def contract_sha256(contract: Any) -> str | None:
    try:
        payload = json.dumps(
            contract,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        return None
    return hashlib.sha256(payload).hexdigest()


def _validate_contract(contract: Any, failures: list[str], blockers: list[str]) -> bool:
    if not isinstance(contract, dict) or frozenset(contract) != CONTRACT_KEYS:
        _append(failures, "contract-schema-invalid")
        return False

    valid = True
    if contract["schema"] != CONTRACT_SCHEMA:
        _append(failures, "contract-schema-invalid")
        valid = False
    if type(contract["schema_version"]) is not int or contract["schema_version"] != SCHEMA_VERSION:
        _append(failures, "contract-schema-version-invalid")
        valid = False
    if not isinstance(contract["contract_id"], str) or not IDENTIFIER_RE.fullmatch(contract["contract_id"]):
        _append(failures, "contract-id-invalid")
        valid = False

    status = contract["status"]
    if status not in {"draft", "approved"}:
        _append(failures, "contract-status-invalid")
        valid = False

    policy = contract["evaluation_policy"]
    if not isinstance(policy, dict) or frozenset(policy) != POLICY_KEYS:
        _append(failures, "contract-evaluation-policy-invalid")
        valid = False
    elif policy["holdout_kind"] != "out_of_time" or policy["future_field_leakage_allowed"] is not False:
        _append(failures, "contract-evaluation-policy-invalid")
        valid = False

    thresholds = contract["thresholds"]
    if not isinstance(thresholds, dict) or frozenset(thresholds) != frozenset(THRESHOLD_KEYS):
        _append(failures, "contract-threshold-schema-invalid")
        valid = False
    else:
        for metric, expected_operator in THRESHOLD_KEYS.items():
            entry = thresholds[metric]
            if not isinstance(entry, dict) or frozenset(entry) != THRESHOLD_ENTRY_KEYS:
                _append(failures, "contract-threshold-schema-invalid")
                valid = False
                continue
            if entry["operator"] != expected_operator:
                _append(failures, f"contract-{metric.replace('_', '-')}-operator-invalid")
                valid = False
            threshold = entry["value"]
            if threshold is not None and not _finite_number(threshold):
                _append(failures, f"contract-{metric.replace('_', '-')}-threshold-invalid")
                valid = False
            if metric in COUNT_METRICS and threshold is not None and (
                type(threshold) is not int or threshold < 1
            ):
                _append(failures, f"contract-{metric.replace('_', '-')}-threshold-invalid")
                valid = False
            if threshold is None:
                _append(blockers, f"threshold-{metric.replace('_', '-')}-unapproved")

    approval_values = (
        contract["approved_at"],
        contract["approved_by"],
        contract["approval_reference"],
    )
    if status == "draft":
        if any(value is not None for value in approval_values):
            _append(failures, "draft-contract-cannot-carry-approval")
            valid = False
        _append(blockers, "contract-approval-required")
    elif status == "approved":
        approved_at, approved_by, approval_reference = approval_values
        if _parse_timestamp(approved_at) is None:
            _append(failures, "contract-approved-at-invalid")
            valid = False
        if not isinstance(approved_by, str) or not IDENTIFIER_RE.fullmatch(approved_by):
            _append(failures, "contract-approved-by-invalid")
            valid = False
        if not isinstance(approval_reference, str) or not REFERENCE_RE.fullmatch(approval_reference):
            _append(failures, "contract-approval-reference-invalid")
            valid = False
        if blockers:
            _append(failures, "approved-contract-has-unapproved-thresholds")
            valid = False

    return valid


def _validate_evidence(
    evidence: Any,
    *,
    contract: Any,
    contract_digest: str | None,
    expected_commit: str | None,
    max_age_seconds: int,
    now: datetime,
    failures: list[str],
) -> bool:
    if not isinstance(evidence, dict) or frozenset(evidence) != EVIDENCE_KEYS:
        _append(failures, "evidence-schema-invalid")
        return False

    valid = True
    if evidence["schema"] != EVIDENCE_SCHEMA:
        _append(failures, "evidence-schema-invalid")
        valid = False
    if type(evidence["schema_version"]) is not int or evidence["schema_version"] != SCHEMA_VERSION:
        _append(failures, "evidence-schema-version-invalid")
        valid = False
    if evidence["candidate_commit_sha"] != expected_commit:
        _append(failures, "evidence-candidate-commit-mismatch")
        valid = False
    if not isinstance(evidence["model_id"], str) or not IDENTIFIER_RE.fullmatch(evidence["model_id"]):
        _append(failures, "evidence-model-id-invalid")
        valid = False
    for digest_name in (
        "model_artifact_sha256",
        "model_feature_columns_sha256",
        "observations_sha256",
    ):
        if (
            not isinstance(evidence[digest_name], str)
            or DIGEST_RE.fullmatch(evidence[digest_name]) is None
        ):
            _append(failures, f"evidence-{digest_name.replace('_', '-')}-invalid")
            valid = False
    expected_contract_id = contract.get("contract_id") if isinstance(contract, dict) else None
    if evidence["contract_id"] != expected_contract_id:
        _append(failures, "evidence-contract-id-mismatch")
        valid = False
    if not isinstance(evidence["contract_sha256"], str) or not DIGEST_RE.fullmatch(evidence["contract_sha256"]):
        _append(failures, "evidence-contract-digest-invalid")
        valid = False
    elif evidence["contract_sha256"] != contract_digest:
        _append(failures, "evidence-contract-digest-mismatch")
        valid = False

    observed_at = _parse_timestamp(evidence["observed_at"])
    if observed_at is None:
        _append(failures, "evidence-observed-at-invalid")
        valid = False
    else:
        age = (now - observed_at).total_seconds()
        if age < -300:
            _append(failures, "evidence-observed-in-future")
            valid = False
        elif age > max_age_seconds:
            _append(failures, "evidence-stale")
            valid = False

    evaluation = evidence["evaluation"]
    if not isinstance(evaluation, dict) or frozenset(evaluation) != EVALUATION_KEYS:
        _append(failures, "evidence-evaluation-schema-invalid")
        valid = False
    else:
        if evaluation["holdout_kind"] != "out_of_time":
            _append(failures, "evidence-holdout-not-out-of-time")
            valid = False
        if evaluation["future_field_leakage_detected"] is not False:
            _append(failures, "evidence-leakage-check-failed")
            valid = False
        started_at = _parse_timestamp(evaluation["started_at"])
        ended_at = _parse_timestamp(evaluation["ended_at"])
        if started_at is None or ended_at is None or ended_at < started_at:
            _append(failures, "evidence-evaluation-window-invalid")
            valid = False
        elif observed_at is not None and ended_at > observed_at:
            _append(failures, "evidence-evaluation-window-invalid")
            valid = False

    metrics = evidence["metrics"]
    if not isinstance(metrics, dict) or frozenset(metrics) != frozenset(THRESHOLD_KEYS):
        _append(failures, "evidence-metrics-schema-invalid")
        valid = False
    else:
        for metric, value in metrics.items():
            if not _finite_number(value):
                _append(failures, f"evidence-{metric.replace('_', '-')}-invalid")
                valid = False
            elif metric in COUNT_METRICS and (type(value) is not int or value < 0):
                _append(failures, f"evidence-{metric.replace('_', '-')}-invalid")
                valid = False
    return valid


def build_report(
    contract: Any,
    evidence: Any,
    *,
    expected_commit: str | None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    now: datetime | None = None,
    initial_failures: Iterable[str] = (),
    require_accepted: bool = False,
) -> dict[str, Any]:
    failures = list(dict.fromkeys(initial_failures))
    blockers: list[str] = []
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    expected_commit_valid = isinstance(expected_commit, str) and COMMIT_RE.fullmatch(expected_commit) is not None
    if not expected_commit_valid:
        _append(failures, "expected-commit-required")

    contract_valid = _validate_contract(contract, failures, blockers)
    digest = contract_sha256(contract)
    if digest is None:
        _append(failures, "contract-digest-unavailable")
    evidence_valid = _validate_evidence(
        evidence,
        contract=contract,
        contract_digest=digest,
        expected_commit=expected_commit if expected_commit_valid else None,
        max_age_seconds=max_age_seconds,
        now=now_utc,
        failures=failures,
    )

    metrics_pass = False
    if contract_valid and evidence_valid and isinstance(contract, dict) and isinstance(evidence, dict):
        metrics_pass = True
        thresholds = contract["thresholds"]
        for metric, expected_operator in THRESHOLD_KEYS.items():
            threshold = thresholds[metric]["value"]
            if threshold is None:
                metrics_pass = False
                continue
            observed = evidence["metrics"][metric]
            passed = observed >= threshold if expected_operator == "gte" else observed <= threshold
            if not passed:
                _append(blockers, f"metric-{metric.replace('_', '-')}-below-contract")
                metrics_pass = False

    contract_approved = contract_valid and isinstance(contract, dict) and contract.get("status") == "approved"
    accepted = not failures and contract_approved and not blockers and metrics_pass
    if require_accepted and not accepted:
        _append(failures, "model-acceptance-required")

    assessment_valid = not failures or (failures == ["model-acceptance-required"])
    success = not failures
    verdict = "accepted" if success and accepted else "not-accepted" if assessment_valid else "fail"
    reason = (
        "all-approved-thresholds-pass"
        if success and accepted
        else blockers[0]
        if assessment_valid and blockers
        else failures[0]
        if failures
        else "model-acceptance-incomplete"
    )

    contract_id = contract.get("contract_id") if isinstance(contract, dict) else None
    contract_status = contract.get("status") if isinstance(contract, dict) else None
    model_id = evidence.get("model_id") if isinstance(evidence, dict) else None
    model_artifact_digest = (
        evidence.get("model_artifact_sha256") if isinstance(evidence, dict) else None
    )
    feature_columns_digest = (
        evidence.get("model_feature_columns_sha256") if isinstance(evidence, dict) else None
    )
    observations_digest = (
        evidence.get("observations_sha256") if isinstance(evidence, dict) else None
    )
    observed_at_value = evidence.get("observed_at") if isinstance(evidence, dict) else None
    observed_at = _parse_timestamp(observed_at_value)
    return {
        "report_schema": REPORT_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "success": success,
        "verdict": verdict,
        "verdict_reason": reason,
        "accepted": accepted,
        "acceptance_required": require_accepted,
        "evaluated_commit_sha": expected_commit if expected_commit_valid else None,
        "contract": {
            "contract_id": contract_id
            if isinstance(contract_id, str) and IDENTIFIER_RE.fullmatch(contract_id)
            else None,
            "sha256": digest if isinstance(digest, str) and DIGEST_RE.fullmatch(digest) else None,
            "status": contract_status if contract_status in {"draft", "approved"} else None,
        },
        "evidence": {
            "model_id": model_id if isinstance(model_id, str) and IDENTIFIER_RE.fullmatch(model_id) else None,
            "model_artifact_sha256": model_artifact_digest
            if isinstance(model_artifact_digest, str) and DIGEST_RE.fullmatch(model_artifact_digest)
            else None,
            "model_feature_columns_sha256": feature_columns_digest
            if isinstance(feature_columns_digest, str) and DIGEST_RE.fullmatch(feature_columns_digest)
            else None,
            "observations_sha256": observations_digest
            if isinstance(observations_digest, str) and DIGEST_RE.fullmatch(observations_digest)
            else None,
            "observed_at": observed_at.isoformat() if observed_at is not None else None,
        },
        "blockers": blockers,
        "checks": {
            "contract_schema": contract_valid,
            "contract_approved": contract_approved,
            "evidence_schema_and_binding": evidence_valid,
            "metrics_against_thresholds": metrics_pass,
            "promotion_policy": not require_accepted or accepted,
        },
        "failure_codes": failures,
    }


def validate_gate_report(
    report: Any,
    *,
    expected_commit: str | None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    now: datetime | None = None,
) -> tuple[bool, tuple[str, ...]]:
    failures: list[str] = []
    if not isinstance(report, dict) or frozenset(report) != REPORT_KEYS:
        return False, ("report-schema-invalid",)
    if report["report_schema"] != REPORT_SCHEMA:
        _append(failures, "report-schema-invalid")
    if type(report["schema_version"]) is not int or report["schema_version"] != SCHEMA_VERSION:
        _append(failures, "report-schema-version-invalid")
    if not isinstance(expected_commit, str) or COMMIT_RE.fullmatch(expected_commit) is None:
        _append(failures, "expected-commit-required")
    elif report["evaluated_commit_sha"] != expected_commit:
        _append(failures, "report-candidate-commit-mismatch")

    if report["success"] is not True:
        _append(failures, "report-success-required")
    if report["verdict"] != "accepted" or report["verdict_reason"] != "all-approved-thresholds-pass":
        _append(failures, "report-accepted-verdict-required")
    if report["accepted"] is not True or report["acceptance_required"] is not True:
        _append(failures, "report-acceptance-policy-invalid")
    if report["blockers"] != [] or report["failure_codes"] != []:
        _append(failures, "report-cannot-carry-blockers")

    contract = report["contract"]
    if not isinstance(contract, dict) or frozenset(contract) != REPORT_CONTRACT_KEYS:
        _append(failures, "report-contract-projection-invalid")
    else:
        if not isinstance(contract["contract_id"], str) or not IDENTIFIER_RE.fullmatch(contract["contract_id"]):
            _append(failures, "report-contract-projection-invalid")
        if not isinstance(contract["sha256"], str) or not DIGEST_RE.fullmatch(contract["sha256"]):
            _append(failures, "report-contract-projection-invalid")
        if contract["status"] != "approved":
            _append(failures, "report-contract-not-approved")

    evidence = report["evidence"]
    if not isinstance(evidence, dict) or frozenset(evidence) != REPORT_EVIDENCE_KEYS:
        _append(failures, "report-evidence-projection-invalid")
    else:
        if not isinstance(evidence["model_id"], str) or not IDENTIFIER_RE.fullmatch(evidence["model_id"]):
            _append(failures, "report-evidence-projection-invalid")
        for digest_name in (
            "model_artifact_sha256",
            "model_feature_columns_sha256",
            "observations_sha256",
        ):
            if (
                not isinstance(evidence[digest_name], str)
                or DIGEST_RE.fullmatch(evidence[digest_name]) is None
            ):
                _append(failures, "report-evidence-projection-invalid")
        observed_at = _parse_timestamp(evidence["observed_at"])
        if observed_at is None:
            _append(failures, "report-observed-at-invalid")
        else:
            now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
            age = (now_utc - observed_at).total_seconds()
            if age < -300:
                _append(failures, "report-observed-in-future")
            elif age > max_age_seconds:
                _append(failures, "report-stale")

    checks = report["checks"]
    if not isinstance(checks, dict) or frozenset(checks) != REPORT_CHECK_KEYS:
        _append(failures, "report-checks-invalid")
    elif any(value is not True for value in checks.values()):
        _append(failures, "report-checks-invalid")
    return not failures, tuple(failures)


def _positive_max_age(value: str) -> int:
    parsed = int(value)
    if parsed < 1 or parsed > 31 * 24 * 60 * 60:
        raise argparse.ArgumentTypeError("max age must be between 1 second and 31 days")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify the fail-closed model acceptance contract.")
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--trusted-report", type=Path)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--max-age-seconds", type=_positive_max_age, default=DEFAULT_MAX_AGE_SECONDS)
    parser.add_argument("--require-accepted", action="store_true")
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
    if args.trusted_report is not None:
        if args.contract is not None or args.evidence is not None or args.require_accepted:
            raise SystemExit("--trusted-report cannot be combined with contract/evidence generation options")
        report, load_failures = load_json(args.trusted_report, prefix="trusted-report")
        valid, validation_failures = validate_gate_report(
            report,
            expected_commit=args.expected_commit,
            max_age_seconds=args.max_age_seconds,
            now=datetime.now(timezone.utc),
        )
        failures = [*load_failures, *validation_failures]
        print(
            json.dumps(
                {"accepted": valid, "failure_codes": failures},
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
            )
        )
        return 0 if valid and not load_failures else 1
    if args.contract is None or args.evidence is None:
        raise SystemExit("--contract and --evidence are required unless --trusted-report is used")
    contract, contract_failures = load_json(args.contract, prefix="contract")
    evidence, evidence_failures = load_json(args.evidence, prefix="evidence")
    report = build_report(
        contract,
        evidence,
        expected_commit=args.expected_commit,
        max_age_seconds=args.max_age_seconds,
        now=datetime.now(timezone.utc),
        initial_failures=[*contract_failures, *evidence_failures],
        require_accepted=args.require_accepted,
    )
    _write_report_atomic(report)
    print(
        json.dumps(
            {
                "accepted": report["accepted"],
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
