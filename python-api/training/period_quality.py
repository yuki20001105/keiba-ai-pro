"""Read-only missingness audit; empty selected histories are not source proofs."""
from __future__ import annotations

import numpy as np
import pandas as pd

from experiments.goal_diagnostics import _history_requirement
from training.structural_imputation import FALLBACKS


def audit_period_features(
    frame: pd.DataFrame, features: list[str], categorical: list[str], *,
    start_year: int, end_date: str,
) -> dict:
    dates = pd.to_datetime(frame.race_date.astype(str), format="%Y%m%d", errors="raise")
    selected = frame.loc[dates.between(
        pd.Timestamp(f"{start_year}-01-01"), pd.Timestamp(end_date),
    )].copy()
    years = selected.race_date.astype(str).str[:4].astype(int)
    reports = {}
    for year in range(start_year, int(end_date[:4]) + 1):
        part = selected.loc[years.eq(year)]
        details = {}
        for column in features:
            values = (
                part[column].astype("string").str.strip().replace("", pd.NA)
                if column in categorical else
                pd.to_numeric(part[column], errors="coerce").replace([np.inf, -np.inf], np.nan)
            )
            missing = values.isna()
            empty_history = pd.Series(False, index=part.index)
            requirement = _history_requirement(column)
            if requirement is not None:
                count, minimum, _ = requirement
                counts = pd.to_numeric(part[count], errors="coerce")
                empty_history = counts.ge(0) & counts.lt(minimum) & counts.mod(1).eq(0)
            can_backoff = pd.Series(False, index=part.index)
            if column in FALLBACKS:
                source = pd.to_numeric(part[FALLBACKS[column]], errors="coerce")
                starts = pd.to_numeric(part.safe_horse_surface_starts, errors="coerce")
                valid_value = source.between(0, 1) if column.endswith("_rate") else source.gt(0)
                can_backoff = (empty_history & starts.gt(0) & np.isfinite(starts)
                               & starts.mod(1).eq(0) & np.isfinite(source) & valid_value)
            details[column] = {
                "missing": int(missing.sum()),
                "selected_history_insufficient": int((missing & empty_history).sum()),
                "other_or_unknown": int((missing & ~empty_history).sum()),
                "backoff_candidate_not_source_authorized": int((missing & can_backoff).sum()),
            }
        reports[str(year)] = {
            "rows": len(part), "races": int(part.race_id.nunique()),
            "min_date": None if part.empty else str(part.race_date.min()),
            "max_date": None if part.empty else str(part.race_date.max()),
            "missing_cells": sum(item["missing"] for item in details.values()),
            "selected_history_insufficient_cells": sum(
                item["selected_history_insufficient"] for item in details.values()),
            "other_or_unknown_cells": sum(item["other_or_unknown"] for item in details.values()),
            "backoff_candidate_cells_not_authorized": sum(
                item["backoff_candidate_not_source_authorized"] for item in details.values()),
            "features": details,
        }
    return {
        "years": reports, "feature_count": len(features),
        "rows": len(selected), "races": int(selected.race_id.nunique()),
        "missing_cells": sum(item["missing_cells"] for item in reports.values()),
        "excluded_outside_period_rows": len(frame) - len(selected),
        "empty_selected_history_is_not_proof_of_no_public_history": True,
        "imputation_applied": False,
    }
