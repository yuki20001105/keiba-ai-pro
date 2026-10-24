from __future__ import annotations

import json
import numpy as np
import pandas as pd
import pytest

from market_segments.diagnostics import (
    PROBABILITY_EDGES, PROBABILITY_LABELS, RANK_EDGES, RANK_LABELS,
    add_divergence_columns, case_analysis, case_feature_comparison,
    diagnostic_definition, divergence_analysis, extract_cases,
)


def race(date="20221101", n=8, reverse=True):
    probability = np.arange(n, 0, -1, dtype=float)
    probability /= probability.sum()
    return pd.DataFrame({
        "race_id": date + "0001", "race_date": date, "horse_number": np.arange(1, n+1),
        "expected_runner_count": n, "finish": np.arange(n, 0, -1),
        "odds": 1 / probability, "normalized_market_probability": probability,
        "h04_score": np.arange(n) if reverse else -np.arange(n),
        "h04_probability": probability[::-1] if reverse else probability,
        "tansho_payout": [np.nan] * (n-1) + [800.],
        "market_rank": np.arange(n) + .5, "h04_rank": np.arange(n) + .25,
        "rank_difference": np.arange(n) + .75, "safe_prior_speed": np.arange(n) * 2.,
    })


def test_fixed_right_closed_probability_buckets_zero_in_negative_side():
    values = [-.11, -.10, -.05, -.02, 0, .02, .05, .10, .11]
    got = pd.cut(values, PROBABILITY_EDGES, labels=PROBABILITY_LABELS, right=True).tolist()
    assert got == [PROBABILITY_LABELS[i] for i in [0, 0, 1, 2, 3, 4, 5, 6, 7]]
    assert diagnostic_definition()["probability_right_closed"] is True


def test_fixed_ordinal_rank_buckets_cover_every_integer():
    got = pd.cut([-6, -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6], RANK_EDGES, labels=RANK_LABELS).tolist()
    assert got == [RANK_LABELS[i] for i in [0, 0, 1, 1, 2, 2, 3, 4, 4, 5, 5, 6, 6]]


def test_ordinal_ties_use_horse_number_and_keep_original_model_inputs():
    frame = race()
    frame["normalized_market_probability"] = 1 / 8
    frame["h04_probability"] = 1 / 8
    frame["h04_score"] = 0.
    original = frame.copy(deep=True)
    result = add_divergence_columns(frame.sample(frac=1, random_state=1))
    assert result.market_rank_ordinal.eq(result.horse_number).all()
    assert result.h04_rank_ordinal.eq(result.horse_number).all()
    pd.testing.assert_frame_equal(frame, original)
    for col in ("market_rank", "h04_rank", "rank_difference"):
        assert result.set_index("horse_number")[col].sort_index().equals(frame.set_index("horse_number")[col])


def test_probability_and_rank_signs_are_h04_favoring_positive():
    result = add_divergence_columns(race()).set_index("horse_number")
    assert result.loc[8, "rank_divergence"] == 7
    assert result.loc[8, "probability_divergence"] > 0
    assert result.loc[1, "rank_divergence"] == -7


def test_all_empty_and_nonempty_buckets_are_retained():
    frame = race()
    result = divergence_analysis(frame)
    assert len(result) == 15
    assert result.groupby("divergence_type").horses.sum().eq(len(frame)).all()
    empty = result.loc[result.horses.eq(0)]
    assert len(empty)
    assert empty.actual_win_rate.isna().all()
    assert empty.hypothetical_roi_percent.isna().all()
    assert result.roi_use.str.contains("not_policy").all()


def test_reference_roi_is_all_100_yen_tickets_not_best_horse_policy():
    frame = race(reverse=False)
    result = divergence_analysis(frame)
    zero = result.loc[(result.divergence_type == "rank") & (result.bucket == "0")].iloc[0]
    assert zero.horses == 8 and zero.hypothetical_stake_yen == 800
    assert zero.hypothetical_payout_yen == 800 and zero.hypothetical_roi_percent == 100


def test_missing_winner_official_payout_makes_loser_settlement_unknown_too():
    frame = race()
    frame["tansho_payout"] = np.nan
    result = divergence_analysis(frame)
    nonempty = result.loc[result.horses.gt(0)]
    assert nonempty.hypothetical_roi_percent.isna().all()
    assert nonempty.payout_unknown_horses.eq(nonempty.horses).all()


def test_explicit_unconfirmed_payout_blocks_even_present_number():
    frame = race()
    frame["payout_confirmed_race"] = False
    result = divergence_analysis(frame)
    assert result.hypothetical_roi_percent.isna().all()


def test_case_definitions_and_small_fields():
    result = extract_cases(race())
    assert result.loc[result.case_A, "horse_number"].tolist() == [1, 2, 3]
    assert result.loc[result.case_B, "horse_number"].tolist() == [6, 7, 8]
    assert result.loc[result.case_C, "horse_number"].tolist() == [1, 2, 7, 8]
    assert not result.case_D.any() and not result.case_tied.any()
    small = extract_cases(race(n=5))
    assert not small.case_A.any() and not small.case_B.any()


def test_large_divergence_error_ties_kept_separately():
    frame = race(n=7)
    frame["finish"] = [4, 2, 3, 1, 5, 6, 7]
    result = extract_cases(frame)
    assert result.loc[result.horse_number.eq(1), "case_tied"].item()
    assert not result.loc[result.horse_number.eq(1), ["case_C", "case_D"]].any().any()


def test_nonwinning_official_deadheat_uses_average_true_rank():
    frame = race()
    frame["finish"] = [8, 7, 6, 5, 4, 2, 2, 1]
    result = extract_cases(frame)
    assert result.loc[result.horse_number.isin([6, 7]), "actual_average_rank"].eq(2.5).all()
    assert result.loc[result.horse_number.eq(7), "h04_absolute_rank_error"].item() == .5
    assert len(divergence_analysis(frame)) == 15


def test_invalid_dense_rank_is_not_valid_official_competition_rank():
    frame = race()
    frame["finish"] = [7, 6, 5, 4, 3, 2, 2, 1]
    with pytest.raises(ValueError, match="competition-min"):
        extract_cases(frame)


def test_c_d_feature_smd_coverage_and_json_are_diagnostic_only():
    good = race()
    bad = race("20221102")
    bad["finish"] = bad.horse_number
    bad["tansho_payout"] = np.where(bad.finish.eq(1), 200., np.nan)
    bad["safe_prior_speed"] += 5
    frame = pd.concat([good, bad], ignore_index=True)
    frame.loc[(frame.race_date == "20221102") & frame.horse_number.eq(1), "safe_prior_speed"] = np.nan
    result = case_feature_comparison(frame, ["safe_prior_speed", "safe_absent"])
    assert result.feature.tolist() == ["safe_prior_speed", "safe_absent"]
    assert result.iloc[0].C_coverage == 1.
    assert result.iloc[0].D_coverage == .75
    assert result.iloc[0].standardized_mean_difference < 0
    assert result.iloc[1].status == "unavailable_column"
    report = case_analysis(frame, ["safe_prior_speed", "safe_absent"])
    json.dumps(report, allow_nan=False)
    assert "not_feature_selection" in report["feature_comparison"][0]["interpretation"]


@pytest.mark.parametrize("column", ["finish", "tansho_payout", "case_C"])
def test_outcome_columns_cannot_be_called_feature_comparisons(column):
    with pytest.raises(ValueError, match="nonoutcome"):
        case_feature_comparison(race(), [column])


@pytest.mark.parametrize("bad", ["future", "partial", "duplicate", "deadheat", "dnf", "simplex", "date"])
def test_invalid_or_prohibited_cohorts_fail_closed(bad):
    frame = race()
    if bad == "future":
        frame["race_date"] = "20230101"
    elif bad == "partial":
        frame = frame.iloc[:-1]
    elif bad == "duplicate":
        frame.loc[1, "horse_number"] = 1
    elif bad == "deadheat":
        frame.loc[0, "finish"] = 1
    elif bad == "dnf":
        frame.loc[0, "finish"] = np.nan
    elif bad == "simplex":
        frame["h04_probability"] = .9
    else:
        frame["race_date"] = "20220230"
    with pytest.raises(ValueError):
        divergence_analysis(frame)
