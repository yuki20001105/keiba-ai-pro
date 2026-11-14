"""Explicit research-only backoff for source-audited first-condition gaps."""
from __future__ import annotations

import math
from typing import Hashable

import pandas as pd

POLICY_ID = "audited-condition-to-horse-surface-v1"
FIELDS = ("win_rate", "top3_rate", "avg_speed_mps")
GROUPS = ("horse_course", "horse_distance_band")
FALLBACKS = {
    f"safe_{group}_{field}": f"safe_horse_surface_{field}"
    for group in GROUPS for field in FIELDS
}
FLAGS = [f"{column}_was_imputed" for column in FALLBACKS]


def impute_condition_history(
    frame: pd.DataFrame, confirmed_missing: set[tuple[Hashable, str]],
) -> tuple[pd.DataFrame, list[dict]]:
    """Use only previously generated past features; require cell-level authorization.

    The caller must establish that authorized gaps represent no prior condition
    experience, rather than a failed acquisition or a filtered-out history.
    No fitting or population statistics are used, so training and inference share
    the same transform. Debutants without surface history are explicitly refused.
    """
    required = set(FALLBACKS) | set(FALLBACKS.values()) | {
        "safe_horse_surface_starts", *(f"safe_{group}_starts" for group in GROUPS),
    }
    if not frame.index.is_unique or not frame.columns.is_unique:
        raise ValueError("imputation-requires-unique-rows-and-columns")
    if not required.issubset(frame.columns) or set(FLAGS) & set(frame.columns):
        raise ValueError("imputation-schema-invalid")
    missing = {
        (index, column) for column in FALLBACKS
        for index in frame.index[frame[column].isna()]
    }
    if missing != confirmed_missing:
        raise ValueError("imputation-missing-cells-not-exactly-authorized")
    result = frame.copy(deep=True)
    for flag in FLAGS:
        result[flag] = 0
    changes = []
    for column, source in FALLBACKS.items():
        group = next(group for group in GROUPS if column.startswith(f"safe_{group}_"))
        count_column = f"safe_{group}_starts"
        for index in frame.index[frame[column].isna()]:
            if pd.isna(frame.at[index, count_column]) or frame.at[index, count_column] != 0:
                raise ValueError(f"imputation-not-zero-history: {index}, {column}")
            count = frame.at[index, "safe_horse_surface_starts"]
            value = frame.at[index, source]
            if pd.isna(count) or not math.isfinite(float(count)) or count <= 0 or count != int(count):
                raise ValueError(f"imputation-no-observed-fallback-history: {index}, {column}")
            if pd.isna(value) or not math.isfinite(float(value)):
                raise ValueError(f"imputation-fallback-not-finite: {index}, {source}")
            if (column.endswith("_rate") and not 0 <= value <= 1) or (
                column.endswith("_avg_speed_mps") and value <= 0
            ):
                raise ValueError(f"imputation-fallback-out-of-range: {index}, {source}")
            result.at[index, column] = value
            result.at[index, f"{column}_was_imputed"] = 1
            changes.append({
                "row_index": index, "column": column, "value": float(value),
                "source_column": source, "source_starts": int(count),
                "condition_starts": 0, "reason": "audited_no_prior_same_condition",
                "is_observed_condition_statistic": False,
            })
    return result, changes
