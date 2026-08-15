from __future__ import annotations

import pandas as pd
import pytest

from keiba_ai.nested_strategy import (
    StrategyCondition,
    evaluate_strategy,
    select_nested_strategy,
)


def _oof(races: int = 120) -> pd.DataFrame:
    rows = []
    for race_number in range(races):
        race_id = f"R{race_number:04d}"
        winner = race_number % 2
        for horse_number in range(2):
            rows.append(
                {
                    "race_id": race_id,
                    "horse_id": f"H{horse_number}",
                    "race_date": pd.Timestamp("2020-01-01")
                    + pd.Timedelta(days=race_number),
                    "winner": int(horse_number == winner),
                    "probability": 0.7 if horse_number == winner else 0.3,
                    "odds": 2.0 if horse_number == winner else 4.0,
                    "base_prediction_is_oof": True,
                    "probability_prediction_is_oof": True,
                }
            )
    return pd.DataFrame(rows)


def test_nested_search_returns_unapproved_research_candidate() -> None:
    result = select_nested_strategy(
        _oof(),
        search_space={
            "minimum_expected_values": [1.2],
            "minimum_probability_edges": [0.0],
            "minimum_odds": [1.01],
            "maximum_odds": [10.0],
            "target_bet_rates": [1.0],
        },
        minimum_bets=100,
        maximum_drawdown_percent=20.0,
        minimum_bet_rate=0.0,
        maximum_bet_rate=1.0,
        minimum_baseline_roi_delta_percent=-100.0,
    )
    assert result["selected"] is not None
    assert result["deployment_eligible"] is False
    assert result["selection_status"].startswith("research-candidate")
    assert result["selected"]["metrics"]["maximum_wagers_in_any_race"] == 1
    assert "roi_lower_95_percent" in result["selected"]["metrics"]
    assert "neighborhood_robustness" in result["selected"]


def test_nested_search_rejects_non_oof_rows() -> None:
    frame = _oof()
    frame.loc[0, "base_prediction_is_oof"] = False
    with pytest.raises(ValueError, match="OOF"):
        select_nested_strategy(
            frame,
            search_space={
                "minimum_expected_values": [1.2],
                "minimum_probability_edges": [0.0],
                "minimum_odds": [1.01],
                "maximum_odds": [10.0],
                "target_bet_rates": [0.1],
            },
        )


def test_outer_evaluation_uses_fixed_numeric_condition() -> None:
    condition = StrategyCondition(1.2, 0.0, 1.01, 10.0, 1.0, 1.2)
    metrics = evaluate_strategy(_oof(10), condition)
    assert metrics["bet_count"] == 10
    assert metrics["roi_percent"] == pytest.approx(100.0)
    assert metrics["roi_lower_95_percent"] == pytest.approx(100.0)


def test_filtering_happens_before_best_eligible_runner_is_selected() -> None:
    frame = pd.DataFrame(
        [
            {
                "race_id": "R1",
                "horse_id": "H1",
                "race_date": "2020-01-01",
                "winner": 0,
                "probability": 0.6,
                "odds": 20.0,
            },
            {
                "race_id": "R1",
                "horse_id": "H2",
                "race_date": "2020-01-01",
                "winner": 1,
                "probability": 0.4,
                "odds": 4.0,
            },
        ]
    )
    condition = StrategyCondition(1.2, -1.0, 1.01, 10.0, 1.0, 1.2)
    metrics = evaluate_strategy(frame, condition)
    assert metrics["bet_count"] == 1
    assert metrics["win_count"] == 1


def test_nested_search_enforces_actual_bet_rate_bounds() -> None:
    result = select_nested_strategy(
        _oof(),
        search_space={
            "minimum_expected_values": [1.2],
            "minimum_probability_edges": [0.0],
            "minimum_odds": [1.01],
            "maximum_odds": [10.0],
            "target_bet_rates": [1.0],
        },
        minimum_bets=1,
        minimum_bet_rate=0.05,
        maximum_bet_rate=0.15,
        minimum_baseline_roi_delta_percent=-100.0,
    )
    assert result["selected"] is None
    assert (
        "maximum_bet_rate"
        in result["evaluated_conditions"][0]["rejection_reasons"]
    )
