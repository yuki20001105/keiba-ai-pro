from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
WORKER_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{2,79}$")


class RetrainWorkerConfigError(ValueError):
    """Fail-closed worker entrypoint configuration error."""


@dataclass(frozen=True)
class RetrainWorkerConfig:
    worker_id: str
    job_id: str
    expected_version: int
    candidate_commit_sha: str
    snapshot_source: Path
    lease_ttl_seconds: int
    execution_policy: str


def load_config(values: Mapping[str, str]) -> RetrainWorkerConfig:
    environment = (values.get("APP_ENV") or "").strip().lower()
    if environment not in {"staging", "sandbox"}:
        raise RetrainWorkerConfigError("retrain-worker-environment-forbidden")
    if (values.get("MODEL_RETRAIN_EXECUTION_ENABLED") or "").strip().lower() != "true":
        raise RetrainWorkerConfigError("retrain-worker-not-enabled")
    execution_policy = "staging-train" if environment == "staging" else "sandbox-train"

    worker_id = (values.get("MODEL_RETRAIN_WORKER_ID") or "").strip()
    if WORKER_RE.fullmatch(worker_id) is None:
        raise RetrainWorkerConfigError("retrain-worker-id-invalid")
    job_id_value = (values.get("MODEL_RETRAIN_JOB_ID") or "").strip()
    try:
        job_id = str(uuid.UUID(job_id_value))
    except (ValueError, AttributeError) as exc:
        raise RetrainWorkerConfigError("retrain-worker-job-id-invalid") from exc
    try:
        expected_version = int(values.get("MODEL_RETRAIN_JOB_VERSION") or "")
    except ValueError as exc:
        raise RetrainWorkerConfigError("retrain-worker-job-version-invalid") from exc
    if expected_version < 1:
        raise RetrainWorkerConfigError("retrain-worker-job-version-invalid")

    candidate_commit_sha = (values.get("APP_COMMIT_SHA") or "").strip().lower()
    if COMMIT_RE.fullmatch(candidate_commit_sha) is None:
        raise RetrainWorkerConfigError("retrain-worker-commit-invalid")
    snapshot_value = (values.get("MODEL_RETRAIN_SNAPSHOT_PATH") or "").strip()
    snapshot_source = Path(snapshot_value)
    if not snapshot_source.is_absolute():
        raise RetrainWorkerConfigError("retrain-worker-snapshot-path-invalid")
    try:
        lease_ttl_seconds = int(values.get("MODEL_RETRAIN_LEASE_TTL_SECONDS") or "120")
    except ValueError as exc:
        raise RetrainWorkerConfigError("retrain-worker-ttl-invalid") from exc
    if not 30 <= lease_ttl_seconds <= 300:
        raise RetrainWorkerConfigError("retrain-worker-ttl-invalid")

    return RetrainWorkerConfig(
        worker_id=worker_id,
        job_id=job_id,
        expected_version=expected_version,
        candidate_commit_sha=candidate_commit_sha,
        snapshot_source=snapshot_source,
        lease_ttl_seconds=lease_ttl_seconds,
        execution_policy=execution_policy,
    )


async def run(config: RetrainWorkerConfig) -> dict[str, object]:
    from app_config import get_active_model_id, get_supabase_client  # type: ignore
    from training.retrain_worker import (  # type: ignore
        ApprovedRetrainCoordinator,
        RetrainGatewayUnavailable,
        SupabaseRetrainGateway,
    )

    active_model_id = get_active_model_id()
    if not active_model_id:
        raise RetrainGatewayUnavailable("retrain-active-model-unavailable")
    client = get_supabase_client()
    if client is None:
        raise RetrainGatewayUnavailable("retrain-service-client-unavailable")
    coordinator = ApprovedRetrainCoordinator(
        SupabaseRetrainGateway(client),
        worker_id=config.worker_id,
        candidate_commit_sha=config.candidate_commit_sha,
        active_model_id=active_model_id,
        execution_policy=config.execution_policy,
        lease_ttl_seconds=config.lease_ttl_seconds,
    )
    artifact = await coordinator.run_job(
        job_id=config.job_id,
        expected_version=config.expected_version,
        snapshot_source=config.snapshot_source,
    )
    return {
        "success": True,
        "job_id": artifact.job_id,
        "artifact_sha256": artifact.sha256,
        "artifact_size_bytes": artifact.size_bytes,
        "artifact_media_type": artifact.media_type,
        "object_name": artifact.object_name,
    }


def main() -> int:
    try:
        config = load_config(os.environ)
        result = asyncio.run(run(config))
    except Exception:
        print(json.dumps({"success": False, "code": "retrain-worker-failed"}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
