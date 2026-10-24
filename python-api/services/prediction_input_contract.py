"""Describe the served feature input without changing a saved model or values.

Column alignment and a source hash do not prove semantic training/inference
equivalence. Keep that distinction explicit; missing legacy inputs may be shown
as a reference ranking but must not silently acquire betting eligibility.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import inspect
import json

import pandas as pd

from keiba_ai.constants import FUTURE_FIELDS, ID_COLUMNS


@lru_cache(maxsize=1)
def current_feature_hash() -> str:
    # Same definition as the existing training bundle, not a new hashing rule.
    import keiba_ai.feature_engineering as module
    return hashlib.md5(inspect.getsource(module).encode()).hexdigest()[:12]


def describe_input(
    bundle: dict, frame: pd.DataFrame, *, missing_columns: list[str],
    restored_columns: list[str], history_available: bool | None = None,
) -> dict:
    expected = list(bundle.get("feature_columns") or [])
    actual = list(frame.columns)
    forbidden = FUTURE_FIELDS | ID_COLUMNS | {"win", "place"}
    blocked = [name for name in expected if name in forbidden]
    all_missing = [name for name in actual if frame[name].isna().all()]
    training_hash = (bundle.get("pipeline_config") or {}).get("feature_engineering_hash")
    try:
        runtime_hash = current_feature_hash()
    except (OSError, TypeError):
        runtime_hash = None
    hash_matches = (training_hash == runtime_hash) if training_hash and runtime_hash else None
    codes = []
    if not expected:
        codes.append("feature_contract_missing")
    if expected and actual != expected:
        codes.append("column_order_mismatch")
    if missing_columns:
        codes.append("uncomputed_columns")
    if blocked:
        codes.append("post_race_or_identifier_columns")
    if hash_matches is False:
        codes.append("feature_source_changed")
    elif hash_matches is None:
        codes.append("feature_source_unverified")
    if history_available is False:
        codes.append("history_load_failed")
    # Value-level missingness (e.g. debut horses) is not itself a schema error.
    # Existing input-missingness selection rules remain independent.
    status = "degraded" if any(code in codes for code in (
        "column_order_mismatch", "uncomputed_columns",
        "post_race_or_identifier_columns", "history_load_failed",
    )) else "unverified" if codes else "aligned"
    return {
        "schema": "prediction-input-contract-v1", "status": status,
        "scope": "column_alignment_and_source_hash_only",
        "semantic_parity_verified": False,
        "expected_count": len(expected), "actual_count": len(actual),
        "missing_columns": list(missing_columns), "restored_columns": list(restored_columns),
        "blocked_columns": blocked, "all_missing_columns": all_missing,
        "missing_value_rate": float(frame.isna().to_numpy().mean()) if frame.size else 1.0,
        "training_feature_hash": training_hash, "runtime_feature_hash": runtime_hash,
        "source_hash_matches": hash_matches, "history_available": history_available,
        "issue_codes": codes,
        "feature_columns_sha256": hashlib.sha256(json.dumps(actual, separators=(",", ":")).encode()).hexdigest(),
    }
