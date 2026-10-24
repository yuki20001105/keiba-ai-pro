import math

import pandas as pd
import pytest

from prospective.policy import decide, evaluate_policy


def runners():
    return [{"horse_number": 1, "win_probability": .6, "odds": 2.5},
            {"horse_number": 2, "win_probability": .4, "odds": 2.0}]


def frame():
    return pd.DataFrame([{**r, "race_id": "race", "race_date": "20230101", "expected_runner_count": 2,
                          "finish": i + 1, "tansho_payout": 250 if i == 0 else None} for i, r in enumerate(runners())])


def test_paper_only_fixed_selection():
    result = decide(runners())
    assert result["decision"] == "paper_bet" and result["horse_number"] == 1
    assert result["stake_yen"] == 100 and result["live_bet"] is False
    assert result["stressed_expected_return"] == pytest.approx(1.35)


@pytest.mark.parametrize("value", [None, -1, float("nan"), float("inf"), True, "0.6", 1.1])
def test_invalid_probabilities_skipped(value):
    data = runners(); data[0]["win_probability"] = value
    assert decide(data)["decision"] == "skip"


@pytest.mark.parametrize("value", [None, 0, float("nan"), float("inf"), True, "2.5"])
def test_even_unselected_bad_odds_skip_entire_race(value):
    data = runners(); data[1]["odds"] = value
    assert decide(data)["reason"] == "missing_or_invalid_odds"


def test_scores_not_probabilities():
    data = runners(); data[0].pop("win_probability"); data[0]["score"] = .6
    assert decide(data)["reason"] == "invalid_probability"


def test_bad_sum_and_duplicates():
    data = runners(); data[0]["win_probability"] = .7
    assert decide(data)["reason"] == "probabilities_do_not_sum_to_one"
    data = runners(); data[1]["horse_number"] = 1
    assert decide(data)["reason"] == "duplicate_runner"


def test_zero_opportunity_is_skip():
    data = runners()
    for r in data: r["odds"] = 1
    assert decide(data)["decision"] == "skip"


def test_no_arbitrary_tuning():
    with pytest.raises(ValueError): decide(runners(), odds_multiplier=.95)


def test_historical_payout_uses_official_not_odds():
    data = frame(); data.loc[0, "tansho_payout"] = 310
    report, _ = evaluate_policy(data)
    assert report["overall"]["gross_return_percent"] == 310
    assert report["future_operational_evidence"] is False


def test_missing_winner_payout_not_zero():
    data = frame(); data.loc[0, "tansho_payout"] = None
    report, _ = evaluate_policy(data)
    assert report["overall"]["gross_return_percent"] is None
    assert report["overall"]["payout_missing_bets"] == 1


def test_loss_does_not_require_loser_payout():
    data = frame(); data["finish"] = [2, 1]
    report, _ = evaluate_policy(data)
    assert report["overall"]["gross_return_percent"] == 0


def test_no_certifying_old_rows_as_future():
    with pytest.raises(ValueError): evaluate_policy(frame(), mode="prospective")


def test_cohort_failure():
    with pytest.raises(ValueError): evaluate_policy(frame().iloc[:1])


def test_dead_heat_skipped_with_reason():
    data = frame(); data["finish"] = 1
    report, _ = evaluate_policy(data)
    assert report["overall"]["skip_reasons"] == {"non_single_winner": 1}


def test_invalid_finish_is_not_automatic_losing_bet():
    data = frame(); data.loc[0, "finish"] = math.nan
    report, _ = evaluate_policy(data)
    assert report["overall"]["bets"] == 0
