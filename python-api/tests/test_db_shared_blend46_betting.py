"""Fail-closed and fixed-protocol tests for retrospective settlement research."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import db_shared_blend46_betting as betting


def cohort(dates=("20250105", "20260105"), winners=None):
    rows = []
    for race, date in enumerate(dates):
        winner = (winners or [1] * len(dates))[race]
        for horse in range(1, 4):
            rows.append({"race_id": f"{date}-r{race}", "race_date": date, "horse_number": horse,
                         "finish": 1 if horse == winner else 2 + [n for n in range(1, 4) if n != winner].index(horse),
                         "odds": (2.5, 4., 6.)[horse - 1], "probability": (.5, .3, .2)[horse - 1],
                         "raw_score": (1.1, .7, .3)[horse - 1], "venue": "05", "surface": "turf",
                         "expected_runner_count": 3,
                         "tansho_payout": (260., 420., 630.)[horse - 1] if horse == winner else np.nan})
    return pd.DataFrame(rows)


@pytest.fixture
def frame():
    return cohort()


def test_valid_complete_cohort(frame):
    assert betting.validate_cohort(frame) == {"runner_count": 6, "race_count": 2, "date_count": 2,
                                              "date_from": "20250105", "date_to": "20260105"}


@pytest.mark.parametrize("column,value,error", [
    ("odds", np.nan, "valid-odds"), ("odds", np.inf, "valid-odds"), ("odds", .9, "valid-odds"),
    ("odds", True, "valid-odds"), ("odds", "2.5", "valid-odds"),
    ("probability", np.nan, "valid-probability"), ("probability", 0., "valid-probability"),
    ("probability", 1., "valid-probability"), ("probability", -1., "valid-probability"),
    ("raw_score", np.inf, "valid-score"), ("raw_score", np.nan, "valid-score"),
    ("horse_number", 0, "valid-runner"), ("horse_number", 1.5, "valid-runner"),
    ("horse_number", True, "valid-runner"), ("expected_runner_count", 1, "valid-runner"),
    ("finish", 0, "valid-finish"), ("finish", np.nan, "valid-finish"), ("finish", True, "valid-finish"),
    ("venue", "", "valid-venue"), ("surface", None, "valid-surface"),
    ("race_id", "", "valid-race_id"), ("race_date", "2025-01-05", "valid-yyyymmdd"),
    ("race_date", "20250230", "valid-yyyymmdd"), ("race_date", 20250105, "valid-yyyymmdd"),
])
def test_invalid_fields_fail_closed(frame, column, value, error):
    frame[column] = frame[column].astype(object)
    frame.loc[0, column] = value
    with pytest.raises(ValueError, match=error):
        betting.validate_cohort(frame)


def test_incomplete_race_not_silently_repaired(frame):
    with pytest.raises(ValueError, match="whole-race"):
        betting.validate_cohort(frame.drop(index=1))


def test_duplicate_runner_not_silently_removed(frame):
    with pytest.raises(ValueError, match="duplicate-runner"):
        betting.validate_cohort(pd.concat([frame, frame.iloc[[0]]]))


@pytest.mark.parametrize("column,value", [("race_date", "20250106"), ("venue", "06"),
                                         ("surface", "dirt"), ("expected_runner_count", 4)])
def test_race_metadata_consistent(frame, column, value):
    frame.loc[1, column] = value
    with pytest.raises(ValueError, match="inconsistent-race"):
        betting.validate_cohort(frame)


def test_probabilities_must_sum_one(frame):
    frame.loc[0, "probability"] += .01
    with pytest.raises(ValueError, match="race-normalized"):
        betting.validate_cohort(frame)


@pytest.mark.parametrize("winner_count", [0, 2])
def test_whole_deadheat_or_no_winner_refused(frame, winner_count):
    frame.loc[:2, "finish"] = [2, 3, 3] if winner_count == 0 else [1, 1, 2]
    with pytest.raises(ValueError, match="single-winner"):
        betting.validate_cohort(frame)


@pytest.mark.parametrize("payout", [np.nan, np.inf, 0, 90, 101, 105, True, "260"])
def test_missing_or_invalid_winner_payout_never_odds_fallback(frame, payout):
    frame["tansho_payout"] = frame.tansho_payout.astype(object)
    frame.loc[0, "tansho_payout"] = payout
    with pytest.raises(ValueError, match="winner-payout"):
        betting.settle_bets(frame, betting.select_bets(frame, "top1"))


def test_positive_loser_payout_refused(frame):
    frame.loc[1, "tansho_payout"] = 100
    with pytest.raises(ValueError, match="loser-positive-payout"):
        betting.validate_cohort(frame)


def test_zero_loser_fields_allowed_but_not_used(frame):
    frame.loc[frame.finish.ne(1), "tansho_payout"] = 0.
    assert betting.validate_cohort(frame)["race_count"] == 2


@pytest.mark.parametrize("rule", betting.RULES)
def test_selection_does_not_access_outcomes(frame, rule):
    before = betting.select_bets(frame, rule)
    without = betting.select_bets(frame.drop(columns=["finish", "tansho_payout"]), rule)
    frame["finish"] = "not-even-a-valid-result"
    frame["tansho_payout"] = -np.inf
    after = betting.select_bets(frame, rule)
    pd.testing.assert_frame_equal(before, without)
    pd.testing.assert_frame_equal(before, after)
    assert "finish" not in before and "tansho_payout" not in before
    assert before.stake.eq(100).all()


def test_selection_ties_are_horse_number_stable(frame):
    frame.probability = 1. / 3.
    selected = betting.select_bets(frame.sample(frac=1, random_state=7), "top1")
    assert selected.horse_number.tolist() == [1, 1]


def test_market_inverse_odds_normalized_not_raw_odds(frame):
    expected = np.array([1 / 2.5, 1 / 4, 1 / 6])
    expected /= expected.sum()
    actual = betting.market_probabilities(frame)
    np.testing.assert_allclose(actual, np.tile(expected, 2))
    assert not np.allclose(actual[:3], 1. / frame.odds.to_numpy()[:3])


@pytest.mark.parametrize("rule,horses", [("top1", [1]), ("top1_odds3_20", []),
                                         ("top1_ev105", [1]), ("max_ev105", [1]),
                                         ("max_ev115", [1]), ("top3_ev105", [1, 2, 3])])
def test_six_fixed_rules(frame, rule, horses):
    selected = betting.select_bets(frame.iloc[:3], rule)
    assert selected.horse_number.tolist() == horses


def test_ev_max_ceiling_and_probability_floor(frame):
    frame = frame.iloc[:3].copy()
    frame.odds = [31., 30., 300.]
    frame.probability = [.98, .019, .001]
    assert betting.select_bets(frame, "max_ev105").empty
    assert betting.select_bets(frame, "top1_ev105").horse_number.tolist() == [1]


def test_unknown_rule_refused(frame):
    with pytest.raises(ValueError, match="fixed-rule"):
        betting.select_bets(frame, "tune_until_profitable")


def test_settlement_uses_independent_payout_not_odds(frame):
    bets = betting.select_bets(frame, "top1")
    bets["finish"] = 99
    bets["tansho_payout"] = 999999
    settled = betting.settle_bets(frame, bets)
    assert settled.payout.tolist() == [260., 260.]
    assert settled.profit.tolist() == [160., 160.]
    assert (settled.payout != bets.odds.to_numpy() * 100).all()


@pytest.mark.parametrize("mutation", ["duplicate", "stake", "outside", "invalid_key"])
def test_invalid_tickets_refused(frame, mutation):
    bets = betting.select_bets(frame, "top1")
    if mutation == "duplicate":
        bets = pd.concat([bets, bets.iloc[[0]]])
    elif mutation == "stake":
        bets.loc[0, "stake"] = 200
    elif mutation == "outside":
        bets.loc[0, "horse_number"] = 99
    else:
        bets.loc[0, "horse_number"] = 0
    with pytest.raises(ValueError):
        betting.settle_bets(frame, bets)


def test_empty_bets_valid_without_invented_roi(frame):
    bets = betting.select_bets(frame, "top1_odds3_20")
    summary = betting.summarize_policy(frame, bets)
    assert summary["bets"] == 0 and summary["roi_percent"] is None and summary["hit_rate"] is None
    assert summary["max_drawdown_jpy"] == 0
    assert summary["date_count"] == 2
    assert all(item["stake"] == 0 for item in summary["daily_ledger"])


def test_roi_and_drawdown_chronological_daily_fixed_stake():
    frame = cohort(("20250105", "20250106", "20250107"), winners=[1, 2, 2])
    summary = betting.summarize_policy(frame, betting.select_bets(frame, "top1"))
    assert summary["bets"] == 3 and summary["hits"] == 1
    assert summary["stake"] == 300 and summary["payout"] == 260 and summary["profit"] == -40
    assert summary["roi_percent"] == pytest.approx(86.66666667)
    assert summary["max_drawdown_jpy"] == 200
    assert summary["race_coverage"] == 1 and summary["date_coverage"] == 1
    assert summary["profit_per_eligible_race"] == pytest.approx(-40 / 3)
    assert summary["settlement_source"] == "saved_netkeiba_result_return_table"
    assert summary["payout_independently_verified_with_jra"] is False


def test_feature_matrix_order_entropy_gap_and_index_independent(frame):
    original = betting.feature_matrix(frame)
    altered = frame.copy()
    altered.index = [19, 17, 14, 100, 101, 99]
    np.testing.assert_allclose(betting.feature_matrix(altered), original)
    assert original.shape == (6, 10)
    np.testing.assert_allclose(original[:, 8], .4)
    np.testing.assert_allclose(original[:, 9], -sum(p * np.log(p) for p in (.5, .3, .2)))
    altered["finish"] = object()
    altered["tansho_payout"] = object()
    np.testing.assert_allclose(betting.feature_matrix(altered), original)


def test_market_control_excludes_h04_information(frame):
    original = betting.feature_matrix(frame, market_only=True)
    altered = frame.copy()
    altered.probability = np.tile([.1, .2, .7], 2)
    altered.raw_score = np.arange(6) * 999
    np.testing.assert_array_equal(betting.feature_matrix(altered, market_only=True), original)
    assert original.shape == (6, 4)


@pytest.fixture(scope="module", params=[("logistic", False), ("logistic", True),
                                        ("lightgbm", False), ("lightgbm", True)])
def fitted(request):
    train = cohort(("20160105", "20170105", "20200105"), winners=[1, 2, 1])
    cal = cohort(("20210105", "20220105"), winners=[1, 2])
    return betting.fit_offset_model(train, cal, kind=request.param[0], market_only=request.param[1])


def test_both_offset_model_serialization_reload_parity(fitted, frame):
    serialized = json.loads(json.dumps(fitted, allow_nan=False))
    before, after = betting.predict_offset_model(fitted, frame), betting.predict_offset_model(serialized, frame)
    np.testing.assert_allclose(before, after, atol=0, rtol=0)
    assert np.all(before > 0) and np.all(before < 1)
    np.testing.assert_allclose(before.reshape(2, 3).sum(axis=1), 1)
    assert serialized["train"]["date_to"] < serialized["cal"]["date_from"]
    assert serialized["objective_description"] == betting.OBJECTIVE_DESCRIPTION
    assert serialized["eligible_for_ev"] is False and serialized["artifact_published"] is False
    assert serialized["calibration_audit"]["test_outcomes_consumed"] is False
    assert .5 <= serialized["temperature"] <= 2.


def test_training_receipts_exclude_cal_and_test(fitted):
    assert fitted["train"]["date_from"] == "20160105"
    assert fitted["train"]["date_to"] == "20200105"
    assert fitted["cal"]["date_from"] == "20210105"
    assert fitted["cal"]["date_to"] == "20220105"


@pytest.mark.parametrize("train_date,cal_date", [("20210105", "20220105"), ("20160105", "20230105"),
                                               ("20150105", "20210105"), ("20250105", "20210105")])
def test_fit_period_boundary_strict(train_date, cal_date):
    with pytest.raises(ValueError, match="period-required"):
        betting.fit_offset_model(cohort((train_date,)), cohort((cal_date,)))


def test_bad_fit_kind_or_market_control():
    train, cal = cohort(("20160105",)), cohort(("20210105",))
    with pytest.raises(ValueError, match="fixed-ai"):
        betting.fit_offset_model(train, cal, kind="optuna")
    with pytest.raises(ValueError, match="market-control"):
        betting.fit_offset_model(train, cal, market_only="yes")


@pytest.mark.parametrize("kind", ["logistic", "lightgbm"])
def test_cal_outcomes_cannot_change_residual_fit_or_encoder(kind):
    train = cohort(("20160105", "20170105", "20200105"), winners=[1, 2, 1])
    a = betting.fit_offset_model(train, cohort(("20210105", "20220105"), winners=[1, 1]), kind=kind)
    b = betting.fit_offset_model(train, cohort(("20210105", "20220105"), winners=[3, 3]), kind=kind)
    fields = ("coefficients", "means", "standard_deviations") if kind == "logistic" else ("model_text",)
    for field in fields:
        assert a[field] == b[field]
    assert a["train_cohort_sha256"] == b["train_cohort_sha256"]
    assert a["cal_cohort_sha256"] != b["cal_cohort_sha256"]


@pytest.mark.parametrize("kind", ["logistic", "lightgbm"])
def test_market_control_fit_and_calibration_never_uses_model_features(kind):
    train = cohort(("20160105", "20200105"), winners=[1, 2])
    cal = cohort(("20210105", "20220105"), winners=[1, 2])
    a = betting.fit_offset_model(train, cal, kind=kind, market_only=True)
    changed_train, changed_cal = train.copy(), cal.copy()
    for frame in (changed_train, changed_cal):
        frame.probability = np.tile([.05, .05, .9], len(frame) // 3)
        frame.raw_score = np.arange(len(frame)) * 10000.
    b = betting.fit_offset_model(changed_train, changed_cal, kind=kind, market_only=True)
    fields = ("coefficients", "means", "standard_deviations") if kind == "logistic" else ("model_text",)
    for field in fields:
        assert a[field] == b[field]
    assert a["temperature"] == b["temperature"]


def test_train_cal_duplicate_race_id_even_with_different_dates_refused():
    train, cal = cohort(("20160105",)), cohort(("20210105",))
    train.race_id = "collision"
    cal.race_id = "collision"
    with pytest.raises(ValueError, match="disjoint-chronology"):
        betting.fit_offset_model(train, cal)


def test_unvalidated_calibrator_cannot_predict(fitted, frame):
    artifact = copy.deepcopy(fitted)
    artifact.pop("temperature")
    with pytest.raises(ValueError, match="calibrated-temperature"):
        betting.predict_offset_model(artifact, frame)
    assert betting.predict_offset_uncalibrated(artifact, frame).shape == (len(frame),)


@pytest.mark.parametrize("fitted", [("logistic", False), ("logistic", True)], indirect=True)
def test_zero_logistic_residual_explicit_market_offset(fitted, frame):
    artifact = copy.deepcopy(fitted)
    artifact["coefficients"] = [0.] * (len(artifact["features"]) + 1)
    np.testing.assert_allclose(betting.predict_offset_uncalibrated(artifact, frame), betting.market_probabilities(frame), atol=1e-14)


@pytest.mark.parametrize("fitted", [("lightgbm", False), ("lightgbm", True)], indirect=True)
def test_tiny_lgbm_no_splits_keeps_market_offset(fitted, frame):
    # min_leaf=200 on 9 training rows: residual has no split or initial-score baseline.
    np.testing.assert_allclose(betting.predict_offset_uncalibrated(fitted, frame), betting.market_probabilities(frame), atol=1e-14)


@pytest.mark.parametrize("mutation", ["publish", "schema", "feature_order", "temperature", "objective"])
def test_invalid_artifacts_cannot_authorize_predictions(fitted, frame, mutation):
    artifact = copy.deepcopy(fitted)
    if mutation == "publish":
        artifact["eligible_for_ev"] = True
    elif mutation == "schema":
        artifact["schema"] = "production"
    elif mutation == "feature_order":
        artifact["features"] = list(reversed(artifact["features"]))
    elif mutation == "temperature":
        artifact["temperature"] = .49
    else:
        artifact["objective_description"] = "multinomial"
    with pytest.raises(ValueError):
        betting.predict_offset_model(artifact, frame)


def test_probability_metrics_all_races_exact_nll_brier(frame):
    metrics = betting.probability_metrics(frame)
    assert metrics["nll"] == pytest.approx(-np.log(.5))
    assert metrics["brier"] == pytest.approx((1 - .5) ** 2 + .3 ** 2 + .2 ** 2)
    assert metrics["top1"] == 1.
    assert metrics["selection_ece"] == .5
    assert metrics["selection_bias"] == -.5
    assert metrics["race_count"] == 2


def test_finish_cannot_exceed_actual_field_size(frame):
    frame.loc[2, "finish"] = 4
    with pytest.raises(ValueError, match="finish-within-runner"):
        betting.validate_cohort(frame)


def test_bootstrap_common_weights_all_dates_and_reproducible():
    frame = cohort(("20250105", "20250106", "20260105", "20260106"), winners=[1, 2, 1, 2])
    policies = {"reference": betting.select_bets(frame, "top1"), "same": betting.select_bets(frame, "top1"),
                "none": betting.select_bets(frame, "top1_odds3_20")}
    a = betting.bootstrap_policy_comparisons(frame, policies, "reference", repetitions=100, seed=20261012)
    b = betting.bootstrap_policy_comparisons(frame, policies, "reference", repetitions=100, seed=20261012)
    assert a == b
    candidates = {item["policy_id"]: item for item in a["candidates"]}
    assert candidates["same"]["paired_profit_difference_ci95"] == [0., 0.]
    assert candidates["same"]["paired_profit_difference_ci_simultaneous"] == [0., 0.]
    assert candidates["none"]["roi_ci95"] is None
    assert candidates["none"]["bootstrap_samples_with_stake"] == 0
    assert all(item["summary"]["date_count"] == 4 for item in a["candidates"])
    assert a["all_cohort_dates_included"] is True and a["common_candidate_weights"] is True
    assert a["simultaneous_confidence"] == pytest.approx(1 - .05 / 31)


@pytest.mark.parametrize("kwargs", [{"repetitions": 1}, {"repetitions": True}, {"family_size": 0},
                                    {"family_size": True}, {"seed": True}])
def test_bootstrap_contract(frame, kwargs):
    policies = {"reference": betting.select_bets(frame, "top1")}
    with pytest.raises(ValueError):
        betting.bootstrap_policy_comparisons(frame, policies, "reference", **kwargs)


def selection_frame(winner=1):
    dates = []
    for year in (2023, 2024):
        for day in range(1, 29):
            dates.extend([f"{year}01{day:02d}"] * 4)
    return cohort(dates, winners=[winner] * len(dates))


def test_selection_frozen_scope_and_profitable_or_no_bet():
    frame = selection_frame()
    bets = {"top1": betting.select_bets(frame, "top1")}
    boot = betting.bootstrap_policy_comparisons(frame, bets, "top1", repetitions=100)
    selected = betting.choose_policy_on_select(frame, bets, boot)
    assert selected["selected_policy_id"] == "top1"
    assert selected["highest_candidate"]["lower_roi_bound"] == 260
    assert selected["test_outcomes_consumed"] is False
    loss = selection_frame(winner=2)
    loss_bets = {"top1": betting.select_bets(loss, "top1")}
    loss_boot = betting.bootstrap_policy_comparisons(loss, loss_bets, "top1", repetitions=100)
    selected_loss = betting.choose_policy_on_select(loss, loss_bets, loss_boot)
    assert selected_loss["selected_policy_id"] is None
    assert selected_loss["highest_candidate"]["policy_id"] == "top1"
    assert selected_loss["no_bet_comparator"]["roi_percent"] is None


def test_selection_refuses_test_and_stale_receipt(frame):
    policies = {"top1": betting.select_bets(frame, "top1")}
    boot = betting.bootstrap_policy_comparisons(frame, policies, "top1", repetitions=100)
    with pytest.raises(ValueError, match="select-period"):
        betting.choose_policy_on_select(frame, policies, boot)
    selected_frame = selection_frame()
    current_policies = {"top1": betting.select_bets(selected_frame, "top1")}
    with pytest.raises(ValueError, match="receipt-mismatch"):
        betting.choose_policy_on_select(selected_frame, current_policies, boot)


def test_selection_requires_bets_each_year_and_dates():
    frame = cohort(("20230105", "20240105"))
    policies = {"top1": betting.select_bets(frame, "top1")}
    boot = betting.bootstrap_policy_comparisons(frame, policies, "top1", repetitions=100)
    selected = betting.choose_policy_on_select(frame, policies, boot)
    assert selected["selected_policy_id"] is None
    assert set(selected["rejected_candidates"][0]["reasons"]) == {
        "fewer_than_200_bets", "fewer_than_20_bet_dates", "fewer_than_30_bets_in_each_selection_year"}


def test_sensitivity_largest_payouts_removed_stake_not_reoptimized():
    frame = cohort(("20250105", "20250106", "20250107", "20250108"))
    frame.loc[frame.finish.eq(1), "tansho_payout"] = [100, 200, 300, 10000]
    result = betting.payout_sensitivity(frame, betting.select_bets(frame, "top1"))
    assert result[0]["roi_percent"] == 150
    assert result[1]["roi_percent"] == 25
    assert result[0]["removed_payout"] == 10000
    assert result[1]["removed_payout"] == 10500
    assert all(item["stake_unchanged"] is True and item["sensitivity_not_new_strategy"] is True for item in result)
