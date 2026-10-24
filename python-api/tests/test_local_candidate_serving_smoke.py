"""Offline fixtures only: never execute the live opt-in candidate serving run."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import time
from urllib.error import HTTPError, URLError

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("local_candidate_serving_smoke", ROOT / "scripts/smoke_local_candidate_serving.py")
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)
TOKEN = "fixture.privatejwt.signature"
EMAIL, PASSWORD, PRIVATE = "fixture@example.test", "fixture-password-secret", "private-owner-secret"


class Response(BytesIO):
    status = 200


class Opener:
    def __init__(self, bodies):
        self.bodies, self.requests, self.timeouts = list(bodies), [], []

    def open(self, request, timeout):
        self.requests.append(request)
        self.timeouts.append(timeout)
        body = self.bodies.pop(0)
        if isinstance(body, Exception):
            raise body
        return Response(body if isinstance(body, bytes) else json.dumps(body).encode())


def success_body():
    predictions = [{"horse_number": i, "predicted_rank": i, "p_raw": 17.0 - i, "horse_name": PRIVATE,
        "explanation": {"method": "tree_shap", "base_value": 0.1,
                        "features": [{"feature": PRIVATE, "contribution": 0.3}]}}
        for i in range(1, 17)]
    tickets = [{"bet_type": kind, "combination": "-".join(map(str, range(1, smoke.BET_CARDINALITIES[kind] + 1))),
                "horse_numbers": list(range(1, smoke.BET_CARDINALITIES[kind] + 1))}
               for kind in sorted(smoke.BET_TYPES)]
    return {"success": True, "model_id": smoke.MODEL_ID, "race_info": {"race_id": smoke.RACE_ID},
        "predictions": predictions, "explanation_method": "tree_shap", "performance_snapshot_id": PRIVATE,
        "input_diagnostics": {
            "scope": "local-safe-candidate-input-v1", "source_hash_matches": True,
            "history_available": True, "blocked_columns": [], "issue_codes": ["card_capture_provenance_unverified"],
            "schema": "prediction-input-contract-v1", "status": "unverified", "expected_count": 47,
            "actual_count": 47, "missing_columns": [], "semantic_parity_verified": True,
            "live_capture_hash_bound": False, "current_outcomes_consumed": False,
            "same_day_or_future_history_used": False, "score_is_probability": False,
            "calibration_verified": False, "profitability_verified": False},
        "reference_bets": {"schema": "rank-reference-bets-v1", "basis": "ranking_only",
                           "purchase_eligible": False, "monetary_amounts_assigned": False, "tickets": tickets},
        "recommendation": {"decision": "skip", "tickets": [], "purchase_count": 0,
                           "unit_price": 0, "total_cost": 0, "expected_return": 0}}


def fixture_environment(root):
    (root / ".env.local").write_text(
        "NEXT_PUBLIC_SUPABASE_URL=https://fixture-project.supabase.co\n"
        "NEXT_PUBLIC_SUPABASE_ANON_KEY=sb_publishable_fixture\nML_API_URL=http://localhost:8000\n",
        encoding="utf-8")
    (root / ".env.test").write_text(f"E2E_EMAIL={EMAIL}\nE2E_PASSWORD={PASSWORD}\n", encoding="utf-8")


def fixture_state(root):
    db = root / "keiba/data/keiba_ultimate.db"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as connection:
        connection.executescript("""
            CREATE TABLE races_ultimate(race_id TEXT PRIMARY KEY,data TEXT,created_at TEXT);
            CREATE TABLE race_results_ultimate(id INTEGER PRIMARY KEY,race_id TEXT,data TEXT);
            CREATE TABLE return_tables_ultimate(id INTEGER PRIMARY KEY,race_id TEXT,bet_type TEXT,payout INT);
            CREATE TABLE prediction_log(private TEXT);
        """)
        connection.execute("INSERT INTO races_ultimate VALUES(?,?,?)", (smoke.RACE_ID, PRIVATE, "2026-10-10"))
        connection.executemany("INSERT INTO race_results_ultimate VALUES(?,?,?)",
            [(i, smoke.RACE_ID, json.dumps({"horse_number": i})) for i in range(1, 17)])
    directory = root / "python-api/models"
    directory.mkdir(parents=True)
    (directory / ".active_model.json").write_text('{"model_id":"model_preserved"}', encoding="utf-8")
    (directory / "model_preserved.joblib").write_bytes(b"preserved-fixture-artifact")
    return db


def successful_opener(body=None):
    return Opener([
        {"access_token": TOKEN, "refresh_token": PRIVATE, "user": {"email": EMAIL}},
        {"models": [{"model_id": smoke.MODEL_ID}] + [{"private": PRIVATE}] * 7, "count": 999},
        {"races": [{"private": PRIVATE}] * 24, "count": 999},
        body or success_body(),
    ])


def test_default_never_authenticate_or_read_source(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("default must not read credentials/source or create opener")
    for name in ("configuration", "secure_opener", "authenticate", "protected_state"):
        monkeypatch.setattr(smoke, name, forbidden)
    opener = Opener([])
    result = smoke.run_smoke(execute=False, root=tmp_path, opener=opener)
    assert result["authentication_status"] == result["prediction_status"] == "NOT_RUN"
    assert result["prediction_post_attempts"] == 0 and opener.requests == []
    assert not result["serving_gate_passed"] and not result["release_complete"]


def test_fixed_one_prediction_after_auth_two_gets_secret_free(tmp_path):
    fixture_environment(tmp_path)
    fixture_state(tmp_path)
    opener = successful_opener()
    result = smoke.run_smoke(execute=True, root=tmp_path, opener=opener)
    assert result["serving_gate_passed"] and result["protected_state_unchanged"]
    assert result["personal_snapshot_id_present"] and result["prediction_post_attempts"] == 1
    assert [probe["count"] for probe in result["probes"]] == [8, 24]
    assert len(opener.requests) == 4
    auth, models, races, predict = opener.requests
    assert auth.method == "POST" and "/auth/v1/token?grant_type=password" in auth.full_url
    assert json.loads(auth.data) == {"email": EMAIL, "password": PASSWORD}
    assert [(request.method, request.full_url) for request in (models, races, predict)] == [
        ("GET", smoke.ORIGIN + smoke.GET_ENDPOINTS[0]),
        ("GET", smoke.ORIGIN + smoke.GET_ENDPOINTS[1]),
        ("POST", smoke.ORIGIN + smoke.PREDICT_ENDPOINT)]
    assert json.loads(predict.data) == {"race_id": smoke.RACE_ID, "model_id": smoke.MODEL_ID, "include_explanation": True}
    assert all(0 < timeout <= 90 for timeout in opener.timeouts)
    assert opener.timeouts[0] == 8 and all(timeout <= 8 for timeout in opener.timeouts[1:3])
    for request in (models, races, predict):
        assert request.get_header("Authorization") == "Bearer " + TOKEN
        assert not request.has_header("Cookie") and not request.has_header("Apikey")
    assert result["prediction_validation"]["tree_shap_count"] == 16
    assert result["prediction_validation"]["reference_type_count"] == 6
    serialized = json.dumps(result)
    for secret in (EMAIL, PASSWORD, TOKEN, PRIVATE, "fixture-project"):
        assert secret not in serialized
    for key in ("actual_ticket_posts", "paper_ticket_posts", "collection_calls", "activation_calls", "restart_calls", "training_calls"):
        assert result[key] == 0
    assert result["all_five_features_completed"] is result["profitability_validated"] is False


@pytest.mark.parametrize("endpoint", ["/api/performance", "/api/performance/paper", "/api/performance/actual",
    "/api/scrape", "/api/ml/train/start", "/api/analyze_race", "/api/models?extra=1",
    "https://evil.test/api/analyze-race", "/api/races/by-date?date=20261011"])
def test_nonallowlisted_endpoint_rejected(endpoint):
    with pytest.raises(smoke.SafeSmokeError, match="ENDPOINT_REJECTED"):
        smoke.fixed_request(endpoint, TOKEN)


def test_original_data_hash_readonly_and_prediction_log_not_part_of_source(tmp_path):
    db = fixture_state(tmp_path)
    before = smoke.protected_state(tmp_path)
    with sqlite3.connect(db) as connection:
        connection.execute("INSERT INTO prediction_log VALUES(?)", (PRIVATE,))
    assert smoke.protected_state(tmp_path) == before
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE race_results_ultimate SET data=? WHERE id=1", ("changed",))
    assert smoke.protected_state(tmp_path)["target_source_rows_sha256"] != before["target_source_rows_sha256"]


def test_source_change_during_prediction_fails_gate(tmp_path, monkeypatch):
    fixture_environment(tmp_path)
    db = fixture_state(tmp_path)
    opener = successful_opener()
    original = opener.open
    def mutate(request, timeout):
        if request.full_url == smoke.ORIGIN + smoke.PREDICT_ENDPOINT:
            with sqlite3.connect(db) as connection:
                connection.execute("UPDATE race_results_ultimate SET data=? WHERE id=1", ("changed",))
        return original(request, timeout)
    monkeypatch.setattr(opener, "open", mutate)
    result = smoke.run_smoke(execute=True, root=tmp_path, opener=opener)
    assert result["prediction_validation"]["passed"] and not result["protected_state_unchanged"]
    assert not result["serving_gate_passed"]


def test_list_mismatch_aborts_before_prediction(tmp_path):
    fixture_environment(tmp_path)
    fixture_state(tmp_path)
    opener = successful_opener()
    opener.bodies[2] = {"races": []}
    result = smoke.run_smoke(execute=True, root=tmp_path, opener=opener)
    assert result["failure_code"] == "EXPECTED_LOCAL_LISTS_NOT_CONFIRMED"
    assert result["prediction_post_attempts"] == 0 and len(opener.requests) == 3


@pytest.mark.parametrize("section,key,value", [
    (None, "model_id", "active-default"), (None, "success", False),
    ("input_diagnostics", "actual_count", 46),
    ("input_diagnostics", "missing_columns", ["missing"]),
    ("input_diagnostics", "semantic_parity_verified", False),
    ("input_diagnostics", "status", "aligned"),
    ("input_diagnostics", "live_capture_hash_bound", True),
    ("input_diagnostics", "current_outcomes_consumed", True),
    ("input_diagnostics", "same_day_or_future_history_used", True),
    ("input_diagnostics", "score_is_probability", True),
    ("reference_bets", "purchase_eligible", True),
    ("reference_bets", "monetary_amounts_assigned", True),
    ("reference_bets", "tickets", []),
    ("recommendation", "decision", "buy"), ("recommendation", "total_cost", 100),
    ("recommendation", "tickets", [{"stake": 100}]),
])
def test_semantic_parity_does_not_waive_monetary_or_cutoff_gates(section, key, value):
    body = success_body()
    target = body if section is None else body[section]
    target[key] = value
    assert not smoke.validate_prediction(body)["passed"]


def test_shap_identity_and_nonmonetary_reference_cannot_be_lost():
    body = success_body()
    body["predictions"][0]["explanation"] = None
    assert smoke.validate_prediction(body)["tree_shap_count"] == 15
    assert not smoke.validate_prediction(body)["passed"]
    body = success_body()
    body["predictions"][1]["horse_number"] = 1
    assert not smoke.validate_prediction(body)["checks"]["runner_identity_count"]
    body = success_body()
    body["reference_bets"]["tickets"][0]["stake"] = 100
    assert not smoke.validate_prediction(body)["checks"]["all_six_reference_types_only"]


def test_body_and_deadline_bounded_without_private_error_details(monkeypatch):
    monkeypatch.setattr(smoke, "MAX_BODY_BYTES", 50)
    status, _ = smoke.request_json(Opener([b"x" * 51]), smoke.PREDICT_ENDPOINT, TOKEN, time.monotonic() + 90)
    assert status == "RESPONSE_REJECTED"
    status, _ = smoke.request_json(Opener([b'{"a":NaN}']), smoke.PREDICT_ENDPOINT, TOKEN, time.monotonic() + 90)
    assert status == "RESPONSE_REJECTED"
    with pytest.raises(smoke.SafeSmokeError):
        smoke.bounded_json(Response(b"{}"), -1)
    opener = Opener([{}])
    status, _ = smoke.request_json(opener, smoke.PREDICT_ENDPOINT, TOKEN, -1)
    assert status == "RESPONSE_REJECTED" and opener.requests == []


def test_error_and_redirect_body_never_read(tmp_path):
    class Unreadable(BytesIO):
        def read(self, *args):
            raise AssertionError("private error body must not be read")
        read1 = read
    error = HTTPError(smoke.ORIGIN + smoke.PREDICT_ENDPOINT, 302, PRIVATE,
        {"Location": "https://evil.test/" + TOKEN}, Unreadable(PRIVATE.encode()))
    opener = Opener([error])
    result = smoke.request_json(opener, smoke.PREDICT_ENDPOINT, TOKEN, time.monotonic() + 90)
    assert result == (302, None) and len(opener.requests) == 1
    assert smoke.request_json(Opener([URLError(PRIVATE + TOKEN)]), smoke.PREDICT_ENDPOINT, TOKEN,
        time.monotonic() + 90) == ("TRANSPORT_FAILED", None)


def test_auth_failure_secret_free_and_no_prediction(tmp_path):
    fixture_environment(tmp_path)
    fixture_state(tmp_path)
    opener = Opener([URLError(EMAIL + PASSWORD + TOKEN)])
    result = smoke.run_smoke(execute=True, root=tmp_path, opener=opener)
    assert result["authentication_status"] == "TRANSPORT_FAILED" and len(opener.requests) == 1
    assert result["prediction_post_attempts"] == 0 and result["protected_state_unchanged"]
    assert not any(secret in json.dumps(result) for secret in (EMAIL, PASSWORD, TOKEN))


def test_write_only_new_scoped_safe_serving_json(tmp_path):
    output = smoke.report_path(tmp_path)
    assert output.name.startswith("safe-serving-") and output.is_relative_to(tmp_path / smoke.OUTPUT_RELATIVE)
    smoke.write_report(output, {"status": "NOT_RUN"})
    before = output.read_bytes()
    with pytest.raises(smoke.SafeSmokeError):
        smoke.report_path(tmp_path, output)
    with pytest.raises(FileExistsError):
        smoke.write_report(output, {"status": "overwritten"})
    assert output.read_bytes() == before
    for path in (tmp_path / "outside.json", tmp_path / smoke.OUTPUT_RELATIVE / "different-prefix.json"):
        with pytest.raises(smoke.SafeSmokeError):
            smoke.report_path(tmp_path, path)


def test_incomplete_original_cohort_aborts_before_auth(tmp_path, monkeypatch):
    db = fixture_state(tmp_path)
    with sqlite3.connect(db) as connection:
        connection.execute("DELETE FROM race_results_ultimate WHERE id=16")
    opener = Opener([])
    result = smoke.run_smoke(execute=True, root=tmp_path, opener=opener)
    assert result["failure_code"] == "SAFE_PREFLIGHT_OR_DEADLINE_REJECTED"
    assert opener.requests == [] and result["prediction_post_attempts"] == 0
