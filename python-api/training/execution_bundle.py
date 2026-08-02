from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from training.approved_execution import ApprovedExecutionError, ApprovedTrainingExecution


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
WORKER_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{2,79}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
MAX_LEASE_TTL = timedelta(seconds=300)
BUNDLE_KEYS = frozenset(
    {
        "schema_version",
        "job_id",
        "approval_id",
        "dry_run_id",
        "approved_payload_hash",
        "worker_id",
        "fencing_token",
        "record_version",
        "lease_expires_at",
        "execution_policy",
        "target",
        "model_type",
        "train_period",
        "validation_period",
        "selected_features",
        "removed_features",
        "data_snapshot_sha256",
        "feature_contract_sha256",
        "candidate_commit_sha",
        "active_model_id",
        "training_parameters",
    }
)
TRAINING_PARAMETERS = {
    "force_sync": False,
    "test_size": 0.2,
    "cv_folds": 5,
    "use_optuna": False,
}


def _uuid(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ApprovedExecutionError(f"execution-bundle-{label}-invalid")
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ApprovedExecutionError(f"execution-bundle-{label}-invalid") from exc


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise ApprovedExecutionError("execution-bundle-lease-invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApprovedExecutionError("execution-bundle-lease-invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ApprovedExecutionError("execution-bundle-lease-invalid")
    return parsed.astimezone(timezone.utc)


def _period(value: Any, label: str) -> tuple[str, str]:
    if not isinstance(value, dict) or set(value) != {"start", "end"}:
        raise ApprovedExecutionError(f"execution-bundle-{label}-invalid")
    start = value.get("start")
    end = value.get("end")
    if not isinstance(start, str) or not isinstance(end, str):
        raise ApprovedExecutionError(f"execution-bundle-{label}-invalid")
    return start, end


def _features(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ApprovedExecutionError(f"execution-bundle-{label}-invalid")
    return tuple(value)


@dataclass(frozen=True)
class ApprovedExecutionBundle:
    job_id: str
    approval_id: str
    dry_run_id: str
    approved_payload_hash: str
    worker_id: str
    fencing_token: int
    record_version: int
    lease_expires_at: datetime
    execution_policy: str
    target: str
    model_type: str
    train_period_start: str
    train_period_end: str
    validation_period_start: str
    validation_period_end: str
    selected_features: tuple[str, ...]
    removed_features: tuple[str, ...]
    data_snapshot_sha256: str
    feature_contract_sha256: str
    candidate_commit_sha: str
    active_model_id: str

    @classmethod
    def from_rpc(
        cls,
        value: Mapping[str, Any],
        *,
        expected_job_id: str,
        expected_worker_id: str,
        expected_fencing_token: int,
        expected_candidate_commit_sha: str,
        expected_active_model_id: str,
        now: datetime,
    ) -> "ApprovedExecutionBundle":
        if not isinstance(value, Mapping) or set(value) != BUNDLE_KEYS:
            raise ApprovedExecutionError("execution-bundle-schema-invalid")
        if type(value.get("schema_version")) is not int or value.get("schema_version") != 1:
            raise ApprovedExecutionError("execution-bundle-version-invalid")
        job_id = _uuid(value.get("job_id"), "job-id")
        approval_id = _uuid(value.get("approval_id"), "approval-id")
        dry_run_id = _uuid(value.get("dry_run_id"), "dry-run-id")
        if job_id != str(uuid.UUID(expected_job_id)):
            raise ApprovedExecutionError("execution-bundle-job-binding-mismatch")

        worker_id = value.get("worker_id")
        if (
            not isinstance(worker_id, str)
            or WORKER_RE.fullmatch(worker_id) is None
            or worker_id != expected_worker_id
        ):
            raise ApprovedExecutionError("execution-bundle-worker-binding-mismatch")
        fencing_token = value.get("fencing_token")
        record_version = value.get("record_version")
        if (
            type(fencing_token) is not int
            or fencing_token < 1
            or fencing_token != expected_fencing_token
            or type(record_version) is not int
            or record_version < 1
        ):
            raise ApprovedExecutionError("execution-bundle-fence-binding-mismatch")

        if now.tzinfo is None or now.utcoffset() is None:
            raise ApprovedExecutionError("execution-bundle-clock-invalid")
        observed_at = now.astimezone(timezone.utc)
        lease_expires_at = _timestamp(value.get("lease_expires_at"))
        if (
            lease_expires_at <= observed_at
            or lease_expires_at > observed_at + MAX_LEASE_TTL + timedelta(seconds=5)
        ):
            raise ApprovedExecutionError("execution-bundle-lease-invalid")

        approved_payload_hash = value.get("approved_payload_hash")
        data_snapshot_sha256 = value.get("data_snapshot_sha256")
        feature_contract_sha256 = value.get("feature_contract_sha256")
        if any(
            not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None
            for digest in (
                approved_payload_hash,
                data_snapshot_sha256,
                feature_contract_sha256,
            )
        ):
            raise ApprovedExecutionError("execution-bundle-digest-invalid")
        if any(
            digest == "0" * 64
            for digest in (
                approved_payload_hash,
                data_snapshot_sha256,
                feature_contract_sha256,
            )
        ):
            raise ApprovedExecutionError("execution-bundle-digest-invalid")

        candidate_commit_sha = value.get("candidate_commit_sha")
        if (
            not isinstance(candidate_commit_sha, str)
            or COMMIT_RE.fullmatch(candidate_commit_sha) is None
            or candidate_commit_sha != expected_candidate_commit_sha
        ):
            raise ApprovedExecutionError("execution-bundle-commit-binding-mismatch")
        active_model_id = value.get("active_model_id")
        if (
            not isinstance(active_model_id, str)
            or IDENTIFIER_RE.fullmatch(active_model_id) is None
            or active_model_id != expected_active_model_id
        ):
            raise ApprovedExecutionError("execution-bundle-active-model-binding-mismatch")

        execution_policy = value.get("execution_policy")
        target = value.get("target")
        model_type = value.get("model_type")
        if execution_policy not in {"staging-train", "sandbox-train"}:
            raise ApprovedExecutionError("execution-bundle-policy-invalid")
        if target != "win" or model_type != "lightgbm":
            raise ApprovedExecutionError("execution-bundle-training-shape-invalid")
        training_parameters = value.get("training_parameters")
        if (
            not isinstance(training_parameters, dict)
            or set(training_parameters) != set(TRAINING_PARAMETERS)
            or type(training_parameters.get("force_sync")) is not bool
            or training_parameters.get("force_sync") is not False
            or type(training_parameters.get("test_size")) is not float
            or training_parameters.get("test_size") != 0.2
            or type(training_parameters.get("cv_folds")) is not int
            or training_parameters.get("cv_folds") != 5
            or type(training_parameters.get("use_optuna")) is not bool
            or training_parameters.get("use_optuna") is not False
        ):
            raise ApprovedExecutionError("execution-bundle-training-parameters-invalid")

        train_start, train_end = _period(value.get("train_period"), "train-period")
        validation_start, validation_end = _period(
            value.get("validation_period"),
            "validation-period",
        )
        selected_features = _features(value.get("selected_features"), "selected-features")
        removed_features = _features(value.get("removed_features"), "removed-features")

        return cls(
            job_id=job_id,
            approval_id=approval_id,
            dry_run_id=dry_run_id,
            approved_payload_hash=approved_payload_hash,
            worker_id=worker_id,
            fencing_token=fencing_token,
            record_version=record_version,
            lease_expires_at=lease_expires_at,
            execution_policy=execution_policy,
            target=target,
            model_type=model_type,
            train_period_start=train_start,
            train_period_end=train_end,
            validation_period_start=validation_start,
            validation_period_end=validation_end,
            selected_features=selected_features,
            removed_features=removed_features,
            data_snapshot_sha256=data_snapshot_sha256,
            feature_contract_sha256=feature_contract_sha256,
            candidate_commit_sha=candidate_commit_sha,
            active_model_id=active_model_id,
        )

    def create_execution(
        self,
        *,
        workspace: Path,
        snapshot_path: Path,
    ) -> ApprovedTrainingExecution:
        return ApprovedTrainingExecution.create(
            job_id=self.job_id,
            approved_payload_hash=self.approved_payload_hash,
            data_snapshot_sha256=self.data_snapshot_sha256,
            feature_contract_sha256=self.feature_contract_sha256,
            candidate_commit_sha=self.candidate_commit_sha,
            target=self.target,
            model_type=self.model_type,
            active_model_id=self.active_model_id,
            train_period_start=self.train_period_start,
            train_period_end=self.train_period_end,
            validation_period_start=self.validation_period_start,
            validation_period_end=self.validation_period_end,
            selected_features=self.selected_features,
            removed_features=self.removed_features,
            workspace=workspace,
            snapshot_path=snapshot_path,
        )
