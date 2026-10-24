"""Vectorized primary-only screening metrics; not betting-rule evaluation."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .evaluation import COHORT


def primary_race_records(frame: pd.DataFrame, *, task: str) -> pd.DataFrame:
    """Strict complete-race Spearman and error sufficient statistics.

    Produces the same primary aggregation as evaluation.py without per-race
    DataFrame copies, top-pick sorting, ROI, or bootstrap work.
    """
    required = {"race_id", "race_date", "surface", "horse_number", "finish", "prediction", "expected_runner_count"}
    if task not in {"speed_regression", "ranking"} or not required.issubset(frame) or frame.empty:
        raise ValueError("invalid primary evaluation input")
    if frame["race_id"].isna().any() or frame.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("missing race or duplicate runner")
    work = frame.reset_index(drop=True)
    ids = work["race_id"]
    grouped = work.groupby("race_id", sort=False)
    counts = grouped["race_id"].transform("size")
    if counts.lt(2).any() or not pd.to_numeric(work["expected_runner_count"], errors="coerce").eq(counts).all():
        raise ValueError("incomplete screening cohort")
    if grouped[["race_date", "surface"]].nunique(dropna=False).ne(1).any().any():
        raise ValueError("inconsistent screening race metadata")
    number = pd.to_numeric(work["horse_number"], errors="coerce")
    finish = pd.to_numeric(work["finish"], errors="coerce")
    score = pd.to_numeric(work["prediction"], errors="coerce")
    if not (np.isfinite(number).all() and number.between(1, 18).all()
            and number.eq(np.floor(number)).all() and np.isfinite(finish).all()
            and finish.between(1, counts).all() and finish.eq(np.floor(finish)).all()
            and np.isfinite(score).all() and finish.eq(1).groupby(ids).any().all()):
        raise ValueError("unconfirmed target, runner number, or prediction")
    if pd.DataFrame({"race": ids, "number": number}).duplicated().any():
        raise ValueError("duplicate numeric runner number")
    score_rank = score.groupby(ids, sort=False).rank(method="average")
    finish_rank = -finish.groupby(ids, sort=False).rank(method="average")
    score_centered = score_rank - score_rank.groupby(ids, sort=False).transform("mean")
    finish_centered = finish_rank - finish_rank.groupby(ids, sort=False).transform("mean")
    covariance = (score_centered * finish_centered).groupby(ids, sort=False).sum()
    scale = np.sqrt((score_centered ** 2).groupby(ids, sort=False).sum()
                    * (finish_centered ** 2).groupby(ids, sort=False).sum())
    records = grouped[["race_date", "surface"]].first()
    records["year"] = records["race_date"].astype(str).str[:4]
    records["runner_count"] = grouped.size()
    records["rank_correlation"] = covariance / scale.where(scale.gt(0))
    for column in ("squared_error", "absolute_error", "actual_sum", "actual_squared_sum", "error_count",
                   "speed_mps_squared_error", "speed_mps_absolute_error", "speed_mps_error_count"):
        records[column] = 0.0
    if task == "speed_regression":
        if "actual" not in work:
            raise ValueError("regression actual target required")
        actual = pd.to_numeric(work["actual"], errors="coerce")
        if not np.isfinite(actual).all():
            raise ValueError("nonfinite regression target")
        error = score - actual
        for column, values in (("squared_error", error ** 2), ("absolute_error", error.abs()),
                               ("actual_sum", actual), ("actual_squared_sum", actual ** 2)):
            records[column] = values.groupby(ids, sort=False).sum()
        records["error_count"] = grouped.size()
        if {"actual_speed_mps", "prediction_speed_mps"}.issubset(work):
            actual_speed = pd.to_numeric(work["actual_speed_mps"], errors="coerce")
            predicted_speed = pd.to_numeric(work["prediction_speed_mps"], errors="coerce")
            if not (np.isfinite(actual_speed).all() and np.isfinite(predicted_speed).all()):
                raise ValueError("nonfinite raw speed")
            delta = predicted_speed - actual_speed
            records["speed_mps_squared_error"] = (delta ** 2).groupby(ids, sort=False).sum()
            records["speed_mps_absolute_error"] = delta.abs().groupby(ids, sort=False).sum()
            records["speed_mps_error_count"] = grouped.size()
    return records.reset_index()


def _primary_aggregate(records: pd.DataFrame) -> dict:
    count = float(records["error_count"].sum())
    squared = float(records["squared_error"].sum())
    variation = float(records["actual_squared_sum"].sum() - records["actual_sum"].sum() ** 2 / count) if count else 0
    speed_count = float(records["speed_mps_error_count"].sum())
    correlation = records["rank_correlation"].mean()
    return {
        "race_count": len(records), "runner_count": int(records["runner_count"].sum()),
        "rank_correlation": float(correlation) if pd.notna(correlation) else None,
        "rank_correlation_races": int(records["rank_correlation"].notna().sum()),
        "constant_score_or_result_races": int(records["rank_correlation"].isna().sum()),
        "rmse": float(np.sqrt(squared / count)) if count else None,
        "mae": float(records["absolute_error"].sum() / count) if count else None,
        "r2": 1 - squared / variation if variation > 0 else None,
        "rmse_speed_mps": float(np.sqrt(records["speed_mps_squared_error"].sum() / speed_count)) if speed_count else None,
        "mae_speed_mps": float(records["speed_mps_absolute_error"].sum() / speed_count) if speed_count else None,
        "top1_win_rate": None, "top1_vs_popularity_pp": None, "win_roi_percent": None,
        "win_return_rate_percent": None, "ndcg_at_3": None,
    }


def evaluate_primary_records(records: pd.DataFrame) -> dict:
    if records.empty or records["race_id"].duplicated().any():
        raise ValueError("unique screening race records required")
    return {
        "cohort": COHORT, "real_world_all_races": False, "evaluation_mode": "screening_primary_only",
        "overall": _primary_aggregate(records),
        "by_year": {str(key): _primary_aggregate(group) for key, group in records.groupby("year", sort=True)},
        "by_surface": {str(key): _primary_aggregate(group) for key, group in records.groupby("surface", sort=True)},
        "by_year_surface": {f"{year}:{surface}": _primary_aggregate(group) for (year, surface), group in records.groupby(["year", "surface"], sort=True)},
        "day_bootstrap_ci": {"unit": "race_date", "requested_replicates": 0, "evaluated": False},
        "definitions": {"rank_correlation": "macro per-race Spearman(prediction, -finish); constant vectors excluded and counted",
                        "rmse": "horse-weighted error in fold-train-fitted speed deviation units; null for ranking",
                        "score_is_probability": False, "win_roi_evaluated": False},
        "bet_types": {"win": "not_evaluated", "place": "not_evaluated", "other": "not_evaluated"},
    }
