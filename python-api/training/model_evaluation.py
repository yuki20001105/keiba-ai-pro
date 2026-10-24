from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score


def _rate(values: pd.Series) -> float | None:
    if values.empty:
        return None
    return float(values.astype(bool).mean())


def _return_rate(top_picks: pd.DataFrame) -> float | None:
    covered = top_picks.loc[top_picks["payout"].notna() & top_picks["payout"].gt(0)]
    if covered.empty:
        return None
    returns = np.where(covered["finish"].eq(1), covered["payout"], 0.0)
    return float(np.sum(returns) / (len(covered) * 100.0) * 100.0)


def _selection_summary(frame: pd.DataFrame) -> dict[str, float | int | None]:
    if frame.empty:
        return {
            "race_count": 0,
            "top_pick_win_rate": None,
            "favorite_win_rate": None,
            "top_pick_win_rate_delta": None,
            "win_roi": None,
        }

    model_top = (
        frame.sort_values(["race_id", "prediction"], ascending=[True, False], kind="mergesort")
        .groupby("race_id", sort=False)
        .head(1)
    )
    favorite_candidates = frame.loc[frame["popularity"].notna() | frame["odds"].notna()]
    favorites = (
        favorite_candidates.sort_values(
            ["race_id", "popularity", "odds"],
            ascending=[True, True, True],
            na_position="last",
            kind="mergesort",
        )
        .groupby("race_id", sort=False)
        .head(1)
    )
    top_rate = _rate(model_top["finish"].eq(1))
    favorite_rate = _rate(favorites["finish"].eq(1))
    delta = (
        float(top_rate - favorite_rate)
        if top_rate is not None and favorite_rate is not None
        else None
    )
    return {
        "race_count": int(model_top["race_id"].nunique()),
        "top_pick_win_rate": top_rate,
        "favorite_win_rate": favorite_rate,
        "top_pick_win_rate_delta": delta,
        "win_roi": _return_rate(model_top),
    }


def evaluate_speed_deviation_predictions(
    *,
    actual: Sequence[float] | pd.Series,
    predicted: Sequence[float] | pd.Series,
    race_ids: Sequence[object] | pd.Series,
    finish_positions: Sequence[object] | pd.Series,
    race_dates: Sequence[object] | pd.Series,
    popularity: Sequence[object] | pd.Series,
    odds: Sequence[object] | pd.Series,
    payouts: Sequence[object] | pd.Series,
) -> dict[str, Any]:
    """Build user-facing regression and race-selection metrics from one holdout."""

    frame = pd.DataFrame({
        "actual": pd.to_numeric(pd.Series(actual).reset_index(drop=True), errors="coerce"),
        "prediction": pd.to_numeric(pd.Series(predicted).reset_index(drop=True), errors="coerce"),
        "race_id": pd.Series(race_ids).reset_index(drop=True).astype(str),
        "finish": pd.to_numeric(pd.Series(finish_positions).reset_index(drop=True), errors="coerce"),
        "race_date": pd.to_datetime(
            pd.Series(race_dates).reset_index(drop=True).astype(str).str[:8],
            format="%Y%m%d",
            errors="coerce",
        ),
        "popularity": pd.to_numeric(pd.Series(popularity).reset_index(drop=True), errors="coerce"),
        "odds": pd.to_numeric(pd.Series(odds).reset_index(drop=True), errors="coerce"),
        "payout": pd.to_numeric(pd.Series(payouts).reset_index(drop=True), errors="coerce"),
    })
    frame = frame.loc[
        frame["actual"].notna()
        & frame["prediction"].notna()
        & frame["race_id"].ne("")
        & frame["race_id"].ne("nan")
    ].copy()
    if len(frame) < 2:
        return {}

    global_correlation = spearmanr(frame["actual"], frame["prediction"]).statistic
    race_correlations: list[float] = []
    for _race_id, race in frame.groupby("race_id", sort=False):
        if len(race) < 2:
            continue
        actual_values = race["actual"].to_numpy(dtype=float)
        predicted_values = race["prediction"].to_numpy(dtype=float)
        correlation = (
            spearmanr(actual_values, predicted_values).statistic
            if np.ptp(actual_values) > 0 and np.ptp(predicted_values) > 0
            else 0.0
        )
        # A model that assigns every runner the same score has no ranking
        # ability in that race, rather than an undefined score to omit.
        race_correlations.append(
            float(correlation) if np.isfinite(correlation) else 0.0
        )
    primary = _selection_summary(frame)
    primary.update({
        "rank_correlation": (
            float(np.mean(race_correlations)) if race_correlations else None
        ),
        "rmse": float(mean_squared_error(frame["actual"], frame["prediction"]) ** 0.5),
    })

    dated = frame.loc[frame["race_date"].notna()].copy()
    time_slices: list[dict[str, Any]] = []
    if not dated.empty:
        dated["period"] = dated["race_date"].dt.strftime("%Y")
        for period, period_frame in dated.groupby("period", sort=True):
            time_slices.append({"period": str(period), **_selection_summary(period_frame)})

    return {
        "schema": "model-evaluation-v1",
        "target": "speed_deviation",
        "primary": primary,
        "details": {
            "mae": float(mean_absolute_error(frame["actual"], frame["prediction"])),
            "r2": float(r2_score(frame["actual"], frame["prediction"])),
            "global_rank_correlation": (
                float(global_correlation) if np.isfinite(global_correlation) else None
            ),
            "rank_correlation_race_count": int(len(race_correlations)),
            "rank_correlation_coverage": (
                float(len(race_correlations) / frame["race_id"].nunique())
                if frame["race_id"].nunique()
                else 0.0
            ),
            "evaluation_date_from": (
                dated["race_date"].min().date().isoformat() if not dated.empty else None
            ),
            "evaluation_date_to": (
                dated["race_date"].max().date().isoformat() if not dated.empty else None
            ),
            "evaluation_row_count": int(len(frame)),
            "evaluation_race_count": int(frame["race_id"].nunique()),
        },
        "time_slices": time_slices,
    }


def evaluate_ranking_predictions(
    *,
    actual_relevance: Sequence[float] | pd.Series,
    predicted: Sequence[float] | pd.Series,
    race_ids: Sequence[object] | pd.Series,
    race_dates: Sequence[object] | pd.Series,
) -> dict[str, Any]:
    """Evaluate a ranker within each race, then macro-average the races.

    A global correlation is invalid for this target because relevance depends
    on field size.  All primary metrics therefore compare runners only against
    others in the same race.
    """

    frame = pd.DataFrame(
        {
            "actual": pd.to_numeric(
                pd.Series(actual_relevance).reset_index(drop=True),
                errors="coerce",
            ),
            "prediction": pd.to_numeric(
                pd.Series(predicted).reset_index(drop=True),
                errors="coerce",
            ),
            "race_id": pd.Series(race_ids).reset_index(drop=True).astype(str),
            "race_date": pd.to_datetime(
                pd.Series(race_dates).reset_index(drop=True).astype(str).str[:8],
                format="%Y%m%d",
                errors="coerce",
            ),
        }
    )
    frame = frame.loc[
        frame["actual"].notna()
        & frame["prediction"].notna()
        & frame["race_id"].ne("")
        & frame["race_id"].ne("nan")
    ].copy()

    def ndcg(actual: np.ndarray, scores: np.ndarray, k: int) -> float:
        limit = min(int(k), len(actual))
        discounts = np.log2(np.arange(limit, dtype=float) + 2.0)
        predicted_order = np.argsort(-scores, kind="stable")[:limit]
        ideal_order = np.argsort(-actual, kind="stable")[:limit]
        predicted_dcg = np.sum((np.power(2.0, actual[predicted_order]) - 1.0) / discounts)
        ideal_dcg = np.sum((np.power(2.0, actual[ideal_order]) - 1.0) / discounts)
        return float(predicted_dcg / ideal_dcg) if ideal_dcg > 0 else 0.0

    race_metrics: list[dict[str, Any]] = []
    for race_id, race in frame.groupby("race_id", sort=False):
        if len(race) < 2:
            continue
        actual = race["actual"].to_numpy(dtype=float)
        scores = race["prediction"].to_numpy(dtype=float)
        correlation = (
            spearmanr(actual, scores).statistic
            if np.ptp(actual) > 0 and np.ptp(scores) > 0
            else 0.0
        )
        race_metrics.append(
            {
                "race_id": str(race_id),
                "race_date": race["race_date"].dropna().min(),
                "rank_correlation": (
                    float(correlation) if np.isfinite(correlation) else 0.0
                ),
                "ndcg_at_1": ndcg(actual, scores, 1),
                "ndcg_at_3": ndcg(actual, scores, 3),
                "ndcg_at_5": ndcg(actual, scores, 5),
            }
        )
    if not race_metrics:
        return {}

    metrics = pd.DataFrame(race_metrics)
    primary = {
        "rank_correlation": float(metrics["rank_correlation"].mean()),
        "ndcg_at_1": float(metrics["ndcg_at_1"].mean()),
        "ndcg_at_3": float(metrics["ndcg_at_3"].mean()),
        "ndcg_at_5": float(metrics["ndcg_at_5"].mean()),
        "race_count": int(len(metrics)),
    }
    dated = metrics.loc[metrics["race_date"].notna()].copy()
    return {
        "schema": "model-evaluation-v1",
        "target": "rank",
        "primary": primary,
        "details": {
            "evaluation_date_from": (
                dated["race_date"].min().date().isoformat() if not dated.empty else None
            ),
            "evaluation_date_to": (
                dated["race_date"].max().date().isoformat() if not dated.empty else None
            ),
            "evaluation_row_count": int(len(frame)),
            "evaluation_race_count": int(len(metrics)),
        },
    }
