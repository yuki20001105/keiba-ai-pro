from __future__ import annotations

import numpy as np
import pandas as pd

from keiba_ai.constants import FUTURE_FIELDS
from keiba_ai.speed_deviation import (
    apply_speed_deviation_baseline,
    fit_speed_deviation_baseline,
    raw_speed_mps,
)


def _frame(times: list[float], *, surface: str = "turf") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "distance": [1600] * len(times),
            "time_seconds": times,
            "surface": [surface] * len(times),
        }
    )


def test_validation_outcome_does_not_change_training_baseline() -> None:
    training = _frame([100.0, 101.0, 102.0, 103.0])
    baseline = fit_speed_deviation_baseline(training, min_group_size=2)

    before = apply_speed_deviation_baseline(_frame([99.0]), baseline).iloc[0]
    after = apply_speed_deviation_baseline(_frame([99.0, 40.0]), baseline).iloc[0]

    assert before == after
    assert baseline["training_sample_count"] == 4


def test_sparse_unknown_group_uses_training_global_statistics() -> None:
    training = _frame([100.0, 101.0, 102.0, 103.0])
    baseline = fit_speed_deviation_baseline(training, min_group_size=5)
    validation = _frame([98.0], surface="dirt")

    actual = apply_speed_deviation_baseline(validation, baseline).iloc[0]
    speed = 1600.0 / 98.0
    expected = (speed - baseline["global_mean"]) / baseline["global_std"]

    assert np.isclose(actual, expected)


def test_raw_speed_rejects_nonpositive_and_missing_times() -> None:
    frame = _frame([100.0, 0.0, np.nan])

    result = raw_speed_mps(frame)

    assert np.isclose(result.iloc[0], 16.0)
    assert result.iloc[1:].isna().all()


def test_current_race_time_and_lap_figures_are_future_fields() -> None:
    assert {
        "time_index",
        "lap_cumulative",
        "lap_sectional",
        "race_pace_front",
        "race_pace_back",
        "race_pace_diff",
        "race_pace_ratio",
        "lap_200m",
        "lap_sect_200m",
    }.issubset(FUTURE_FIELDS)
