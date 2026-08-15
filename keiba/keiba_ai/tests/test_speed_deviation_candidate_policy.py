from __future__ import annotations

import pandas as pd

from scripts.train_speed_deviation_candidate import _strategy_metrics


def _evaluation() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "race_id": ["r1", "r1", "r2", "r2"],
            "race_date": pd.to_datetime(["2025-01-01"] * 2 + ["2025-01-02"] * 2),
            "score": [2.0, 1.0, 3.0, 2.0],
            "probability": [0.70, 0.30, 0.55, 0.45],
            "odds": [1.5, 5.0, 1.8, 2.0],
            "popularity": [1, 2, 1, 2],
            "winner": [1, 0, 1, 0],
        }
    )


def test_candidate_uses_highest_eligible_expected_value() -> None:
    metrics = _strategy_metrics(
        _evaluation(),
        selector="candidate",
        minimum_expected_value=1.2,
    )

    # r1 selects the second horse (0.30 * 5.0 = 1.5); r2 has no eligible wager.
    assert metrics["bet_count"] == 1
    assert metrics["win_count"] == 0
    assert metrics["roi_percent"] == -100.0


def test_candidate_with_no_eligible_wager_is_fail_closed() -> None:
    metrics = _strategy_metrics(
        _evaluation(),
        selector="candidate",
        minimum_expected_value=10.0,
    )

    assert metrics == {
        "bet_count": 0,
        "win_count": 0,
        "roi_percent": 0.0,
        "max_drawdown_percent": 0.0,
    }
