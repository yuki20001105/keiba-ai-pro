from __future__ import annotations

import asyncio
import sqlite3
import sys

import httpx

sys.path.insert(0, "python-api")
import main  # type: ignore  # noqa: E402
from middleware import auth as middleware_auth  # type: ignore  # noqa: E402
from routers import scrape  # type: ignore  # noqa: E402


async def _request(method: str, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path)


def _set_auth_mode(enabled: bool) -> None:
    # middleware.auth reads SUPABASE_URL as a module-level value.
    middleware_auth.SUPABASE_URL = "http://127.0.0.1:54321" if enabled else ""

def test_health_contract() -> None:
    _set_auth_mode(True)
    res = asyncio.run(_request("GET", "/health"))
    assert res.status_code == 200
    payload = res.json()
    assert isinstance(payload, dict)
    assert payload.get("status") == "ok"


def test_lifespan_initializes_empty_ephemeral_sqlite(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "keiba" / "data" / "keiba_ultimate.db"
    events: list[str] = []

    monkeypatch.setattr(main, "ULTIMATE_DB", db_path)
    monkeypatch.setattr(main, "start_scheduler", lambda: events.append("scheduler-start"))
    monkeypatch.setattr(main, "stop_scheduler", lambda: events.append("scheduler-stop"))

    async def _start_worker() -> None:
        events.append("worker-start")

    async def _stop_worker() -> None:
        events.append("worker-stop")

    monkeypatch.setattr(main, "start_operational_saga_worker", _start_worker)
    monkeypatch.setattr(main, "stop_operational_saga_worker", _stop_worker)

    async def _run_lifespan() -> None:
        async with main.lifespan(main.app):
            with sqlite3.connect(db_path) as conn:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
            assert {
                "races_ultimate",
                "race_results_ultimate",
                "return_tables_ultimate",
                "scraped_dates",
            } <= tables

    asyncio.run(_run_lifespan())
    assert events == [
        "scheduler-start",
        "worker-start",
        "worker-stop",
        "scheduler-stop",
    ]


def test_auth_middleware_exempt_and_protected_paths() -> None:
    _set_auth_mode(True)

    exempt = asyncio.run(_request("GET", "/health"))
    assert exempt.status_code == 200

    protected = asyncio.run(_request("GET", "/api/scrape/health"))
    assert protected.status_code == 401
    payload = protected.json()
    assert isinstance(payload, dict)
    assert "detail" in payload


def test_scrape_router_registered_in_main_app() -> None:
    paths = set()
    for route in main.app.routes:
        if hasattr(route, "path"):
            paths.add(route.path)

    assert "/api/scrape/health" in paths
    assert "/api/scrape/status/{job_id}" in paths


def test_scrape_health_contract_healthy() -> None:
    _set_auth_mode(False)
    with scrape._JOBS_LOCK:
        scrape._scrape_jobs.clear()

    res = asyncio.run(_request("GET", "/api/scrape/health"))
    assert res.status_code == 200
    payload = res.json()
    assert isinstance(payload, dict)
    assert payload.get("success") is True
    assert payload.get("status") == "healthy"
    assert "status" in payload
    assert "service" in payload
    assert payload.get("service") == "scrape"


def test_scrape_health_contract_degraded() -> None:
    _set_auth_mode(False)
    with scrape._JOBS_LOCK:
        scrape._scrape_jobs.clear()
        scrape._scrape_jobs["test-job"] = {
            "status": "error",
            "progress": {},
            "result": None,
            "error": "simulated",
        }

    res = asyncio.run(_request("GET", "/api/scrape/health"))
    assert res.status_code == 200
    payload = res.json()
    assert payload.get("success") is True
    assert payload.get("status") == "degraded"
    assert payload.get("service") == "scrape"
    assert "reason" in payload


def test_scrape_health_contract_unhealthy_when_internal_error(monkeypatch) -> None:
    _set_auth_mode(False)

    def _raise(*_args, **_kwargs):
        raise RuntimeError("forced-failure")

    monkeypatch.setattr(scrape, "_purge_old_jobs", _raise)

    res = asyncio.run(_request("GET", "/api/scrape/health"))
    assert res.status_code == 503
    payload = res.json()
    assert payload.get("success") is False
    assert payload.get("status") == "unhealthy"
    assert payload.get("service") == "scrape"
    assert isinstance(payload.get("reason"), str)
