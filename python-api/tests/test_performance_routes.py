from __future__ import annotations

import asyncio
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
