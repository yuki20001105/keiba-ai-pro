"""Separate research contract: individual history, then observed-history backoff.

An empty history in the frozen store does not assert an empty public career.
This policy is not the source-audited single-race imputation acceptance contract.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from experiments.features import FEATURE_GROUPS, engineer_features
from training.structural_imputation import FALLBACKS, FLAGS

POLICY_ID = "research-individual-history-surface-backoff-v1"
IDENTITY = ["race_id", "horse_id", "horse_number", "race_date"]


def rebuild_same_cohort(
    baseline: pd.DataFrame, history: pd.DataFrame, features: list[str],
) -> tuple[pd.DataFrame, dict]:
    if baseline.duplicated(IDENTITY).any() or history.duplicated(IDENTITY).any():
        raise ValueError("period-application-duplicate-identity")
    wanted = pd.MultiIndex.from_frame(baseline[IDENTITY])
    available = pd.MultiIndex.from_frame(history[IDENTITY])
    if not wanted.isin(available).all():
        raise ValueError("period-application-target-not-in-history")
    engineered, manifest = engineer_features(history)
    if features != manifest["feature_sets"]["M03"]:
        raise ValueError("period-application-feature-contract-mismatch")
    aligned = engineered.set_index(IDENTITY).reindex(wanted)[features].reset_index(drop=True)
    original = baseline.reset_index(drop=True)
    pd.testing.assert_frame_equal(
        original[FEATURE_GROUPS["base"]], aligned[FEATURE_GROUPS["base"]],
        check_dtype=False,
    )
    result = original.copy(deep=True)
    result[features] = aligned
    pd.testing.assert_frame_equal(original.drop(columns=features), result.drop(columns=features))
    return result, manifest["point_in_time_audit"]


def backoff_observed_history(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Estimate only zero-observed-condition gaps from nonempty same-horse history.

    Unlike audited-condition-to-horse-surface-v1, this explicitly permits unknown
    public-career coverage for offline sensitivity analysis. It never labels
    those gaps as a proven first condition, nor repairs unknown identifiers.
    """
    if set(FLAGS) & set(frame.columns) or not frame.index.is_unique:
        raise ValueError("period-backoff-schema-invalid")
    result = frame.copy(deep=True)
    changes = []
    starts = pd.to_numeric(frame.safe_horse_surface_starts, errors="raise")
    for column, source in FALLBACKS.items():
        group = "horse_course" if "_course_" in column else "horse_distance_band"
        count = pd.to_numeric(frame[f"safe_{group}_starts"], errors="raise")
        values = pd.to_numeric(frame[source], errors="raise")
        valid = values.between(0, 1) if column.endswith("_rate") else values.gt(0)
        mask = (frame[column].isna() & count.eq(0) & starts.gt(0) & starts.mod(1).eq(0)
                & np.isfinite(starts) & np.isfinite(values) & valid)
        result[f"{column}_was_imputed"] = mask.astype("int8")
        result.loc[mask, column] = frame.loc[mask, source]
        if mask.any():
            records = frame.loc[mask, IDENTITY].copy()
            records["row_index"] = frame.index[mask]
            records["column"] = column
            records["source_column"] = source
            records["source_starts"] = starts.loc[mask]
            records["value"] = values.loc[mask]
            records["reason"] = "no_prior_condition_in_frozen_eligible_store"
            records["public_career_completeness_verified"] = False
            changes.append(records)
    journal_columns = [*IDENTITY, "row_index", "column", "source_column", "source_starts",
                       "value", "reason", "public_career_completeness_verified"]
    journal = pd.concat(changes, ignore_index=True) if changes else pd.DataFrame(columns=journal_columns)
    return result, journal


def missing_by_year(frame: pd.DataFrame, features: list[str]) -> dict:
    years = frame.race_date.astype(str).str[:4]
    return {
        str(year): {
            "rows": int(years.eq(str(year)).sum()),
            "races": int(frame.loc[years.eq(str(year)), "race_id"].nunique()),
            "missing_cells": int(frame.loc[years.eq(str(year)), features].isna().sum().sum()),
            "features": {column: int(frame.loc[years.eq(str(year)), column].isna().sum())
                         for column in features},
        }
        for year in range(2016, 2027)
    }
