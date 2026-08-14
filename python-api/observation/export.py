from __future__ import annotations

import gzip
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .contracts import ObservationContractError, canonical_sha256, timestamp
from .staking import StakingPayoutPolicy, load_staking_payout_policy


def build_model_acceptance_source(
    predictions: list[Mapping[str, Any]],
    results: list[Mapping[str, Any]],
    progress: Mapping[str, Any],
    *,
    initial_bankroll: float,
    staking_policy: StakingPayoutPolicy | None = None,
) -> dict[str, Any]:
    if progress.get("readiness_verdict") != "READY_FOR_TRUSTED_REVIEW":
        raise ObservationContractError("observation-readiness-not-met")
    if initial_bankroll <= 0:
        raise ObservationContractError("initial-bankroll-invalid")
    policy = staking_policy or load_staking_payout_policy(require_approved=True)
    if not policy.approved or not policy.approval_reference:
        raise ObservationContractError("staking-policy-not-approved")
    result_by_id = {str(row["observation_id"]): row for row in results}
    models = {
        (
            str(row.get("model_id")),
            str(row.get("model_artifact_sha256")),
            str(row.get("candidate_commit_sha")),
            str(row.get("training_data_ended_at")),
            tuple(row.get("model_feature_columns") or []),
        )
        for row in predictions
    }
    if len(models) != 1:
        raise ObservationContractError("acceptance-export-single-model-required")
    model_id, artifact_sha, commit, training_end, feature_columns = next(iter(models))
    rows: list[dict[str, Any]] = []
    for prediction in predictions:
        result = result_by_id.get(str(prediction["observation_id"]))
        if result is None:
            raise ObservationContractError("acceptance-export-result-missing")
        rows.append(
            {
                "observation_id": str(prediction["observation_id"]),
                "race_date": str(prediction["race_date"]),
                "prediction_at": str(prediction["prediction_at"]),
                "data_observed_at": str(prediction["data_observed_at"]),
                "settled_at": str(result["settled_at"]),
                "y_true": int(result["y_true"]),
                "predicted_probability": float(prediction["predicted_probability"]),
                "wager_amount": float(prediction["wager_amount"]),
                "return_amount": float(result["return_amount"]),
                "baseline_wager_amount": float(prediction["baseline_wager_amount"]),
                "baseline_return_amount": float(result["baseline_return_amount"]),
                "latency_ms": float(prediction["latency_ms"]),
            }
        )
    return {
        "schema": "model-evaluation-observations",
        "schema_version": 2,
        "candidate_commit_sha": commit,
        "model_id": model_id,
        "model_artifact_sha256": artifact_sha,
        "generated_at": timestamp(datetime.now(timezone.utc)),
        "training_data_ended_at": training_end,
        "holdout_kind": "out_of_time",
        "model_feature_columns": list(feature_columns),
        "expanding_window_checks_passed": True,
        "initial_bankroll": float(initial_bankroll),
        "staking_payout_policy_id": policy.policy_id,
        "staking_payout_policy_sha256": policy.policy_sha256,
        "staking_payout_approval_reference": policy.approval_reference,
        "rows": rows,
    }


def write_gzip_atomic(path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    serialized = (
        json.dumps(payload, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    compressed = gzip.compress(serialized, compresslevel=9, mtime=0)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        temporary.write_bytes(compressed)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "path": str(path),
        "row_count": len(payload.get("rows") or []),
        "source_sha256": canonical_sha256(payload),
        "gzip_sha256": __import__("hashlib").sha256(compressed).hexdigest(),
        "b64_registered": False,
    }
