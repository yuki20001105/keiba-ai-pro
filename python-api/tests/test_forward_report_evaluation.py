"""Synthetic frozen-ledger fixtures only; no real observations or fitting."""
from copy import deepcopy
import hashlib
import math
from pathlib import Path

import pytest

from forward_reports import evaluation
from forward_runtime.schedule import ForwardError, sha256
from forward_runtime.settlement import settle_win_decision
from test_forward_runtime_ledger import contract, official
from test_forward_runtime_settlement import record

AS_OF = "2026-09-20T06:11:00Z"


def row(*, scores=(.8, .2), ranks=None, winners=(1,), probabilities=(.6, .4), skip=False):
    result = record()
    for runner, score, p in zip(result["snapshot"]["snapshot"]["runners"], scores, probabilities):
        runner.update(score=score, win_probability=p)
        if skip:
            runner["odds"] = 1.
    if skip:
        result["decision"] = {"decision": "skip", "reason": "insufficient_expected_return"}
    else:
        runners = result["snapshot"]["snapshot"]["runners"]
        chosen = min(runners, key=lambda r: (-r["win_probability"] * r["odds"], r["horse_number"]))
        result["decision"].update(horse_number=chosen["horse_number"], win_probability=chosen["win_probability"], indicative_odds=chosen["odds"])
    result["snapshot"]["snapshot_sha256"] = sha256(result["snapshot"]["snapshot"])
    result["snapshot_sha256"] = result["snapshot"]["snapshot_sha256"]
    result["decision_sha256"] = sha256(result["decision"])
    evidence = official(winners=list(winners), win_payouts={str(n): 270 for n in winners}, ranks={"1": 1, "2": 2} if ranks is None else ranks)
    result["settlement"] = settle_win_decision(result, evidence, now=AS_OF)
    result["settlement_sha256"] = sha256(result["settlement"])
    result["status"] = result["settlement"]["status"]
    return result


def snapshot(rows):
    c = contract()
    return {"contract": c, "contract_sha256": sha256(c), "days": [{"date": "20260920", "status": "complete"}], "races": rows}


def evaluate(rows):
    return evaluation.evaluate_forward(snapshot(rows), as_of=AS_OF)


def test_exact_rmse_nll_brier_top1_and_calibration():
    report = evaluate([row()]); p = report["prediction"]
    assert p["rank_correlation"] == 1.
    assert p["normalized_rank_rmse"] == pytest.approx(.2)
    assert p["nll"] == pytest.approx(-math.log(.6))
    assert p["brier"] == pytest.approx(.32)
    assert p["top1_win_rate"] == 1.
    assert p["normalized_rank_rmse_runners"] == 2 and p["probability_races"] == 1
    assert sum(b["runner_count"] for b in p["calibration_bins"]) == 2
    assert p["calibration_bins"][6]["mean_categorical_winner_target"] == 1.
    assert report["paper_betting"]["paper_tickets"] == 1
    assert report["model_or_policy_changed"] is False


def test_skipped_bet_still_evaluates_prediction():
    r = evaluate([row(skip=True)])
    assert r["prediction"]["probability_races"] == 1
    assert r["prediction"]["normalized_rank_rmse"] == pytest.approx(.2)
    assert r["paper_betting"]["paper_tickets"] == 0
    assert r["paper_betting"]["gross_return_percent"] is None


@pytest.mark.parametrize("ranks", [{"1": 1}, {"1": 1, "2": "中止"}, {"1": 1, "2": "失格"}, {"1": 1, "2": 0}])
def test_dnf_or_incomplete_finish_excludes_whole_rank_cohort_only(ranks):
    p = evaluate([row(ranks=ranks)])["prediction"]
    assert p["rank_correlation"] is None and p["normalized_rank_rmse"] is None
    assert p["normalized_rank_rmse_runners"] == 0
    assert p["probability_races"] == 1 and p["top1_win_rate"] == 1.
    assert sum(p["rank_metric_unavailable_reasons"].values()) == 1


def test_deadheat_uses_fractional_winner_distribution_not_multiple_onehots():
    p = evaluate([row(ranks={"1": 1, "2": 1}, winners=(1, 2))])["prediction"]
    assert p["nll"] == pytest.approx(-.5 * (math.log(.6) + math.log(.4)))
    assert p["brier"] == pytest.approx(.02)
    assert p["dead_heat_probability_races"] == 1 and p["top1_win_rate"] == 1.
    assert p["rank_correlation"] is None  # Constant official ranks.
    assert p["normalized_rank_rmse"] == pytest.approx(math.sqrt((.2**2+.8**2)/2))
    assert sum(b["categorical_winner_target_sum"] for b in p["calibration_bins"]) == 1.
    assert sum(b["winning_horse_count"] for b in p["calibration_bins"]) == 2


def test_constant_scores_rank_undefined_top1_tiebreak_is_smallest_number():
    p = evaluate([row(scores=(.5, .5))])["prediction"]
    assert p["rank_correlation"] is None and p["top1_win_rate"] == 1.
    assert p["normalized_rank_rmse"] == .5


def test_missing_score_does_not_remove_valid_winner_probability_observation():
    p = evaluate([row(scores=(None, .2))])["prediction"]
    assert p["normalized_rank_rmse"] is None and p["top1_win_rate"] is None
    assert p["probability_races"] == 1


def test_no_observations_null_not_zero():
    r = evaluate([]); p = r["prediction"]
    for metric in ("rank_correlation", "normalized_rank_rmse", "nll", "brier", "top1_win_rate"):
        assert p[metric] is None
    assert p["probability_races"] == 0 and p["no_observed_evaluation"]
    assert all(b["runner_count"] == 0 and b["mean_probability"] is None for b in p["calibration_bins"])


def test_pending_result_not_substituted_by_stored_odds_or_prediction():
    r = row(); r.update(settlement=None, settlement_sha256=None, status="captured")
    result = evaluate([r])
    assert result["prediction"]["nll"] is None
    assert result["prediction"]["excluded_races"]["final_official_result_pending"] == 1
    assert result["paper_betting"]["payout_yen"] is None


@pytest.mark.parametrize("key", ["contract", "capture", "settlement"])
def test_existing_runtime_integrity_guards_are_reused(key):
    state = snapshot([row()])
    if key == "contract": state["contract"]["protocol_sha256"] = "0" * 64
    if key == "capture": state["races"][0]["snapshot"]["snapshot"]["runners"][0]["score"] = 100.
    if key == "settlement": state["races"][0]["settlement"]["payout_yen"] = 10000
    with pytest.raises(ForwardError): evaluation.evaluate_forward(state, as_of=AS_OF)


def test_zero_winner_probability_finite_explicit_floor_and_probability_one_bin():
    p = evaluate([row(probabilities=(0., 1.))])["prediction"]
    assert math.isfinite(p["nll"]) and p["winning_zero_probabilities_clipped"] == 1
    assert p["calibration_bins"][9]["runner_count"] == 1


def test_definition_binds_actual_source_and_inputs_unchanged():
    state = snapshot([row()]); original = deepcopy(state)
    result = evaluation.evaluate_forward(state, as_of=AS_OF)
    definition = evaluation.evaluation_definition()
    assert state == original
    assert result["analysis_definition"] == definition
    assert definition["evaluation_source_sha256"] == hashlib.sha256(Path(evaluation.__file__).read_bytes()).hexdigest()
    assert "not speed RMSE" in definition["definitions"]["normalized_rank_rmse"]
