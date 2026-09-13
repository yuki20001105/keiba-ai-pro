import asyncio
import json
from types import SimpleNamespace

import pytest

from scraping import browser_transport, jobs, odds
from scraping.fetch_pipeline import FetchAccessBlocked, FetchResult


def test_odds_api_uses_shared_transport_without_stale_cache(monkeypatch):
    calls = []

    async def fetch_text(session, url, **kwargs):
        calls.append((url, kwargs))
        payload = {"status": "middle", "data": {"odds": {"1": {"1": ["2.5", None, "1"]}}}}
        return SimpleNamespace(status=200), json.dumps(payload)

    monkeypatch.setattr(odds, "fetch_text", fetch_text)
    assert asyncio.run(odds.fetch_tansho_odds_api(object(), "202609010101")) == ({1: 2.5}, {1: 1}, "middle")
    url, kwargs = calls[0]
    assert "race_id=202609010101" in url
    assert kwargs["use_cache"] is False
    assert kwargs["request_headers"]["X-Requested-With"] == "XMLHttpRequest"


def test_odds_api_propagates_access_block(monkeypatch):
    async def blocked(*args, **kwargs):
        raise FetchAccessBlocked("http-403")

    monkeypatch.setattr(odds, "fetch_text", blocked)
    with pytest.raises(FetchAccessBlocked):
        asyncio.run(odds.fetch_tansho_odds_api(object(), "202609010101"))


class Page:
    @property
    def context(self):
        return self

    async def route(self, pattern, callback):
        self.callback = callback


class Route:
    def __init__(self, kind="xhr"):
        async def all_headers():
            return {"Referer": "https://race.netkeiba.com/"}

        self.request = SimpleNamespace(
            method="GET", resource_type=kind,
            url="https://race.netkeiba.com/api/odds", all_headers=all_headers,
        )
        self.aborted = False
        self.fulfilled = None

    async def abort(self):
        self.aborted = True

    async def fulfill(self, **kwargs):
        self.fulfilled = kwargs


def test_browser_routes_use_shared_pacing_and_preserve_response(monkeypatch):
    calls = []

    async def fetch(*args, **kwargs):
        calls.append(kwargs)
        return FetchResult("url", "url", 200, b'{"odds":2.5}', "network", 1,
                           response_headers={"Content-Type": "application/json", "Content-Encoding": "gzip", "Content-Length": "99"})

    monkeypatch.setattr(browser_transport, "fetch_bytes", fetch)

    async def run():
        page, route, image = Page(), Route(), Route("image")
        errors = await browser_transport.install_paced_routes(page, object())
        await page.callback(route)
        await page.callback(image)
        assert not errors
        assert image.aborted
        assert route.fulfilled == {
            "status": 200, "headers": {"Content-Type": "application/json"}, "body": b'{"odds":2.5}',
        }

    asyncio.run(run())
    assert len(calls) == 1
    assert calls[0]["allow_redirects"] is False


def test_browser_stops_all_following_requests_on_provider_block(monkeypatch):
    calls = []

    async def fetch(*args, **kwargs):
        calls.append(1)
        return FetchResult("url", "url", 403, b"", "access-blocked", 1, "http-403")

    monkeypatch.setattr(browser_transport, "fetch_bytes", fetch)

    async def run():
        page, first, second = Page(), Route(), Route()
        errors = await browser_transport.install_paced_routes(page, object())
        await page.callback(first)
        await page.callback(second)
        assert first.aborted and second.aborted
        assert len(errors) == 1 and isinstance(errors[0], FetchAccessBlocked)

    asyncio.run(run())
    assert len(calls) == 1


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000/", "file:///local", "https://netkeiba.com.example.org/", "https://user@race.netkeiba.com/"])
def test_browser_rejects_urls_outside_provider(monkeypatch, url):
    calls = []

    async def unexpected(*args, **kwargs):
        calls.append(args)
        raise AssertionError("No external tracker, local URL, or credentialed URL should be fetched")

    monkeypatch.setattr(browser_transport, "fetch_bytes", unexpected)

    async def run():
        page, route = Page(), Route()
        route.request.url = url
        await browser_transport.install_paced_routes(page, object())
        await page.callback(route)
        assert route.aborted
        assert route.fulfilled is None

    asyncio.run(run())
    assert calls == []


@pytest.mark.parametrize("location", ["http://127.0.0.1:8000/", "https://race.netkeiba.com/race/result.html"])
def test_browser_never_follows_redirect_without_shared_admission(monkeypatch, location):
    async def fetch(*args, **kwargs):
        return FetchResult("url", "url", 302, b"", "network", 1,
                           response_headers={"Location": location})

    monkeypatch.setattr(browser_transport, "fetch_bytes", fetch)

    async def run():
        page, route = Page(), Route()
        await browser_transport.install_paced_routes(page, object())
        await page.callback(route)
        assert route.aborted
        assert route.fulfilled is None

    asyncio.run(run())


def test_calendar_uses_shared_transport_and_keeps_date_parsing(monkeypatch):
    calls = []

    async def fetch(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status=200), '<a href="?kaisai_date=20250104">4</a>'

    monkeypatch.setattr(jobs, "fetch_text", fetch)
    assert asyncio.run(jobs._fetch_race_days_for_month(2025, 1)) == ["20250104"]
    assert calls[0]["resume_key"] == "calendar:2025:01"


def test_calendar_does_not_fall_back_after_access_block(monkeypatch):
    async def blocked(*args, **kwargs):
        raise FetchAccessBlocked("http-403")

    monkeypatch.setattr(jobs, "fetch_text", blocked)
    with pytest.raises(FetchAccessBlocked):
        asyncio.run(jobs._fetch_race_days_for_month(2025, 1))


def test_background_date_collection_uses_shared_transport_and_propagates_block(monkeypatch):
    from routers import internal
    from scraping import fetch_pipeline

    calls = []

    async def blocked(session, url, **kwargs):
        calls.append(url)
        raise FetchAccessBlocked("http-403")

    monkeypatch.setattr(fetch_pipeline, "fetch_text", blocked)
    with pytest.raises(FetchAccessBlocked):
        asyncio.run(internal._scrape_date("20250104"))
    assert len(calls) == 1


def test_connectivity_probe_has_a_deadline_including_rate_wait(monkeypatch):
    from routers import stats
    from scraping import fetch_pipeline

    calls = []

    async def fetch(session, url, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status=200), '<a href="/race/202501010101/">race</a>'

    monkeypatch.setattr(stats, "_ensure_non_production_for_test_endpoints", lambda: None)
    monkeypatch.setattr(stats, "SUPABASE_DATA_ENABLED", False)
    monkeypatch.setattr(fetch_pipeline, "fetch_text", fetch)
    result = asyncio.run(stats.test_connectivity())
    assert result["netkeiba"]["race_ids_found"] == 1
    assert calls[0]["total_timeout_sec"] == 10
    assert calls[0]["use_cache"] is False


@pytest.mark.parametrize("kind", ["nar_pedigree", "coat_color"])
def test_backfill_does_not_retry_or_write_after_provider_block(monkeypatch, kind):
    from routers import backfill

    class Query:
        def table(self, name):
            return self

        def select(self, *args):
            return self

        def range(self, *args):
            return self

        def execute(self):
            return SimpleNamespace(data=[{
                "id": 1, "race_id": "202501010101",
                "data": {"horse_id": "B201700001", "sire": "unknown_local"},
            }])

        def update(self, *args):
            raise AssertionError("Must not write after provider block")

    calls = []

    async def blocked(session, url, **kwargs):
        calls.append(url)
        raise FetchAccessBlocked("http-403")

    monkeypatch.setattr(backfill, "SUPABASE_ENABLED", True)
    monkeypatch.setattr(backfill, "get_supabase_client", Query)
    monkeypatch.setattr(backfill, "fetch_text", blocked)
    operation = getattr(backfill, f"backfill_{kind}")
    with pytest.raises(FetchAccessBlocked):
        asyncio.run(operation(limit=1, _={}))
    assert len(calls) == 1


def test_job_wait_checkpoints_are_throttled_without_delaying_cancellation(monkeypatch):
    from contextlib import contextmanager

    callbacks, checkpoints = [], []
    cancelled = False

    @contextmanager
    def context(**kwargs):
        callbacks.append(kwargs["on_wait"])
        yield {}

    async def run(*args):
        nonlocal cancelled
        state = {"interval": 3.0, "cooldown_until": 0}
        callbacks[0](state)
        callbacks[0](state)
        cancelled = True
        with pytest.raises(jobs.ScrapeJobCancellationRequested):
            callbacks[0](state)

    job = {"progress": {}}
    monkeypatch.setitem(jobs._scrape_jobs, "wait-test", job)
    monkeypatch.setattr(jobs, "fetch_context", context)
    monkeypatch.setattr(jobs, "_run_scrape_job_impl", run)
    monkeypatch.setattr(jobs, "_checkpoint_job", lambda *args: checkpoints.append(1))
    asyncio.run(jobs._run_scrape_job(
        "wait-test", "20250101", "20250131", cancel_requested=lambda: cancelled,
    ))
    assert checkpoints == [1]
    assert job["progress"]["message"] == "応答状況に合わせて自動減速中"
