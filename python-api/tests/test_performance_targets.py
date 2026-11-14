import numpy as np
import pandas as pd
import pytest


def _race(day, name, speeds, *, surface="turf", venue="05", distance=1600):
    return pd.DataFrame({
        "race_date": day, "race_id": name, "horse_number": range(1, len(speeds) + 1),
        "distance": distance, "time_seconds": distance / np.asarray(speeds),
        "surface": surface, "venue": venue, "field_condition": "good",
        "weather": "fine",
    })


def _history():
    return pd.concat([
        _race("20200101", "a", [10 * np.exp(-.01)] * 2),
        _race("20200102", "b", [10 * np.exp(.01)] * 6),
        _race("20200103", "query", [10, 10.1]),
    ], ignore_index=True)


def test_condition_baseline_weights_races_not_runner_counts():
    from training.performance_targets import rolling_condition_target

    frame = _history()
    labels, _ = rolling_condition_target(frame, min_parent_races=2)
    assert labels.loc[frame.race_id.eq("query")].iloc[0] == pytest.approx(0, abs=1e-9)
    bigger = pd.concat([
        _race("20200101", "a", [10 * np.exp(-.01)] * 12),
        frame.loc[frame.race_id.ne("a")],
    ], ignore_index=True)
    other, _ = rolling_condition_target(bigger, min_parent_races=2)
    np.testing.assert_allclose(
        labels.loc[frame.race_id.eq("query")],
        other.loc[bigger.race_id.eq("query")], atol=1e-9,
    )


def test_same_day_and_future_results_cannot_change_existing_labels():
    from training.performance_targets import rolling_condition_target

    frame = _history()
    labels, _ = rolling_condition_target(frame, min_parent_races=2)
    extended = pd.concat([
        frame, _race("20200103", "same-day", [100, 200]),
        _race("20200104", "future", [100, 200]),
    ], ignore_index=True)
    other, _ = rolling_condition_target(extended, min_parent_races=2)
    np.testing.assert_allclose(labels, other.iloc[:len(frame)], equal_nan=True)


def test_exact_365_day_boundary_is_included_but_older_day_is_not():
    from training.performance_targets import rolling_condition_target

    frame = pd.concat([
        _race("20200102", "too-old", [100, 200]),
        _race("20200103", "a", [10 * np.exp(-.01)] * 2),
        _race("20200103", "b", [10 * np.exp(.01)] * 2),
        _race("20210102", "query", [10, 10.1]),
    ], ignore_index=True)
    labels, _ = rolling_condition_target(frame, min_parent_races=2)
    assert labels.loc[frame.race_id.eq("query")].iloc[0] == pytest.approx(0, abs=1e-9)


def test_sparse_condition_uses_same_surface_distance_curve_not_mixed_global_mean():
    from training.performance_targets import rolling_condition_target

    frame = pd.concat([
        _race("20200101", "a", [10 * np.exp(-.01)] * 2, distance=1000),
        _race("20200102", "b", [10 * np.exp(.01)] * 2, distance=1000),
        _race("20200101", "c", [20 * np.exp(-.01)] * 2, distance=2000),
        _race("20200102", "d", [20 * np.exp(.01)] * 2, distance=2000),
        _race("20200102", "dirt", [50, 60], surface="dirt"),
        _race("20200103", "query", [15, 15.1], venue="99", distance=1500),
    ], ignore_index=True)
    labels, _ = rolling_condition_target(frame, min_parent_races=2)
    assert labels.loc[frame.race_id.eq("query")].iloc[0] == pytest.approx(0, abs=.002)


def test_missing_surface_history_stays_unavailable():
    from training.performance_targets import rolling_condition_target

    frame = pd.concat([
        _race("20200101", "a", [10, 11], surface="dirt"),
        _race("20200102", "b", [12, 13], surface="dirt"),
        _race("20200103", "query", [10, 11]),
    ], ignore_index=True)
    labels, _ = rolling_condition_target(frame, min_parent_races=2)
    assert labels.loc[frame.race_id.eq("query")].isna().all()


def test_condition_target_preserves_caller_order_and_index():
    from training.performance_targets import rolling_condition_target

    frame = _history()
    expected, _ = rolling_condition_target(frame, min_parent_races=2)
    shuffled = frame.sample(frac=1, random_state=7)
    result, _ = rolling_condition_target(shuffled, min_parent_races=2)
    pd.testing.assert_series_equal(result.sort_index(), expected.sort_index())


def test_constant_history_has_finite_floored_scale_and_extreme_target_is_clipped():
    from training.performance_targets import rolling_condition_target

    frame = pd.concat([
        _race("20200101", "a", [10, 10]),
        _race("20200102", "b", [10, 10]),
        _race("20200103", "query", [10, 100]),
    ], ignore_index=True)
    labels, _ = rolling_condition_target(frame, min_parent_races=2)
    np.testing.assert_allclose(labels.loc[frame.race_id.eq("query")], [0, 5], atol=1e-8)


def test_bad_clock_is_unavailable_not_zero_performance():
    from training.performance_targets import race_relative_speed_target, rolling_condition_target

    frame = _history()
    frame.loc[frame.index[-1], "time_seconds"] = 0
    relative = race_relative_speed_target(frame)
    condition, _ = rolling_condition_target(frame, min_parent_races=2)
    assert pd.isna(relative.iloc[-1])
    assert pd.isna(condition.iloc[-1])


def test_race_relative_target_is_hand_checked_and_constant_race_is_neutral():
    from training.performance_targets import race_relative_speed_target

    frame = pd.concat([
        _race("20200101", "a", [16, 15, 14]),
        _race("20200102", "b", [10, 10, 10]),
    ], ignore_index=True)
    np.testing.assert_allclose(race_relative_speed_target(frame), [1, 0, -1, 0, 0, 0])
