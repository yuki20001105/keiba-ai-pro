"""Isolated venue research contracts; synthetic data, no fitting or network."""
import importlib.util
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

from keiba_ai.speed_deviation import apply_speed_deviation_baseline, fit_speed_deviation_baseline


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/evaluate_db_shared_venues.py"
spec = importlib.util.spec_from_file_location("fixture_db_shared_venue_research", SCRIPT)
venue = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = venue
spec.loader.exec_module(venue)


def race_fixture():
    rows = []
    for day, place in [("20200101", "東京"), ("20200102", "京都")]:
        for number in range(1, 4):
            rows.append({"race_id": day + "0001", "race_date": day, "venue": place,
                         "surface": "turf", "horse_number": number, "finish": number,
                         "time_seconds": 90.0 + number, "distance": 1600,
                         "num_horses": 3, "horse_id": f"horse{number}"})
    return pd.DataFrame(rows)


def paired_fixture():
    return pd.DataFrame({
        "race_date": ["20200101", "20200101", "20210101", "20210101"],
        "year": [2020, 2020, 2021, 2021],
        "rank_correlation": [.8, .8, .8, .8],
        "global_rank_correlation": [.6, .6, .6, .6],
        "squared_error_sum": [1., 1., 1., 1.],
        "global_squared_error_sum": [4., 4., 4., 4.],
        "runner_count": [10, 10, 10, 10],
    })


def test_annual_folds_do_not_see_target_year_and_partial_year_is_capped():
    folds = venue.build_folds(training_start_year=2013, first_test_year=2016,
                              last_test_year=2026, date_to="20260913", rolling_years=5)
    assert len(folds) == 22
    for fold in folds:
        assert fold["train_to"] < fold["test_from"]
        assert fold["test_to"] <= "20260913"
        assert fold["test_from"] == f'{fold["year"]}0101'
        if fold["year"] == 2026:
            assert fold["test_to"] == "20260913"
            assert fold["partial_test_year"] is True
        if fold["window"].startswith("rolling"):
            assert fold["train_from"] == f'{max(2013, fold["year"] - 5)}0101'
        else:
            assert fold["train_from"] == "20130101"


def test_fold_requires_at_least_three_prior_calendar_years():
    with pytest.raises(ValueError):
        venue.build_folds(training_start_year=2013, first_test_year=2015,
                          last_test_year=2026, date_to="20260913", rolling_years=5)


@pytest.mark.parametrize("races,days,runners,allowed", [
    (499, 40, 3000, False), (500, 39, 3000, False),
    (500, 40, 2499, False), (500, 40, 2500, True),
])
def test_specialist_sample_thresholds_are_fixed(races, days, runners, allowed):
    frame = pd.DataFrame({"race_id": [str(i % races) for i in range(runners)],
                          "race_date": [str(i % days) for i in range(runners)]})
    report = venue.specialist_eligibility(frame, minimum_races=500, minimum_dates=40,
                                          minimum_runners=2500)
    assert report["available"] is allowed


def test_physical_error_and_race_rank_have_consistent_units():
    target = race_fixture()
    baseline = fit_speed_deviation_baseline(target, min_group_size=2)
    scores = apply_speed_deviation_baseline(target, baseline).to_numpy()
    records = venue.race_metric_records(target, scores, baseline)
    assert len(records) == 2
    assert records.runner_count.tolist() == [3, 3]
    assert records.rank_correlation.to_numpy() == pytest.approx([1., 1.])
    assert records.squared_error_sum.to_numpy() == pytest.approx([0., 0.], abs=1e-20)
    assert records.top1_correct.to_numpy() == pytest.approx([1., 1.])
    assert records.race_id.tolist() == target.race_id.unique().tolist()


def test_constant_scores_do_not_silently_drop_hard_races():
    target = race_fixture()
    baseline = fit_speed_deviation_baseline(target, min_group_size=2)
    records = venue.race_metric_records(target, np.zeros(len(target)), baseline)
    assert len(records) == 2
    assert records.rank_correlation.tolist() == [0., 0.]
    assert records.constant_score_or_result.tolist() == [True, True]


@pytest.mark.parametrize("scores", [[1.], [np.nan] * 6, [np.inf] * 6])
def test_metrics_reject_invalid_or_misaligned_predictions(scores):
    target = race_fixture()
    baseline = fit_speed_deviation_baseline(target, min_group_size=2)
    with pytest.raises(ValueError):
        venue.race_metric_records(target, np.asarray(scores), baseline)


def test_day_cluster_paired_bootstrap_is_deterministic():
    paired = paired_fixture()
    first = venue.date_cluster_bootstrap(paired, repetitions=100, seed=42, confidence=.95,
                                         stratify_by_year=True)
    second = venue.date_cluster_bootstrap(paired, repetitions=100, seed=42, confidence=.95,
                                          stratify_by_year=True)
    assert first == second
    assert first["delta_rank_correlation_ci"] == pytest.approx([.2, .2])
    assert first["delta_rmse_speed_mps_ci"] == pytest.approx([np.sqrt(.1) - np.sqrt(.4)] * 2)
    assert first["date_count"] == 2


def test_identical_predictions_have_zero_delta_not_a_fake_positive_ci():
    paired = paired_fixture()
    paired["rank_correlation"] = paired.global_rank_correlation
    paired["squared_error_sum"] = paired.global_squared_error_sum
    report = venue.date_cluster_bootstrap(paired, repetitions=100, seed=42, confidence=.95,
                                          stratify_by_year=True)
    assert report["delta_rank_correlation_ci"] == [0., 0.]
    assert report["delta_rmse_speed_mps_ci"] == [0., 0.]


def test_pooled_rmse_uses_runner_sse_not_mean_race_rmse():
    paired = paired_fixture()
    paired["absolute_error_sum"] = 1.
    paired["top1_correct"] = 1
    paired["fallback_to_global"] = False
    paired["constant_score_or_result"] = False
    paired["runner_count"] = [2, 18, 2, 18]
    paired["squared_error_sum"] = [8., 0., 8., 0.]
    paired["global_squared_error_sum"] = [8., 0., 8., 0.]
    report = venue._group_summary(paired)
    assert report["rmse_speed_mps"] == pytest.approx(np.sqrt(16 / 40))
    assert report["rmse_speed_mps"] != pytest.approx(1.)


def strategy_records_fixture():
    target = race_fixture()
    baseline = fit_speed_deviation_baseline(target, min_group_size=2)
    scores = apply_speed_deviation_baseline(target, baseline).to_numpy()
    pieces = []
    for strategy in venue.STRATEGIES:
        records = venue.race_metric_records(target, scores, baseline)
        records["window"] = "expanding"
        records["year"] = 2020
        records["strategy"] = strategy
        records["fallback_to_global"] = False
        pieces.append(records)
    return pd.concat(pieces, ignore_index=True)


def test_pairing_requires_the_identical_race_cohort_for_all_strategies():
    records = strategy_records_fixture()
    paired = venue._paired_frame(records)
    assert len(paired) == 6
    records = records.drop(records.index[records.strategy.eq("venue_only")][0])
    with pytest.raises(ValueError, match="exact-same-races"):
        venue._paired_frame(records)


@pytest.mark.parametrize("column,value", [
    ("runner_count", 4), ("race_date", "20200103"),
    ("venue", "札幌"), ("surface", "dirt"),
])
def test_pairing_rejects_changed_runner_counts_or_metadata(column, value):
    records = strategy_records_fixture()
    position = records.index[records.strategy.eq("venue_only")][0]
    records.loc[position, column] = value
    with pytest.raises(ValueError, match="cohort-or-metadata-mismatch"):
        venue._paired_frame(records)


def test_protocol_rejects_test_driven_mixture_weight_or_preset_changes():
    changed = dict(venue.DEFAULT_PROTOCOL, specialist_weight=.8)
    with pytest.raises(ValueError, match="protocol"):
        venue.validate_protocol(changed)
    changed = dict(venue.DEFAULT_PROTOCOL, preset="lean")
    with pytest.raises(ValueError, match="protocol"):
        venue.validate_protocol(changed)
