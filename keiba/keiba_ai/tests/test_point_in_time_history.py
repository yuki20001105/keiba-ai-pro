from __future__ import annotations

import math

import pandas as pd

from keiba_ai.point_in_time_history import add_point_in_time_horse_history_features


def _history() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"race_id": "h1", "horse_id": "horse-a", "race_date": "20190101", "finish": 5, "last_3f_time": 37.0},
            {"race_id": "h2", "horse_id": "horse-a", "race_date": "20200101", "finish": 3, "last_3f_time": 36.0},
            {"race_id": "h3", "horse_id": "horse-a", "race_date": "20210101", "finish": 1, "last_3f_time": 35.0},
            # Same-day result must be invisible to a target on 2022-01-01.
            {"race_id": "same-day", "horse_id": "horse-a", "race_date": "20220101", "finish": 1, "last_3f_time": 34.0},
            # A future row deliberately has an attractive result.
            {"race_id": "future", "horse_id": "horse-a", "race_date": "20230101", "finish": 1, "last_3f_time": 33.0},
            {"race_id": "other", "horse_id": "horse-b", "race_date": "20210101", "finish": 1, "last_3f_time": 34.0},
        ]
    )


def test_strict_date_predicate_excludes_same_day_and_future_rows() -> None:
    targets = pd.DataFrame(
        [{"race_id": "target", "horse_id": "horse-a", "race_date": "20220101"}]
    )

    result = add_point_in_time_horse_history_features(targets, _history())

    assert result.loc[0, "horse_history_total_prior_starts"] == 3
    assert result.loc[0, "past1_avg_finish"] == 1.0
    assert result.loc[0, "past2_avg_finish"] == 2.0
    assert result.loc[0, "past3_avg_finish"] == 3.0
    assert result.loc[0, "past3_win_rate"] == 1 / 3
    assert result.loc[0, "past5_count"] == 3
    assert result.loc[0, "past5_is_insufficient"] == 1
    assert result.loc[0, "past5_coverage"] == 0.6
    assert result.attrs["point_in_time_history_audit"]["future_leakage_rows"] == 0
    assert result.attrs["point_in_time_history_audit"]["same_day_rows_excluded"] is True


def test_windows_one_two_three_five_ten_and_missing_flags_are_created() -> None:
    targets = pd.DataFrame(
        [
            {"race_id": "target-a", "horse_id": "horse-a", "race_date": "20240101"},
            {"race_id": "target-new", "horse_id": "new-horse", "race_date": "20240101"},
            {"race_id": "target-bad-date", "horse_id": "horse-a", "race_date": None},
        ]
    )

    result = add_point_in_time_horse_history_features(targets, _history())

    assert [result.loc[0, f"past{n}_count"] for n in (1, 2, 3, 5, 10)] == [1, 2, 3, 5, 5]
    assert result.loc[0, "past5_is_insufficient"] == 0
    assert result.loc[0, "past10_is_insufficient"] == 1
    assert result.loc[1, "horse_history_total_prior_starts"] == 0
    assert result.loc[1, "past1_is_insufficient"] == 1
    assert math.isnan(result.loc[1, "past1_avg_finish"])
    assert result.loc[2, "horse_history_target_date_missing"] == 1


def test_target_order_and_index_are_preserved() -> None:
    targets = pd.DataFrame(
        [
            {"race_id": "later", "horse_id": "horse-a", "race_date": "20240101"},
            {"race_id": "earlier", "horse_id": "horse-a", "race_date": "20200101"},
        ],
        index=[20, 10],
    )

    result = add_point_in_time_horse_history_features(targets, _history())

    assert result.index.tolist() == [20, 10]
    assert result.loc[20, "horse_history_total_prior_starts"] == 5
    assert result.loc[10, "horse_history_total_prior_starts"] == 1


def test_duplicate_history_rows_do_not_inflate_counts() -> None:
    history = pd.concat([_history(), _history().iloc[[0]]], ignore_index=True)
    targets = pd.DataFrame(
        [{"race_id": "target", "horse_id": "horse-a", "race_date": "20220101"}]
    )

    result = add_point_in_time_horse_history_features(targets, history)

    assert result.loc[0, "horse_history_total_prior_starts"] == 3
