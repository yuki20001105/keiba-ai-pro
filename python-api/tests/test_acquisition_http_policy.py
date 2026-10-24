from __future__ import annotations

import asyncio
import pytest

from scraping import acquisition_http_policy as policy
from scraping import fetch_pipeline as fetch
from scraping.request_pacing import RequestPacer


class Response:
    def __init__(self, status=200, body=b"<html>valid data</html>", *, headers=None, enter_gate=None, exit_error=None):
        self.status, self.body = status, body
        self.headers = headers or {}
        self.enter_gate, self.exit_error = enter_gate, exit_error
        self.reads = self.exits = 0
        self.state_at_exit = None

    async def __aenter__(self):
        if self.enter_gate is not None:
            await self.enter_gate.wait()
        return self

    async def read(self):
        self.reads += 1
        if isinstance(self.body, BaseException):
            raise self.body
        return self.body

    async def __aexit__(self, *_exc):
        self.exits += 1
        self.state_at_exit = fetch._REQUEST_PACER.status()
        if self.exit_error is not None:
            raise self.exit_error
        return False


class Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def get(self, url, **kwargs):
        self.calls.append((url, fetch._REQUEST_PACER.clock(), kwargs))
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def no_live_cache(monkeypatch):
    # The suite's shared fixture already replaces the pacer with a temp DB.
    # Cache persistence is also isolated: not one test needs the live cache.
    monkeypatch.setattr(fetch, "_lookup_cached_fetch", lambda *_a: (None, None))
    monkeypatch.setattr(fetch, "_write_cache", lambda *_a: {"url": _a[0], "fetched_at": 1, "expires_at": 2})
    monkeypatch.setattr(fetch, "_write_resume", lambda *_a: None)


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("body_cap", [None, 10])
def test_400_header_blocks_before_body_and_lease_release_without_fallback(body_cap):
    response = Response(400, body=AssertionError("400 body must not be read"))
    session = Session([response])
    with policy.acquisition_http_policy(), fetch.fetch_context(job_id="strict") as metrics:
        with pytest.raises(fetch.FetchAccessBlocked, match="http-400"):
            run(policy.fetch_text(session, "https://db.netkeiba.com/race/1", use_cache=False,
                                  max_body_bytes=body_cap, retry_statuses={400, 429, 500}, max_retries=3))
    assert len(session.calls) == 1
    assert response.reads == 0 and response.exits == 1
    assert response.state_at_exit["blocked_reason"] == "http-400"
    assert response.state_at_exit["owner"]
    assert fetch._REQUEST_PACER.status()["owner"] is None
    assert metrics["status_400"] == 1
    assert metrics["network_requests"] == 1
    restarted = RequestPacer(fetch._REQUEST_PACER.path)
    assert restarted.status()["blocked_reason"] == "http-400"
    with pytest.raises(fetch.FetchAccessBlocked, match="http-400"):
        run(fetch.fetch_text(session, "https://race.netkeiba.com/race/2", use_cache=False))
    assert len(session.calls) == 1


def test_waiting_siblings_stop_after_first_400_and_context_is_reset():
    async def scenario():
        gate = asyncio.Event()
        response = Response(400, enter_gate=gate)
        session = Session([response])
        with policy.acquisition_http_policy():
            tasks = [asyncio.create_task(policy.fetch_text(
                session, f"https://{host}.netkeiba.com/race/{number}", use_cache=False,
            )) for number, host in enumerate(("db", "race", "db.sp"))]
            for _ in range(5):
                await asyncio.sleep(0)
            assert len(session.calls) == 1
            gate.set()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            assert all(isinstance(item, fetch.FetchAccessBlocked) for item in results)
        assert policy._POLICY.get() is None
        assert len(session.calls) == 1
        assert response.reads == 0
        assert fetch._REQUEST_PACER.status()["owner"] is None
    run(scenario())


@pytest.mark.parametrize("minimum", [None, 1.0, 3.0, 7.0])
def test_scoped_requests_use_at_least_three_seconds_and_keep_stricter_minimum(minimum):
    async def scenario():
        session = Session([Response(), Response(), Response()])
        kwargs = {} if minimum is None else {"min_interval_sec": minimum}
        with policy.acquisition_http_policy():
            for number in range(3):
                await policy.fetch_text(session, f"https://db.netkeiba.com/race/{number}", use_cache=False, **kwargs)
        expected = max(3.0, minimum or 1.0)
        assert all(b[1] - a[1] >= expected for a, b in zip(session.calls, session.calls[1:]))
    run(scenario())


def test_existing_slowdown_and_429_retry_after_are_not_shortened():
    async def scenario():
        session = Session([Response(429, headers={"Retry-After": "900"}), Response(), Response()])
        with policy.acquisition_http_policy():
            result, _ = await policy.fetch_text(session, "https://db.netkeiba.com/race/1", use_cache=False,
                                               max_retries=2, retry_base_sec=0, retry_jitter_sec=0)
            assert result.status == 200
            await policy.fetch_text(session, "https://race.netkeiba.com/race/2", use_cache=False)
        assert session.calls[1][1] - session.calls[0][1] >= 900
        assert session.calls[2][1] - session.calls[1][1] >= 3
        assert fetch._REQUEST_PACER.status()["blocked_reason"] == ""
    run(scenario())


def test_existing_adaptive_interval_above_three_is_not_shortened():
    pacer = fetch._REQUEST_PACER
    for _ in range(4):
        pacer.observe("netkeiba.com", kind="race", status=503, circuit_threshold=100)
    previous = pacer.status()["interval"]
    assert previous > 3
    session = Session([Response(), Response()])
    with policy.acquisition_http_policy():
        for number in range(2):
            run(policy.fetch_text(session, f"https://db.netkeiba.com/race/{number}", use_cache=False))
    assert session.calls[1][1] - session.calls[0][1] >= previous


def test_400_does_not_clear_existing_cooldown_or_its_retry_after():
    pacer = fetch._REQUEST_PACER
    pacer.observe("netkeiba.com", kind="race", status=429, retry_after=900)
    first_cooldown = pacer.status()["cooldown_until"]
    session = Session([Response(400, headers={"Retry-After": "1200"})])
    with policy.acquisition_http_policy():
        with pytest.raises(fetch.FetchAccessBlocked, match="http-400"):
            run(policy.fetch_text(session, "https://db.netkeiba.com/race/1", use_cache=False))
    state = pacer.status()
    assert session.calls[0][1] >= first_cooldown
    assert state["cooldown_until"] >= session.calls[0][1] + 1200
    assert state["blocked_reason"] == "http-400"


@pytest.mark.parametrize("status", [401, 403])
def test_scoped_401_403_keep_existing_provider_block(status):
    session = Session([Response(status)])
    with policy.acquisition_http_policy():
        with pytest.raises(fetch.FetchAccessBlocked, match=f"http-{status}"):
            run(policy.fetch_text(session, "https://db.netkeiba.com/race/1", use_cache=False))
    assert fetch._REQUEST_PACER.status()["blocked_reason"] == f"http-{status}"
    assert len(session.calls) == 1


def test_cache_hit_is_unchanged_and_has_no_http_or_pacer_creation(monkeypatch):
    cached = {"url": "https://db.netkeiba.com/race/1", "status": 200, "body": b"cached",
              "fetched_at": 123, "expires_at": 456}
    monkeypatch.setattr(fetch, "_lookup_cached_fetch", lambda *_a: (None, cached))
    session = Session([])
    with policy.acquisition_http_policy(), fetch.fetch_context(job_id="cache") as metrics:
        response, html = run(policy.fetch_text(session, cached["url"]))
    assert html == "cached" and response.source == "cache"
    assert response.cache_snapshot == {"url": cached["url"], "fetched_at": 123, "expires_at": 456}
    assert metrics["cache_reuse_hits"] == 1
    assert not session.calls and not fetch._REQUEST_PACER.path.exists()


def test_horse_source_wrapper_preserves_body_bound_provenance(monkeypatch):
    from scraping.parsed_cache import collect_sources

    url = "https://db.netkeiba.com/horse/1/"
    cached = {"url": url, "status": 200, "body": b"cached horse", "fetched_at": 123, "expires_at": 456}
    monkeypatch.setattr(fetch, "_lookup_cached_fetch", lambda *_a: (None, cached))
    with policy.acquisition_http_policy(), collect_sources() as sources:
        response, html = run(policy.fetch_source_text(Session([]), url))
    assert html == "cached horse"
    assert sources == {url: response.cache_snapshot}


def test_unscoped_delegates_exact_session_and_kwargs(monkeypatch):
    sentinel = object()
    session, seen = object(), []

    async def delegate(*args, **kwargs):
        seen.append((args, kwargs))
        return sentinel

    monkeypatch.setattr(fetch, "fetch_text", delegate)
    result = run(policy.fetch_text(session, "https://db.netkeiba.com/race/1", min_interval_sec=1.0))
    assert result is sentinel
    assert seen == [((session, "https://db.netkeiba.com/race/1"), {"min_interval_sec": 1.0})]


def test_unscoped_400_is_not_a_new_block():
    response = Response(400, body=b"bad request")
    session = Session([response])
    result, html = run(policy.fetch_text(session, "https://db.netkeiba.com/race/1", use_cache=False))
    assert result.status == 400 and html == "bad request"
    assert response.reads == 1
    assert fetch._REQUEST_PACER.status()["blocked_reason"] == ""


def test_nested_scopes_and_to_thread_do_not_reset_stop():
    async def scenario():
        assert not policy.acquisition_policy_active()
        with policy.acquisition_http_policy():
            assert policy.acquisition_policy_active()
            original = policy._POLICY.get()
            assert await asyncio.to_thread(policy._POLICY.get) is original
            original.halt_reason = "http-400"
            with policy.acquisition_http_policy():
                assert policy.acquisition_policy_active()
                assert policy._POLICY.get() is original
                with pytest.raises(fetch.FetchAccessBlocked):
                    await policy.fetch_bytes(Session([]), "https://db.netkeiba.com/race/1", use_cache=False)
        assert policy._POLICY.get() is None
        assert not policy.acquisition_policy_active()
    run(scenario())


def test_400_response_cleanup_failure_is_not_swallowed():
    failure = OSError("response close failed")
    response = Response(400, exit_error=failure)
    session = Session([response])
    with policy.acquisition_http_policy():
        with pytest.raises(OSError, match="response close failed"):
            run(policy.fetch_bytes(session, "https://db.netkeiba.com/race/1", use_cache=False,
                                   retry_base_sec=0, retry_jitter_sec=0))
    assert response.exits == 1 and response.reads == 0
    assert len(session.calls) == 1
    assert fetch._REQUEST_PACER.status()["blocked_reason"] == "http-400"
    assert fetch._REQUEST_PACER.status()["owner"] is None


def test_actual_race_consumer_does_not_take_400_endpoint_fallback():
    from scraping.race import scrape_race_full

    response = Response(400)
    session = Session([response])
    with policy.acquisition_http_policy():
        with pytest.raises(fetch.FetchAccessBlocked, match="http-400"):
            run(scrape_race_full(session, "202605010101", date_hint="20260101", quick_mode=True))
    assert len(session.calls) == 1 and response.reads == 0


@pytest.mark.parametrize("listing", ["jra", "all"])
def test_actual_listing_consumers_stop_on_400_before_any_endpoint_fallback(monkeypatch, listing):
    from scraping import jra_listing, race_list

    response = Response(400)
    session = Session([response])
    monkeypatch.setattr(jra_listing.aiohttp, "ClientSession", lambda **_kwargs: session)
    monkeypatch.setattr(jra_listing.aiohttp, "TCPConnector", lambda **_kwargs: None)
    monkeypatch.setattr(race_list, "source_metadata", lambda _url: None)
    monkeypatch.setattr(race_list, "_recent_or_future", lambda _date: False)
    get_ids = jra_listing.fetch_jra_race_ids if listing == "jra" else race_list.fetch_race_ids
    with policy.acquisition_http_policy(), fetch.fetch_context(job_id="listing") as metrics:
        with pytest.raises(fetch.FetchAccessBlocked, match="http-400"):
            run(get_ids("20160105", force_refresh=True))
    assert len(session.calls) == metrics["network_requests"] == metrics["status_400"] == 1
    assert response.reads == 0


def test_actual_jra_month_pagination_keeps_three_second_floor(monkeypatch):
    from scraping import jra_listing, race_list

    first = b'<a href="/race/201606010101/">2016/01/05 1R</a><a href="?page=2">2</a>'
    second = b'<a href="/race/201606010102/">2016/01/05 2R</a>'
    session = Session([Response(body=first), Response(body=second)])
    monkeypatch.setattr(jra_listing.aiohttp, "ClientSession", lambda **_kwargs: session)
    monkeypatch.setattr(jra_listing.aiohttp, "TCPConnector", lambda **_kwargs: None)
    monkeypatch.setattr(race_list, "source_metadata", lambda _url: None)
    monkeypatch.setattr(race_list, "_recent_or_future", lambda _date: False)
    monkeypatch.setattr(race_list, "put_parsed", lambda *_args, **_kwargs: None)
    with policy.acquisition_http_policy():
        ids, _source = run(jra_listing.fetch_jra_race_ids("20160105", force_refresh=True))
    assert ids == ["201606010101", "201606010102"]
    assert len(session.calls) == 2
    assert session.calls[1][1] - session.calls[0][1] >= 3


def test_consumer_transport_bindings_cover_race_horse_lists_odds_browser():
    from scraping import browser_transport, horse, odds, race, race_list

    assert race.fetch_text is policy.fetch_text
    assert race_list.fetch_text is policy.fetch_text
    assert odds.fetch_text is policy.fetch_text
    assert horse.fetch_text is policy.fetch_source_text
    assert browser_transport.fetch_bytes is policy.fetch_bytes


def test_capability_is_bound_to_matching_preservation_version(monkeypatch):
    from scraping import storage_preservation

    value = policy.acquisition_safety_capabilities()
    assert value == {"safety_policy_version": "normal-acquisition-safe-v1",
                     "minimum_provider_interval_seconds": 3.0, "halt_http400": True,
                     "preserve_saved_values": True, "max_concurrent_http_requests": 1}
    monkeypatch.setattr(storage_preservation, "PRESERVATION_POLICY_VERSION", "not-the-same-contract")
    assert policy.acquisition_safety_capabilities()["preserve_saved_values"] is False
