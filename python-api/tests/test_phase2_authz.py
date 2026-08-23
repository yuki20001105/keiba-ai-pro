from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, "python-api")
import main  # type: ignore  # noqa: E402
import app_config  # type: ignore  # noqa: E402
from deps import auth as deps_auth  # type: ignore  # noqa: E402
from deps import pred_limit  # type: ignore  # noqa: E402
from middleware import auth as middleware_auth  # type: ignore  # noqa: E402
from routers import internal, purchase, scrape, train  # type: ignore  # noqa: E402


async def _request(method: str, path: str, *, token: str | None = None, json: dict | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=main.app)
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, headers=headers, json=json)


def _run_request(method: str, path: str, *, token: str | None = None, json: dict | None = None) -> httpx.Response:
    return asyncio.run(_request(method, path, token=token, json=json))


@pytest.fixture(autouse=True)
def _auth_setup(monkeypatch: pytest.MonkeyPatch):
    middleware_auth.SUPABASE_URL = "http://127.0.0.1:54321"

    async def _verify_jwt(token: str):
        if token == "free":
            return {"sub": "user-free", "app_metadata": {"role": "user", "subscription_tier": "free"}}
        if token == "premium":
            return {"sub": "user-premium", "app_metadata": {"role": "user", "subscription_tier": "premium"}}
        if token == "admin":
            return {"sub": "user-admin", "app_metadata": {"role": "admin", "subscription_tier": "free"}}
        if token == "meta-escalation":
            return {
                "sub": "user-meta",
                "app_metadata": {},
                "user_metadata": {"role": "admin", "subscription_tier": "premium"},
            }
        return {"sub": "user-unknown", "app_metadata": {"role": "user", "subscription_tier": "free"}}

    monkeypatch.setattr(middleware_auth, "verify_jwt", _verify_jwt)


def test_require_premium_allows_admin(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(deps_auth, "_get_profile_from_db", lambda _uid: {"role": "admin", "subscription_tier": "free"})
    out = asyncio.run(deps_auth.require_premium({"user_id": "u", "role": "user", "subscription_tier": "free"}))
    assert out["role"] == "admin"


def test_require_premium_rejects_free(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(deps_auth, "_get_profile_from_db", lambda _uid: {"role": "user", "subscription_tier": "free"})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(deps_auth.require_premium({"user_id": "u", "role": "user", "subscription_tier": "free"}))
    assert exc.value.status_code == 403
    assert "Premiumプラン" in str(exc.value.detail)


def test_production_profile_failure_fail_closed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(deps_auth, "_get_profile_from_db", lambda _uid: None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(deps_auth.require_admin({"user_id": "u", "role": "admin", "subscription_tier": "premium"}))
    assert exc.value.status_code == 503
    assert exc.value.detail == "認可基盤が利用できません"


def test_user_metadata_escalation_is_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(deps_auth, "_get_profile_from_db", lambda _uid: None)
    res = _run_request(
        "POST",
        "/api/scrape/start",
        token="meta-escalation",
        json={"start_date": "20260101", "end_date": "20260101", "force_rescrape": False, "dry_run": True},
    )
    assert res.status_code == 403


def test_train_start_denied_does_not_create_job(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(deps_auth, "_get_profile_from_db", lambda _uid: {"role": "user", "subscription_tier": "free"})
    before = len(train._train_jobs)
    res = _run_request("POST", "/api/train/start", token="free", json={})
    after = len(train._train_jobs)
    assert res.status_code == 403
    assert after == before


class _FakeRpcResult:
    def __init__(self, data):
        self.data = data


class _FakeRpcCall:
    def __init__(self, data):
        self._data = data

    def execute(self):
        return _FakeRpcResult(self._data)


class _FakeClient:
    def __init__(self, data):
        self.data = data

    def rpc(self, _name, _payload):
        return _FakeRpcCall(self.data)


def _make_req(user_id: str = "u"):
    class _State:
        pass

    class _Req:
        pass

    req = _Req()
    req.state = _State()
    req.state.user_id = user_id
    return req


@pytest.mark.parametrize("bad", [None, True, False, "abc", -2])
def test_quota_bad_rpc_values_fail_closed_in_production(monkeypatch: pytest.MonkeyPatch, bad):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRED_LIMIT_ALLOW_FAIL_OPEN", "false")
    monkeypatch.setattr(pred_limit, "_is_local_or_test_env", lambda: False)
    monkeypatch.setattr(pred_limit, "_allow_local_bypass", lambda: False)

    def _client():
        return _FakeClient(bad)

    monkeypatch.setattr(app_config, "get_supabase_client", _client)

    with pytest.raises(HTTPException) as exc:
        pred_limit._consume_pred_count("u", units=1)
    assert exc.value.status_code == 503
    assert isinstance(exc.value.detail, dict)
    assert exc.value.detail.get("error") == "pred_limit_backend_unavailable"


def test_quota_batch_preconsumption_dedup_and_single_charge(monkeypatch: pytest.MonkeyPatch):
    called = []

    async def _consume(_req, units=1):
        called.append(units)

    async def _impl(_request):
        from models import AnalyzeRaceResponse  # type: ignore

        return AnalyzeRaceResponse(
            success=True,
            race_info={},
            pro_evaluation={},
            predictions=[],
            bet_types={},
            best_bet_type="none",
            best_bet_info={},
            race_level="",
            recommendation={},
        )

    from routers import predict as predict_router  # type: ignore

    monkeypatch.setattr(predict_router, "check_and_consume_pred_count", _consume)
    monkeypatch.setattr(predict_router, "_analyze_race_impl", _impl)

    req = _make_req("u")
    from models import BatchAnalyzeRequest  # type: ignore

    payload = BatchAnalyzeRequest(race_ids=["r1", "r1", "r2"], bankroll=1000)
    out = asyncio.run(predict_router.analyze_races_batch(payload, req))

    assert called == [2]
    assert out["charged_units"] == 2
    assert out["requested_total"] == 3


def test_analyze_race_charges_on_cache_hit_policy_fixed(monkeypatch: pytest.MonkeyPatch):
    called = []

    async def _consume(_req, units=1):
        called.append(units)

    async def _impl(_request):
        from models import AnalyzeRaceResponse  # type: ignore

        return AnalyzeRaceResponse(
            success=True,
            race_info={},
            pro_evaluation={},
            predictions=[],
            bet_types={},
            best_bet_type="none",
            best_bet_info={},
            race_level="",
            recommendation={},
        )

    from routers import predict as predict_router  # type: ignore
    from models import AnalyzeRaceRequest  # type: ignore

    monkeypatch.setattr(predict_router, "check_and_consume_pred_count", _consume)
    monkeypatch.setattr(predict_router, "_analyze_race_impl", _impl)

    req = _make_req("u")
    payload = AnalyzeRaceRequest(race_id="202601010101")

    asyncio.run(predict_router.analyze_race(payload, req))
    asyncio.run(predict_router.analyze_race(payload, req))
    assert called == [1, 1]


def test_purchase_schema_migration_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "tracking.db"
    con = sqlite3.connect(str(db_path))
    try:
        purchase._ensure_tracking_user_column(con)
        purchase._ensure_tracking_user_column(con)
    finally:
        con.close()


def test_purchase_ownership_and_orphan_invisibility(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "tracking.db"
    monkeypatch.setattr(purchase, "_tracking_db_path", lambda: db_path)
    monkeypatch.setattr(purchase, "get_supabase_client", lambda: None)

    payload = {
        "race_id": "202601010101",
        "venue": "tokyo",
        "bet_type": "win",
        "combinations": ["1"],
        "strategy_type": "test",
        "purchase_count": 1,
        "unit_price": 100,
        "total_cost": 100,
        "expected_value": 1.2,
        "expected_return": 120,
    }

    a_save = _run_request("POST", "/api/purchase", token="free", json=payload)
    b_save = _run_request("POST", "/api/purchase", token="premium", json=payload)
    assert a_save.status_code == 200
    assert b_save.status_code == 200

    b_id = b_save.json()["purchase_id"]

    con = sqlite3.connect(str(db_path))
    try:
        purchase._ensure_tracking_user_column(con)
        con.execute(
            "INSERT INTO purchase_history (race_id, bet_type, combinations, total_cost, expected_value, expected_return) VALUES (?,?,?,?,?,?)",
            ("202601010102", "win", "2", 100, 1.0, 100),
        )
        con.commit()
    finally:
        con.close()

    a_history = _run_request("GET", "/api/purchase_history", token="free")
    assert a_history.status_code == 200
    assert a_history.json()["count"] == 1

    patch_other = _run_request("PATCH", f"/api/purchase/{b_id}", token="free", json={"actual_return": 0, "is_hit": False})
    delete_other = _run_request("DELETE", f"/api/purchase/{b_id}", token="free")
    assert patch_other.status_code == 404
    assert delete_other.status_code == 404


def test_scrape_start_denied_does_not_enqueue_job(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(deps_auth, "_get_profile_from_db", lambda _uid: {"role": "user", "subscription_tier": "free"})
    with scrape._JOBS_LOCK:
        before = len(scrape._scrape_jobs)

    res = _run_request(
        "POST",
        "/api/scrape/start",
        token="free",
        json={"start_date": "20260101", "end_date": "20260101", "force_rescrape": False, "dry_run": True},
    )

    with scrape._JOBS_LOCK:
        after = len(scrape._scrape_jobs)

    assert res.status_code == 403
    assert after == before


def test_internal_secret_unset_or_mismatch_or_match(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("INTERNAL_SECRET", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(HTTPException) as exc1:
        internal._verify_secret("anything")
    assert exc1.value.status_code == 503
    assert exc1.value.detail == "internal identity is unavailable"

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("INTERNAL_SECRET", "s3cr3t")
    with pytest.raises(HTTPException) as exc2:
        internal._verify_secret("wrong")
    assert exc2.value.status_code == 403

    internal._verify_secret("s3cr3t")


def test_debug_endpoints_are_disabled_in_production(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "production")

    res_debug = _run_request("GET", "/api/debug", token="admin")
    res_ids = _run_request("GET", "/api/debug/race-ids", token="admin")

    assert res_debug.status_code == 404
    assert res_ids.status_code == 404


def test_realtime_refresh_direct_access_authz(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        deps_auth,
        "_get_profile_from_db",
        lambda uid: {"role": "admin", "subscription_tier": "premium"} if uid == "user-admin" else {"role": "user", "subscription_tier": "free"},
    )

    no_jwt = _run_request("POST", "/api/realtime-odds/refresh", json={"race_ids": []})
    user = _run_request("POST", "/api/realtime-odds/refresh", token="free", json={"race_ids": []})
    admin = _run_request("POST", "/api/realtime-odds/refresh", token="admin", json={"race_ids": []})

    assert no_jwt.status_code == 401
    assert user.status_code == 403
    assert admin.status_code == 200


def test_profiling_status_html_direct_access_authz(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        deps_auth,
        "_get_profile_from_db",
        lambda uid: {"role": "admin", "subscription_tier": "premium"} if uid == "user-admin" else {"role": "user", "subscription_tier": "free"},
    )
    from routers import profiling as profiling_router  # type: ignore

    profiling_router._profiling_jobs["job-authz"] = {
        "status": "completed",
        "message": "ok",
        "html": "<html><body>ok</body></html>",
    }

    try:
        no_jwt_status = _run_request("GET", "/api/profiling/status/job-authz")
        no_jwt_html = _run_request("GET", "/api/profiling/html/job-authz")
        user_status = _run_request("GET", "/api/profiling/status/job-authz", token="free")
        user_html = _run_request("GET", "/api/profiling/html/job-authz", token="free")
        admin_status = _run_request("GET", "/api/profiling/status/job-authz", token="admin")
        admin_html = _run_request("GET", "/api/profiling/html/job-authz", token="admin")

        assert no_jwt_status.status_code == 401
        assert no_jwt_html.status_code == 401
        assert user_status.status_code == 403
        assert user_html.status_code == 403
        assert admin_status.status_code == 200
        assert admin_html.status_code == 200
    finally:
        profiling_router._profiling_jobs.pop("job-authz", None)


def test_netkeiba_race_list_direct_access_authz(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        deps_auth,
        "_get_profile_from_db",
        lambda uid: {"role": "admin", "subscription_tier": "premium"} if uid == "user-admin" else {"role": "user", "subscription_tier": "free"},
    )

    class _FakeResp:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return '{"races": ["202601010101"]}'

    class _FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            return _FakeResp()

    monkeypatch.setattr(scrape.aiohttp, "ClientSession", _FakeSession)

    no_jwt = _run_request("GET", "/api/netkeiba/race-list?date=2026-01-01")
    user = _run_request("GET", "/api/netkeiba/race-list?date=2026-01-01", token="free")
    admin = _run_request("GET", "/api/netkeiba/race-list?date=2026-01-01", token="admin")

    assert no_jwt.status_code == 401
    assert user.status_code == 403
    assert admin.status_code == 200


def test_batch_analyze_rejects_empty_race_ids_before_consume(monkeypatch: pytest.MonkeyPatch):
    called = []

    async def _consume(_req, units=1):
        called.append(units)

    from routers import predict as predict_router  # type: ignore

    monkeypatch.setattr(predict_router, "check_and_consume_pred_count", _consume)

    req = _make_req("00000000-0000-0000-0000-000000000001")
    from models import BatchAnalyzeRequest  # type: ignore

    with pytest.raises(ValidationError):
        # Pydantic validation should fail before endpoint logic runs.
        BatchAnalyzeRequest(race_ids=[], bankroll=1000)
    assert called == []


def test_batch_analyze_rejects_over_100_race_ids(monkeypatch: pytest.MonkeyPatch):
    called = []

    async def _consume(_req, units=1):
        called.append(units)

    from routers import predict as predict_router  # type: ignore

    monkeypatch.setattr(predict_router, "check_and_consume_pred_count", _consume)

    with pytest.raises(ValidationError):
        from models import BatchAnalyzeRequest  # type: ignore

        BatchAnalyzeRequest(race_ids=[f"r{i}" for i in range(101)], bankroll=1000)
    assert called == []


def test_batch_analyze_quota_insufficient_does_not_call_analyze(monkeypatch: pytest.MonkeyPatch):
    analyze_called = []

    async def _consume(_req, units=1):
        raise HTTPException(status_code=429, detail={"error": "pred_limit_exceeded"})

    async def _impl(_request):
        analyze_called.append(1)
        raise AssertionError("_analyze_race_impl should not be called when quota is insufficient")

    from routers import predict as predict_router  # type: ignore
    from models import BatchAnalyzeRequest  # type: ignore

    monkeypatch.setattr(predict_router, "check_and_consume_pred_count", _consume)
    monkeypatch.setattr(predict_router, "_analyze_race_impl", _impl)

    req = _make_req("00000000-0000-0000-0000-000000000001")
    payload = BatchAnalyzeRequest(race_ids=["r1", "r2"], bankroll=1000)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(predict_router.analyze_races_batch(payload, req))
    assert exc.value.status_code == 429
    assert analyze_called == []
