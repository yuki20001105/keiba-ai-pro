from __future__ import annotations

import asyncio
from email.utils import formatdate

import pytest

from scraping import fetch_pipeline as fetch
from scraping.request_pacing import AccessPaused, RequestPacer, site_family


class Response:
    def __init__(self, status=200, body=b"<html>race data</html>", headers=None, gate=None):
        self.status, self.body, self.headers, self.gate = status, body, headers or {}, gate

    async def read(self):
        if self.gate:
            await self.gate.wait()
        if isinstance(self.body, Exception):
            raise self.body
        return self.body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, fetch._REQUEST_PACER.clock(), kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def run(coro):
    return asyncio.run(coro)


def test_cross_host_concurrency_shares_atomic_start_interval_and_one_lease():
    async def scenario():
        gate = asyncio.Event()
        session = Session([Response(gate=gate), Response(), Response()])
        tasks = [asyncio.create_task(fetch.fetch_bytes(session, f"https://{host}.netkeiba.com/race/{i}", use_cache=False))
                 for i, host in enumerate(("db", "race", "db"))]
        for _ in range(10):
            await asyncio.sleep(0)
        assert len(session.calls) == 1
        gate.set()
        results = await asyncio.gather(*tasks)
        assert all(result.status == 200 for result in results)
        assert all(b[1] - a[1] >= 2.0 for a, b in zip(session.calls, session.calls[1:]))
        assert fetch.get_fetch_control_status()["owner"] is None
    run(scenario())


def test_retry_after_persists_full_duration_and_updates_waiting_requests(tmp_path):
    pacer = fetch._REQUEST_PACER
    owner, _ = run(pacer.acquire("netkeiba.com"))
    now = pacer.clock()
    pacer.observe("netkeiba.com", kind="race", status=429, retry_after=900)
    pacer.release("netkeiba.com", owner)
    restarted = RequestPacer(pacer.path, clock=pacer.clock, sleep=pacer.sleep)
    assert restarted.status()["cooldown_until"] == now + 900
    observed = []

    def during_wait(state):
        observed.append(state["cooldown_until"])
        if len(observed) == 1:
            # Another job adds a longer cooldown while this one is queued.
            restarted.observe("netkeiba.com", kind="horse", status=429, retry_after=1800)

    token, _ = run(pacer.acquire("netkeiba.com", on_wait=during_wait))
    assert pacer.clock() >= now + 1800
    assert observed[-1] == now + 1800
    pacer.release("netkeiba.com", token)


@pytest.mark.parametrize("status,body", [(403, b"denied"), (401, b"login"), (200, b"<title>Just a moment...</title>cf-chl-")])
def test_access_block_stops_alternate_hosts_and_is_not_cached(monkeypatch, status, body):
    writes = []
    monkeypatch.setattr(fetch, "_write_cache", lambda *args: writes.append(args))
    session = Session([Response(status, body)])
    with pytest.raises(fetch.FetchAccessBlocked):
        run(fetch.fetch_text(session, "https://db.netkeiba.com/horse/1", force_refresh=True))
    with pytest.raises(fetch.FetchAccessBlocked):
        run(fetch.fetch_text(session, "https://race.netkeiba.com/race/2", force_refresh=True))
    assert len(session.calls) == 1
    assert not writes
    assert RequestPacer(fetch._REQUEST_PACER.path).status()["blocked_reason"]


def test_429_at_final_attempt_still_blocks_next_request_and_full_header_wins():
    session = Session([Response(429, headers={"Retry-After": "900"}), Response()])
    first = run(fetch.fetch_bytes(session, "https://db.netkeiba.com/race/1", use_cache=False,
                                max_retries=1, max_retry_after_sec=0))
    second = run(fetch.fetch_bytes(session, "https://race.netkeiba.com/race/2", use_cache=False, max_retries=1))
    assert first.status == 429 and second.status == 200
    assert session.calls[1][1] - session.calls[0][1] >= 900


def test_503_retry_after_also_pauses_queued_other_host():
    async def scenario():
        session = Session([Response(503, headers={"Retry-After": "900"}), Response()])
        first, second = await asyncio.gather(
            fetch.fetch_bytes(session, "https://db.netkeiba.com/race/1", use_cache=False,
                              max_retries=1, max_retry_after_sec=0),
            fetch.fetch_bytes(session, "https://race.netkeiba.com/race/2", use_cache=False, max_retries=1),
        )
        assert first.status == 503 and second.status == 200
        assert session.calls[1][1] - session.calls[0][1] >= 900
    run(scenario())


def test_retry_after_accepts_http_dates_without_clamping():
    header = formatdate(fetch.time.time() + 3600, usegmt=True)
    assert 3598 <= fetch._parse_retry_after({"Retry-After": header}) <= 3600


def test_timeout_and_connection_failure_attempts_are_counted_in_job_scope():
    session = Session([asyncio.TimeoutError(), OSError("connection lost")])
    with fetch.fetch_context(job_id="test") as metrics:
        result = run(fetch.fetch_bytes(session, "https://db.netkeiba.com/race/1", use_cache=False,
                                      max_retries=2, retry_base_sec=0, retry_jitter_sec=0))
    assert result.status == 0
    assert metrics["network_requests"] == 2
    assert metrics["timeout_count"] == metrics["transport_error_count"] == 1
    assert fetch.get_fetch_control_status()["interval"] > 2
    assert fetch.get_fetch_control_status()["owner"] is None


def test_cached_body_uses_no_network_or_rate_wait(monkeypatch):
    monkeypatch.setattr(fetch, "_read_cache", lambda _: {"url": "url", "status": 200, "body": b"valid"})
    session = Session([])
    with fetch.fetch_context(job_id="cache") as metrics:
        result = run(fetch.fetch_bytes(session, "https://db.netkeiba.com/race/1"))
    assert result.source == "cache"
    assert metrics == {
        "cache_hits": 1, "by_kind_race_cache_hits": 1,
        "cache_reuse_hits": 1, "by_kind_race_cache_reuse_hits": 1,
    }
    assert not session.calls
    assert not fetch._REQUEST_PACER.path.exists()


def test_cancelled_duplicate_does_not_cancel_producer():
    async def scenario():
        gate = asyncio.Event()
        session = Session([Response(gate=gate)])
        producer = asyncio.create_task(fetch.fetch_bytes(session, "https://db.netkeiba.com/race/1", use_cache=False))
        await asyncio.sleep(0)
        duplicate = asyncio.create_task(fetch.fetch_bytes(session, "https://db.netkeiba.com/race/1", use_cache=False))
        await asyncio.sleep(0)
        duplicate.cancel()
        with pytest.raises(asyncio.CancelledError):
            await duplicate
        gate.set()
        assert (await producer).status == 200
        assert len(session.calls) == 1
        assert not fetch._LOOP_INFLIGHT
    run(scenario())


def test_cancelled_producer_releases_waiter_and_lease():
    async def scenario():
        session = Session([Response(gate=asyncio.Event())])
        producer = asyncio.create_task(fetch.fetch_bytes(session, "https://db.netkeiba.com/race/1", use_cache=False))
        await asyncio.sleep(0)
        duplicate = asyncio.create_task(fetch.fetch_bytes(session, "https://db.netkeiba.com/race/1", use_cache=False))
        await asyncio.sleep(0)
        producer.cancel()
        results = await asyncio.gather(producer, duplicate, return_exceptions=True)
        assert all(isinstance(result, asyncio.CancelledError) for result in results)
        assert fetch.get_fetch_control_status()["owner"] is None
        assert not fetch._LOOP_INFLIGHT
    run(scenario())


def test_wait_callback_cancellation_propagates_without_attempt():
    class StopRequested(Exception):
        pass
    pacer = fetch._REQUEST_PACER
    pacer.observe("netkeiba.com", kind="race", status=429)

    def stop(state):
        assert state["cooldown_until"] > pacer.clock()
        raise StopRequested()

    session = Session([])
    with fetch.fetch_context(job_id="stop", on_wait=stop):
        with pytest.raises(StopRequested):
            run(fetch.fetch_bytes(session, "https://db.netkeiba.com/race/1", use_cache=False))
    assert not session.calls
    assert not fetch._LOOP_INFLIGHT


def test_total_timeout_releases_active_network_lease():
    async def scenario():
        session = Session([Response(gate=asyncio.Event())])
        with fetch.fetch_context(job_id="deadline") as metrics:
            result = await fetch.fetch_bytes(session, "https://db.netkeiba.com/race/1", use_cache=False,
                                             total_timeout_sec=0.01, max_retries=1)
        assert result.source == "network-timeout"
        assert metrics["network_requests"] == metrics["total_timeout_count"] == 1
        assert fetch.get_fetch_control_status()["owner"] is None
        assert not fetch._LOOP_INFLIGHT
    run(scenario())


def test_heartbeat_failure_cannot_leave_a_lease_behind(monkeypatch):
    async def failed_heartbeat(*args):
        raise RuntimeError("lease renewal failed")

    class YieldingResponse(Response):
        async def read(self):
            await asyncio.sleep(0)
            return self.body

    monkeypatch.setattr(fetch._REQUEST_PACER, "heartbeat", failed_heartbeat)
    with pytest.raises(RuntimeError, match="lease renewal failed"):
        run(fetch.fetch_bytes(Session([YieldingResponse()]), "https://db.netkeiba.com/race/1", use_cache=False))
    assert fetch.get_fetch_control_status()["owner"] is None
    assert not fetch._LOOP_INFLIGHT


def test_stable_responses_accelerate_only_after_count_and_duration():
    pacer = fetch._REQUEST_PACER
    for _ in range(50):
        pacer.observe("netkeiba.com", kind="race", status=200, latency=0.2)
    assert pacer.status()["interval"] == 2
    run(pacer.sleep(121))
    pacer.observe("netkeiba.com", kind="race", status=200, latency=0.2)
    assert pacer.status()["interval"] == pytest.approx(1.8)
    # Horse pages have a separate response-time baseline and stability counter.
    pacer.observe("netkeiba.com", kind="horse", status=200, latency=10)
    assert pacer.status()["interval"] == pytest.approx(1.8)
    for _ in range(20):
        pacer.observe("netkeiba.com", kind="race", status=200, latency=1)
    assert pacer.status()["interval"] > 1.8


def test_acceleration_never_goes_below_one_second_and_failure_resets_window():
    pacer = fetch._REQUEST_PACER
    for _ in range(12):
        for _ in range(50):
            pacer.observe("netkeiba.com", kind="race", status=200, latency=0.2)
        run(pacer.sleep(121))
        pacer.observe("netkeiba.com", kind="race", status=200, latency=0.2)
    assert pacer.status()["interval"] == 1
    pacer.observe("netkeiba.com", kind="race", status=503)
    assert pacer.status()["interval"] >= 2


def test_two_independent_controllers_cannot_hold_same_lease():
    first = fetch._REQUEST_PACER
    second = RequestPacer(first.path, clock=first.clock, sleep=first.sleep)
    assert first._claim("netkeiba.com", 1, "first") == 0
    assert second._claim("netkeiba.com", 1, "second") > 0
    first.release("netkeiba.com", "first")
    run(first.sleep(2))
    assert second._claim("netkeiba.com", 1, "second") == 0
    first.release("netkeiba.com", "first")  # Stale cleanup cannot release another worker.
    assert first.status()["owner"] == "second"
    second.release("netkeiba.com", "second")


def test_scoped_metrics_do_not_reset_another_job():
    with fetch.fetch_context(job_id="outer"):
        fetch.record_fetch_metric("parsed_cache_hits", 2)
        with fetch.fetch_context(job_id="inner"):
            fetch.record_fetch_metric("parsed_cache_hits", 1)
            assert fetch.get_fetch_metrics(reset=True) == {"parsed_cache_hits": 1}
        assert fetch.get_fetch_metrics() == {"parsed_cache_hits": 2}


def test_request_headers_and_response_headers_survive_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, "_CACHE_DB_PATH", tmp_path / "cache.db")
    session = Session([Response(headers={"Content-Type": "application/json"}, body=b'{"ok":true}')])
    url = "https://race.netkeiba.com/api/api_get_jra_odds.html?race_id=1"
    result = run(fetch.fetch_bytes(session, url, request_headers={"X-Requested-With": "XMLHttpRequest"}))
    cached = run(fetch.fetch_bytes(session, url))
    assert session.calls[0][2]["headers"] == {"X-Requested-With": "XMLHttpRequest"}
    assert result.response_headers == cached.response_headers == {"Content-Type": "application/json"}
    assert len(session.calls) == 1


def test_redirect_is_separately_paced_and_cannot_escape_site():
    session = Session([Response(302, headers={"Location": "https://race.netkeiba.com/race/2"}), Response()])
    result = run(fetch.fetch_bytes(session, "https://db.netkeiba.com/race/1", use_cache=False))
    assert result.status == 200 and result.attempts == 2
    assert session.calls[1][1] - session.calls[0][1] >= 2
    assert all(call[2]["allow_redirects"] is False for call in session.calls)
    unsafe = Session([Response(302, headers={"Location": "http://169.254.169.254/latest"})])
    result = run(fetch.fetch_bytes(unsafe, "https://db.netkeiba.com/race/3", use_cache=False))
    assert result.source == "network-rejected"
    assert len(unsafe.calls) == 1
    assert site_family("https://netkeiba.com.evil.test") != "netkeiba.com"
