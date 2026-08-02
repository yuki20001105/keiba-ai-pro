from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Mapping


IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{2,79}$")


class RetrainReconcilerConfigError(ValueError):
    """Fail-closed reconciliation entrypoint configuration error."""


@dataclass(frozen=True)
class RetrainReconcilerConfig:
    reconciler_id: str
    min_age_seconds: int
    limit: int


def load_config(values: Mapping[str, str]) -> RetrainReconcilerConfig:
    environment = (values.get("APP_ENV") or "").strip().lower()
    if environment not in {"staging", "sandbox"}:
        raise RetrainReconcilerConfigError("retrain-reconciler-environment-forbidden")
    if (
        values.get("MODEL_RETRAIN_ORPHAN_RECONCILIATION_ENABLED") or ""
    ).strip().lower() != "true":
        raise RetrainReconcilerConfigError("retrain-reconciler-not-enabled")
    reconciler_id = (values.get("MODEL_RETRAIN_RECONCILER_ID") or "").strip()
    if IDENTIFIER_RE.fullmatch(reconciler_id) is None:
        raise RetrainReconcilerConfigError("retrain-reconciler-id-invalid")
    try:
        min_age_seconds = int(
            values.get("MODEL_RETRAIN_ORPHAN_MIN_AGE_SECONDS") or "3600"
        )
        limit = int(values.get("MODEL_RETRAIN_RECONCILIATION_LIMIT") or "20")
    except ValueError as exc:
        raise RetrainReconcilerConfigError("retrain-reconciler-bounds-invalid") from exc
    if not 3600 <= min_age_seconds <= 604800 or not 1 <= limit <= 50:
        raise RetrainReconcilerConfigError("retrain-reconciler-bounds-invalid")
    return RetrainReconcilerConfig(reconciler_id, min_age_seconds, limit)


def run(config: RetrainReconcilerConfig) -> dict[str, object]:
    from app_config import get_supabase_client  # type: ignore
    from training.retrain_reconciler import (  # type: ignore
        RetrainOrphanReconciler,
        SupabaseRetrainReconciliationGateway,
    )

    client = get_supabase_client()
    result = RetrainOrphanReconciler(
        SupabaseRetrainReconciliationGateway(client),
        reconciler_id=config.reconciler_id,
        min_age_seconds=config.min_age_seconds,
        limit=config.limit,
    ).run_once()
    return {
        "success": result.successful,
        "run_id": result.run_id,
        "recovered_count": result.recovered_count,
        "candidate_count": result.candidate_count,
        "deleted_count": result.deleted_count,
        "not_found_count": result.not_found_count,
        "failed_count": result.failed_count,
    }


def main() -> int:
    try:
        result = run(load_config(os.environ))
    except Exception:
        print(json.dumps({"success": False, "code": "retrain-reconciler-failed"}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
