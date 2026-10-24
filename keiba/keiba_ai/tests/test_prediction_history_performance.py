from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from keiba_ai import feature_engineering as fe
from keiba_ai.point_in_time_history import add_point_in_time_horse_history_features


def _history() -> pd.DataFrame:
    rows = []
    for horse in range(30):
        for event, date in enumerate(("20250101", "20250201", "20250301", "20250401")):
            rows.append({
                "race_id": f"event-{event}-{horse}",
                "race_date": date,
                "horse_id": f"horse-{horse}",
                "jockey_id": f"jockey-{horse % 3}",
                "trainer_id": f"trainer-{horse % 2}",
                "sire": f"sire-{horse % 4}",
                "damsire": f"damsire-{horse % 5}",
                "venue": "東京" if event % 2 else "京都",
                "surface": "芝" if event % 2 else "ダート",
                "distance": 1600 if event % 2 else 1800,
                "bracket_number": horse % 8 + 1,
                "finish": horse % 8 + 1,
                "last_3f_time": 34.0 + horse % 4,
                "last_3f_rank": horse % 5 + 1,
                "speed_deviation": float(horse - event),
                "running_style_num": horse % 4,
                "tansho_payout": 240 if horse % 8 == 0 else 0,
            })
    return pd.DataFrame(rows)


@pytest.mark.parametrize("helper", ["_expanding_win_rate_by_group", "_expanding_grouped_stats"])
def test_vectorized_key_preserves_legacy_row_join_and_statistics(helper: str) -> None:
    h = _history()
    h["horse_id"] = h["horse_id"].astype(object)
    # Numeric IDs, delimiters and literal strings resembling nulls retain the
    # legacy two-column string-key semantics.
    h.loc[:4, "horse_id"] = ["None", "nan", 42, "horse|x", ""]
    ordered = fe._sort_history_chronologically(h)
    ordered["_history_group"] = ordered[["horse_id", "surface"]].astype(str).apply("|".join, axis=1)
    finish = pd.to_numeric(ordered["finish"], errors="coerce").fillna(0)
    ordered["_w"] = finish.eq(1).astype(float)
    ordered["_f"] = finish
    count, sums = fe._prior_cumulative_values(ordered, ["_history_group"], ["_w", "_f"])
    expected_rate = (sums["_w"] / count.clip(1)).fillna(0.0).reindex(h.index)
    expected_count = count.reindex(h.index)
    if helper == "_expanding_win_rate_by_group":
        actual = fe._expanding_win_rate_by_group(h, "horse_id", "surface", "rate", "count")
        pd.testing.assert_series_equal(actual["rate"], expected_rate, check_names=False, check_exact=True)
        pd.testing.assert_series_equal(actual["count"], expected_count, check_names=False, check_exact=True)
    else:
        actual = fe._expanding_grouped_stats(h, "horse_id", "surface", "horse")
        pd.testing.assert_series_equal(actual["horse_win_rate"], expected_rate, check_names=False, check_exact=True)
        pd.testing.assert_series_equal(actual["horse_races"], expected_count, check_names=False, check_exact=True)
        expected_finish = (sums["_f"] / count.clip(1)).fillna(0.0).reindex(h.index)
        pd.testing.assert_series_equal(actual["horse_avg_finish"], expected_finish, check_names=False, check_exact=True)


@pytest.mark.parametrize("null", [None, np.nan, pd.NA])
def test_group_key_preserves_pandas_version_specific_null_behavior(null: object) -> None:
    frame = pd.DataFrame({"a": pd.Series(["one", null], dtype=object), "b": ["芝", "芝"]})
    try:
        expected = frame[["a", "b"]].astype(str).apply("|".join, axis=1)
    except TypeError:
        with pytest.raises(TypeError):
            fe._history_group_key(frame, "a", "b")
    else:
        pd.testing.assert_series_equal(fe._history_group_key(frame, "a", "b"), expected, check_exact=True)


@pytest.mark.parametrize("helper", [
    "_feh_horse_aptitude", "_feh_last_3f", "_feh_payout_history", "_feh_running_style",
])
def test_horse_only_stages_target_subset_equals_all_horse_computation(helper: str) -> None:
    history = _history()
    original = history.copy(deep=True)
    stage = getattr(fe, helper)
    full_result, _ = stage(history.copy(), history.copy())
    targets = history[history["horse_id"].isin(["horse-1", "horse-7"])].copy()
    result, returned_history = stage(targets, history)
    expected = full_result[full_result["horse_id"].isin(["horse-1", "horse-7"])]
    pd.testing.assert_frame_equal(result.reset_index(drop=True), expected.reset_index(drop=True), check_exact=True)
    # Non-horse stages must still receive the complete unchanged population.
    assert returned_history is history
    pd.testing.assert_frame_equal(history, original, check_exact=True)


@pytest.mark.parametrize("with_race_id", [True, False])
def test_pit_target_subset_equals_all_horse_computation(with_race_id: bool) -> None:
    history = _history()
    if not with_race_id:
        history = history.drop(columns="race_id")
    # Include whitespace normalization and two source rows on a prior date.
    history.loc[history["horse_id"].eq("horse-1"), "horse_id"] = " horse-1 "
    history = pd.concat([history, history.iloc[[1]]], ignore_index=True)
    targets = pd.DataFrame({
        "race_id": [f"target-{horse}" for horse in range(31)],
        "race_date": "20250401",
        "horse_id": [f"horse-{horse}" for horse in range(31)],
    })
    full_result = add_point_in_time_horse_history_features(targets, history)
    chosen = targets.iloc[[7, 1, 30]].copy()
    result = add_point_in_time_horse_history_features(chosen, history)
    expected = full_result.loc[chosen.index].copy()
    pd.testing.assert_frame_equal(result, expected, check_exact=True)
    assert result.attrs["point_in_time_history_audit"]["future_leakage_rows"] == 0
    assert result.attrs["point_in_time_history_audit"]["same_day_rows_excluded"] is True
    # Latest source on the target's own day remains excluded after pruning.
    assert result.loc[7, "horse_history_total_prior_starts"] == 3


def test_pit_normalized_id_pruning_does_not_mutate_shared_history() -> None:
    history = _history()
    before = history.copy(deep=True)
    targets = pd.DataFrame([{
        "race_id": "target", "race_date": "20250301", "horse_id": " horse-1 ",
    }])
    result = add_point_in_time_horse_history_features(targets, history)
    assert result.loc[0, "horse_history_total_prior_starts"] == 2
    pd.testing.assert_frame_equal(history, before, check_exact=True)
