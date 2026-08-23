from __future__ import annotations

import importlib.util
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol


ROOT = Path(__file__).resolve().parents[2]
BUILDER_PATH = ROOT / "scripts" / "build_model_acceptance_evidence.py"
VERIFIER_PATH = ROOT / "scripts" / "verify_model_acceptance.py"
CANONICAL_CONTRACT_PATH = ROOT / "config" / "model_acceptance_contract.v1.json"
CANONICAL_STAKING_POLICY_PATH = ROOT / "config" / "phase3n_staking_payout_policy.v1.json"

DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
EVALUATOR_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{2,79}$")

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
CONTRACT_KEYS = frozenset({"contract_id", "sha256", "status"})
EVIDENCE_KEYS = frozenset(
    {
        "model_id",
        "model_artifact_sha256",
        "model_feature_columns_sha256",
        "observations_sha256",
        "observed_at",
    }
)
CHECK_KEYS = frozenset(
    {
        "contract_schema",
        "contract_approved",
        "evidence_schema_and_binding",
        "metrics_against_thresholds",
        "promotion_policy",
    }
)


class RetrainEvaluationError(RuntimeError):
    """Sanitized accepted-evaluation runtime failure."""


@dataclass(frozen=True)
class RecordedEvaluation:
    job_id: str
    record_version: int
    evaluation_report_sha256: str
    evaluated_at: datetime


class EvaluationGateway(Protocol):
    def register_accepted(
        self,
        *,
        evaluator_id: str,
        job_id: str,
        expected_version: int,
        report: Mapping[str, Any],
    ) -> RecordedEvaluation: ...


class AcceptancePipeline(Protocol):
    def accepted_report(
        self,
        observations_path: Path,
        *,
        expected_commit: str,
    ) -> dict[str, Any]: ...


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RetrainEvaluationError("retrain-evaluation-runtime-unavailable")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise RetrainEvaluationError("retrain-evaluation-runtime-unavailable") from exc
    return module


class ModelAcceptancePipeline:
    """Strict rows -> recomputed evidence -> accepted sanitized report, in memory only."""

    def __init__(
        self,
        contract_path: Path = CANONICAL_CONTRACT_PATH,
        staking_policy_path: Path = CANONICAL_STAKING_POLICY_PATH,
    ) -> None:
        if not contract_path.is_absolute() or contract_path.is_symlink():
            raise RetrainEvaluationError("retrain-evaluation-contract-invalid")
        try:
            self._contract_path = contract_path.resolve(strict=True)
        except OSError as exc:
            raise RetrainEvaluationError("retrain-evaluation-contract-invalid") from exc
        if not self._contract_path.is_file():
            raise RetrainEvaluationError("retrain-evaluation-contract-invalid")
        if not staking_policy_path.is_absolute() or staking_policy_path.is_symlink():
            raise RetrainEvaluationError("retrain-evaluation-staking-policy-invalid")
        try:
            self._staking_policy_path = staking_policy_path.resolve(strict=True)
        except OSError as exc:
            raise RetrainEvaluationError("retrain-evaluation-staking-policy-invalid") from exc
        if not self._staking_policy_path.is_file():
            raise RetrainEvaluationError("retrain-evaluation-staking-policy-invalid")
        self._builder = _load_module("retrain_evaluation_builder", BUILDER_PATH)
        self._verifier = _load_module("retrain_evaluation_verifier", VERIFIER_PATH)

    def accepted_report(
        self,
        observations_path: Path,
        *,
        expected_commit: str,
    ) -> dict[str, Any]:
        if not observations_path.is_absolute() or observations_path.is_symlink():
            raise RetrainEvaluationError("retrain-evaluation-observations-invalid")
        try:
            resolved = observations_path.resolve(strict=True)
            source = self._builder.load_json(resolved, prefix="observations")
            contract = self._builder.load_json(
                self._contract_path,
                prefix="contract",
                max_bytes=self._verifier.MAX_INPUT_BYTES,
            )
            evidence = self._builder.build_evidence(
                source,
                contract,
                expected_commit=expected_commit,
                staking_policy_path=self._staking_policy_path,
            )
            report = self._verifier.build_report(
                contract,
                evidence,
                expected_commit=expected_commit,
                require_accepted=True,
            )
            valid, _failures = self._verifier.validate_gate_report(
                report,
                expected_commit=expected_commit,
            )
        except Exception as exc:
            raise RetrainEvaluationError("retrain-evaluation-not-accepted") from exc
        if valid is not True:
            raise RetrainEvaluationError("retrain-evaluation-not-accepted")
        return report


class SupabaseRetrainEvaluationGateway:
    def __init__(self, client: Any) -> None:
        if client is None or not callable(getattr(client, "rpc", None)):
            raise RetrainEvaluationError("retrain-evaluation-client-unavailable")
        self._client = client

    @staticmethod
    def _timestamp(value: Any) -> datetime:
        if not isinstance(value, str):
            raise RetrainEvaluationError("retrain-evaluation-response-invalid")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RetrainEvaluationError("retrain-evaluation-response-invalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise RetrainEvaluationError("retrain-evaluation-response-invalid")
        return parsed.astimezone(timezone.utc)

    def register_accepted(
        self,
        *,
        evaluator_id: str,
        job_id: str,
        expected_version: int,
        report: Mapping[str, Any],
    ) -> RecordedEvaluation:
        try:
            response = self._client.rpc(
                "register_model_retrain_accepted_evaluation",
                {
                    "p_evaluator_id": evaluator_id,
                    "p_job_id": job_id,
                    "p_expected_version": expected_version,
                    "p_sanitized_report": dict(report),
                },
            ).execute()
            data = getattr(response, "data", None)
        except Exception as exc:
            raise RetrainEvaluationError("retrain-evaluation-registration-failed") from exc
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            raise RetrainEvaluationError("retrain-evaluation-response-invalid")
        row = data[0]
        try:
            returned_job_id = str(uuid.UUID(str(row["job_id"])))
            version = row["record_version"]
            report_sha256 = row["evaluation_report_sha256"]
            evaluated_at = self._timestamp(row["evaluated_at"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RetrainEvaluationError("retrain-evaluation-response-invalid") from exc
        if (
            returned_job_id != row["job_id"]
            or returned_job_id != job_id
            or type(version) is not int
            or version != expected_version + 1
            or row.get("job_state") != "evaluation-recorded"
            or row.get("evaluation_recorded") is not True
            or row.get("acceptance_passed") is not True
            or row.get("promotion_eligible") is not False
            or row.get("evaluator_id") != evaluator_id
            or not isinstance(report_sha256, str)
            or DIGEST_RE.fullmatch(report_sha256) is None
        ):
            raise RetrainEvaluationError("retrain-evaluation-response-invalid")
        return RecordedEvaluation(
            job_id=returned_job_id,
            record_version=version,
            evaluation_report_sha256=report_sha256,
            evaluated_at=evaluated_at,
        )


class ApprovedRetrainEvaluator:
    def __init__(
        self,
        gateway: EvaluationGateway,
        *,
        evaluator_id: str,
        candidate_commit_sha: str,
        pipeline: AcceptancePipeline | None = None,
    ) -> None:
        if EVALUATOR_RE.fullmatch(evaluator_id or "") is None:
            raise RetrainEvaluationError("retrain-evaluator-id-invalid")
        if (
            COMMIT_RE.fullmatch(candidate_commit_sha or "") is None
            or candidate_commit_sha == "0" * 40
        ):
            raise RetrainEvaluationError("retrain-evaluator-commit-invalid")
        self._gateway = gateway
        self._evaluator_id = evaluator_id
        self._candidate_commit_sha = candidate_commit_sha
        self._pipeline = pipeline or ModelAcceptancePipeline()

    def _validate_report(self, report: object) -> dict[str, Any]:
        if not isinstance(report, dict) or frozenset(report) != REPORT_KEYS:
            raise RetrainEvaluationError("retrain-evaluation-report-invalid")
        contract = report.get("contract")
        evidence = report.get("evidence")
        checks = report.get("checks")
        if (
            report.get("report_schema") != "model-acceptance-gate-report"
            or type(report.get("schema_version")) is not int
            or report.get("schema_version") != 1
            or report.get("success") is not True
            or report.get("verdict") != "accepted"
            or report.get("verdict_reason") != "all-approved-thresholds-pass"
            or report.get("accepted") is not True
            or report.get("acceptance_required") is not True
            or report.get("evaluated_commit_sha") != self._candidate_commit_sha
            or report.get("blockers") != []
            or report.get("failure_codes") != []
            or not isinstance(contract, dict)
            or frozenset(contract) != CONTRACT_KEYS
            or contract.get("status") != "approved"
            or not isinstance(contract.get("contract_id"), str)
            or IDENTIFIER_RE.fullmatch(contract["contract_id"]) is None
            or not isinstance(contract.get("sha256"), str)
            or DIGEST_RE.fullmatch(contract["sha256"]) is None
            or not isinstance(evidence, dict)
            or frozenset(evidence) != EVIDENCE_KEYS
            or not isinstance(evidence.get("model_id"), str)
            or IDENTIFIER_RE.fullmatch(evidence["model_id"]) is None
            or any(
                not isinstance(evidence.get(key), str)
                or DIGEST_RE.fullmatch(evidence[key]) is None
                for key in (
                    "model_artifact_sha256",
                    "model_feature_columns_sha256",
                    "observations_sha256",
                )
            )
            or not isinstance(checks, dict)
            or frozenset(checks) != CHECK_KEYS
            or any(value is not True for value in checks.values())
        ):
            raise RetrainEvaluationError("retrain-evaluation-report-invalid")
        try:
            observed_at = datetime.fromisoformat(
                str(evidence["observed_at"]).replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise RetrainEvaluationError("retrain-evaluation-report-invalid") from exc
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise RetrainEvaluationError("retrain-evaluation-report-invalid")
        now = datetime.now(timezone.utc)
        observed_at = observed_at.astimezone(timezone.utc)
        if observed_at < now - timedelta(days=7) or observed_at > now + timedelta(minutes=5):
            raise RetrainEvaluationError("retrain-evaluation-report-invalid")
        return report

    def run(
        self,
        *,
        job_id: str,
        expected_version: int,
        observations_path: Path,
    ) -> RecordedEvaluation:
        try:
            canonical_job_id = str(uuid.UUID(job_id))
        except (AttributeError, TypeError, ValueError) as exc:
            raise RetrainEvaluationError("retrain-evaluation-job-invalid") from exc
        if canonical_job_id != job_id or type(expected_version) is not int or expected_version < 1:
            raise RetrainEvaluationError("retrain-evaluation-job-invalid")
        report = self._validate_report(
            self._pipeline.accepted_report(
                observations_path,
                expected_commit=self._candidate_commit_sha,
            )
        )
        result = self._gateway.register_accepted(
            evaluator_id=self._evaluator_id,
            job_id=canonical_job_id,
            expected_version=expected_version,
            report=report,
        )
        if result.job_id != canonical_job_id or result.record_version != expected_version + 1:
            raise RetrainEvaluationError("retrain-evaluation-response-invalid")
        return result
