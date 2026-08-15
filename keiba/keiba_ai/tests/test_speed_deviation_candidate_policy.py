from __future__ import annotations

import pandas as pd
import numpy as np

from scripts import evaluate_speed_deviation_walk_forward as walk_forward
from scripts.train_speed_deviation_candidate import _evaluate, _strategy_metrics


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


def test_ability_metrics_run_without_final_or_point_in_time_odds() -> None:
    frame = pd.DataFrame(
        {
            "race_id": ["r1"] * 5,
            "date": ["20240101"] * 5,
            "finish": [1, 2, 3, 4, 5],
        }
    )
    metrics, evaluated = _evaluate(
        frame,
        pd.Series([1.0, 0.5, 0.0, -0.5, -1.0]),
        pd.Series([0.9, 0.4, 0.1, -0.4, -0.8]).to_numpy(),
        require_value_data=False,
    )

    assert len(evaluated) == 5
    assert metrics["race_count"] == 1
    assert metrics["winner_auc"] == 1.0
    assert metrics["point_in_time_value_race_count"] == 0
    assert metrics["candidate_roi_percent"] is None
    assert metrics["candidate_max_drawdown_percent"] is None


def test_final_odds_are_not_mislabeled_as_point_in_time_value_data() -> None:
    frame = pd.DataFrame(
        {
            "race_id": ["r1"] * 5,
            "date": ["20240101"] * 5,
            "finish": [1, 2, 3, 4, 5],
            "odds": [2.0, 3.0, 4.0, 5.0, 6.0],
            "popularity": [1, 2, 3, 4, 5],
        }
    )
    metrics, _ = _evaluate(
        frame,
        pd.Series([1.0, 0.5, 0.0, -0.5, -1.0]),
        np.array([0.9, 0.4, 0.1, -0.4, -0.8]),
        require_value_data=False,
    )
    assert metrics["point_in_time_value_race_count"] == 0
    assert metrics["candidate_roi_percent"] is None


def test_ability_matrix_excludes_market_fields(monkeypatch) -> None:
    def identity(frame, **_kwargs):
        return frame.copy(), object(), []

    monkeypatch.setattr(walk_forward, "prepare_for_lightgbm_ultimate", identity)
    training = pd.DataFrame(
        {
            "feature": [1.0, 2.0],
            "odds": [2.0, 3.0],
            "popularity": [1, 2],
            "implied_prob": [0.5, 1 / 3],
            "odds_z_in_race": [-1.0, 1.0],
            "market_entropy": [0.6, 0.6],
            "top3_probability": [1.0, 1.0],
            "popularity_normalized": [0.5, 1.0],
            "past5_avg_tansho_log": [4.0, 5.0],
            "speed_deviation": [0.2, -0.2],
        }
    )
    train_x, validation_x, _ = walk_forward._prepare_matrices(training, training)
    assert train_x.columns.tolist() == ["feature"]
    assert validation_x.columns.tolist() == ["feature"]


def test_winner_scores_drive_probability_without_replacing_speed_target() -> None:
    frame = pd.DataFrame(
        {
            "race_id": ["r1"] * 5,
            "date": ["20240101"] * 5,
            "finish": [1, 2, 3, 4, 5],
        }
    )
    target = pd.Series([1.0, 0.5, 0.0, -0.5, -1.0])
    speed_scores = np.array([-0.9, 0.8, 0.4, 0.1, -0.2])
    winner_scores = np.array([3.0, 1.0, 0.0, -1.0, -2.0])

    metrics, evaluated = _evaluate(
        frame,
        target,
        speed_scores,
        winner_scores=winner_scores,
        require_value_data=False,
    )

    assert evaluated.loc[evaluated["winner"].eq(1), "probability"].iloc[0] == max(
        evaluated["probability"]
    )
    assert metrics["winner_auc"] == 1.0
    assert metrics["rmse"] > 0.0


def test_winner_meta_matrix_is_market_free_and_race_relative() -> None:
    base = pd.DataFrame(
        {
            "horse_number": [1, 2, 3],
            "prev_race_finish": [2.0, 4.0, 1.0],
            "odds": [2.0, 3.0, 4.0],
            "implied_prob": [0.5, 1 / 3, 0.25],
        }
    )
    matrix = walk_forward._winner_meta_matrix(
        base,
        pd.Series(["r1", "r1", "r1"]),
        np.array([2.0, 1.0, 0.0]),
    )

    assert "odds" not in matrix
    assert "implied_prob" not in matrix
    assert matrix["field_size"].tolist() == [3.0, 3.0, 3.0]
    assert matrix["speed_gap_to_best"].tolist() == [0.0, -1.0, -2.0]
    assert matrix["speed_rank_percentile"].is_monotonic_increasing


def test_prior_speed_history_is_strictly_shifted() -> None:
    source = pd.DataFrame(
        {
            "horse_id": ["h1", "h2", "h1", "h1"],
            "distance": [1000, 1000, 1000, 1400],
            "time_seconds": [60.0, 50.0, 50.0, 70.0],
            "surface": ["turf", "turf", "turf", "turf"],
        }
    )

    result = walk_forward._add_prior_speed_history(source)

    assert pd.isna(result.loc[0, "prior_speed_mps_last"])
    assert pd.isna(result.loc[1, "prior_speed_mps_last"])
    assert result.loc[2, "prior_speed_mps_last"] == 1000 / 60.0
    assert result.loc[3, "prior_speed_mps_last"] == 1000 / 50.0
    assert result.loc[3, "prior_speed_mps_mean_3"] == np.mean(
        [1000 / 60.0, 1000 / 50.0]
    )
    assert result.loc[2, "prior_speed_observation_count"] == 1
    assert result.loc[3, "prior_speed_observation_count"] == 2
