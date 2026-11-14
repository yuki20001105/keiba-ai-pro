from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from betting.strategy import BettingRecommender
from betting.value_policy import build_live_context, load_probability_validation, recommend

NOW = datetime(2026, 10, 10, 5, 0, tzinfo=timezone.utc)
RACE = {"race_id": "202605040111", "date": "20261010", "post_time": "15:00", "num_horses": 3}
PREDS = [
    {"horse_number": 1, "p_norm": 0.5, "p_raw": -5, "odds": 4.0},
    {"horse_number": 2, "p_norm": 0.3, "p_raw": -6, "odds": 7.0},
    {"horse_number": 3, "p_norm": 0.2, "p_raw": -9, "odds": 10.0},
]


def context():
    return {
        "input_diagnostics": {"schema": "prediction-input-contract-v1", "status": "aligned",
            "expected_count": 3, "actual_count": 3, "missing_columns": [], "blocked_columns": [],
            "issue_codes": [], "source_hash_matches": True, "history_available": True,
            "semantic_parity_verified": True},
        "probability_validation": {"validated": True, "model_id": "test-model",
            "eligible_bet_types": ["単勝", "馬連", "ワイド", "馬単", "三連複", "三連単"],
            "validated_at": "2026-10-01T00:00:00Z", "validation_period_end": "2026-09-01"},
        "feature_missing_rate": 0.1,
        "sources": [{"horse_number": n, "odds_status": "middle", "observed_at": NOW.isoformat(),
            "odds_evidence": {"schema": "netkeiba-win-odds-capture-v1", "race_id": RACE["race_id"],
                "source_url": f"https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={RACE['race_id']}&type=1&action=update",
                "http_status": 200, "odds_status": "middle", "response_sha256": "a" * 64,
                "odds_by_horse_number": {str(n): PREDS[n - 1]["odds"]}}, "has_result": False}
            for n in (1, 2, 3)],
        "joint_tickets": [],
    }


def run(preds=None, info=None, evidence=None, **kwargs):
    return recommend(deepcopy(PREDS if preds is None else preds), deepcopy(RACE if info is None else info),
        bankroll=kwargs.pop("bankroll", 10000), risk_mode=kwargs.pop("risk_mode", "balanced"),
        min_ev=kwargs.pop("min_ev", 1.2), use_kelly=kwargs.pop("use_kelly", True),
        dynamic_unit=kwargs.pop("dynamic_unit", True), context=context() if evidence is None else evidence,
        now=kwargs.pop("now", NOW), **kwargs)

def fixed_context():
    preds = [{"horse_number": n, "p_norm": p, "odds": odds}
             for n, p, odds in [(1, .36, 2), (2, .22, 14), (3, .16, 8),
                                (4, .12, 15), (5, .09, 30), (6, .05, 60)]]
    info = {**RACE, "num_horses": 6}
    ctx = context()
    ctx["probability_validation"]["model_sha256"] = "c" * 64
    ctx["sources"] = [
        {**context()["sources"][0], "horse_number": pred["horse_number"],
         "odds_evidence": {**context()["sources"][0]["odds_evidence"],
                          "odds_by_horse_number": {str(pred["horse_number"]): pred["odds"]}}}
        for pred in preds
    ]
    ctx["axis_forecast"] = {
        "schema": "fixed-two-axis-forecast-v1", "race_id": info["race_id"],
        "model_sha256": "c" * 64, "axis": 1, "probability": .75, "validated": True,
        "fitted_through": "20260901", "validation_period_end": "2026-09-01",
        "validated_at": "2026-10-01T00:00:00Z",
    }
    ctx["joint_tickets"] = []
    for kind, members, p, odds, market in [
        ("ワイド", [1, 5], .1, 20, "b5"),
        ("三連単", [6, 1, 5], .015, 150, "b8"),
        ("三連単", [5, 1, 6], .015, 180, "b8"),
    ]:
        key = ("→" if kind == "三連単" else "-").join(map(str, members))
        capture = {
            "schema": "netkeiba-combination-odds-capture-v1", "race_id": info["race_id"],
            "bet_type": kind, "source_url": f"https://race.netkeiba.com/odds/index.html?race_id={info['race_id']}&type={market}",
            "http_status": 200, "odds_status": "middle", "response_sha256": "b" * 64,
            "observed_at": NOW.isoformat(), "odds_by_combination": {key: odds},
        }
        if kind == "ワイド":
            capture["price_basis"] = "lower_bound"
        ctx["joint_tickets"].append({
            "race_id": info["race_id"], "model_id": "test-model", "probability_validated": True,
            "bet_type": kind, "horse_numbers": members, "probability": p, "odds": odds,
            "odds_status": "middle", "observed_at": NOW.isoformat(), "odds_evidence": capture,
        })
    return preds, info, ctx


def test_fixed_two_selects_only_the_exact_three_tickets_at_equal_bounded_stakes():
    preds, info, ctx = fixed_context()
    rec = run(preds=preds, info=info, evidence=ctx, bankroll=100000,
              betting_strategy="fixed-two-v1")["recommendation"]
    assert rec["betting_strategy"] == "fixed-two-v1"
    assert rec["decision"] == "priority"
    assert [(t["bet_type"], t["horse_numbers"], t["stake"]) for t in rec["tickets"]] == [
        ("ワイド", [1, 5], 500), ("三連単", [6, 1, 5], 500), ("三連単", [5, 1, 6], 500),
    ]
    assert rec["total_cost"] == rec["per_race_limit"] == 1500
    assert rec["max_points"] == 3
    assert rec["fixed_selection"]["axis_probability"] == .75


@pytest.mark.parametrize("problem,code", [
    ("axis_missing", "axis_probability_unvalidated"), ("axis_low", "axis_unlikely"),
    ("model_mismatch", "axis_probability_unvalidated"),
    ("axis_future", "axis_probability_unvalidated"),
    ("one_quote_missing", "fixed_book_unavailable"),
    ("one_quote_stale", "fixed_book_unavailable"),
    ("wide_upper_bound", "fixed_book_unavailable"),
    ("wide_wrong_market", "fixed_book_unavailable"),
    ("one_price_not_valuable", "fixed_book_unavailable"),
    ("budget", "fixed_budget_below_minimum"),
    ("not_priority", "not_numeric_priority"),
])
def test_fixed_two_never_substitutes_win_or_partial_books(problem, code):
    preds, info, ctx = fixed_context()
    if problem == "axis_missing": ctx.pop("axis_forecast")
    elif problem == "axis_low": ctx["axis_forecast"]["probability"] = .64
    elif problem == "model_mismatch": ctx["axis_forecast"]["model_sha256"] = "d" * 64
    elif problem == "axis_future": ctx["axis_forecast"]["fitted_through"] = "20261010"
    elif problem == "one_quote_missing": ctx["joint_tickets"].pop()
    elif problem == "one_quote_stale": ctx["joint_tickets"][0]["observed_at"] = (NOW - timedelta(minutes=6)).isoformat()
    elif problem == "wide_upper_bound": ctx["joint_tickets"][0]["odds_evidence"]["price_basis"] = "upper_bound"
    elif problem == "wide_wrong_market": ctx["joint_tickets"][0]["odds_evidence"]["source_url"] = ctx["joint_tickets"][0]["odds_evidence"]["source_url"].replace("type=b5", "type=b6")
    elif problem == "one_price_not_valuable": ctx["joint_tickets"][0]["probability"] = .01
    elif problem == "not_priority": preds[1]["odds"] = 4
    rec = run(preds=preds, info=info, evidence=ctx,
              bankroll=9000 if problem == "budget" else 100000,
              risk_mode="conservative" if problem == "budget" else "balanced",
              betting_strategy="fixed-two-v1")["recommendation"]
    assert rec["decision"] == "skip" and rec["tickets"] == []
    assert rec["total_cost"] == 0 and code in rec["reason_codes"]


@pytest.mark.parametrize("reversed_wide", [False, True])
def test_fixed_two_expiry_includes_the_oldest_selected_joint_quote(reversed_wide):
    preds, info, ctx = fixed_context()
    observed = NOW - timedelta(minutes=4)
    ctx["joint_tickets"][0]["observed_at"] = observed.isoformat()
    ctx["joint_tickets"][0]["odds_evidence"]["observed_at"] = observed.isoformat()
    if reversed_wide:
        ctx["joint_tickets"][0]["horse_numbers"].reverse()
    rec = run(preds=preds, info=info, evidence=ctx, bankroll=100000,
              betting_strategy="fixed-two-v1")["recommendation"]
    assert rec["valid_until"] == (NOW + timedelta(minutes=1)).isoformat()
    assert len(rec["tickets"]) == 3

def test_fixed_axis_adapter_requires_reviewed_exact_model_and_past_artifact(tmp_path, monkeypatch):
    import runpy
    import betting.adaptive_strategy as adaptive
    import betting.value_policy as policy
    loader = getattr(policy, "load_axis_forecast", None)
    assert callable(loader), "Missing exact-model live axis adapter"
    model = tmp_path / "test-model.joblib"
    model.write_bytes(b"fixed-axis-test-model")
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    monkeypatch.setattr(adaptive, "BASE_MODEL_SHA", digest)
    training = runpy.run_path(str(Path(__file__).with_name("test_adaptive_strategy.py")))["training"]()
    bundle = adaptive.fit_bundle(training, "20240201")
    preds, info, _ = fixed_context()
    info.update(distance=1600, track_type="芝", venue="東京", field_condition="良", race_class=None)
    assert loader(model, preds, info, now=NOW) is None
    sidecar = model.with_suffix(".fixed-two-axis.json")
    sidecar.write_text(json.dumps(bundle), encoding="utf-8")
    assert loader(model, preds, info, now=NOW) is None
    bundle["live_axis_validation"] = {
        "status": "validated", "evaluation_method": "out_of_sample",
        "selection_calibration_reviewed": True, "race_count": 300,
        "validation_period_end": "2026-09-01", "validated_at": "2026-10-01T00:00:00Z",
        "metrics": {"nll": .4, "baseline_nll": .5, "brier": .15, "baseline_brier": .2,
                    "selection_ece": .03, "selection_absolute_bias": .02},
    }
    bundle = adaptive.seal(bundle)
    sidecar.write_text(json.dumps(bundle), encoding="utf-8")
    forecast = loader(model, preds, info, now=NOW)
    assert forecast["axis"] == 1 and forecast["validated"] is True
    assert forecast["model_sha256"] == digest and 0 <= forecast["probability"] <= 1
    model.write_bytes(b"changed-model")
    assert loader(model, preds, info, now=NOW) is None


def test_exact_tickets_and_caps_no_heuristic_combinations():
    result = run()
    rec = result["recommendation"]
    assert rec["odds_evidence"]["verified"] is True
    assert rec["odds_evidence"]["covered_horses"] == 3
    assert rec["total_cost"] == sum(t["stake"] for t in rec["tickets"]) == 300
    assert rec["per_race_limit"] == 300
    assert len(rec["tickets"]) == 3
    assert all(t["stake"] == 100 for t in rec["tickets"])
    assert all(t["bet_type"] == "単勝" for t in rec["tickets"])
    assert all(t["conservative_expected_value"] == pytest.approx(t["probability"] * t["odds"] * .9 * .95) for t in rec["tickets"])
    assert result["bet_types"]["馬連"] == []
    assert rec["valid_until"] == (NOW + timedelta(minutes=5)).isoformat()


def test_unvalidated_score_is_never_a_win_probability_or_purchase():
    rec = run(evidence={})
    assert rec["recommendation"]["decision"] == "skip"
    assert rec["recommendation"]["total_cost"] == 0
    assert rec["recommendation"]["tickets"] == []
    assert rec["best_bet_type"] == ""
    assert all(p["expected_value"] is None for p in rec["predictions"])


@pytest.mark.parametrize("field,value,code", [
    ("odds_status", "result", "result_odds"), ("odds_status", "yoso", "odds_not_market"),
    ("odds_status", None, "odds_not_market"), ("has_result", True, "result_odds"),
    ("observed_at", None, "odds_provenance_missing"),
    ("observed_at", "2026-10-10T04:54:59Z", "odds_stale"),
    ("observed_at", "2026-10-10T05:00:01Z", "odds_stale"),
    ("observed_at", "2026-10-10T05:00:00", "odds_provenance_missing"),
    ("odds_evidence", None, "odds_provenance_missing"),
    ("odds_evidence", {"schema": "netkeiba-win-odds-capture-v1", "race_id": "other",
        "source_url": "https://race.netkeiba.com/api/api_get_jra_odds.html?race_id=other&type=1&action=update",
        "http_status": 200, "odds_status": "middle", "response_sha256": "a" * 64,
        "odds_by_horse_number": {"1": 4.0}}, "odds_provenance_missing"),
])
def test_bad_odds_evidence_is_fail_closed(field, value, code):
    evidence = context()
    evidence["sources"][0][field] = value
    result = run(evidence=evidence)["recommendation"]
    assert result["decision"] == "skip"
    assert code in result["reason_codes"]
    assert result["purchase_count"] == result["total_cost"] == 0


@pytest.mark.parametrize("change,code", [
    ({"post_time": ""}, "post_time_unknown"), ({"post_time": "14:00"}, "race_closed"),
    ({"post_time": "14:01"}, "race_closed"), ({"date": "20261009"}, "race_closed"),
    ({"date": "20261011"}, "not_today"), ({"num_horses": 4}, "runner_count_mismatch"),
    ({"num_horses": 0}, "runner_count_mismatch"),
])
def test_race_conditions(change, code):
    result = run(info={**RACE, **change})["recommendation"]
    assert code in result["reason_codes"] and not result["tickets"]


@pytest.mark.parametrize("bankroll", [0, 100, 999, 2857])
def test_never_rounds_up_to_minimum_stake(bankroll):
    rec = run(bankroll=bankroll)["recommendation"]
    assert rec["total_cost"] == 0
    assert "budget_below_minimum" in rec["reason_codes"]


@pytest.mark.parametrize("mode,cap,points", [("conservative", 200, 1), ("balanced", 300, 3), ("aggressive", 500, 5)])
def test_risk_modes_cap_total_not_each_ticket(mode, cap, points):
    rec = run(risk_mode=mode)["recommendation"]
    assert sum(t["stake"] for t in rec["tickets"]) <= cap
    assert len(rec["tickets"]) <= points
    assert rec["max_points"] == points


def test_zero_probability_does_not_fall_back_to_raw():
    preds = deepcopy(PREDS)
    preds[0].update(p_norm=0, p_raw=100, odds=200)
    preds[1]["p_norm"] = .8
    result = run(preds=preds)
    assert result["predictions"][0]["expected_value"] == 0
    assert not any(t["horse_numbers"] == [1] for t in result["recommendation"]["tickets"])


@pytest.mark.parametrize("mutate,code", [
    (lambda p: p[0].update(p_norm=float("nan")), "probabilities_invalid"),
    (lambda p: p[0].update(p_norm=-.1), "probabilities_invalid"),
    (lambda p: p[0].update(p_norm=.6), "probabilities_invalid"),
    (lambda p: p[0].update(horse_number=2), "runner_invalid"),
    (lambda p: p[0].update(odds=float("inf")), "odds_incomplete"),
    (lambda p: p[0].update(odds=None), "odds_incomplete"),
])
def test_invalid_runner_input(mutate, code):
    preds = deepcopy(PREDS)
    mutate(preds)
    rec = run(preds=preds)["recommendation"]
    assert rec["decision"] == "skip" and code in rec["reason_codes"]


def test_feature_missingness_and_lookahead_validation():
    for mutate, code in [
        (lambda c: c.update(feature_missing_rate=.21), "input_incomplete"),
        (lambda c: c.update(feature_missing_by_runner=[.01, .01, .21]), "input_incomplete"),
        (lambda c: c.update(all_missing_features=["safe_past10_avg_finish"]), "input_incomplete"),
        (lambda c: c["probability_validation"].update(validation_period_end="2026-10-10"), "validation_timing_unknown"),
        (lambda c: c["probability_validation"].update(validated_at="2026-10-11T00:00:00Z"), "validation_timing_unknown"),
    ]:
        evidence = context()
        mutate(evidence)
        rec = run(evidence=evidence)["recommendation"]
        assert code in rec["reason_codes"] and not rec["tickets"]


def test_joint_tickets_use_own_odds_and_probability_and_deduplicate():
    evidence = context()
    combo_capture = {"schema": "netkeiba-combination-odds-capture-v1", "race_id": RACE["race_id"],
        "bet_type": "馬連", "source_url": f"https://race.netkeiba.com/odds/index.html?type=b6&race_id={RACE['race_id']}",
        "http_status": 200, "odds_status": "middle", "response_sha256": "b" * 64,
        "observed_at": NOW.isoformat(),
        "odds_by_combination": {"1-2": 12}}
    ticket = {"race_id": RACE["race_id"], "model_id": "test-model", "probability_validated": True,
        "bet_type": "馬連", "horse_numbers": [1, 2], "probability": .25, "odds": 12,
        "odds_status": "middle", "observed_at": NOW.isoformat(), "odds_evidence": combo_capture}
    evidence["joint_tickets"] = [ticket, {**ticket, "horse_numbers": [2, 1]}]
    rec = run(evidence=evidence)["recommendation"]
    pair = [t for t in rec["tickets"] if t["bet_type"] == "馬連"]
    assert len(pair) == 1
    assert pair[0]["expected_value"] == 3  # not average single-win EV=2.05
    assert pair[0]["combination"] == "1-2"


@pytest.mark.parametrize("change", [{"probability_validated": False}, {"race_id": "wrong"}, {"model_id": "wrong"},
    {"odds_status": "result"}, {"odds": None}, {"horse_numbers": [1, 1]}])
def test_unproven_joint_tickets_never_created(change):
    evidence = context()
    evidence["joint_tickets"] = [{"race_id": RACE["race_id"], "model_id": "test-model", "probability_validated": True,
        "bet_type": "馬連", "horse_numbers": [1, 2], "probability": .25, "odds": 12,
        "odds_status": "middle", "observed_at": NOW.isoformat(), **change}]
    assert not run(evidence=evidence)["bet_types"]["馬連"]


def test_joint_ticket_without_exact_combo_market_capture_is_excluded():
    evidence = context()
    evidence["joint_tickets"] = [{"race_id": RACE["race_id"], "model_id": "test-model", "probability_validated": True,
        "bet_type": "馬連", "horse_numbers": [1, 2], "probability": .25, "odds": 12,
        "odds_status": "middle", "observed_at": NOW.isoformat()}]
    assert not run(evidence=evidence)["bet_types"]["馬連"]


def test_does_not_mutate_predictions_and_default_live_wrapper_skips():
    preds, original = deepcopy(PREDS), deepcopy(PREDS)
    result = BettingRecommender(10000).analyze_and_recommend(preds, RACE)
    assert result["recommendation"]["decision"] == "skip"
    assert preds == original


def report_for(path):
    return {"schema_version": 1, "status": "validated", "model_id": path.stem,
        "model_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "probability_kind": "race_normalized_win",
        "evaluation_method": "out_of_sample", "race_count": 1000,
        "selection_calibration_reviewed": True,
        "metrics": {"nll": 1.0, "brier": .5, "uniform_nll": 1.8, "uniform_brier": .7,
            "selection_ece": .04, "selection_absolute_bias": .02}, "validation_period_start": "2026-01-01",
        "validation_period_end": "2026-09-01", "validated_at": "2026-10-01T00:00:00Z",
        "eligible_bet_types": ["単勝"]}


def test_sidecar_binds_to_exact_model_artifact(tmp_path):
    path = tmp_path / "test.joblib"
    path.write_bytes(b"fixed-model")
    assert not load_probability_validation(path, target="win", calibrated=True)["validated"]
    report = report_for(path)
    path.with_suffix(".betting-validation.json").write_text(json.dumps(report), encoding="utf-8")
    assert load_probability_validation(path, target="win", calibrated=True)["validated"]
    assert not load_probability_validation(path, target="speed_deviation", calibrated=True)["validated"]
    assert not load_probability_validation(path, target="win", calibrated=False)["validated"]
    path.write_bytes(b"changed-model")
    assert not load_probability_validation(path, target="win", calibrated=True)["validated"]


@pytest.mark.parametrize("metric,value", [
    ("nll", 2.0), ("brier", .8), ("selection_ece", .081), ("selection_absolute_bias", .051),
])
def test_probability_validation_sidecar_must_pass_quality_gates(tmp_path, metric, value):
    path = tmp_path / "test.joblib"
    path.write_bytes(b"fixed-model")
    report = report_for(path)
    report["metrics"][metric] = value
    path.with_suffix(".betting-validation.json").write_text(json.dumps(report), encoding="utf-8")
    assert not load_probability_validation(path, target="win", calibrated=True)["validated"]


def test_internal_adapter_does_not_invent_source_time_or_joint_probabilities(tmp_path):
    evidence = build_live_context(model_path=tmp_path / "none.joblib", predictor=SimpleNamespace(target="speed_deviation"),
        source_records=[{"horse_number": 1, "odds": 4}], feature_frame=pd.DataFrame({"x": [None]}))
    assert not evidence["probability_validation"]["validated"]
    assert evidence["sources"][0]["observed_at"] is None
    assert evidence["feature_missing_rate"] == 1
    assert evidence["feature_missing_by_runner"] == [1.0]
    assert evidence["all_missing_features"] == ["x"]
    assert evidence["joint_tickets"] == []


@pytest.mark.parametrize("change", [[], {"metrics": []}, {"race_count": True}, {"race_count": 1},
    {"selection_calibration_reviewed": False}, {"validation_period_start": "bad"},
    {"validation_period_start": "2026-10-01"}, {"validated_at": "2026-08-01T00:00:00Z"}])
def test_invalid_sidecar_fails_closed_not_api_error(tmp_path, change):
    path = tmp_path / "test.joblib"
    path.write_bytes(b"fixed-model")
    report = {**report_for(path), **change} if isinstance(change, dict) else change
    path.with_suffix(".betting-validation.json").write_text(json.dumps(report), encoding="utf-8")
    assert not load_probability_validation(path, target="win", calibrated=True)["validated"]


def test_calibration_success_flag_never_borrows_rank_score_temperature():
    import numpy as np
    from routers.predict import ModelPredictor

    class Model:
        def predict(self, _):
            return np.array([.6, .4])

    class Calibration:
        def predict(self, p):
            return p

    class BrokenCalibration:
        def predict(self, p):
            raise ValueError("unavailable")

    class InvalidCalibration:
        def predict(self, p):
            return np.array([float("nan"), .4])

    class WrongShapeCalibration:
        def predict(self, p):
            return np.array([[.6], [.4]])

    X = pd.DataFrame({"x": [1, 2]})
    for target, calibrator, applied in [
        ("win", Calibration(), True), ("win", BrokenCalibration(), False),
        ("win", InvalidCalibration(), False), ("win", WrongShapeCalibration(), False),
        ("speed_deviation", Calibration(), False), ("win", None, False),
    ]:
        predictor = ModelPredictor({"model": Model(), "target": target, "calibrator": calibrator}, "test.joblib")
        _, ratios = predictor.predict_scores(X)
        assert predictor.calibrator_applied is applied
        assert ratios.shape == (2,) and np.isfinite(ratios).all()


def test_live_score_adapter_and_response_contract_do_not_reintroduce_buys(tmp_path):
    import numpy as np
    from routers.predict import ModelPredictor
    from models import AnalyzeRaceResponse

    class Model:
        def predict(self, _):
            return np.array([-5, -6, -9])

    features = pd.DataFrame({"x": [1, 2, 3]})
    predictor = ModelPredictor({"model": Model(), "target": "speed_deviation"}, "active-speed.joblib")
    raw, ratios = predictor.predict_scores(features)
    predictions = [{**pred, "p_raw": float(raw[i]), "p_norm": float(ratios[i])} for i, pred in enumerate(PREDS)]
    evidence = build_live_context(model_path=tmp_path / "active-speed.joblib", predictor=predictor,
        source_records=[{"horse_number": p["horse_number"], "odds_status": "middle",
            "data_observed_at": NOW.isoformat()} for p in predictions], feature_frame=features)
    result = run(preds=predictions, evidence=evidence)
    response = AnalyzeRaceResponse(success=True, model_id="active-speed", **result).model_dump(mode="json")
    assert response["recommendation"]["policy_version"] == "value-selection-v4"
    assert response["recommendation"]["probability_basis"] == "uncalibrated_score"
    assert response["recommendation"]["decision"] == "skip"
    assert all(not tickets for tickets in response["bet_types"].values())
    json.dumps(response, allow_nan=False)
