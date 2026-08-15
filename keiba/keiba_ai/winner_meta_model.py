"""Leakage-resistant winner probability meta-model for speed scores."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Sequence

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .point_in_time_odds import normalized_market_probability

META_FEATURE_COLUMNS = (
    "base_score",
    "base_probability",
    "score_rank_fraction",
    "score_gap_to_top",
    "market_probability",
    "market_probability_gap",
    "log_odds",
    "field_size",
)


def _softmax_by_race(frame: pd.DataFrame, column: str) -> pd.Series:
    def softmax(values: pd.Series) -> pd.Series:
        array = values.to_numpy(dtype=float)
        array = array - np.max(array)
        exponent = np.exp(np.clip(array, -50.0, 50.0))
        return pd.Series(exponent / exponent.sum(), index=values.index)

    return frame.groupby("race_id", sort=False, group_keys=False)[column].apply(softmax)


def build_winner_meta_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Build only race-relative and decision-time market features."""

    required = {"race_id", "base_score", "odds"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError("winner meta input is missing: " + ", ".join(sorted(missing)))
    result = frame.copy()
    result["base_score"] = pd.to_numeric(result["base_score"], errors="coerce")
    if result["base_score"].isna().any() or not np.isfinite(result["base_score"]).all():
        raise ValueError("base_score must be finite")
    result["base_probability"] = _softmax_by_race(result, "base_score")
    result["market_probability"] = normalized_market_probability(result)
    result["field_size"] = result.groupby("race_id")["race_id"].transform("size")
    result["score_rank_fraction"] = (
        result.groupby("race_id")["base_score"].rank(method="average", ascending=False)
        / result["field_size"]
    )
    result["score_gap_to_top"] = result["base_score"] - result.groupby("race_id")[
        "base_score"
    ].transform("max")
    result["market_probability_gap"] = (
        result["base_probability"] - result["market_probability"]
    )
    result["log_odds"] = np.log(pd.to_numeric(result["odds"], errors="coerce"))
    if result[list(META_FEATURE_COLUMNS)].isna().any().any():
        raise ValueError("winner meta features contain missing values")
    return result


def expanding_year_splits(
    dates: pd.Series,
    *,
    minimum_training_years: int = 2,
) -> Iterator[tuple[np.ndarray, np.ndarray, int]]:
    """Yield strictly past-to-future annual OOF splits."""

    parsed = pd.to_datetime(dates, errors="coerce")
    if parsed.isna().any():
        raise ValueError("dates contain invalid values")
    years = parsed.dt.year.to_numpy(dtype=int)
    unique_years = sorted(set(years.tolist()))
    for position in range(minimum_training_years, len(unique_years)):
        validation_year = unique_years[position]
        training_years = set(unique_years[:position])
        training = np.flatnonzero(np.isin(years, list(training_years)))
        validation = np.flatnonzero(years == validation_year)
        if len(training) and len(validation):
            yield training, validation, validation_year


@dataclass
class WinnerProbabilityMetaModel:
    """Logistic meta-model fitted exclusively on out-of-fold base scores."""

    random_state: int = 42

    def __post_init__(self) -> None:
        self.pipeline = Pipeline(
            [
                ("scale", StandardScaler()),
                (
                    "logistic",
                    LogisticRegression(
                        C=1.0,
                        max_iter=2_000,
                        random_state=self.random_state,
                    ),
                ),
            ]
        )
        self.fitted_through: pd.Timestamp | None = None

    def fit(self, oof_frame: pd.DataFrame) -> "WinnerProbabilityMetaModel":
        required = {"winner", "race_date", "base_prediction_is_oof"}
        missing = required - set(oof_frame.columns)
        if missing:
            raise ValueError(
                "OOF meta training input is missing: " + ", ".join(sorted(missing))
            )
        if not oof_frame["base_prediction_is_oof"].eq(True).all():  # noqa: E712
            raise ValueError("meta-model training requires OOF base predictions only")
        dates = pd.to_datetime(oof_frame["race_date"], errors="coerce")
        if dates.isna().any():
            raise ValueError("race_date contains invalid values")
        winner = pd.to_numeric(oof_frame["winner"], errors="coerce")
        if not winner.isin([0, 1]).all():
            raise ValueError("winner must be binary")
        winner_counts = winner.groupby(oof_frame["race_id"]).sum()
        if not winner_counts.eq(1).all():
            raise ValueError("each OOF race must contain exactly one winner")
        features = build_winner_meta_features(oof_frame)
        self.pipeline.fit(features[list(META_FEATURE_COLUMNS)], winner.astype(int))
        self.fitted_through = pd.Timestamp(dates.max())
        return self

    def predict(self, frame: pd.DataFrame) -> pd.Series:
        if self.fitted_through is None:
            raise ValueError("winner probability meta-model is not fitted")
        if "race_date" in frame.columns:
            dates = pd.to_datetime(frame["race_date"], errors="coerce")
            if dates.isna().any():
                raise ValueError("race_date contains invalid values")
            if pd.Timestamp(dates.min()) <= self.fitted_through:
                raise ValueError(
                    "meta-model inference must be strictly after its OOF fit period"
                )
        features = build_winner_meta_features(frame)
        raw = self.pipeline.predict_proba(features[list(META_FEATURE_COLUMNS)])[:, 1]
        raw_series = pd.Series(np.clip(raw, 1e-9, 1.0), index=features.index)
        totals = raw_series.groupby(features["race_id"]).transform("sum")
        return raw_series / totals


def concatenate_oof_predictions(folds: Sequence[pd.DataFrame]) -> pd.DataFrame:
    """Combine non-overlapping fold outputs and stamp their OOF invariant."""

    if not folds:
        raise ValueError("at least one OOF fold is required")
    combined = pd.concat(folds, ignore_index=True)
    required = {"race_id", "horse_id", "race_date", "base_score", "winner", "odds"}
    missing = required - set(combined.columns)
    if missing:
        raise ValueError("OOF folds are missing: " + ", ".join(sorted(missing)))
    if combined[["race_id", "horse_id"]].duplicated().any():
        raise ValueError("OOF folds overlap on race_id/horse_id")
    combined["base_prediction_is_oof"] = True
    return combined
