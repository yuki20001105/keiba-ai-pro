import numpy as np
import pandas as pd

from training.period_quality import audit_period_features


def test_empty_selected_history_is_only_a_candidate_and_does_not_authorize_imputation():
    frame = pd.DataFrame({
        "race_date": ["20160101", "20261028", "20261029"],
        "race_id": ["201601010101", "202605010101", "202605010102"],
        "safe_horse_course_starts": [0, 2, 0],
        "safe_horse_surface_starts": [3, 5, 3],
        "safe_horse_surface_win_rate": [0.2, 0.4, 0.2],
        "safe_horse_course_win_rate": [np.nan, np.nan, np.nan],
    })
    before = frame.copy(deep=True)
    result = audit_period_features(
        frame, ["safe_horse_course_win_rate"], [], start_year=2016, end_date="20261028")
    assert result["rows"] == 2
    assert result["excluded_outside_period_rows"] == 1
    assert result["years"]["2016"]["backoff_candidate_cells_not_authorized"] == 1
    assert result["years"]["2026"]["other_or_unknown_cells"] == 1
    assert result["years"]["2017"]["rows"] == 0
    assert result["imputation_applied"] is False
    pd.testing.assert_frame_equal(frame, before)


def test_unknown_counts_and_invalid_surface_values_are_not_backoff_candidates():
    frame = pd.DataFrame({
        "race_date": ["20260101"] * 4, "race_id": ["202601010101"] * 4,
        "safe_horse_course_starts": [np.nan, -1, 0, 0],
        "safe_horse_surface_starts": [3, 3, 0, 3],
        "safe_horse_surface_win_rate": [0.2, 0.2, 0.2, np.inf],
        "safe_horse_course_win_rate": [np.nan] * 4,
    })
    result = audit_period_features(
        frame, ["safe_horse_course_win_rate"], [], start_year=2026, end_date="20261028")
    year = result["years"]["2026"]
    assert year["missing_cells"] == 4
    assert year["selected_history_insufficient_cells"] == 2
    assert year["backoff_candidate_cells_not_authorized"] == 0
