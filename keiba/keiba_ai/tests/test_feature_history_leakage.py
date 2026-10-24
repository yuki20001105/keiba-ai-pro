from __future__ import annotations

import math

import pandas as pd

from keiba_ai.feature_engineering import (
    _expanding_stats,
    _feh_gate_bias,
    _fe_prev_race,
    _prior_rolling_std,
    _sort_history_chronologically,
)
from keiba_ai.constants import FUTURE_FIELDS


def test_entity_history_uses_race_date_instead_of_provider_race_id() -> None:
    history = pd.DataFrame(
        [
            {"race_id": "202409010101", "race_date": "20240101", "jockey_id": "j1", "finish": 1},
            {"race_id": "202401010101", "race_date": "20241201", "jockey_id": "j1", "finish": 4},
        ]
    )

    result = _expanding_stats(history, "jockey_id", "jockey")

    assert result.loc[1, "jockey_win_rate"] == 1.0


def test_entity_history_excludes_every_result_from_the_current_date() -> None:
    history = pd.DataFrame(
        [
            {"race_id": "past", "race_date": "20240101", "jockey_id": "j1", "finish": 1},
            {"race_id": "today-a", "race_date": "20240102", "jockey_id": "j1", "finish": 8},
            {"race_id": "today-b", "race_date": "20240102", "jockey_id": "j1", "finish": 8},
        ]
    )

    result = _expanding_stats(history, "jockey_id", "jockey")

    assert result.loc[1, "jockey_win_rate"] == 1.0
    assert result.loc[2, "jockey_win_rate"] == 1.0


def test_gate_bias_excludes_current_date_and_future_results() -> None:
    rows = [
        {
            "race_id": f"202401{i:02d}0101",
            "race_date": f"202401{i:02d}",
            "venue": "東京",
            "surface": "芝",
            "distance": 1600,
            "bracket_number": 1,
            "finish": 1 if i <= 2 else 4,
        }
        for i in range(1, 11)
    ]
    rows.extend(
        [
            {**rows[-1], "race_id": "current-a", "race_date": "20240111", "finish": 1},
            {**rows[-1], "race_id": "current-b", "race_date": "20240111", "finish": 1},
            {**rows[-1], "race_id": "future", "race_date": "20250101", "finish": 1},
        ]
    )
    history = pd.DataFrame(rows)
    targets = history.loc[history["race_id"].isin(["current-a", "current-b"])].copy()

    result, _ = _feh_gate_bias(targets, history)

    assert result["gate_win_rate"].tolist() == [0.2, 0.2]


def test_prior_rolling_std_keeps_sample_standard_deviation() -> None:
    ordered = _sort_history_chronologically(
        pd.DataFrame(
            {
                "race_id": ["r1", "r2", "target"],
                "race_date": ["20240101", "20240102", "20240103"],
                "horse_id": ["h1", "h1", "h1"],
                "value": [0.0, 2.0, 9.0],
            }
        )
    )

    result = _prior_rolling_std(
        ordered,
        group_column="horse_id",
        value_column="value",
        window=5,
        min_periods=2,
    )

    target_index = ordered.index[ordered["race_id"].eq("target")][0]
    assert math.isclose(result.loc[target_index], math.sqrt(2.0))


def test_current_race_running_style_is_classified_as_future_information() -> None:
    assert {
        "running_style",
        "running_style_num",
        "speed_deviation",
        "finish_time_seconds",
    }.issubset(FUTURE_FIELDS)


def test_validation_rows_do_not_change_training_speed_zscores() -> None:
    base = pd.DataFrame(
        {
            "race_id": ["train", "train", "validation", "validation"],
            "prev_race_time": ["1:40.0", "1:45.0", "1:30.0", "1:35.0"],
            "prev_race_distance": [1600, 1600, 1600, 1600],
        }
    )
    changed = base.copy()
    changed.loc[changed["race_id"].eq("validation"), "prev_race_time"] = [
        "2:30.0",
        "3:00.0",
    ]

    first = _fe_prev_race(base)
    second = _fe_prev_race(changed)

    pd.testing.assert_series_equal(
        first.loc[first["race_id"].eq("train"), "prev_speed_zscore"],
        second.loc[second["race_id"].eq("train"), "prev_speed_zscore"],
    )
