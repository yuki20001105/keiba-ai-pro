"""Exact synthetic ranking fixtures only; no historical data/model access."""
from itertools import combinations, permutations
import math

import numpy as np
import pandas as pd
import pytest

from bet_types.distribution import (CalibrationRejectedError, audit_distribution,
    fit_temperature, ordered_top3_nll, plackett_luce, scores_to_distribution)


def enumerate_full_rankings(weights):
    n = len(weights)
    result = {"win": np.zeros(n), "top2": np.zeros(n), "top3": np.zeros(n),
        "ordered_pair": np.zeros((n, n)), "unordered_pair": np.zeros((n, n)),
        "wide": np.zeros((n, n)), "ordered_triple": np.zeros((n, n, n)),
        "unordered_triple": np.zeros((n, n, n))}
    for order in permutations(range(n)):
        p = math.prod(weights[index] / sum(weights[k] for k in order[position:]) for position, index in enumerate(order))
        result["win"][order[0]] += p
        result["top2"][list(order[:2])] += p
        result["top3"][list(order[:3])] += p
        result["ordered_pair"][order[:2]] += p
        result["unordered_pair"][tuple(sorted(order[:2]))] += p
        for pair in combinations(sorted(order[:3]), 2):
            result["wide"][pair] += p
        if n >= 3:
            result["ordered_triple"][order[:3]] += p
            result["unordered_triple"][tuple(sorted(order[:3]))] += p
    return result


@pytest.mark.parametrize("n", [2, 3, 4, 5])
def test_exact_distribution_matches_independent_full_permutation_enumeration(n):
    weights = np.array([.3, 1.7, 2.1, .8, 4.2])[:n]
    actual, expected = plackett_luce(weights), enumerate_full_rankings(weights)
    for key in expected:
        np.testing.assert_allclose(actual[key], expected[key], rtol=2e-14, atol=2e-14, err_msg=key)


@pytest.mark.parametrize("n", [3, 8, 18])
def test_uniform_closed_form_and_event_sums(n):
    result = plackett_luce(np.ones(n))
    for k, key in [(1, "win"), (2, "top2"), (3, "top3")]:
        np.testing.assert_allclose(result[key], k / n)
    assert result["ordered_pair"][0, 1] == pytest.approx(1 / (n * (n - 1)))
    assert result["unordered_pair"][0, 1] == pytest.approx(1 / math.comb(n, 2))
    assert result["wide"][0, 1] == pytest.approx(3 / math.comb(n, 2))
    assert result["ordered_triple"][0, 1, 2] == pytest.approx(1 / (n * (n - 1) * (n - 2)))
    assert result["unordered_triple"][0, 1, 2] == pytest.approx(1 / math.comb(n, 3))
    sums = audit_distribution(result)["probability_sums"]
    assert sums == pytest.approx(dict(win=1, top2=2, top3=3, ordered_pair=1,
        unordered_pair=1, wide=3, ordered_triple=1, unordered_triple=1))


def test_two_runners_have_no_triple_events_but_both_are_in_top3():
    result = plackett_luce([7., 3.])
    np.testing.assert_array_equal(result["top2"], [1., 1.])
    np.testing.assert_array_equal(result["top3"], [1., 1.])
    assert result["wide"].sum() == pytest.approx(1.)
    assert result["ordered_triple"].sum() == result["unordered_triple"].sum() == 0


def test_strict_no_repeated_horses_and_unordered_events_not_double_counted():
    result = plackett_luce(np.arange(1, 19))
    i, j, k = np.indices((18, 18, 18))
    assert not np.diag(result["ordered_pair"]).any()
    assert not result["ordered_triple"][(i == j) | (i == k) | (j == k)].any()
    assert not np.tril(result["unordered_pair"]).any()
    assert not np.tril(result["wide"]).any()
    assert not result["unordered_triple"][~((i < j) & (j < k))].any()


def test_score_orientation_offsets_scaling_and_dependence():
    scores = np.array([2., 1., -.5, -.8])
    a = scores_to_distribution(scores, .5)
    b = scores_to_distribution(scores + 1e6, .5)
    c = plackett_luce(np.exp(scores / .5) * 1000)
    for key in a:
        np.testing.assert_allclose(a[key], b[key], atol=1e-10)
        np.testing.assert_allclose(a[key], c[key], atol=1e-14)
    assert np.argmax(a["win"]) == 0
    assert a["ordered_pair"][0, 1] != pytest.approx(a["win"][0] * a["win"][1])
    assert a["wide"][0, 1] != pytest.approx(a["top3"][0] * a["top3"][1], rel=1e-12, abs=1e-12)


@pytest.mark.parametrize("weights", [[1e300, 1., 1e-300], [1e-300, 1e-299, 1e-298], [1e308, 1e308, 1e-308]])
def test_extreme_positive_weights_remain_valid_without_denominator_cancellation(weights):
    result = plackett_luce(weights)
    assert np.isfinite(result["ordered_triple"]).all()
    np.testing.assert_allclose(result["top3"], 1.)
    assert result["ordered_triple"].sum() == pytest.approx(1.)


def test_extreme_scores_recover_remaining_horses_after_dominant_winner():
    result = scores_to_distribution([1000., 0., -1000.], 1.)
    assert result["ordered_triple"][0, 1, 2] == 1.
    assert ordered_top3_nll([1000., 0., -1000.], [2, 1, 0], 1.) == pytest.approx(3000.)


@pytest.mark.parametrize("weights", [[], [1], [1] * 19, [1, 0], [1, -1], [1, np.nan], [1, np.inf], [[1, 2]]])
def test_invalid_weights_rejected(weights):
    with pytest.raises(ValueError):
        plackett_luce(weights)


@pytest.mark.parametrize("temperature", [0, -1, np.nan, np.inf, True])
def test_invalid_temperature_rejected(temperature):
    with pytest.raises(ValueError):
        scores_to_distribution([1., 0.], temperature)


@pytest.mark.parametrize("order", [[0, 0, 1], [0, 1], [0, 1, 3], [.0, 1., 2.], [-1, 0, 1]])
def test_invalid_order_rejected(order):
    with pytest.raises(ValueError):
        ordered_top3_nll([1., 0., -.2], order, 1.)


def test_order_nll_matches_exact_probability_and_audit_rejects_corruption():
    result = scores_to_distribution([.9, .2, -.1, -.4], .8)
    assert ordered_top3_nll([.9, .2, -.1, -.4], [1, 3, 0], .8) == pytest.approx(-np.log(result["ordered_triple"][1, 3, 0]))
    result["wide"][0, 1], result["wide"][0, 2] = result["wide"][0, 2], result["wide"][0, 1]
    with pytest.raises(ValueError, match="unordered-event-consistency"):
        audit_distribution(result)


def calibration_fixture():
    scores = np.array([1.1, .4, .1, -.3])
    weights = np.exp(scores / .6)
    rows, race = [], 0
    for order in permutations(range(4)):
        probability = math.prod(weights[index] / sum(weights[k] for k in order[position:]) for position, index in enumerate(order))
        for _ in range(round(probability * 400)):
            for horse in range(4):
                rows.append(dict(race_id=f"20210101{race:04d}", race_date="20210101", horse_number=horse + 1,
                    expected_runner_count=4, h04_score=scores[horse], finish=order.index(horse) + 1,
                    fold_train_date_to="20201231", odds=horse + 2., payout=10000.))
            race += 1
    return pd.DataFrame(rows)


def test_single_scale_recovers_synthetic_temperature_and_uses_race_mean_top3_nll():
    frame = calibration_fixture()
    before = frame.copy(deep=True)
    result = fit_temperature(frame, minimum_races=100)
    assert result["accepted"] and result["temperature"] == pytest.approx(.6, abs=.03)
    assert result["fit_nll"] < result["reference_t1_nll"]
    losses = [ordered_top3_nll(g.h04_score, np.argsort(g.finish.to_numpy())[:3], result["temperature"])
              for _, g in frame.groupby("race_id")]
    assert result["fit_nll"] == pytest.approx(np.mean(losses))
    pd.testing.assert_frame_equal(frame, before)
    assert not result["model_refit"] and not result["odds_used"]
    changed = frame.sample(frac=1, random_state=2).copy()
    changed["odds"], changed["payout"] = np.nan, -9999.
    assert fit_temperature(changed, minimum_races=100) == result


@pytest.mark.parametrize("column,value", [("race_date", "20210701"), ("race_date", "20220101"),
                                         ("fold_train_date_to", "20210101")])
def test_calibration_rejects_any_non_development_row_before_fitting(column, value):
    frame = calibration_fixture()
    frame.loc[0, column] = value
    with pytest.raises(ValueError, match="2021-H1-prior-year-OOF"):
        fit_temperature(frame)


def test_calibration_exclusions_are_whole_race_and_audited():
    frame = calibration_fixture()
    frame["finish"] = frame.finish.astype(float)
    ids = frame.race_id.unique()
    frame.loc[frame.race_id.eq(ids[0]), "expected_runner_count"] = 5
    frame.loc[frame.race_id.eq(ids[1]), "finish"] = [1, 2, 2, 4]
    frame.loc[frame.race_id.eq(ids[2]), "finish"] = [1, 2, 4, 4]
    frame.loc[frame.race_id.eq(ids[3]), "finish"] = [1, 2, 3, np.nan]
    result = fit_temperature(frame)
    assert result["excluded_races"] == 4 and result["fit_races"] == len(ids) - 4
    assert result["fit_runners"] == len(frame) - 16
    assert {item["reason"] for item in result["exclusions"]} == {
        "incomplete-or-less-than-three-runner-race", "top-three-dead-heat",
        "invalid-competition-ranking", "unconfirmed-or-invalid-finish"}


@pytest.mark.parametrize("case", ["perfect", "reverse", "constant", "too-few"])
def test_unidentified_or_boundary_scale_is_rejected_with_receipt(case):
    frame = calibration_fixture().iloc[:16].copy()
    minimum = 1
    if case == "constant":
        frame["h04_score"] = 1.
    elif case in {"perfect", "reverse"}:
        frame["finish"] = frame.horse_number if case == "perfect" else 5 - frame.horse_number
    else:
        minimum = 100
    with pytest.raises(CalibrationRejectedError) as caught:
        fit_temperature(frame, minimum_races=minimum)
    receipt = caught.value.receipt
    assert receipt["accepted"] is False and receipt["rejection_reason"]
    if case in {"perfect", "reverse"}:
        assert receipt["distance_to_boundary_log_fraction"] <= .01
        assert receipt["temperature"] == pytest.approx(.01 if case == "perfect" else 100.)


def test_duplicate_identity_and_missing_h04_never_silently_fit_a_different_column():
    frame = calibration_fixture().iloc[:16].copy()
    bad = pd.concat([frame, frame.iloc[:1]], ignore_index=True)
    with pytest.raises(ValueError, match="unique-integer-runner"):
        fit_temperature(bad)
    with pytest.raises(ValueError, match="H04-calibration-columns"):
        fit_temperature(frame.rename(columns={"h04_score": "odds_score"}))
