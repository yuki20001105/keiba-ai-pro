from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
DISPATCHER_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{2,49}$")


class RetrainDispatcherConfigError(ValueError):
    """Fail-closed dispatcher entrypoint configuration error."""


@dataclass(frozen=True)
class RetrainDispatcherConfig:
    dispatcher_id: str
    execution_policy: str
    candidate_commit_sha: str
    snapshot_directory: Path
    limit: int
    lease_ttl_seconds: int


def load_config(values: Mapping[str, str]) -> RetrainDispatcherConfig:
    environment = (values.get("APP_ENV") or "").strip().lower()
    if environment not in {"staging", "sandbox"}:
        raise RetrainDispatcherConfigError("retrain-dispatcher-environment-forbidden")
    if (values.get("MODEL_RETRAIN_DISPATCH_ENABLED") or "").strip().lower() != "true":
        raise RetrainDispatcherConfigError("retrain-dispatcher-not-enabled")
    dispatcher_id = (values.get("MODEL_RETRAIN_DISPATCHER_ID") or "").strip()
    if DISPATCHER_RE.fullmatch(dispatcher_id) is None:
        raise RetrainDispatcherConfigError("retrain-dispatcher-id-invalid")
    commit_sha = (values.get("APP_COMMIT_SHA") or "").strip().lower()
    if COMMIT_RE.fullmatch(commit_sha) is None or commit_sha == "0" * 40:
        raise RetrainDispatcherConfigError("retrain-dispatcher-commit-invalid")
    directory = Path((values.get("MODEL_RETRAIN_SNAPSHOT_DIRECTORY") or "").strip())
    if not directory.is_absolute():
        raise RetrainDispatcherConfigError("retrain-dispatcher-snapshot-directory-invalid")
    try:
        limit = int(values.get("MODEL_RETRAIN_DISPATCH_LIMIT") or "1")
        ttl = int(values.get("MODEL_RETRAIN_LEASE_TTL_SECONDS") or "120")
    except ValueError as exc:
        raise RetrainDispatcherConfigError("retrain-dispatcher-bounds-invalid") from exc
    if not 1 <= limit <= 5 or not 30 <= ttl <= 300:
        raise RetrainDispatcherConfigError("retrain-dispatcher-bounds-invalid")
    return RetrainDispatcherConfig(
        dispatcher_id=dispatcher_id,
        execution_policy="staging-train" if environment == "staging" else "sandbox-train",
        candidate_commit_sha=commit_sha,
        snapshot_directory=directory,
        limit=limit,
        lease_ttl_seconds=ttl,
    )


async def run(config: RetrainDispatcherConfig) -> dict[str, object]:
    from app_config import get_active_model_id, get_supabase_client  # type: ignore
    from training.retrain_dispatcher import (  # type: ignore
        BoundedRetrainDispatcher,
        SnapshotCatalog,
        SupabaseRetrainDispatchGateway,
    )

    active_model_id = get_active_model_id()
    client = get_supabase_client()
    if not active_model_id or client is None:
        raise RuntimeError("retrain-dispatcher-runtime-unavailable")
    result = await BoundedRetrainDispatcher(
        SupabaseRetrainDispatchGateway(client),
        snapshot_catalog=SnapshotCatalog(config.snapshot_directory),
        dispatcher_id=config.dispatcher_id,
        execution_policy=config.execution_policy,
        candidate_commit_sha=config.candidate_commit_sha,
        active_model_id=active_model_id,
        limit=config.limit,
        lease_ttl_seconds=config.lease_ttl_seconds,
    ).run_once()
    return {
        "success": result.successful,
        "candidate_count": result.candidate_count,
        "dispatched_count": result.dispatched_count,
        "skipped_count": result.skipped_count,
        "failed_count": result.failed_count,
    }


def main() -> int:
    try:
        result = asyncio.run(run(load_config(os.environ)))
    except Exception:
        print(json.dumps({"success": False, "code": "retrain-dispatcher-failed"}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result["success"] is True else 1


if __name__ == "__main__":
    sys.exit(main())
