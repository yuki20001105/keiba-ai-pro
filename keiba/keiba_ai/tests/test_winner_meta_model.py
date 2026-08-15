from __future__ import annotations

import pandas as pd
import pytest

from keiba_ai.winner_meta_model import (
    WinnerProbabilityMetaModel,
    expanding_year_splits,
)


def _frame(years: list[int], *, oof: bool = True) -> pd.DataFrame:
    rows = []
    for index, year in enumerate(years):
        race_id = f"{year}01010101"
        for horse_number in range(1, 6):
            rows.append(
                {
                    "race_id": race_id,
                    "horse_id": f"H{horse_number}",
                    "race_date": f"{year}-01-01",
                    "base_score": float(6 - horse_number) + index * 0.01,
                    "odds": float(horse_number + 1),
                    "winner": int(horse_number == 1),
                    "base_prediction_is_oof": oof,
                }
            )
    return pd.DataFrame(rows)


def test_meta_model_requires_oof_predictions_and_predicts_later_races() -> None:
    model = WinnerProbabilityMetaModel().fit(_frame([2019, 2020, 2021]))
    future = _frame([2022]).drop(columns=["base_prediction_is_oof"])
    probabilities = model.predict(future)
    assert probabilities.groupby(future["race_id"]).sum().iloc[0] == pytest.approx(1.0)
    assert probabilities.iloc[0] > probabilities.iloc[-1]


def test_meta_model_rejects_in_sample_base_predictions() -> None:
    with pytest.raises(ValueError, match="OOF"):
        WinnerProbabilityMetaModel().fit(_frame([2019, 2020], oof=False))


def test_meta_model_rejects_prediction_inside_fit_period() -> None:
    model = WinnerProbabilityMetaModel().fit(_frame([2019, 2020, 2021]))
    with pytest.raises(ValueError, match="strictly after"):
        model.predict(_frame([2021]).drop(columns=["base_prediction_is_oof"]))


def test_expanding_year_splits_never_include_validation_year_in_training() -> None:
    dates = pd.Series(["2018-01-01", "2019-01-01", "2020-01-01", "2021-01-01"])
    splits = list(expanding_year_splits(dates, minimum_training_years=2))
    assert [split[2] for split in splits] == [2020, 2021]
    for training, validation, year in splits:
        assert pd.to_datetime(dates.iloc[training]).dt.year.max() < year
        assert pd.to_datetime(dates.iloc[validation]).dt.year.eq(year).all()
