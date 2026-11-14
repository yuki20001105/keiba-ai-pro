from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
import pytest

from betting.value_policy import POLICY_VERSION
from models import AnalyzeRaceRequest, AnalyzeRaceResponse, BatchAnalyzeRequest
from routers import performance, predict


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(performance, "APP_ENV", "test")
    monkeypatch.setattr(performance, "_ledger_path", lambda: tmp_path / "performance.db")
    monkeypatch.setattr(performance, "ULTIMATE_DB", tmp_path / "results.db")
    app = FastAPI()

    @app.middleware("http")
    async def authenticated_test_user(request, call_next):
        request.state.user_id = request.headers.get("X-Test-User")
        return await call_next(request)

    app.include_router(performance.router)
    # Installed Starlette predates httpx's removed Client(app=...). Exercise
    # the actual ASGI router/middleware without changing runtime dependencies.
    class ASGIClient:
        def request(self, method, url, **kwargs):
            async def run():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport:
                    return await transport.request(method, url, **kwargs)
            return asyncio.run(run())

        def get(self, url, **kwargs):
            return self.request("GET", url, **kwargs)

        def post(self, url, **kwargs):
            return self.request("POST", url, **kwargs)

    return ASGIClient()


def response():
    return AnalyzeRaceResponse(
        success=True, model_id="frozen-model", race_info={"date": "2026-10-10", "race_name": "Example"},
        pro_evaluation={}, predictions=[], bet_types={}, best_bet_type="none", best_bet_info={}, race_level="",
        recommendation={"policy_version": POLICY_VERSION, "decision": "skip", "reason": "校正未検証", "tickets": []},
    )


def test_api_requires_authentication(client):
    assert client.get("/api/performance").status_code == 401
    assert client.post("/api/performance/records", json={"snapshot_id": "id", "tickets": []}).status_code == 401


@pytest.fixture
def research_report(tmp_path, monkeypatch):
    days = {
        "development": [{"date": "2020-01-05", "investment": 1500, "payout": 0,
                         "opportunities": 1, "bet_races": 1, "hit_races": 0}],
        "retrospective": [
            {"date": "2026-01-04", "investment": 1500, "payout": 3000,
             "opportunities": 2, "bet_races": 1, "hit_races": 1},
            {"date": "2026-01-05", "investment": 0, "payout": 0,
             "opportunities": 1, "bet_races": 0, "hit_races": 0},
            {"date": "2026-02-01", "investment": 1500, "payout": 0,
             "opportunities": 1, "bet_races": 1, "hit_races": 0},
        ],
    }
    report = {
        "schema": "betting-research-study-v1", "study_id": "search101-20261107-v1",
        "feature_count": 101, "reference_only": True, "purchase_eligible": False,
        "independently_verified": True, "recommended_method": None, "historical_support": False,
        "periods": {"development": ["2020-01-05", "2022-05-21"],
                    "retrospective": ["2023-01-05", "2026-10-17"]},
        "exclusions": {"development": {"payout_pending": 3}, "retrospective": {"review": 2}},
        "policies": [{"id": name + "__" + gate, "label": name + " " + gate,
                      "days": days, "baseline_days": days}
                     for name in ("single_ai", "single_favorite", "wide_ai", "wide_axis_h3",
                                  "trio_ai", "fixed_two", "trifecta_ai")
                     for gate in ("all", "market_top3")],
    }
    path = tmp_path / "study.json"
    raw = json.dumps(report).encode()
    path.write_bytes(raw)
    monkeypatch.setattr(performance, "RESEARCH_REPORT_PATH", path, raising=False)
    monkeypatch.setattr(performance, "RESEARCH_REPORT_SHA256", hashlib.sha256(raw).hexdigest(), raising=False)
    return path, report


def test_research_api_auth_local_read_only(client, research_report, monkeypatch, tmp_path):
    assert client.get("/api/performance/research").status_code == 401
    result = client.get("/api/performance/research", headers={"X-Test-User": "alice"})
    assert result.status_code == 200
    assert result.headers["Cache-Control"] == "no-store"
    data = result.json()
    assert data["purchase_eligible"] is False and data["reference_only"] is True
    assert len(data["policies"]) == 14 and data["recommended_method"] is None
    assert not (tmp_path / "performance.db").exists()
    assert client.get("/api/performance/research", headers={"X-Test-User": "bob"}).json() == data
    assert client.post("/api/performance/research", headers={"X-Test-User": "alice"}, json={}).status_code == 405
    monkeypatch.setattr(performance, "APP_ENV", "production")
    assert client.get("/api/performance/research", headers={"X-Test-User": "alice"}).status_code == 403


def test_research_filters_keep_zero_cost_opportunities_and_consistent_totals(client, research_report):
    res = client.get("/api/performance/research?policy=fixed_two__all&date_from=20260101&date_to=20260131",
                     headers={"X-Test-User": "alice"})
    assert res.status_code == 200
    data = res.json()
    stats = next(row for row in data["policies"] if row["id"] == "fixed_two__all")["retrospective"]
    assert stats == {"opportunities": 3, "bet_races": 1, "hit_races": 1,
                     "investment": 1500, "payout": 3000, "profit": 1500, "roi": 200.0}
    assert data["points"][-1]["investment"] == stats["investment"]
    assert data["points"][-1]["roi"] == stats["roi"] == data["monthly"][0]["roi"]
    assert data["points"][-1]["baseline_roi"] == 200.0
    assert data["excluded_races"] == 2
    assert data["date_from"] == "2026-01-01"
    empty = client.get("/api/performance/research?date_from=20260105&date_to=20260105",
                       headers={"X-Test-User": "alice"}).json()
    assert empty["points"][-1]["roi"] is None
    assert empty["monthly"][0]["opportunities"] == 1


@pytest.mark.parametrize("query", ["date_from=20260230", "date_from=20270101&date_to=20260101",
                                   "scope=actual", "policy=unknown__all", "policy=..\\private"])
def test_research_query_validation(client, research_report, query):
    assert client.get(f"/api/performance/research?{query}", headers={"X-Test-User": "alice"}).status_code in {400, 422}


def test_research_report_integrity_and_missing_file_fail_closed(client, research_report):
    path, report = research_report
    report["purchase_eligible"] = True
    path.write_text(json.dumps(report), encoding="utf-8")
    assert client.get("/api/performance/research", headers={"X-Test-User": "alice"}).status_code == 503
    path.unlink()
    assert client.get("/api/performance/research", headers={"X-Test-User": "alice"}).status_code == 503


def test_owner_snapshots_are_unique_and_do_not_mutate_cache(client):
    cached = response()
    first = asyncio.run(performance.attach_performance_snapshot(cached, "alice", "202606040611"))
    second = asyncio.run(performance.attach_performance_snapshot(cached, "bob", "202606040611"))
    repeated = asyncio.run(performance.attach_performance_snapshot(cached, "alice", "202606040611"))
    assert first.performance_snapshot_id and first.performance_snapshot_id == repeated.performance_snapshot_id
    assert first.performance_snapshot_id != second.performance_snapshot_id
    assert cached.performance_snapshot_id is None
    assert "race_id" not in cached.race_info
    body = {"snapshot_id": first.performance_snapshot_id, "tickets": [], "mode": "paper"}
    assert client.post("/api/performance/records", headers={"X-Test-User": "bob"}, json=body).status_code == 404
    saved = client.post("/api/performance/records", headers={"X-Test-User": "alice"}, json=body)
    assert saved.status_code == 200
    assert saved.json()["record"]["status"] == "skipped"
    assert client.post("/api/performance/records", headers={"X-Test-User": "alice"}, json=body).json()["existing"]
    assert client.get("/api/performance", headers={"X-Test-User": "bob"}).json()["total_count"] == 0
    own = client.get("/api/performance", headers={"X-Test-User": "alice"})
    assert own.headers["Cache-Control"] == "no-store"
    assert own.json()["summary"]["skipped_races"] == 1


def test_actual_requires_explicit_confirmation(client, monkeypatch):
    calls = []
    monkeypatch.setattr(performance, "record_selection", lambda *args, **kwargs: calls.append(args) or {"id": "record", "existing": False})
    res = client.post("/api/performance/records", headers={"X-Test-User": "alice"}, json={"snapshot_id": "s", "mode": "actual", "tickets": []})
    assert res.status_code == 400 and calls == []


@pytest.mark.parametrize("extra", [{"user_id": "victim"}, {"actual_return": 10000}, {"confirmed": "true"}])
def test_post_rejects_client_owner_results_and_coercion(client, extra):
    res = client.post("/api/performance/records", headers={"X-Test-User": "alice"}, json={"snapshot_id": "s", "tickets": [], **extra})
    assert res.status_code == 422


@pytest.mark.parametrize("ticket", [
    {"bet_type": "単勝", "combination": "1", "horse_numbers": [1], "stake": 100, "probability": 1.0},
    {"bet_type": "単勝", "combination": "1", "horse_numbers": [1], "stake": "100"},
    {"bet_type": "単勝", "combination": "1", "horse_numbers": [True], "stake": 100},
    {"bet_type": "単勝", "combination": "1", "horse_numbers": [1], "stake": 150},
])
def test_ticket_metadata_injection_and_invalid_stakes_rejected(client, ticket):
    res = client.post("/api/performance/records", headers={"X-Test-User": "alice"}, json={"snapshot_id": "s", "tickets": [ticket]})
    assert res.status_code == 422


@pytest.mark.parametrize("query,status", [
    ("date_from=20260230", 400), ("date_from=20260101&date_to=20250101", 400),
    ("date_from=2026-01-01", 400), ("limit=0", 422), ("limit=201", 422), ("mode=all", 422),
])
def test_query_validation(client, query, status):
    assert client.get(f"/api/performance?{query}", headers={"X-Test-User": "alice"}).status_code == status


def test_query_dates_passed_to_owner_scoped_read(client, monkeypatch):
    calls = []
    monkeypatch.setattr(performance, "list_performance", lambda *args, **kwargs: calls.append((args, kwargs)) or {})
    res = client.get("/api/performance?mode=actual&limit=2&date_from=20260101&date_to=20261010", headers={"X-Test-User": "alice"})
    assert res.status_code == 200
    assert calls[0][0][-2:] == ("alice", "actual")
    assert calls[0][1] == {"limit": 2, "date_from": "2026-01-01", "date_to": "2026-10-10"}


def test_production_never_writes_snapshots_or_records(client, monkeypatch):
    monkeypatch.setattr(performance, "APP_ENV", "production")
    monkeypatch.setattr(performance, "save_snapshot", lambda *args: pytest.fail("production write"))
    out = asyncio.run(performance.attach_performance_snapshot(response(), "alice", "202606040611"))
    assert out.performance_snapshot_id is None
    assert client.get("/api/performance", headers={"X-Test-User": "alice"}).status_code == 403
    assert client.post("/api/performance/records", headers={"X-Test-User": "alice"}, json={"snapshot_id": "s", "tickets": []}).status_code == 403


def test_snapshot_failure_does_not_destroy_prediction(client, monkeypatch):
    def fail(*args):
        raise OSError("storage unavailable")
    monkeypatch.setattr(performance, "save_snapshot", fail)
    out = asyncio.run(performance.attach_performance_snapshot(response(), "alice", "202606040611"))
    assert out.success and out.performance_snapshot_id is None and out.performance_recording_error


def test_single_and_batch_attach_owner_snapshots_only_after_inference(client, monkeypatch):
    cached = response()
    async def quota(*args, **kwargs):
        pass
    async def impl(*args):
        return cached
    monkeypatch.setattr(predict, "check_and_consume_pred_count", quota)
    monkeypatch.setattr(predict, "_analyze_race_impl", impl)
    req = SimpleNamespace(state=SimpleNamespace(user_id="alice"))
    single = asyncio.run(predict.analyze_race(AnalyzeRaceRequest(race_id="202606040611"), req))
    batch = asyncio.run(predict.analyze_races_batch(BatchAnalyzeRequest(race_ids=["202606040611", "202605040611"]), req))
    assert single.performance_snapshot_id
    assert batch["results"]["202606040611"]["data"]["performance_snapshot_id"] == single.performance_snapshot_id
    assert batch["results"]["202605040611"]["data"]["performance_snapshot_id"] != single.performance_snapshot_id
    assert cached.performance_snapshot_id is None


def test_simulation_queries_are_owner_scoped_year_limited_and_never_actual(client):
    headers = {"X-Test-User": "alice"}
    data = client.get("/api/performance?source=simulation&origin=historical", headers=headers)
    assert data.status_code == 200
    assert data.json()["model_performance"]["source"] == "unseen-simulation"
    months = client.get("/api/performance?source=simulation&year=2026", headers=headers)
    assert months.json()["schema"] == "model-monthly-roi-v1"
    assert months.json()["year"] == 2026
    for query in ("source=simulation&mode=actual", "source=recorded&year=2026"):
        assert client.get("/api/performance?" + query, headers=headers).status_code == 400
    assert client.get("/api/performance?source=simulation&year=1800", headers=headers).status_code == 422


def test_prediction_hook_automatically_freezes_reference_without_mutating_cache(client, monkeypatch):
    from test_paper_simulation import prediction, model, SHA
    data = prediction()
    cached = response().model_copy(update={key: data[key] for key in
        ("model_id", "race_info", "predictions", "input_diagnostics")})
    cached.freshness = {"schema_version": "prediction-freshness-v1"}
    monkeypatch.setattr(performance, "_simulation_model_context", lambda value: (model(), SHA), raising=False)
    first = asyncio.run(performance.attach_performance_snapshot(cached, "alice", "race-one"))
    assert first.paper_simulation["eligible"] and first.paper_simulation["purchase_eligible"] is False
    assert cached.paper_simulation is None
    assert client.get("/api/performance?source=simulation", headers={"X-Test-User": "alice"}).json()["total_count"] == 1
    assert client.get("/api/performance?source=simulation", headers={"X-Test-User": "bob"}).json()["total_count"] == 0


def test_historical_validation_hook_and_queries_exclude_train_and_keep_actual_separate(client, monkeypatch):
    from test_paper_simulation import prediction, model, SHA
    monkeypatch.setattr(performance, "_simulation_model_context", lambda value: (model(), SHA))
    for day, expected in [("20220604", False), ("20220605", True), ("20240811", True)]:
        data = prediction(rid=day, day=day)
        data["input_diagnostics"]["issue_codes"].append("target_not_after_training_evaluation_period")
        cached = response().model_copy(update={key: data[key] for key in
            ("model_id", "race_info", "predictions", "input_diagnostics")})
        cached.freshness = {"schema_version": "prediction-freshness-v1"}
        saved = asyncio.run(performance.attach_performance_snapshot(cached, "alice", day))
        assert saved.paper_simulation["eligible"] is expected
    headers = {"X-Test-User": "alice"}
    for year in ("", "&year=2024"):
        result = client.get("/api/performance?source=simulation&origin=historical_validation" + year, headers=headers)
        assert result.status_code == 200
        data = result.json()
        assert data.get("model_performance", data)["source"] == "historical-validation-simulation"
    assert client.get("/api/performance?source=simulation", headers=headers).json()["total_count"] == 0
    assert client.get("/api/performance?mode=actual", headers=headers).json()["total_count"] == 0
    assert client.get("/api/performance?source=recorded&origin=historical_validation", headers=headers).status_code == 400


def test_combined_dashboard_automatically_picks_up_future_predictions_and_settlement(client, monkeypatch):
    from test_paper_simulation import prediction, model, SHA, official
    monkeypatch.setattr(performance, "_simulation_model_context", lambda value: (model(), SHA))
    for rid, day in (("evaluation", "20240811"), ("future", "20991010")):
        data = prediction(rid=rid, day=day)
        if rid == "evaluation":
            data["input_diagnostics"]["issue_codes"].append("target_not_after_training_evaluation_period")
        cached = response().model_copy(update={key: data[key] for key in
            ("model_id", "race_info", "predictions", "input_diagnostics")})
        cached.freshness = {"schema_version": "prediction-freshness-v1"}
        saved = asyncio.run(performance.attach_performance_snapshot(cached, "alice", rid))
        assert saved.paper_simulation["eligible"]
    headers = {"X-Test-User": "alice"}
    query = "/api/performance?source=simulation&origin=combined"
    before = client.get(query, headers=headers)
    assert before.status_code == 200
    assert before.json()["summary"]["pending_races"] == 2
    official(performance.ULTIMATE_DB, "evaluation", "20240811")
    official(performance.ULTIMATE_DB, "future", "20991010", hit=False)
    after = client.get(query, headers=headers).json()
    assert after["model_performance"]["models"][0]["race_count"] == 2
    assert after["model_performance"]["models"][0]["total_return"] == 29000
    assert after["summary"]["pending_races"] == 0
    assert client.get(query, headers={"X-Test-User": "bob"}).json()["total_count"] == 0
    assert client.get("/api/performance?mode=actual", headers=headers).json()["total_count"] == 0
