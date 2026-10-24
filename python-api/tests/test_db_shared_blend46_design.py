"""Synthetic, read-only design tests; never train or touch active artifacts."""
from copy import deepcopy
import importlib.util
import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

from training.db_shared_features import FEATURE_COLUMNS


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/db_shared_blend46_design.py"
spec = importlib.util.spec_from_file_location("fixture_db_shared_blend46_design", SCRIPT)
design = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = design
spec.loader.exec_module(design)


def target_fixture():
    return pd.DataFrame([
        {"race_id": f"r{date}", "race_date": date, "horse_number": number,
         "venue": "05", "surface": "芝", "num_horses": 2}
        for date in ("20130101", "20141231", "20150101", "20151231", "20160101", "20161231", "20170101")
        for number in (1, 2)
    ])


def records_fixture():
    rows = []
    for year in (2016, 2017):
        for date, runners, rho, squared in ((f"{year}0101", 2, .4, 8.), (f"{year}1231", 18, .6, 0.)):
            for strategy, delta, scale in (("global", 0., 1.), ("venue_only", .01, .9), ("blend50", .02, .8)):
                confidence = .6 if runners == 2 else .3
                rows.append({"fold_id": f"Y{year}", "test_year": year, "split": "TEST", "strategy": strategy,
                             "race_id": f"r{date}", "race_date": date, "venue": "05", "surface": "芝",
                             "runner_count": runners, "rank_correlation": rho + delta,
                             "squared_speed_error_sum": squared * scale,
                             "fallback_to_global": False, "constant_score_or_result": False,
                             "absolute_error_sum": squared / 2., "probability_eligible": True,
                             "nll": 1.2 if runners == 2 else 2.1, "brier": .7,
                             "uniform_nll": math.log(runners), "uniform_brier": 1. - 1. / runners,
                             "top1_confidence": confidence, "top1_correct": int(runners == 2),
                             "selection_bin": int(confidence * 10)})
    return pd.DataFrame(rows)


def test_canonical46_matches_normal47_minus_preselected_feature_in_original_order():
    expected = tuple(name for name in FEATURE_COLUMNS if name != "safe_past5_top3_rate")
    assert len(design.CANONICAL46) == 46
    assert design.validate_feature_columns(list(expected)) == expected
    assert not any(word in name for name in expected for word in ("odds", "market", "popularity", "payout"))


@pytest.mark.parametrize("change", [lambda c: c[::-1], lambda c: c[:-1], lambda c: [*c, c[0]],
                                    lambda c: ["unknown", *c[1:]], lambda c: "not-list"])
def test_canonical_features_cannot_be_changed_after_design(change):
    with pytest.raises(ValueError, match="canonical-feature"):
        design.validate_feature_columns(change(list(design.CANONICAL46)))


def test_fixed_annual_folds_exclude_cal_and_test_from_train_and_cap_final_test():
    folds = design.build_folds()
    assert len(folds) == 11 and [row["test_year"] for row in folds] == list(range(2016, 2027))
    assert folds[0] == {"fold_id": "Y2016", "test_year": 2016, "train_date_from": "20130101",
                        "train_date_to": "20141231", "cal_date_from": "20150101", "cal_date_to": "20151231",
                        "test_date_from": "20160101", "test_date_to": "20161231"}
    assert folds[-1]["test_date_to"] == "20260913"
    assert all(row["train_date_to"] < row["cal_date_from"] <= row["cal_date_to"] < row["test_date_from"]
               for row in folds)


@pytest.mark.parametrize("field,value", [("train_date_to", "20151231"), ("cal_date_to", "20160101"),
                                        ("test_date_to", "20161230"), ("fold_id", "unlocked"),
                                        ("test_year", 2015), ("unexpected", True)])
def test_changed_fold_is_rejected(field, value):
    fold = design.build_folds()[0]
    fold[field] = value
    with pytest.raises(ValueError, match="fixed-annual-fold"):
        design.validate_fold(fold)


def test_target_split_returns_integer_positions_and_preserves_all_runners():
    target = target_fixture()
    target.index = np.arange(len(target)) * 10
    result = design.validate_fold_target(target, design.build_folds()[0])
    assert list(result) == ["TRAIN", "CAL", "TEST"]
    assert all(index.dtype.kind == "i" and len(index) == 4 for index in result.values())
    assert list(target.iloc[result["CAL"]].race_date.unique()) == ["20150101", "20151231"]
    assert not set(np.concatenate(list(result.values()))) & {12, 13}


@pytest.mark.parametrize("reason", ["date_format", "date_invalid", "date_missing", "date_inconsistent",
                                    "venue_inconsistent", "duplicate", "fractional_horse", "zero_horse",
                                    "missing_horse", "empty_race_id", "partial_race", "singleton_race",
                                    "missing_cal", "missing_train", "missing_test", "missing_columns"])
def test_target_split_rejects_bad_keys_incomplete_races_or_empty_partitions(reason):
    frame = target_fixture()
    if reason == "fractional_horse": frame["horse_number"] = frame.horse_number.astype(object)
    if reason == "date_format": frame.loc[0:1, "race_date"] = "2013-01-01"
    elif reason == "date_invalid": frame.loc[0:1, "race_date"] = "20130230"
    elif reason == "date_missing": frame.loc[0, "race_date"] = None
    elif reason == "date_inconsistent": frame.loc[0, "race_date"] = "20130102"
    elif reason == "venue_inconsistent": frame.loc[0, "venue"] = "06"
    elif reason == "duplicate": frame = pd.concat([frame, frame.iloc[:1]], ignore_index=True)
    elif reason == "fractional_horse": frame.loc[0, "horse_number"] = 1.5
    elif reason == "zero_horse": frame.loc[0, "horse_number"] = 0
    elif reason == "missing_horse": frame.loc[0, "horse_number"] = None
    elif reason == "empty_race_id": frame.loc[0, "race_id"] = ""
    elif reason == "partial_race": frame.loc[0, "num_horses"] = 3
    elif reason == "singleton_race": frame = frame.iloc[1:]
    elif reason == "missing_cal": frame = frame.loc[~frame.race_date.str.startswith("2015")]
    elif reason == "missing_train": frame = frame.loc[frame.race_date.ge("20150101")]
    elif reason == "missing_test": frame = frame.loc[~frame.race_date.str.startswith("2016")]
    elif reason == "missing_columns": frame = frame.drop(columns="horse_number")
    with pytest.raises(ValueError):
        design.validate_fold_target(frame, design.build_folds()[0])


def test_specialist_thresholds_are_train_counts_with_explicit_global_fallback():
    frame = target_fixture()
    failed = design.specialist_eligibility(frame)
    assert failed["available"] is False and len(failed["reason_codes"]) == 3
    passed = design.specialist_eligibility(frame, minimum_races=7, minimum_runners=14, minimum_dates=7)
    assert passed["available"] is True and not passed["reason_codes"]
    empty = design.specialist_eligibility(frame.iloc[:0])
    assert empty["training_race_count"] == 0 and empty["available"] is False


@pytest.mark.parametrize("minimum", [0, -1, True, 2.5, "5"])
def test_specialist_thresholds_cannot_be_invalid(minimum):
    with pytest.raises(ValueError):
        design.specialist_eligibility(target_fixture(), minimum_races=minimum)


def test_accuracy_pools_physical_sse_with_runner_weights_not_average_race_rmse():
    frame = records_fixture()
    validated = design.validate_metric_cohort(frame)
    assert len(validated) == 12
    summary = design.accuracy_summary(validated.loc[validated.strategy.eq("global")])
    assert summary["rank_correlation"] == pytest.approx(.5)
    assert summary["rmse_speed_mps"] == pytest.approx(math.sqrt(16 / 40))
    assert summary["race_count"] == 4 and summary["runner_count"] == 40
    assert summary["mae_speed_mps"] == pytest.approx(8 / 40)
    assert summary["rmse_speed_mps"] != pytest.approx(1.)


@pytest.mark.parametrize("reason", ["missing_strategy", "missing_race", "duplicate", "unknown_strategy",
                                    "unknown_fold", "date_format", "date_invalid", "cal_leak", "mixed_split",
                                    "year_mismatch", "date_mismatch", "venue_mismatch", "surface_mismatch",
                                    "runner_mismatch", "nan", "infinity", "numeric_string", "negative_sse",
                                    "bad_rho", "fractional_runner", "singleton_runner", "missing_column"])
def test_metric_cohort_rejects_partial_different_or_leaked_records(reason):
    frame = records_fixture()
    if reason == "numeric_string": frame["rank_correlation"] = frame.rank_correlation.astype(object)
    if reason == "fractional_runner": frame["runner_count"] = frame.runner_count.astype(object)
    index = frame.index[frame.strategy.eq("blend50")][0]
    if reason == "missing_strategy": frame = frame.loc[~frame.strategy.eq("blend50")]
    elif reason == "missing_race": frame = frame.drop(index=index)
    elif reason == "duplicate": frame = pd.concat([frame, frame.iloc[:1]], ignore_index=True)
    elif reason == "unknown_strategy": frame.loc[index, "strategy"] = "blend70"
    elif reason == "unknown_fold": frame.loc[index, "fold_id"] = "Y2015"
    elif reason == "date_format": frame.loc[index, "race_date"] = "2016-01-01"
    elif reason == "date_invalid": frame.loc[index, "race_date"] = "20160230"
    elif reason == "cal_leak": frame.loc[index, "race_date"] = "20151231"
    elif reason == "mixed_split": frame.loc[index, "split"] = "CAL"
    elif reason == "year_mismatch": frame.loc[index, "test_year"] = 2017
    elif reason == "date_mismatch": frame.loc[index, "race_date"] = "20160102"
    elif reason == "venue_mismatch": frame.loc[index, "venue"] = "06"
    elif reason == "surface_mismatch": frame.loc[index, "surface"] = "ダ"
    elif reason == "runner_mismatch": frame.loc[index, "runner_count"] = 3
    elif reason == "nan": frame.loc[index, "rank_correlation"] = np.nan
    elif reason == "infinity": frame.loc[index, "squared_speed_error_sum"] = np.inf
    elif reason == "numeric_string": frame.loc[index, "rank_correlation"] = ".5"
    elif reason == "negative_sse": frame.loc[index, "squared_speed_error_sum"] = -1
    elif reason == "bad_rho": frame.loc[index, "rank_correlation"] = 1.1
    elif reason == "fractional_runner": frame.loc[index, "runner_count"] = 2.5
    elif reason == "singleton_runner": frame.loc[index, "runner_count"] = 1
    elif reason == "missing_column": frame = frame.drop(columns="squared_speed_error_sum")
    with pytest.raises(ValueError):
        design.validate_metric_cohort(frame)


def test_all_eleven_folds_check_is_explicit_not_silently_satisfied_by_partial_study():
    with pytest.raises(ValueError, match="all-eleven"):
        design.validate_metric_cohort(records_fixture(), require_all_folds=True)


def test_cal_metrics_must_be_explicit_and_cannot_enter_primary_comparisons():
    frame = records_fixture()
    frame["split"] = "CAL"
    frame["race_date"] = frame.race_date.map(lambda value: str(int(value[:4]) - 1) + value[4:])
    assert len(design.validate_metric_cohort(frame, split="CAL")) == 12
    with pytest.raises(ValueError):
        design.paired_comparisons(frame, repetitions=100)


def test_cohort_hash_is_order_and_strategy_independent_but_sensitive_to_runner_cohort():
    frame = records_fixture()
    baseline = frame.loc[frame.strategy.eq("global")]
    candidate = frame.loc[frame.strategy.eq("blend50")]
    assert design.cohort_hash(baseline) == design.cohort_hash(candidate.sample(frac=1, random_state=42))
    changed = candidate.copy()
    changed.loc[changed.index[0], "runner_count"] = 4
    assert design.cohort_hash(changed) != design.cohort_hash(baseline)


def test_probabilities_pool_races_recompute_top_bins_and_do_not_grant_ev_authority():
    frame = records_fixture().loc[lambda row: row.strategy.eq("blend50")]
    summary = design.probability_summary(frame)
    assert summary["race_count"] == summary["total_races"] == 4
    assert summary["nll"] == pytest.approx(1.65)
    assert summary["selection_ece"] == pytest.approx(.35)
    assert summary["selection_bias"] == pytest.approx(-.05)
    assert summary["selection_absolute_bias"] == pytest.approx(.05)
    assert summary["eligible_for_ev"] is False and summary["probability_authority_created"] is False
    assert sum(row["count"] for row in summary["selection_calibration_bins"]) == 4
    assert summary["selection_calibration_bins"][6]["observed_win_rate"] == 1.


def test_probability_coverage_preserves_deadheats_or_unavailable_calibration_as_denominator():
    frame = records_fixture().loc[lambda row: row.strategy.eq("global")].copy()
    index = frame.index[0]
    frame.loc[index, "probability_eligible"] = False
    for name in design._PROBABILITY_COLUMNS - {"probability_eligible"}:
        frame.loc[index, name] = np.nan
    summary = design.probability_summary(frame)
    assert summary["total_races"] == 4 and summary["race_count"] == 3
    assert summary["eligible_race_coverage"] == pytest.approx(.75)
    assert summary["eligible_runner_coverage"] == pytest.approx(38 / 40)
    assert summary["ineligible_races"] == 1


def test_probability_unavailable_entire_fold_reports_missing_not_zero_precision():
    frame = records_fixture().loc[lambda row: row.strategy.eq("global")].copy()
    frame["probability_eligible"] = False
    for name in design._PROBABILITY_COLUMNS - {"probability_eligible"}:
        frame[name] = np.nan
    summary = design.probability_summary(frame)
    assert summary["race_count"] == 0 and summary["eligible_race_coverage"] == 0
    assert summary["nll"] is summary["brier"] is summary["selection_ece"] is None
    assert summary["eligible_cohort_sha256"] is None


@pytest.mark.parametrize("field,value", [("nll", np.nan), ("nll", np.inf), ("nll", -1.),
                                        ("brier", -1.), ("brier", 3.), ("brier", "0.5"),
                                        ("top1_confidence", .1), ("top1_confidence", 1.1),
                                        ("top1_correct", .5), ("uniform_nll", .01),
                                        ("uniform_brier", .01), ("selection_bin", 8),
                                        ("probability_eligible", 1)])
def test_probability_fields_cannot_silently_drop_nan_or_invent_uniform_baselines(field, value):
    frame = records_fixture().loc[lambda row: row.strategy.eq("global")].copy()
    frame[field] = frame[field].astype(object)
    frame.loc[frame.index[0], field] = value
    with pytest.raises(ValueError):
        design.probability_summary(frame)


def test_pooled_ece_is_not_mean_fold_ece_when_biases_cancel_within_same_bin():
    frame = records_fixture().loc[lambda row: row.strategy.eq("global")].copy()
    frame["top1_confidence"] = .6
    frame["selection_bin"] = 6
    frame["top1_correct"] = frame.test_year.eq(2016).astype(int)
    pooled = design.probability_summary(frame)
    annual = [design.probability_summary(group)["selection_ece"] for _, group in frame.groupby("test_year")]
    assert pooled["selection_ece"] == pytest.approx(.1)
    assert np.mean(annual) == pytest.approx(.5)


def test_top_bin_includes_one_probability_once():
    frame = records_fixture().loc[lambda row: row.strategy.eq("global")].copy()
    frame["top1_confidence"] = 1.
    frame["selection_bin"] = 9
    summary = design.probability_summary(frame)
    assert summary["selection_calibration_bins"][9]["count"] == 4
    assert sum(row["count"] for row in summary["selection_calibration_bins"]) == 4


def test_probability_top1_outcome_is_used_instead_of_raw_score_tie_outcome_when_present():
    frame = records_fixture().loc[lambda row: row.strategy.eq("global")].copy()
    frame["probability_top1_correct"] = 0.
    summary = design.probability_summary(frame)
    assert summary["selection_bias"] == pytest.approx(.45)
    assert summary["selection_ece"] == pytest.approx(.45)
    frame.loc[frame.index[0], "probability_top1_correct"] = np.nan
    with pytest.raises(ValueError, match="eligible-probability-fields"):
        design.probability_summary(frame)


def test_primary_bootstrap_is_paired_by_date_year_and_reproducible_with_four_delta_family():
    frame = records_fixture()
    first = design.paired_comparisons(frame, repetitions=100, seed=123)
    second = design.paired_comparisons(frame.sample(frac=1., random_state=4), repetitions=100, seed=123)
    assert first == second
    assert [row["strategy"] for row in first] == ["venue_only", "blend50"]
    for row in first:
        assert row["primary_comparison_family_size"] == 4
        assert row["bootstrap98_75"]["confidence"] == .9875
        assert row["bootstrap95"]["year_strata"] == 2
        assert row["bootstrap95"]["date_count"] == 4
        assert row["bootstrap95"]["resampling"] == "paired whole race dates; annual TEST strata"
        assert row["bootstrap95"]["delta_rank_correlation_ci"] == pytest.approx([row["delta_rank_correlation"]] * 2)
        assert row["positive_rmse_delta_is_worse"] is True and row["delta_rmse_speed_mps"] < 0
        assert row["fresh_unknown_validation"] is False and row["feature_selection_independent_of_history"] is False


@pytest.mark.parametrize("repetitions,seed", [(99, 42), (True, 42), (100., 42), (100, True), (100, -1), (100, "1")])
def test_bootstrap_invalid_options_rejected(repetitions, seed):
    with pytest.raises(ValueError):
        design.paired_comparisons(records_fixture(), repetitions=repetitions, seed=seed)


def test_single_date_reports_insufficient_uncertainty_without_inventing_ci():
    frame = records_fixture().loc[lambda row: row.race_date.eq("20160101")]
    for row in design.paired_comparisons(frame, repetitions=100):
        assert row["bootstrap95"]["status"] == "insufficient_dates"
        assert row["bootstrap95"]["delta_rank_correlation_ci"] is None


def test_protocol_hash_is_canonical_and_nonfinite_values_not_allowed():
    assert design.canonical_hash({"b": 2, "a": 1}) == design.canonical_hash({"a": 1, "b": 2})
    assert design.canonical_hash({"a": 1}) != design.canonical_hash({"a": 2})
    with pytest.raises(ValueError):
        design.canonical_hash({"a": np.nan})


def test_multiple_strategy_summary_cannot_double_count_same_races():
    with pytest.raises(ValueError, match="one-strategy"):
        design.accuracy_summary(records_fixture())
    with pytest.raises(ValueError, match="one-strategy"):
        design.probability_summary(records_fixture())


def test_probability_comparisons_pair_same_races_date_clusters_and_race_macro_metrics():
    frame = records_fixture()
    frame.loc[frame.strategy.eq("blend50"), "nll"] -= .1
    frame.loc[frame.strategy.eq("blend50"), "brier"] -= .05
    rows = design.probability_comparisons(frame, repetitions=100, seed=42)
    assert rows == design.probability_comparisons(frame.sample(frac=1., random_state=8), repetitions=100, seed=42)
    assert [row["strategy"] for row in rows] == ["venue_only", "blend50"]
    assert rows[1]["delta_nll"] == pytest.approx(-.1)
    assert rows[1]["delta_brier"] == pytest.approx(-.05)
    assert rows[1]["bootstrap95"]["delta_nll_ci"] == pytest.approx([-.1, -.1])
    assert rows[1]["bootstrap95"]["delta_brier_ci"] == pytest.approx([-.05, -.05])
    assert rows[1]["bootstrap95"]["year_strata"] == 2
    assert rows[1]["secondary_exploratory_unadjusted"] is True
    assert rows[1]["in_primary_accuracy_comparison_family"] is False
    assert rows[1]["eligible_for_ev"] is False


def test_probability_comparison_never_uses_runner_weights():
    frame = records_fixture()
    frame.loc[frame.strategy.eq("blend50") & frame.runner_count.eq(2), "nll"] += .2
    frame.loc[frame.strategy.eq("blend50") & frame.runner_count.eq(18), "nll"] -= .2
    blend = design.probability_comparisons(frame, repetitions=100)[1]
    assert blend["delta_nll"] == pytest.approx(0.)
    assert blend["delta_nll"] != pytest.approx(-.16)  # Incorrect runner weighting.


def test_probability_comparison_mismatched_eligible_races_fail_instead_of_intersection_drop():
    frame = records_fixture()
    frame.loc[frame.index[2], "probability_eligible"] = False
    with pytest.raises(ValueError, match="exact-shared-eligible-cohort"):
        design.probability_comparisons(frame, repetitions=100)


def test_probability_comparison_same_deadheat_excluded_whole_race_keeps_denominator():
    frame = records_fixture()
    excluded = frame.race_id.eq("r20160101")
    frame.loc[excluded, "probability_eligible"] = False
    for name in design._PROBABILITY_COLUMNS - {"probability_eligible"}:
        frame.loc[excluded, name] = np.nan
    for row in design.probability_comparisons(frame, repetitions=100):
        assert row["race_count"] == 3 and row["total_races"] == 4
        assert row["eligible_race_coverage"] == pytest.approx(.75)


def test_probability_comparison_no_eligible_records_report_missing_not_fake_zero():
    frame = records_fixture()
    frame["probability_eligible"] = False
    for name in design._PROBABILITY_COLUMNS - {"probability_eligible"}:
        frame[name] = np.nan
    for row in design.probability_comparisons(frame, repetitions=100):
        assert row["delta_nll"] is row["delta_brier"] is None
        assert row["bootstrap95"]["status"] == "no_eligible_races"


def test_probability_comparison_missing_eligible_nll_fails_before_bootstrap():
    frame = records_fixture()
    frame.loc[frame.index[2], "nll"] = np.nan
    with pytest.raises(ValueError, match="eligible-probability-fields"):
        design.probability_comparisons(frame, repetitions=100)


def test_probability_comparison_cannot_use_cal_to_estimate_test_improvement():
    frame = records_fixture()
    frame["split"] = "CAL"
    with pytest.raises(ValueError, match="mixed-or-leaked"):
        design.probability_comparisons(frame, repetitions=100)
