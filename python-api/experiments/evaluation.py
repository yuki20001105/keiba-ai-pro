"""Retrospective, race-complete evaluation for isolated model experiments.

Ranking scores are never interpreted as probabilities. ROI means net profit /
stake; return rate additionally reports gross payout / stake. Neither metric is
reported for the full cohort when a race's winning payout is unverified.
"""
from __future__ import annotations

from typing import Any, Callable

import numpy as np
import pandas as pd


COHORT = "complete_runner_target_cohort"


def race_evaluation_records(frame: pd.DataFrame, *, task: str, progress: Callable[[int], None] | None = None) -> pd.DataFrame:
    """Create one record per race; fail closed on missing runner predictions."""
    required = {
        "race_id", "race_date", "horse_id", "horse_number", "surface",
        "finish", "prediction", "popularity", "tansho_payout",
    }
    if not required.issubset(frame.columns):
        raise ValueError(f"evaluation missing columns: {sorted(required - set(frame.columns))}")
    if task not in {"speed_regression", "ranking"}:
        raise ValueError("unsupported evaluation task")
    if frame.empty:
        raise ValueError("evaluation requires races")
    records = []
    for race_id, race in frame.groupby("race_id", sort=True):
        if progress is not None and len(records) % 250 == 0:
            progress(len(records))
        race = race.copy()
        race["horse_id"] = race["horse_id"].astype("string").fillna("").str.strip()
        identified = race.loc[race["horse_id"].ne(""), "horse_id"]
        if len(race) < 2 or identified.duplicated().any():
            raise ValueError(f"incomplete or duplicate runner cohort: {race_id}")
        if race["race_date"].nunique(dropna=False) != 1 or race["surface"].nunique(dropna=False) != 1:
            raise ValueError(f"inconsistent race metadata: {race_id}")
        for size_column in ("expected_runner_count", "num_horses"):
            if size_column in race:
                expected = pd.to_numeric(race[size_column], errors="coerce")
                if not expected.eq(len(race)).all():
                    raise ValueError(f"runner count mismatch: {race_id}")
        finish = pd.to_numeric(race["finish"], errors="coerce")
        score = pd.to_numeric(race["prediction"], errors="coerce")
        number = pd.to_numeric(race["horse_number"], errors="coerce")
        if not (np.isfinite(finish).all() and finish.gt(0).all()
                and finish.eq(np.floor(finish)).all() and finish.le(len(race)).all()
                and finish.eq(1).any() and np.isfinite(score).all()
                and np.isfinite(number).all() and number.between(1, 18).all()
                and number.eq(np.floor(number)).all() and not number.duplicated().any()):
            raise ValueError(f"unconfirmed results or predictions: {race_id}")
        race["_score"] = score
        race["_number"] = number
        ordered = race.sort_values(["_score", "_number", "horse_id"], ascending=[False, True, True], kind="stable")
        picked = ordered.index[0]
        top_win = float(finish.loc[picked] == 1)
        rank_corr = score.rank(method="average").corr((-finish).rank(method="average")) if score.nunique() > 1 and finish.nunique() > 1 else np.nan
        relevance = (len(race) - finish + 1).clip(lower=0)
        cutoff = min(3, len(race))
        discount = np.log2(np.arange(2, cutoff + 2))
        ideal_dcg = float(np.sum(np.sort(relevance.to_numpy())[::-1][:cutoff] / discount))
        ndcg = float(np.sum(relevance.loc[ordered.index].to_numpy()[:cutoff] / discount) / ideal_dcg) if ideal_dcg > 0 else np.nan

        popularity = pd.to_numeric(race["popularity"], errors="coerce")
        popularity_valid = bool(np.isfinite(popularity).all() and popularity.gt(0).all() and popularity.eq(1).any())
        popularity_win = np.nan
        if popularity_valid:
            popular = race.assign(_popularity=popularity).sort_values(["_popularity", "_number", "horse_id"], kind="stable").index[0]
            popularity_win = float(finish.loc[popular] == 1)

        payout = pd.to_numeric(race["tansho_payout"], errors="coerce")
        winning_payout = payout.loc[finish.eq(1)]
        payout_verified = bool(np.isfinite(winning_payout).all() and winning_payout.gt(0).all())
        picked_return = float(payout.loc[picked]) if payout_verified and top_win else (0.0 if payout_verified else np.nan)
        record = {
            "race_id": str(race_id), "race_date": str(race["race_date"].iloc[0]),
            "year": str(race["race_date"].iloc[0])[:4], "surface": str(race["surface"].iloc[0]),
            "runner_count": len(race), "rank_correlation": rank_corr, "ndcg_at_3": ndcg,
            "top1_win": top_win, "popularity_top1_win": popularity_win,
            "paired_top1_win": top_win if popularity_valid else np.nan,
            "popularity_difference": top_win - popularity_win,
            "payout_verified": int(payout_verified), "win_return_yen": picked_return,
            "picked_horse_id": str(race.loc[picked, "horse_id"]),
            "picked_horse_number": int(number.loc[picked]),
            "squared_error": 0.0, "absolute_error": 0.0, "actual_sum": 0.0,
            "actual_squared_sum": 0.0, "error_count": 0,
            "speed_mps_squared_error": 0.0, "speed_mps_absolute_error": 0.0,
            "speed_mps_error_count": 0,
        }
        if task == "speed_regression":
            if "actual" not in race:
                raise ValueError("regression requires actual target")
            actual = pd.to_numeric(race["actual"], errors="coerce")
            if not np.isfinite(actual).all():
                raise ValueError("nonfinite regression target")
            error = score - actual
            record.update(squared_error=float((error ** 2).sum()), absolute_error=float(error.abs().sum()),
                          actual_sum=float(actual.sum()), actual_squared_sum=float((actual ** 2).sum()), error_count=len(race))
            if {"actual_speed_mps", "prediction_speed_mps"}.issubset(race.columns):
                actual_speed = pd.to_numeric(race["actual_speed_mps"], errors="coerce")
                predicted_speed = pd.to_numeric(race["prediction_speed_mps"], errors="coerce")
                if not (np.isfinite(actual_speed).all() and np.isfinite(predicted_speed).all()):
                    raise ValueError("nonfinite raw-speed evaluation")
                speed_error = predicted_speed - actual_speed
                record.update(speed_mps_squared_error=float((speed_error ** 2).sum()),
                              speed_mps_absolute_error=float(speed_error.abs().sum()), speed_mps_error_count=len(race))
        records.append(record)
    return pd.DataFrame.from_records(records)


def _aggregate(records: pd.DataFrame, weights: np.ndarray | None = None) -> dict[str, Any]:
    weight = np.ones(len(records), dtype=float) if weights is None else np.asarray(weights, dtype=float)
    count = float(weight.sum())

    def total(column: str) -> float:
        return float(np.dot(records[column].fillna(0).to_numpy(dtype=float), weight))

    def mean(column: str) -> float | None:
        valid = records[column].notna().to_numpy()
        denominator = float(weight[valid].sum())
        return total(column) / denominator if denominator else None

    errors = total("error_count")
    actual_variation = total("actual_squared_sum") - total("actual_sum") ** 2 / errors if errors else 0
    verified = total("payout_verified")
    payout_complete = bool(count and verified == count)
    checked_return = total("win_return_yen")
    raw_count = total("speed_mps_error_count")
    difference = mean("popularity_difference")
    return {
        "race_count": int(count), "runner_count": int(total("runner_count")),
        "rank_correlation": mean("rank_correlation"),
        "rank_correlation_races": int(np.dot(records["rank_correlation"].notna(), weight)),
        "constant_score_or_result_races": int(np.dot(records["rank_correlation"].isna(), weight)),
        "rmse": float(np.sqrt(total("squared_error") / errors)) if errors else None,
        "mae": total("absolute_error") / errors if errors else None,
        "r2": 1 - total("squared_error") / actual_variation if actual_variation > 0 else None,
        "rmse_speed_mps": float(np.sqrt(total("speed_mps_squared_error") / raw_count)) if raw_count else None,
        "mae_speed_mps": total("speed_mps_absolute_error") / raw_count if raw_count else None,
        "ndcg_at_3": mean("ndcg_at_3"), "top1_win_rate": mean("top1_win"),
        "top1_win_rate_on_popularity_races": mean("paired_top1_win"),
        "popularity_top1_win_rate": mean("popularity_top1_win"),
        "popularity_comparison_races": int(np.dot(records["popularity_top1_win"].notna(), weight)),
        "top1_vs_popularity_pp": difference * 100 if difference is not None else None,
        "win_roi_percent": (checked_return / (100 * count) - 1) * 100 if payout_complete else None,
        "win_return_rate_percent": checked_return / count if payout_complete else None,
        "win_stake_yen": int(count * 100),
        "win_payout_yen": checked_return if payout_complete else None,
        "payout_verified_races": int(verified), "payout_missing_races": int(count - verified),
        "verified_subset_win_roi_percent": (checked_return / (100 * verified) - 1) * 100 if verified else None,
        "verified_subset_win_return_rate_percent": checked_return / verified if verified else None,
    }


def evaluate_predictions(frame: pd.DataFrame, *, task: str, seed: int = 42, bootstrap_samples: int = 300) -> dict[str, Any]:
    """Report the five headline metrics plus date-clustered uncertainty."""
    records = race_evaluation_records(frame.reset_index(drop=True), task=task)
    return evaluate_race_records(records, seed=seed, bootstrap_samples=bootstrap_samples)


def evaluate_race_records(records: pd.DataFrame, *, seed: int = 42, bootstrap_samples: int = 300,
                          progress: Callable[[int], None] | None = None) -> dict[str, Any]:
    """Aggregate already-validated race records without redoing runner work."""
    if not 0 <= int(bootstrap_samples) <= 2000:
        raise ValueError("bootstrap_samples must be between 0 and 2000")
    if records.empty or records["race_id"].duplicated().any():
        raise ValueError("unique validated race records are required")
    overall = _aggregate(records)
    metrics = ("rank_correlation", "rmse", "top1_win_rate", "top1_vs_popularity_pp", "win_roi_percent", "win_return_rate_percent")
    samples: dict[str, list[float]] = {key: [] for key in metrics}
    days, inverse = np.unique(records["race_date"].to_numpy(), return_inverse=True)
    rng = np.random.default_rng(seed)
    if len(days) >= 2:
        for replicate in range(int(bootstrap_samples)):
            if progress is not None and replicate % 25 == 0:
                progress(replicate)
            day_weights = np.bincount(rng.integers(0, len(days), size=len(days)), minlength=len(days))
            values = _aggregate(records, day_weights[inverse])
            for key in metrics:
                # An unverified full-cohort ROI cannot acquire a CI by chance
                # from a bootstrap draw that omits its unverified dates.
                if overall[key] is not None and values[key] is not None:
                    samples[key].append(float(values[key]))
    confidence = {}
    for key in metrics:
        values = samples[key]
        confidence[key] = {
            "lower": float(np.quantile(values, 0.025)) if values else None,
            "upper": float(np.quantile(values, 0.975)) if values else None,
            "valid_replicates": len(values),
        }
    return {
        "cohort": COHORT, "real_world_all_races": False,
        "overall": overall,
        "by_year": {str(key): _aggregate(group) for key, group in records.groupby("year", sort=True)},
        "by_surface": {str(key): _aggregate(group) for key, group in records.groupby("surface", sort=True)},
        "by_year_surface": {f"{year}:{surface}": _aggregate(group) for (year, surface), group in records.groupby(["year", "surface"], sort=True)},
        "day_bootstrap_ci": {"unit": "race_date", "level": 0.95, "seed": seed,
                             "requested_replicates": int(bootstrap_samples), "date_count": len(days), "metrics": confidence},
        "definitions": {
            "rank_correlation": "macro mean of per-race Spearman(prediction, -finish); constant vectors excluded and counted",
            "rmse": "horse-weighted error in fold-train-fitted speed deviation units; null for ranking",
            "ndcg_at_3": "macro per-race NDCG with linear relevance=runner_count-finish+1",
            "top1_tie_break": "descending score, ascending horse_number, ascending horse_id",
            "popularity": "retrospective recorded popularity, not proven pre-cutoff market information; paired race set only",
            "win_roi_percent": "net return=(verified payout-100 yen stake)/stake*100; every race must have all winner payouts verified",
            "win_return_rate_percent": "gross verified payout/stake*100",
            "bet_rule": "one 100-yen win bet on prediction top1 in every cohort race; no threshold tuning",
            "score_is_probability": False,
        },
        "bet_types": {"win": "retrospective_complete_cohort", "place": "not_evaluated", "other": "not_evaluated"},
    }
