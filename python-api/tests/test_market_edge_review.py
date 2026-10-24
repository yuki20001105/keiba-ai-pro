"""Independent metric and provenance regressions; synthetic data only."""
import json

import numpy as np
import pandas as pd
import pytest
from scipy.stats import spearmanr

from market_edge.evaluation import (betting_summary, fit_conservative, maximum_ev,
                                    prediction_metrics, payout_for)
from market_edge.modeling import FEATURES, MARKET_FEATURES, fit_model, predict_model, run_worker


def races():
    rows = []
    for rid, probabilities, finishes in [("a", [.6, .4], [2, 1]),
                                          ("b", [.4, .3, .2, .1], [1, 3, 2, 4]),
                                          ("c", [.4, .4, .2], [2, 1, 3])]:
        for number, (p, finish) in enumerate(zip(probabilities, finishes), 1):
            rows.append({"race_id": rid, "race_date": "20211010", "horse_number": number,
                         "expected_runner_count": len(probabilities), "finish": finish, "odds": 1 / p,
                         "probability": p, "tansho_payout": 300. if finish == 1 else np.nan,
                         "payout_confirmed_race": True})
    return pd.DataFrame(rows)


def test_spearman_matches_independent_scipy_macro_with_ties_and_unequal_fields():
    data = races().sample(frac=1, random_state=7)
    expected = np.mean([spearmanr(group.probability, -group.finish).statistic
                        for _, group in data.groupby("race_id")])
    actual = prediction_metrics(data)
    assert actual["spearman"] == pytest.approx(expected, abs=1e-14)
    assert actual["top1_win_rate"] == pytest.approx(1 / 3)


def test_probability_metrics_are_equal_race_not_equal_runner_weighted():
    data = races()
    nll, brier = [], []
    for _, group in data.groupby("race_id"):
        nll.append(-np.log(group.loc[group.finish.eq(1), "probability"].iloc[0]))
        brier.append(float(((group.probability - group.finish.eq(1)) ** 2).sum()))
    metrics = prediction_metrics(data)
    assert metrics["nll"] == pytest.approx(np.mean(nll))
    assert metrics["brier"] == pytest.approx(np.mean(brier))
    assert metrics["horses"] == 9 and metrics["races"] == 3


def test_h04_temperature_probability_rank_equals_h04_raw_score_rank():
    data = races(); scores = np.log(data.probability) * .14843942620053036
    expected = np.mean([spearmanr(scores.loc[group.index], -group.finish).statistic
                        for _, group in data.groupby("race_id")])
    assert prediction_metrics(data)["spearman"] == pytest.approx(expected)


def test_model_feature_allowlists_cannot_use_fixed_reference_or_outcomes():
    forbidden = {"h04_probability_fixed_reference", "finish", "is_win", "tansho_payout", "payout_confirmed_race"}
    assert not forbidden & set(FEATURES + MARKET_FEATURES)
    assert "h04_probability" in FEATURES
    assert not any("h04" in name for name in MARKET_FEATURES)


@pytest.mark.parametrize("model", ["logistic", "market_logistic"])
def test_zero_residual_identity_and_adversarial_reference_do_not_change_predictions(tmp_path, model):
    data = races()
    features = MARKET_FEATURES if model == "market_logistic" else FEATURES
    for name in features:
        data[name] = .5
    data["normalized_market_probability"] = data.probability
    data["h04_probability_fixed_reference"] = [1, 0, 0, 1, 0, 0, 1, 0, 0]
    artifact = {"model": model, "features": features, "mean": [0] * len(features),
                "scale": [1] * len(features), "beta": [0] * (len(features) + 1)}
    (tmp_path / "model.json").write_text(json.dumps(artifact), encoding="utf-8")
    result = predict_model(data, tmp_path)
    np.testing.assert_allclose(result, data.probability)
    data["h04_probability_fixed_reference"] = np.nan
    data["finish"] = 999
    data["tansho_payout"] = 999999
    np.testing.assert_allclose(predict_model(data, tmp_path), result)


def test_lightgbm_saved_residual_still_receives_market_offset_at_inference(tmp_path):
    pytest.importorskip("lightgbm")
    rows = []
    winners = [1] * 6 + [2] * 3 + [3]
    for index, winner in enumerate(winners):
        for horse, probability in enumerate([.6, .3, .1], 1):
            row = {name: float(horse) for name in FEATURES}
            row.update(race_id=str(index), race_date="20210710", horse_number=horse,
                       normalized_market_probability=probability, finish=1 if horse == winner else 2)
            rows.append(row)
    data = pd.DataFrame(rows)
    fit_model(data, "lightgbm", "cpu", tmp_path, rounds=2)
    # The fixed minimum leaf size exceeds this synthetic sample: no split or
    # nonzero residual is learnable. Saved init_score must not be assumed present.
    np.testing.assert_allclose(predict_model(data, tmp_path), data.normalized_market_probability, atol=1e-7)


def test_missing_race_payout_cannot_certify_a_selected_loser_as_zero():
    selected = races().loc[lambda x: x.race_id.eq("a") & x.finish.eq(2)].copy()
    selected["payout_confirmed_race"] = False
    assert payout_for(selected).isna().all()
    result = betting_summary(selected, race_count=1)
    assert result["unknown_payouts"] == 1 and result["roi_percent"] is None


@pytest.mark.parametrize("amount", [101., 99., 101.5, np.inf])
def test_invalid_jpy_dividend_is_unknown(amount):
    selected = races().loc[lambda x: x.race_id.eq("a") & x.finish.eq(1)].copy()
    selected["tansho_payout"] = amount
    assert payout_for(selected).isna().all()


def test_confirmed_winner_dividend_not_market_odds_determines_roi():
    data = races(); data["odds"] = 500.
    selected = data.loc[data.race_id.eq("b") & data.finish.eq(1)]
    result = betting_summary(selected, race_count=1)
    assert result["roi_percent"] == 300 and result["profit_yen"] == 200


def test_conservative_fitting_cannot_read_validation_labels():
    data = races(); data["race_date"] = "20220105"
    with pytest.raises(ValueError):
        fit_conservative(data)


def test_unknown_worker_fold_is_not_silently_validation(tmp_path, monkeypatch):
    from market_edge import modeling
    train = races(); train["phase"] = "development"
    validation = races(); validation["phase"] = "validation"; validation["race_date"] = "20221010"
    data = pd.concat([train, validation], ignore_index=True)
    path = tmp_path / "data.parquet"; data.to_parquet(path, index=False)
    def must_not_fit(*args, **kwargs):
        pytest.fail("unknown fold must be rejected before any fitting")
    monkeypatch.setattr(modeling, "fit_model", must_not_fit)
    with pytest.raises(ValueError):
        run_worker(path, tmp_path / "fit", model="logistic", fold="misspelled")


def test_ev_tie_break_is_lowest_horse_number_and_not_finish():
    data = races().loc[lambda x: x.race_id.eq("c")].copy()
    # The winner is number 2; all EVs are exactly 1.
    data["probability"] = .25; data["odds"] = 4.
    assert maximum_ev(data).horse_number.iloc[0] == 1
