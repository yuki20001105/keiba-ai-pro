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

    correlation = spearmanr(frame["actual"], frame["prediction"]).statistic
    primary = _selection_summary(frame)
    primary.update({
        "rank_correlation": float(correlation) if np.isfinite(correlation) else None,
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
