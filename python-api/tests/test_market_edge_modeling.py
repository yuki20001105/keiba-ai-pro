import numpy as np
import pandas as pd
import pytest
from scipy.optimize._numdiff import approx_derivative
from market_edge.modeling import FEATURES, logistic_loss, normalize, fit_model, predict_model
from market_edge.evaluation import (prediction_metrics, fit_conservative, apply_conservative,
                                   betting_summary, coverage_cutoffs, robustness, maximum_ev)


def cohort(n=50, date="20211001"):
    rows=[]
    for r in range(n):
        for h, p in enumerate([.6,.3,.1],1):
            rows.append(dict(race_id=str(r), race_date=date, horse_number=h, expected_runner_count=3,
                             finish=h, odds=1/p, probability=p, tansho_payout=200 if h==1 else np.nan,
                             normalized_market_probability=p, log_odds=np.log(1/p), market_rank=h,
                             h04_score=p, h04_rank=h, h04_probability=p, rank_difference=0,
                             probability_difference=0, field_size=3))
    return pd.DataFrame(rows)


def test_gradient():
    rng=np.random.default_rng(42)
    x=rng.normal(size=(20,5)); offset=rng.normal(size=20)
    y=rng.integers(0,2,20); w=np.ones(20)/20; beta=rng.normal(size=5)
    _, analytic=logistic_loss(beta,x,offset,y,w)
    numeric=approx_derivative(lambda b: logistic_loss(b,x,offset,y,w)[0],beta)
    assert np.allclose(analytic,numeric,atol=1e-7)


def test_identity_probability_offset():
    frame=cohort()
    assert np.allclose(normalize(frame,frame.probability),frame.probability)
    result=prediction_metrics(frame)
    assert result["nll"] == pytest.approx(-np.log(.6))
    assert result["brier"] == pytest.approx(.26)
    assert result["spearman"] == pytest.approx(1)
    assert result["top1_win_rate"] == 1


def test_payout_unknown_not_zero():
    frame=cohort(1).iloc[:1].copy(); frame["tansho_payout"]=np.nan
    result=betting_summary(frame,race_count=1)
    assert result["unknown_payouts"] == 1
    assert result["roi_percent"] is None


def test_conservative_frozen_no_validation_labels():
    dev=cohort(50)
    artifact=fit_conservative(dev)
    val=cohort(50,"20220101")
    p=apply_conservative(val,artifact).p_lower
    val["finish"]=4-val.finish
    assert np.allclose(p,apply_conservative(val,artifact).p_lower)
    assert (p <= val.probability).all()
    cuts=coverage_cutoffs(dev)
    dev["finish"]=4-dev.finish
    assert cuts == coverage_cutoffs(dev)


def test_small_bins_cannot_create_confidence():
    dev=cohort(3)
    assert apply_conservative(dev,fit_conservative(dev)).p_lower.eq(0).all()


def test_reject_incomplete_races():
    with pytest.raises(ValueError,match="Incomplete"):
        prediction_metrics(cohort().iloc[1:])


def test_fit_and_inference_labels_not_used(tmp_path):
    train=cohort(date="20210701")
    fit_model(train,"logistic","cpu",tmp_path)
    val=cohort(date="20220101")
    p=predict_model(val,tmp_path)
    val["finish"]=4-val.finish
    assert np.allclose(p,predict_model(val,tmp_path))
    assert np.allclose(pd.Series(p).groupby(val.race_id).sum(),1)
    with pytest.raises(FileExistsError):
        fit_model(train,"logistic","cpu",tmp_path)


def test_bootstrap_cluster_and_jackpot():
    frame=cohort(4)
    frame["odds"]=10
    result=robustness(frame,samples=50)
    assert result["roi_percent"] == 200
    assert result["roi_ci95"] == [200,200]
    assert result["roi_excluding_top3_tickets"] == 200


def test_exact_tie_lower_horse_number():
    frame=cohort(1)
    assert maximum_ev(frame).horse_number.iloc[0] == 1
