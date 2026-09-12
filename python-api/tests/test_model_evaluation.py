from __future__ import annotations

import pytest

from training.model_evaluation import evaluate_speed_deviation_predictions


def test_speed_deviation_evaluation_reports_simple_primary_and_detail_metrics():
    evaluation = evaluate_speed_deviation_predictions(
        actual=[1.2, 0.4, 0.1, 1.0, 0.5, -0.2],
        predicted=[1.0, 0.5, 0.2, 0.8, 0.6, -0.1],
        race_ids=["r1", "r1", "r1", "r2", "r2", "r2"],
        finish_positions=[1, 2, 3, 2, 1, 3],
        race_dates=["20250101"] * 3 + ["20260101"] * 3,
        popularity=[2, 1, 3, 2, 1, 3],
        odds=[4.0, 2.0, 8.0, 3.0, 2.2, 9.0],
        payouts=[400, 400, 400, 220, 220, 220],
    )

    assert evaluation["schema"] == "model-evaluation-v1"
    assert evaluation["primary"]["rank_correlation"] == pytest.approx(1.0)
    assert evaluation["primary"]["top_pick_win_rate"] == pytest.approx(0.5)
    assert evaluation["primary"]["favorite_win_rate"] == pytest.approx(0.5)
    assert evaluation["primary"]["top_pick_win_rate_delta"] == pytest.approx(0.0)
    assert evaluation["primary"]["win_roi"] == pytest.approx(200.0)
    assert evaluation["details"]["evaluation_race_count"] == 2
    assert evaluation["details"]["evaluation_date_from"] == "2025-01-01"
    assert [item["period"] for item in evaluation["time_slices"]] == ["2025", "2026"]


def test_speed_deviation_evaluation_marks_roi_unmeasured_without_payouts():
    evaluation = evaluate_speed_deviation_predictions(
        actual=[1.0, 0.0],
        predicted=[0.8, 0.2],
        race_ids=["r1", "r1"],
        finish_positions=[1, 2],
        race_dates=["20250101", "20250101"],
        popularity=[1, 2],
        odds=[2.0, 4.0],
        payouts=[None, None],
    )

    assert evaluation["primary"]["win_roi"] is None
