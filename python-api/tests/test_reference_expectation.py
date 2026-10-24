"""Reference arithmetic never grants purchase, live-odds or forward authority."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import numpy as np
import pandas as pd
import pytest

from betting.reference_value import build_reference_expectation
from betting.value_policy import recommend

NOW = datetime(2026, 10, 11, 11, 0, tzinfo=timezone.utc)
MODEL = "model_speed_deviation_lightgbm_dbshared_fixture"
RACE = {"race_id": "202605040411", "date": "20261011", "post_time": "15:45", "num_horses": 2}
PREDS = [
    {"horse_number": 1, "p_norm": .13, "odds": 5.7, "expected_value": None},
    {"horse_number": 2, "p_norm": .87, "odds": 3., "expected_value": None},
]


def context():
    return {
        "input_diagnostics": {
            "schema": "prediction-input-contract-v1", "feature_pipeline": "local-db-shared-v1",
            "status": "unverified", "expected_count": 47, "actual_count": 47,
            "missing_columns": [], "blocked_columns": [],
            "source_hash_matches": True, "semantic_parity_verified": True,
            "history_available": True, "live_capture_hash_bound": False,
            "current_outcomes_consumed": False, "issue_codes": ["pre_race_db_capture_unverified"],
            "missing_value_rate": .10,
        },
        "probability_validation": {
            "validated": True, "model_id": MODEL, "model_sha256": "a" * 64,
            "probability_kind": "race_normalized_win", "evaluation_method": "out_of_sample",
            "selection_calibration_reviewed": True, "eligible_bet_types": ["単勝"],
            "validation_period_end": "2026-09-13", "validated_at": "2026-10-11T10:30:00Z",
        },
        "feature_missing_rate": .10,
        # Old/result observations are intentionally not converted to fresh odds.
        "sources": [{"horse_number": n, "has_result": True, "odds_status": "result",
                     "observed_at": "2026-10-10T01:00:00Z"} for n in (1, 2)],
    }


def recommendation():
    return {
        "policy_version": "value-selection-v2", "probability_basis": "calibrated",
        "input_verified": False, "decision": "skip", "tickets": [],
        "purchase_count": 0, "unit_price": 0, "total_cost": 0, "expected_return": 0.,
    }


def build(**kwargs):
    args = dict(predictions=deepcopy(PREDS), race_info=deepcopy(RACE),
                recommendation=recommendation(), context=context(), model_id=MODEL,
                environment="test", now=NOW)
    args.update(kwargs)
    return build_reference_expectation(**args)


def test_calibrated_display_trial_is_separate_and_does_not_mutate_inputs():
    ctx, rec, preds, race = context(), recommendation(), deepcopy(PREDS), deepcopy(RACE)
    before = deepcopy((ctx, rec, preds, race))
    result = build(context=ctx, recommendation=rec, predictions=preds, race_info=race)
    assert result["schema"] == "calibrated-win-reference-v1"
    assert result["items"][0]["expected_value"] == pytest.approx(.741)
    assert result["items"][1]["expected_value"] == pytest.approx(2.61)
    assert result["purchase_eligible"] is False
    assert result["capture_verified"] is False
    assert result["odds_freshness_verified"] is False
    assert result["computed_at"] == NOW.isoformat()
    assert result["valid_until"] == (NOW + timedelta(minutes=5)).isoformat()
    assert (ctx, rec, preds, race) == before
    assert all(p["expected_value"] is None for p in preds)


@pytest.mark.parametrize("environment", ["production", "staging", "unknown", "", None, {}])
def test_nonlocal_environment_has_no_reference_field(environment):
    assert build(environment=environment) is None


@pytest.mark.parametrize("key,value", [
    ("schema", "unknown"), ("feature_pipeline", "legacy"), ("status", "aligned"),
    ("expected_count", 46), ("expected_count", 47.), ("actual_count", 46),
    ("missing_columns", ["age"]), ("blocked_columns", ["finish"]),
    ("source_hash_matches", False), ("semantic_parity_verified", False),
    ("history_available", False), ("live_capture_hash_bound", True),
    ("current_outcomes_consumed", True), ("current_outcomes_consumed", None),
    ("missing_value_rate", None), ("missing_value_rate", .11),
    ("issue_codes", []), ("issue_codes", ["target_not_after_training_evaluation_period"]),
    ("issue_codes", ["pre_race_db_capture_unverified", "history_missing"]),
    ("issue_codes", [["pre_race_db_capture_unverified"]]),
])
def test_only_capture_gap_with_exact_shared_formula_is_eligible(key, value):
    ctx = context()
    ctx["input_diagnostics"][key] = value
    assert build(context=ctx) is None


def test_capture_receipt_failure_can_only_generate_a_reference():
    ctx = context()
    ctx["input_diagnostics"]["issue_codes"] = ["pre_race_db_capture_failed"]
    assert build(context=ctx)["purchase_eligible"] is False


@pytest.mark.parametrize("key,value", [
    ("validated", False), ("validated", 1), ("model_id", "other-model"),
    ("model_sha256", "a" * 63), ("probability_kind", "unnormalized_win"),
    ("evaluation_method", "in_sample"), ("selection_calibration_reviewed", False),
    ("eligible_bet_types", []), ("eligible_bet_types", "単勝"),
    ("validated_at", "2026-10-11T11:00:01Z"), ("validated_at", "2026-10-11T11:00:00"),
    ("validated_at", None), ("validation_period_end", "2026-10-11"),
    ("validated_at", "0001-01-01T00:00:00+01:00"),
    ("validation_period_end", "bad-date"),
])
def test_exact_model_calibration_and_temporal_validation_are_required(key, value):
    ctx = context()
    ctx["probability_validation"][key] = value
    assert build(context=ctx) is None


@pytest.mark.parametrize("key,value", [
    ("probability_basis", "uncalibrated_score"), ("input_verified", True),
    ("input_verified", None), ("policy_version", "value-selection-v1"),
    ("decision", "buy"), ("decision", "priority"),
    ("tickets", [{"stake": 100}]), ("purchase_count", 1),
    ("unit_price", 100), ("total_cost", 100), ("expected_return", 1),
])
def test_reference_cannot_revive_or_contradict_a_recommendation(key, value):
    rec = recommendation()
    rec[key] = value
    assert build(recommendation=rec) is None


@pytest.mark.parametrize("missing_rate", [None, True, "0.1", -.01, .200001, float("nan"), float("inf"), 10**1000])
def test_incomplete_feature_inputs_remain_blocked(missing_rate):
    ctx = context()
    ctx["feature_missing_rate"] = missing_rate
    assert build(context=ctx) is None


@pytest.mark.parametrize("probability", [None, True, "0.13", -1., 1.1, float("nan"), float("inf")])
def test_invalid_probability_blocks_entire_distribution(probability):
    preds = deepcopy(PREDS)
    preds[0]["p_norm"] = probability
    assert build(predictions=preds) is None


@pytest.mark.parametrize("odds", [None, True, "5.7", 0., -1., .99, float("nan"), float("inf")])
def test_missing_or_invalid_odds_is_not_zero_filled_or_inferred(odds):
    preds = deepcopy(PREDS)
    preds[0]["odds"] = odds
    result = build(predictions=preds)
    assert [row["horse_number"] for row in result["items"]] == [2]
    preds[1]["odds"] = None
    assert build(predictions=preds) is None


@pytest.mark.parametrize("problem", ["duplicate", "number", "count", "sum", "naive_now", "date", "empty_model"])
def test_inconsistent_cohort_or_time_is_never_repaired(problem):
    preds, race, args = deepcopy(PREDS), deepcopy(RACE), {}
    if problem == "duplicate": preds[1]["horse_number"] = 1
    if problem == "number": preds[1]["horse_number"] = True
    if problem == "count": race["num_horses"] = 3
    if problem == "sum": preds[1]["p_norm"] = .5
    if problem == "naive_now": args["now"] = NOW.replace(tzinfo=None)
    if problem == "date": race["date"] = "bad-date"
    if problem == "empty_model": args["model_id"] = ""
    assert build(predictions=preds, race_info=race, **args) is None


@pytest.mark.parametrize("now", ["invalid", False, 0, datetime.max.replace(tzinfo=timezone.utc)])
def test_invalid_or_overflowing_calculation_clock_does_not_break_prediction(now):
    assert build(now=now) is None


def test_official_purchase_policy_and_ev_remain_unchanged_even_above_one():
    ctx, preds = context(), deepcopy(PREDS)
    selected = recommend(preds, deepcopy(RACE), bankroll=10000, risk_mode="balanced",
                         min_ev=1.05, use_kelly=True, dynamic_unit=True, context=ctx, now=NOW)
    before = deepcopy(selected)
    reference = build(predictions=selected["predictions"], recommendation=selected["recommendation"], context=ctx)
    assert reference["items"][1]["expected_value"] > 1
    assert selected == before
    assert selected["recommendation"]["decision"] == "skip"
    assert selected["recommendation"]["tickets"] == []
    assert selected["recommendation"]["total_cost"] == 0
    assert all(p["expected_value"] is None for p in selected["predictions"])


def test_response_serialization_preserves_the_separate_reference_contract():
    from models import AnalyzeRaceResponse
    response = AnalyzeRaceResponse(success=True, model_id=MODEL, race_info=RACE,
        predictions=PREDS, recommendation=recommendation(), reference_expectation=build(),
        pro_evaluation={}, bet_types={}, best_bet_type="", best_bet_info={}, race_level="skip")
    data = response.model_dump()
    assert data["reference_expectation"]["items"][0]["expected_value"] == pytest.approx(.741)
    assert data["predictions"] == PREDS
    assert data["recommendation"] == recommendation()


@pytest.mark.parametrize("environment", ["development", "production"])
def test_analyze_endpoint_connects_reference_without_changing_official_payload(tmp_path, monkeypatch, environment):
    """Temporary card and fake inference only; no network, training or live writes."""
    import app_config
    from models import AnalyzeRaceRequest
    from observation import capture
    from routers import predict
    from services import prediction_compute, db_shared_input

    path = tmp_path / (MODEL + ".joblib")
    path.write_bytes(b"test-only-model-identity")
    race = {**RACE, "distance": 1600, "venue": "東京", "track_type": "芝", "race_name": "fixture"}
    horses = [dict(race_id=RACE["race_id"], horse_number=p["horse_number"],
        horse_id=f"202300000{p['horse_number']}", horse_name=f"fixture-{p['horse_number']}",
        odds=p["odds"], age=3, distance=1600) for p in PREDS]
    database = tmp_path / "temporary-card.db"
    with sqlite3.connect(database) as db:
        db.executescript("CREATE TABLE races_ultimate(race_id TEXT, data TEXT);"
                        "CREATE TABLE race_results_ultimate(race_id TEXT, data TEXT);")
        db.execute("INSERT INTO races_ultimate VALUES (?,?)", (race["race_id"], json.dumps(race)))
        db.executemany("INSERT INTO race_results_ultimate VALUES (?,?)",
                       [(race["race_id"], json.dumps(horse)) for horse in horses])
    ctx = context()
    # The endpoint owns its real calculation clock; only fixture model evidence
    # predates it. This does not backdate any card/capture receipt.
    ctx["probability_validation"]["validated_at"] = "2026-09-14T00:00:00Z"
    bundle = {"feature_pipeline": "local-db-shared-v1", "model": object(), "model_id": MODEL,
              "target": "speed_deviation"}
    class FakePredictor:
        def __init__(self, actual_bundle, actual_path):
            assert actual_bundle is bundle and actual_path == path
            self.bundle, self.target = bundle, "speed_deviation"
            self.calibrator_applied = False
            self.input_diagnostics = None
    async def compute(**kwargs):
        assert kwargs["db_path"] == str(database)
        return {"features": pd.DataFrame({"safe_age": [3., 3.]}), "raw": np.array([2., 1.]),
                "probabilities": np.array([.13, .87]), "calibrator_applied": True,
                "input_diagnostics": deepcopy(ctx["input_diagnostics"]), "explanations": None,
                "place3": None, "timings": {}, "history_rows": 0}
    monkeypatch.setattr(app_config, "APP_ENV", environment)
    monkeypatch.setattr(predict, "ULTIMATE_DB", database)
    monkeypatch.setattr(predict, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(predict, "_ANALYZE_CACHE", {})
    monkeypatch.setattr(predict, "_resolve_model_path", lambda _: path)
    monkeypatch.setattr(predict, "_local_candidate_spec", lambda **_: None)
    monkeypatch.setattr(predict, "_load_served_model_bundle", lambda _: bundle)
    monkeypatch.setattr(predict, "ModelPredictor", FakePredictor)
    monkeypatch.setattr(predict, "stored_rows_need_refresh", lambda *_: False)
    monkeypatch.setattr(db_shared_input, "verify_bundle_sources", lambda _: None)
    monkeypatch.setattr(db_shared_input, "shared_pre_race_input_needs_refresh", lambda *_, **__: False)
    monkeypatch.setattr(prediction_compute, "run_prediction_compute", compute)
    monkeypatch.setattr(predict, "build_live_context", lambda **_: deepcopy(ctx))
    monkeypatch.setattr(predict, "_save_prediction_log", lambda *_: None)
    monkeypatch.setattr(capture, "capture_analyze_predictions", lambda **_: {"enabled": False})
    response = asyncio.run(predict._analyze_race_impl(AnalyzeRaceRequest(race_id=race["race_id"], model_id=MODEL)))
    data = response.model_dump()
    if environment == "development":
        assert data["reference_expectation"]["items"][0]["expected_value"] == pytest.approx(.741)
    else:
        assert data["reference_expectation"] is None
    assert data["recommendation"]["decision"] == "skip"
    assert data["recommendation"]["tickets"] == []
    assert data["recommendation"]["total_cost"] == 0
    assert all(p["expected_value"] is None for p in data["predictions"])
    assert all("reference_expected_value" not in p for p in data["predictions"])
