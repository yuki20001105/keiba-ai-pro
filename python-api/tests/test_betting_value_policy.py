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
        "sources": [{"horse_number": n, "odds_status": "middle", "observed_at": NOW.isoformat(), "has_result": False} for n in (1, 2, 3)],
        "joint_tickets": [],
    }


def run(preds=None, info=None, evidence=None, **kwargs):
    return recommend(deepcopy(PREDS if preds is None else preds), deepcopy(RACE if info is None else info),
        bankroll=kwargs.pop("bankroll", 10000), risk_mode=kwargs.pop("risk_mode", "balanced"),
        min_ev=kwargs.pop("min_ev", 1.2), use_kelly=kwargs.pop("use_kelly", True),
        dynamic_unit=kwargs.pop("dynamic_unit", True), context=context() if evidence is None else evidence,
        now=kwargs.pop("now", NOW), **kwargs)


def test_exact_tickets_and_caps_no_heuristic_combinations():
    result = run()
    rec = result["recommendation"]
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
        (lambda c: c["probability_validation"].update(validation_period_end="2026-10-10"), "validation_timing_unknown"),
        (lambda c: c["probability_validation"].update(validated_at="2026-10-11T00:00:00Z"), "validation_timing_unknown"),
    ]:
        evidence = context()
        mutate(evidence)
        rec = run(evidence=evidence)["recommendation"]
        assert code in rec["reason_codes"] and not rec["tickets"]


def test_joint_tickets_use_own_odds_and_probability_and_deduplicate():
    evidence = context()
    ticket = {"race_id": RACE["race_id"], "model_id": "test-model", "probability_validated": True,
        "bet_type": "馬連", "horse_numbers": [1, 2], "probability": .25, "odds": 12,
        "odds_status": "middle", "observed_at": NOW.isoformat()}
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
        "metrics": {"nll": 1.8, "brier": .7}, "validation_period_start": "2026-01-01",
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


def test_internal_adapter_does_not_invent_source_time_or_joint_probabilities(tmp_path):
    evidence = build_live_context(model_path=tmp_path / "none.joblib", predictor=SimpleNamespace(target="speed_deviation"),
        source_records=[{"horse_number": 1, "odds": 4}], feature_frame=pd.DataFrame({"x": [None]}))
    assert not evidence["probability_validation"]["validated"]
    assert evidence["sources"][0]["observed_at"] is None
    assert evidence["feature_missing_rate"] == 1
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
    assert response["recommendation"]["policy_version"] == "value-selection-v2"
    assert response["recommendation"]["probability_basis"] == "uncalibrated_score"
    assert response["recommendation"]["decision"] == "skip"
    assert all(not tickets for tickets in response["bet_types"].values())
    json.dumps(response, allow_nan=False)
