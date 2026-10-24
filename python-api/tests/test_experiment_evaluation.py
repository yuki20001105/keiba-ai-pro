from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from experiments.evaluation import evaluate_predictions, race_evaluation_records


def evaluation_frame():
    rows = []
    for day, race_id, scores, popularity in [
        ("20230101", "r1", [3.0, 2.0, 1.0], [2, 1, 3]),
        ("20230102", "r2", [1.0, 3.0, 2.0], [1, 2, 3]),
    ]:
        for index in range(3):
            rows.append({"race_id": race_id, "race_date": day, "horse_id": f"{race_id}h{index}",
                         "horse_number": index + 1, "surface": "芝" if race_id == "r1" else "ダート",
                         "finish": index + 1, "prediction": scores[index], "actual": 3.0 - index,
                         "popularity": popularity[index], "tansho_payout": 250.0 if index == 0 else np.nan,
                         "expected_runner_count": 3})
    return pd.DataFrame(rows)


def test_all_five_metrics_and_net_vs_gross_roi_are_explicit():
    result = evaluate_predictions(evaluation_frame(), task="speed_regression", bootstrap_samples=20)
    metrics = result["overall"]
    assert metrics["rank_correlation"] == pytest.approx(0.25)
    assert metrics["rmse"] == pytest.approx(1.0)
    assert metrics["mae"] == pytest.approx(4 / 6)
    assert metrics["top1_win_rate"] == 0.5
    assert metrics["popularity_top1_win_rate"] == 0.5
    assert metrics["top1_vs_popularity_pp"] == 0
    assert metrics["win_roi_percent"] == 25
    assert metrics["win_return_rate_percent"] == 125
    assert metrics["win_payout_yen"] == 250
    assert result["day_bootstrap_ci"]["unit"] == "race_date"
    assert result["real_world_all_races"] is False
    assert result["bet_types"]["place"] == "not_evaluated"
    assert set(result["by_year_surface"]) == {"2023:芝", "2023:ダート"}


def test_missing_payout_is_not_zero_even_for_a_losing_pick():
    frame = evaluation_frame()
    frame.loc[(frame.race_id == "r2") & (frame.finish == 1), "tansho_payout"] = np.nan
    result = evaluate_predictions(frame, task="ranking", bootstrap_samples=20)
    metrics = result["overall"]
    assert metrics["win_roi_percent"] is None
    assert metrics["win_payout_yen"] is None
    assert metrics["payout_missing_races"] == 1
    assert metrics["verified_subset_win_roi_percent"] == 150
    assert metrics["rmse"] is None
    assert metrics["mae"] is None
    assert result["day_bootstrap_ci"]["metrics"]["win_roi_percent"]["valid_replicates"] == 0
    assert result["definitions"]["score_is_probability"] is False


def test_popularity_difference_uses_identical_paired_races():
    frame = evaluation_frame()
    frame.loc[frame.race_id == "r2", "popularity"] = np.nan
    metrics = evaluate_predictions(frame, task="ranking", bootstrap_samples=0)["overall"]
    assert metrics["top1_win_rate"] == 0.5
    assert metrics["top1_win_rate_on_popularity_races"] == 1
    assert metrics["popularity_top1_win_rate"] == 0
    assert metrics["top1_vs_popularity_pp"] == 100
    assert metrics["popularity_comparison_races"] == 1


@pytest.mark.parametrize("mutation", ["missing_prediction", "missing_runner", "duplicate_runner", "invalid_result"])
def test_incomplete_or_invalid_cohort_fails_closed(mutation):
    frame = evaluation_frame()
    if mutation == "missing_prediction":
        frame.loc[0, "prediction"] = np.nan
    elif mutation == "missing_runner":
        frame = frame.drop(index=0)
    elif mutation == "duplicate_runner":
        frame = pd.concat([frame, frame.iloc[:1]], ignore_index=True)
    else:
        frame["finish"] = frame["finish"].astype(object)
        frame.loc[0, "finish"] = "取消"
    with pytest.raises(ValueError):
        evaluate_predictions(frame, task="ranking", bootstrap_samples=0)


def test_prediction_ties_are_not_broken_by_actual_result():
    frame = evaluation_frame().iloc[:3].copy()
    frame["prediction"] = 1.0
    frame["horse_number"] = [3, 1, 2]
    records = race_evaluation_records(frame, task="ranking")
    assert records.iloc[0].picked_horse_id == "r1h1"
    result = evaluate_predictions(frame, task="ranking", bootstrap_samples=10)
    assert result["overall"]["top1_win_rate"] == 0
    assert result["overall"]["rank_correlation"] is None
    assert result["overall"]["constant_score_or_result_races"] == 1
    assert result["day_bootstrap_ci"]["metrics"]["top1_win_rate"]["lower"] is None


def test_dead_heat_requires_each_winner_payout_and_uses_selected_horse_payout():
    frame = evaluation_frame().iloc[:3].copy()
    frame["finish"] = [1, 1, 3]
    frame["tansho_payout"] = [130, 320, np.nan]
    frame["prediction"] = [1, 3, 2]
    result = evaluate_predictions(frame, task="ranking", bootstrap_samples=0)
    assert result["overall"]["win_payout_yen"] == 320
    frame.loc[0, "tansho_payout"] = np.nan
    assert evaluate_predictions(frame, task="ranking", bootstrap_samples=0)["overall"]["win_roi_percent"] is None


def test_bootstrap_is_seeded_and_clusters_all_races_of_a_day():
    frame = evaluation_frame()
    duplicate = frame.iloc[:3].copy()
    duplicate["race_id"] = "r3"
    duplicate["horse_id"] = "extra-" + duplicate["horse_id"]
    frame = pd.concat([frame, duplicate], ignore_index=True)
    first = evaluate_predictions(frame, task="ranking", seed=71, bootstrap_samples=30)
    second = evaluate_predictions(frame, task="ranking", seed=71, bootstrap_samples=30)
    assert first["day_bootstrap_ci"] == second["day_bootstrap_ci"]
    assert first["day_bootstrap_ci"]["date_count"] == 2
    assert first["overall"]["race_count"] == 3


def test_missing_horse_ids_use_verified_runner_numbers_without_inventing_identity():
    frame = evaluation_frame()
    frame.loc[[0, 1], "horse_id"] = None
    records = race_evaluation_records(frame, task="ranking")
    assert records.iloc[0].picked_horse_id == ""
    assert records.iloc[0].picked_horse_number == 1
    assert evaluate_predictions(frame, task="ranking", bootstrap_samples=0)["overall"]["top1_win_rate"] == 0.5
    frame.loc[1, "horse_number"] = 1
    with pytest.raises(ValueError):
        race_evaluation_records(frame, task="ranking")
