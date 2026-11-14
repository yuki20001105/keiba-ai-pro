from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from training.structural_imputation import FALLBACKS, FLAGS, impute_condition_history


def sample():
    row = {
        "safe_horse_surface_starts": 7,
        "safe_horse_surface_win_rate": 0.0,
        "safe_horse_surface_top3_rate": 0.5,
        "safe_horse_surface_avg_speed_mps": 16.4,
        "safe_horse_course_starts": 0,
        "safe_horse_distance_band_starts": 2,
    }
    for column, source in FALLBACKS.items():
        row[column] = np.nan if "_course_" in column else row[source]
    frame = pd.DataFrame([row], index=["runner"])
    authorized = {("runner", column) for column in FALLBACKS if "_course_" in column}
    return frame, authorized


def test_audited_gaps_back_off_without_changing_observations_or_counts():
    frame, authorized = sample()
    original = frame.copy(deep=True)
    result, changes = impute_condition_history(frame, authorized)
    assert result.isna().sum().sum() == 0
    assert len(changes) == 3
    assert result.loc["runner", "safe_horse_course_starts"] == 0
    assert result.loc["runner", "safe_horse_course_win_rate"] == 0
    assert result.loc["runner", "safe_horse_course_win_rate_was_imputed"] == 1
    assert result.loc["runner", "safe_horse_distance_band_win_rate_was_imputed"] == 0
    assert result[FLAGS].sum().sum() == 3
    assert all(not change["is_observed_condition_statistic"] for change in changes)
    pd.testing.assert_frame_equal(frame, original)
    pd.testing.assert_frame_equal(frame, result[frame.columns].mask(frame.isna()))


@pytest.mark.parametrize("count", [1, np.nan, -1])
def test_unknown_or_nonzero_condition_count_cannot_be_imputed(count):
    frame, authorized = sample()
    frame["safe_horse_course_starts"] = count
    with pytest.raises(ValueError, match="not-zero-history"):
        impute_condition_history(frame, authorized)


@pytest.mark.parametrize("count", [0, np.nan, np.inf, -1, 0.5])
def test_no_fallback_history_is_not_silently_zero_filled(count):
    frame, authorized = sample()
    frame["safe_horse_surface_starts"] = count
    with pytest.raises(ValueError, match="no-observed-fallback-history"):
        impute_condition_history(frame, authorized)


@pytest.mark.parametrize("value", [np.nan, np.inf, -0.1, 1.1])
def test_invalid_fallback_rates_are_explicit_errors(value):
    frame, authorized = sample()
    frame["safe_horse_surface_win_rate"] = value
    with pytest.raises(ValueError, match="fallback-"):
        impute_condition_history(frame, authorized)


def test_missing_cells_need_exact_authorization_and_reprocessing_is_rejected():
    frame, authorized = sample()
    with pytest.raises(ValueError, match="not-exactly-authorized"):
        impute_condition_history(frame, set())
    with pytest.raises(ValueError, match="not-exactly-authorized"):
        impute_condition_history(frame, authorized | {("runner", "safe_prev_finish")})
    result, _ = impute_condition_history(frame, authorized)
    with pytest.raises(ValueError, match="schema-invalid"):
        impute_condition_history(result, set())


def test_no_missing_condition_stats_produces_stable_zero_flags():
    frame, _ = sample()
    for column, source in FALLBACKS.items():
        frame[column] = frame[source]
    result, changes = impute_condition_history(frame, set())
    assert list(result.columns) == [*frame.columns, *FLAGS]
    assert result[FLAGS].sum().sum() == 0
    assert changes == []


def test_unrelated_missing_values_are_not_disguised_as_condition_gaps():
    frame, authorized = sample()
    frame["safe_prev_speed_mps"] = np.nan
    result, _ = impute_condition_history(frame, authorized)
    assert result.safe_prev_speed_mps.isna().all()


def test_row_local_transform_does_not_fit_from_other_rows_or_outcomes():
    frame, authorized = sample()
    expected, _ = impute_condition_history(frame, authorized)
    other = frame.copy()
    other.index = ["future"]
    for column, source in FALLBACKS.items():
        other[column] = other[source]
    other["safe_horse_surface_avg_speed_mps"] = 100
    batch = pd.concat([frame, other])
    batch["finish"] = [16, 1]
    result, _ = impute_condition_history(batch, authorized)
    pd.testing.assert_series_equal(expected.loc["runner"], result.loc["runner", expected.columns])
