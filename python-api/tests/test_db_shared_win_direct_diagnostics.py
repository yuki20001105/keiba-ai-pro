"""Independent outcome diagnostics and fail-closed cohort tests."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest
from scipy.stats import spearmanr

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import db_shared_win_direct_diagnostics as diagnostics


def cohort():
    rows = []
    for race, (day, winner, p) in enumerate([
        ("20250105", 1, (.55, .25, .20)),
        ("20250105", 2, (.30, .40, .30)),
        ("20250112", 3, (.15, .20, .65)),
        ("20260104", 2, (.40, .40, .20)),
        ("20260111", 3, (.10, .60, .30)),
    ]):
        for number in range(1, 4):
            rows.append({"race_id": f"{day}-r{race}", "race_date": day,
                         "horse_number": number, "expected_runner_count": 3,
                         "odds": (2.5, 5., 30.)[number - 1], "probability": p[number - 1],
                         "finish": 1 if number == winner else 2 + [n for n in range(1, 4) if n != winner].index(number),
                         "venue": "東京", "surface": "turf"})
    return pd.DataFrame(rows)


@pytest.fixture
def frame():
    return cohort()


def test_metrics_independent_formulas(frame):
    result = diagnostics.analyze_probability(frame, repetitions=100)
    truth = frame.finish.eq(1).astype(float)
    assert result["nll"] == pytest.approx(-np.log(frame.loc[truth.eq(1), "probability"]).mean())
    assert result["brier"] == pytest.approx(((frame.probability - truth) ** 2).groupby(frame.race_id).sum().mean())
    expected_rho = frame.groupby("race_id").apply(lambda x: spearmanr(x.probability, -x.finish).statistic,
                                                  include_groups=False).mean()
    assert result["spearman"] == pytest.approx(expected_rho)
    # Fourth race has a probability tie, resolved by horse 1, not winning horse 2.
    assert result["top1"] == pytest.approx(3 / 5)
    assert result["race_count"] == 5 and result["date_count"] == 4 and result["runner_count"] == 15
    assert result["comparison_vs_reference"] is None
    assert "rmse" not in json.dumps(result).lower()
    json.dumps(result, allow_nan=False)


def test_inputs_unchanged_unknown_columns_not_consumed(frame):
    original = frame.copy(deep=True)
    expected = diagnostics.analyze_probability(frame, repetitions=30)
    added = frame.assign(raw_score=np.inf, predicted_rank=-999, tansho_payout=99999999,
                         secret_future_results="ignored")
    assert diagnostics.analyze_probability(added, repetitions=30) == expected
    pd.testing.assert_frame_equal(frame, original)


def test_bootstrap_deterministic_and_row_order_invariant(frame):
    a = diagnostics.analyze_probability(frame, repetitions=150, seed=20261012)
    b = diagnostics.analyze_probability(frame.sample(frac=1, random_state=5), repetitions=150, seed=20261012)
    assert a == b


def test_same_reference_paired_deltas_exact_zero(frame):
    result = diagnostics.analyze_probability(frame, frame.copy(), repetitions=100)
    comparison = result["comparison_vs_reference"]
    assert comparison["delta_nll"] == 0. and comparison["delta_brier"] == 0.
    assert comparison["delta_nll_ci95"] == [0., 0.]
    assert comparison["delta_brier_ci95"] == [0., 0.]


def test_better_reference_comparison_is_input_aligned(frame):
    reference = frame.copy()
    reference["probability"] = 1 / 3
    result = diagnostics.analyze_probability(frame.sample(frac=1, random_state=3), reference,
                                             repetitions=100)
    comparison = result["comparison_vs_reference"]
    assert comparison["reference_nll"] == pytest.approx(np.log(3))
    assert comparison["reference_brier"] == pytest.approx(2 / 3)
    assert comparison["delta_nll"] == pytest.approx(result["nll"] - np.log(3))
    assert comparison["delta_brier"] == pytest.approx(result["brier"] - 2 / 3)
    assert result["constant_rank_races"] == 0


@pytest.mark.parametrize("column,value", [
    ("race_date", "20250106"), ("finish", 3), ("odds", 2.6), ("venue", "京都"),
])
def test_reference_labels_market_dates_metadata_must_match(frame, column, value):
    reference = frame.copy()
    if column in ("race_date", "venue"):
        reference.loc[reference.race_id.eq(reference.race_id.iloc[0]), column] = value
    else:
        reference.loc[0, column] = value
        if column == "finish":
            reference.loc[2, "finish"] = 1
    with pytest.raises(ValueError, match="matched-reference-cohort"):
        diagnostics.analyze_probability(frame, reference, repetitions=10)


def test_reference_missing_cohort_rejected(frame):
    reference = frame.loc[frame.race_id.ne(frame.race_id.iloc[0])]
    with pytest.raises(ValueError, match="matched-reference-cohort"):
        diagnostics.analyze_probability(frame, reference, repetitions=10)


def test_reference_missing_optional_metadata_rejected(frame):
    with pytest.raises(ValueError, match="matched-reference-cohort"):
        diagnostics.analyze_probability(frame, frame.drop(columns="surface"), repetitions=10)


@pytest.mark.parametrize("column,value,error", [
    ("odds", np.nan, "invalid-odds"), ("odds", np.inf, "invalid-odds"),
    ("odds", .99, "invalid-odds"), ("odds", "2.5", "invalid-odds"),
    ("odds", True, "invalid-odds"), ("probability", 0., "invalid-probability"),
    ("probability", 1., "invalid-probability"), ("probability", np.nan, "invalid-probability"),
    ("horse_number", True, "invalid-horse_number"), ("horse_number", 1.5, "invalid-horse_number"),
    ("finish", 0., "invalid-finish"), ("finish", 1.1, "invalid-finish"),
    ("race_date", "2025-01-05", "invalid-race_date"),
    ("race_date", "20250230", "invalid-race_date"),
    ("race_id", "", "invalid-race_id"), ("surface", None, "invalid-surface"),
])
def test_invalid_fields_fail_closed(frame, column, value, error):
    frame[column] = frame[column].astype(object)
    frame.loc[0, column] = value
    with pytest.raises(ValueError, match=error):
        diagnostics.analyze_probability(frame, repetitions=10)


def test_incomplete_race_rejected(frame):
    with pytest.raises(ValueError, match="whole-race"):
        diagnostics.analyze_probability(frame.drop(index=0), repetitions=10)


def test_duplicate_runner_rejected(frame):
    with pytest.raises(ValueError, match="duplicate-runner"):
        diagnostics.analyze_probability(pd.concat([frame, frame.iloc[:1]]), repetitions=10)


def test_probabilities_not_silently_normalized(frame):
    frame.loc[0, "probability"] += .01
    with pytest.raises(ValueError, match="race-normalized"):
        diagnostics.analyze_probability(frame, repetitions=10)


def test_dead_heat_not_partially_removed(frame):
    frame.loc[1, "finish"] = 1
    with pytest.raises(ValueError, match="single-winner"):
        diagnostics.analyze_probability(frame, repetitions=10)


def test_constant_probabilities_keep_race_and_zero_spearman(frame):
    frame["probability"] = 1 / 3
    result = diagnostics.analyze_probability(frame, repetitions=10)
    assert result["spearman"] == 0. and result["constant_rank_races"] == 5
    assert result["race_count"] == 5


def test_fixed_odds_boundaries_left_inclusive(frame):
    frame["odds"] = np.resize([1., 3., 5., 10., 20., 30.], len(frame))
    result = diagnostics.analyze_probability(frame, repetitions=20)
    records = result["odds_band_calibration"]
    assert [record["odds_band"] for record in records] == list(diagnostics.ODDS_LABELS)
    assert [record["count"] for record in records] == [3, 3, 3, 2, 2, 2]
    assert sum(record["wins"] for record in records) == 5
    assert sum(record["count"] for record in records) == len(frame)


def test_calibration_is_prediction_minus_actual(frame):
    result = diagnostics.analyze_probability(frame, repetitions=20)
    first = result["odds_band_calibration"][0]
    values = frame.loc[frame.odds.lt(3)]
    assert first["mean_probability"] == pytest.approx(values.probability.mean())
    assert first["observed_win_rate"] == pytest.approx(values.finish.eq(1).mean())
    assert first["bias_predicted_minus_actual"] == pytest.approx(values.probability.mean() - values.finish.eq(1).mean())
    assert first["count"] == 5


def test_selection_uses_only_keys_and_independent_cohort(frame):
    keys = frame.loc[[0, 4, 11], ["race_id", "horse_number", "race_date"]].copy()
    expected = diagnostics.selection_calibration(frame, keys, repetitions=50)
    poison = keys.assign(probability=100., odds=np.nan, finish=-50, payout=999999)
    assert diagnostics.selection_calibration(frame, poison, repetitions=50) == expected
    assert expected["selected_bet_count"] == 3 and expected["selected_race_count"] == 3
    assert expected["date_count"] == 4 and expected["selected_date_count"] == 2
    assert expected["eligible_no_bet_dates_included"] is True
    assert expected["overall"]["wins"] == 2
    assert expected["overall"]["observed_win_rate"] == pytest.approx(2 / 3)
    json.dumps(expected, allow_nan=False)


def test_multiple_bets_in_same_race_allowed_unique_runner(frame):
    keys = frame.loc[:2, ["race_id", "horse_number"]]
    result = diagnostics.selection_calibration(frame, keys, repetitions=50)
    assert result["selected_bet_count"] == 3 and result["selected_race_count"] == 1
    assert result["overall"]["observed_win_rate"] == pytest.approx(1 / 3)


def test_zero_bets_null_not_imputed_or_100_percent(frame):
    result = diagnostics.selection_calibration(frame, frame.iloc[:0][["race_id", "horse_number"]], repetitions=20)
    assert result["overall"]["count"] == 0
    assert result["overall"]["mean_probability"] is None
    assert result["overall"]["observed_win_rate"] is None
    assert result["overall"]["bias_ci95"] is None
    assert result["overall"]["bootstrap_samples_with_observations"] == 0
    assert all(record["count"] == 0 for record in result["odds_band_calibration"])
    assert result["date_count"] == 4


def test_selected_keys_unknown_rejected(frame):
    bets = frame.iloc[:1][["race_id", "horse_number"]].copy()
    bets.loc[0, "horse_number"] = 18
    with pytest.raises(ValueError, match="outside-cohort"):
        diagnostics.selection_calibration(frame, bets, repetitions=10)


def test_selected_date_mismatch_rejected(frame):
    bets = frame.iloc[:1][["race_id", "horse_number", "race_date"]].copy()
    bets["race_date"] = "20260111"
    with pytest.raises(ValueError, match="selected-date-mismatch"):
        diagnostics.selection_calibration(frame, bets, repetitions=10)


def test_selected_duplicate_rejected(frame):
    bets = frame.iloc[:1][["race_id", "horse_number"]]
    with pytest.raises(ValueError, match="duplicate-selected"):
        diagnostics.selection_calibration(frame, pd.concat([bets, bets]), repetitions=10)


def test_selection_deterministic_order_independent(frame):
    bets = frame.loc[[0, 4, 11], ["race_id", "horse_number"]]
    expected = diagnostics.selection_calibration(frame, bets, repetitions=120)
    observed = diagnostics.selection_calibration(frame.sample(frac=1, random_state=2),
                                                  bets.sample(frac=1, random_state=1), repetitions=120)
    assert expected == observed


@pytest.mark.parametrize("repetitions,seed", [(1, 1), (True, 1), (20.5, 1), (20, -1), (20, True)])
def test_bad_bootstrap_arguments(frame, repetitions, seed):
    with pytest.raises(ValueError, match="bootstrap"):
        diagnostics.analyze_probability(frame, repetitions=repetitions, seed=seed)
    with pytest.raises(ValueError, match="bootstrap"):
        diagnostics.selection_calibration(frame, frame.iloc[:0][["race_id", "horse_number"]],
                                          repetitions=repetitions, seed=seed)


def test_no_bet_dates_influence_resampling_but_not_point_mean(frame):
    keys = frame.loc[:2, ["race_id", "horse_number"]]
    result = diagnostics.selection_calibration(frame, keys, repetitions=200)
    assert result["date_count"] == 4
    assert result["overall"]["bootstrap_samples_with_observations"] < 200
    assert result["overall"]["mean_probability"] == pytest.approx(1 / 3)

