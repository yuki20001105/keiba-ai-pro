from __future__ import annotations

import json
import os
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
EVALUATOR_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{2,79}$")


class RetrainEvaluatorConfigError(ValueError):
    """Fail-closed evaluator entrypoint configuration error."""


@dataclass(frozen=True)
class RetrainEvaluatorConfig:
    evaluator_id: str
    candidate_commit_sha: str
    job_id: str
    expected_version: int
    observations_path: Path


def load_config(values: Mapping[str, str]) -> RetrainEvaluatorConfig:
    environment = (values.get("APP_ENV") or "").strip().lower()
    if environment not in {"staging", "sandbox"}:
        raise RetrainEvaluatorConfigError("retrain-evaluator-environment-forbidden")
    if (values.get("MODEL_RETRAIN_EVALUATION_ENABLED") or "").strip().lower() != "true":
        raise RetrainEvaluatorConfigError("retrain-evaluator-not-enabled")
    evaluator_id = (values.get("MODEL_RETRAIN_EVALUATOR_ID") or "").strip()
    if EVALUATOR_RE.fullmatch(evaluator_id) is None:
        raise RetrainEvaluatorConfigError("retrain-evaluator-id-invalid")
    commit_sha = (values.get("APP_COMMIT_SHA") or "").strip().lower()
    if COMMIT_RE.fullmatch(commit_sha) is None or commit_sha == "0" * 40:
        raise RetrainEvaluatorConfigError("retrain-evaluator-commit-invalid")
    raw_job_id = (values.get("MODEL_RETRAIN_JOB_ID") or "").strip()
    try:
        job_id = str(uuid.UUID(raw_job_id))
    except ValueError as exc:
        raise RetrainEvaluatorConfigError("retrain-evaluator-job-invalid") from exc
    if job_id != raw_job_id:
        raise RetrainEvaluatorConfigError("retrain-evaluator-job-invalid")
    try:
        expected_version = int(values.get("MODEL_RETRAIN_JOB_VERSION") or "")
    except ValueError as exc:
        raise RetrainEvaluatorConfigError("retrain-evaluator-version-invalid") from exc
    if expected_version < 1:
        raise RetrainEvaluatorConfigError("retrain-evaluator-version-invalid")
    observations_path = Path(
        (values.get("MODEL_RETRAIN_OBSERVATIONS_PATH") or "").strip()
    )
    if not observations_path.is_absolute() or observations_path.is_symlink():
        raise RetrainEvaluatorConfigError("retrain-evaluator-observations-invalid")
    return RetrainEvaluatorConfig(
        evaluator_id=evaluator_id,
        candidate_commit_sha=commit_sha,
        job_id=job_id,
        expected_version=expected_version,
        observations_path=observations_path,
    )


def run(config: RetrainEvaluatorConfig) -> dict[str, object]:
    from app_config import get_supabase_client  # type: ignore
    from training.retrain_evaluator import (  # type: ignore
        ApprovedRetrainEvaluator,
        SupabaseRetrainEvaluationGateway,
    )

    client = get_supabase_client()
    if client is None:
        raise RuntimeError("retrain-evaluator-runtime-unavailable")
    result = ApprovedRetrainEvaluator(
        SupabaseRetrainEvaluationGateway(client),
        evaluator_id=config.evaluator_id,
        candidate_commit_sha=config.candidate_commit_sha,
    ).run(
        job_id=config.job_id,
        expected_version=config.expected_version,
        observations_path=config.observations_path,
    )
    return {
        "success": True,
        "job_id": result.job_id,
        "record_version": result.record_version,
        "evaluation_report_sha256": result.evaluation_report_sha256,
        "evaluated_at": result.evaluated_at.isoformat(),
        "promotion_eligible": False,
    }


def main() -> int:
    try:
        result = run(load_config(os.environ))
    except Exception:
        print(json.dumps({"success": False, "code": "retrain-evaluator-failed"}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
