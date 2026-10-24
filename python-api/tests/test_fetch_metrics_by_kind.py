"""Request accounting uses fake transport and isolated temporary cache only."""
import asyncio
from collections import deque

import pytest

from scraping import fetch_pipeline as pipeline


class Pacer:
    async def acquire(self, *args):
        return "test-owner", 0.0

    async def heartbeat(self, *args):
        await asyncio.Event().wait()

    def observe(self, *args, **kwargs):
        pass

    def release(self, *args):
        pass

    def status(self, *args):
        return {}

    async def sleep(self, seconds):
        await asyncio.sleep(0)


class Response:
    def __init__(self, status=200, body=b"valid page", headers=None, *, entered=None, gate=None):
        self.status = status
        self.body = body
        self.headers = headers or {}
        self.entered = entered
        self.gate = gate

    async def __aenter__(self):
        if self.entered:
            self.entered.set()
        return self

    async def __aexit__(self, *args):
        pass

    async def read(self):
        if self.gate:
            await self.gate.wait()
        return self.body


class Session:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        result = self.responses.popleft()
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture(autouse=True)
def isolated_metrics(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "_METRICS", {})
    monkeypatch.setattr(pipeline, "_CACHE_DB_PATH", tmp_path / "http-cache.db")
    monkeypatch.setattr(pipeline, "_REQUEST_PACER", Pacer())
    monkeypatch.setattr(pipeline, "_LOOP_INFLIGHT", {})


@pytest.mark.parametrize(("url", "kind"), [
    ("https://race.netkeiba.com/top/calendar.html?year=2020", "calendar"),
    ("https://race.netkeiba.com/top/race_list.html", "list"),
    ("https://db.sp.netkeiba.com/?pid=race_list", "list"),
    ("https://db.sp.netkeiba.com/race/202001010101/", "race"),
    ("https://db.sp.netkeiba.com/horse/result/2016100000/", "horse"),
    ("https://db.netkeiba.com/horse/ped/2016100000/", "pedigree"),
    ("https://race.netkeiba.com/api/api_get_jra_odds.html", "odds"),
])
def test_bounded_metric_categories(url, kind):
    assert pipeline.metric_page_kind(url) == kind


def test_actual_redirect_targets_and_retry_requests_are_attributed():
    first = "https://db.netkeiba.com/race/202001010101/"
    redirected = "https://db.netkeiba.com/horse/result/2016100000/"
    session = Session([
        Response(302, headers={"Location": redirected}),
        Response(503), Response(200),
    ])

    async def run():
        with pipeline.fetch_context(job_id="parent-month"):
            result = await pipeline.fetch_bytes(session, first, use_cache=False,
                                                retry_base_sec=0, retry_jitter_sec=0)
            return result, pipeline.get_fetch_metrics()

    result, metrics = asyncio.run(run())
    assert result.status == 200 and result.attempts == 3
    assert session.urls == [first, redirected, redirected]
    assert metrics["network_requests"] == 3
    assert metrics["by_kind_race_network_requests"] == 1
    assert metrics["by_kind_horse_network_requests"] == 2
    assert metrics["by_kind_race_status_302"] == 1
    assert metrics["by_kind_horse_status_503"] == 1
    assert metrics["by_kind_horse_status_200"] == 1
    assert metrics["by_kind_horse_network_retry_requests"] == 1
    assert metrics["by_kind_horse_retry_count"] == 1
    assert metrics["by_kind_horse_request_failure_count"] == 1
    assert metrics["by_kind_horse_http_success_count"] == 1
    for key in ("network_requests", "network_active_cumulative_ms", "pacing_wait_cumulative_ms"):
        assert metrics[key] == sum(metrics.get(f"by_kind_{kind}_{key}", 0)
                                   for kind in ("race", "horse"))


@pytest.mark.parametrize(("response", "metric", "kwargs"), [
    (asyncio.TimeoutError(), "timeout_count", {}),
    (OSError("fake connection failure"), "transport_error_count", {}),
    (Response(403), "access_block_count", {}),
    (Response(200, body=b"<title>Access denied</title>"), "access_block_count", {}),
    (Response(200, body=b"too big"), "body_limit_count", {"max_body_bytes": 2}),
])
def test_failed_actual_attempts_count_once(response, metric, kwargs):
    async def run():
        with pipeline.fetch_context(job_id="failed-month"):
            await pipeline.fetch_bytes(Session([response]), "https://db.netkeiba.com/horse/ped/1/",
                                       use_cache=False, max_retries=1, **kwargs)
            return pipeline.get_fetch_metrics()

    metrics = asyncio.run(run())
    assert metrics["network_requests"] == metrics["by_kind_pedigree_network_requests"] == 1
    assert metrics["request_failure_count"] == metrics["by_kind_pedigree_request_failure_count"] == 1
    assert metrics[metric] == metrics[f"by_kind_pedigree_{metric}"] == 1
    assert not metrics.get("http_success_count", 0)


def test_repeated_cached_resume_does_not_repeat_original_attempts():
    url = "https://db.netkeiba.com/horse/result/1/"
    session = Session([Response()])

    async def run():
        with pipeline.fetch_context(job_id="first"):
            await pipeline.fetch_bytes(session, url, resume_key="horse:1")
            first = pipeline.get_fetch_metrics()
        # A receipt's original number of attempts is not this invocation's HTTP.
        pipeline._write_resume("horse:1", pipeline._normalize_url(url), "success", "network", 200, 5, None)
        with pipeline.fetch_context(job_id="second"):
            second_result = await pipeline.fetch_bytes(session, url, resume_key="horse:1")
            second = pipeline.get_fetch_metrics()
        return first, second_result, second

    first, result, metrics = asyncio.run(run())
    assert first["network_requests"] == 1
    assert result.source == "resume-cache" and result.attempts == 5
    assert metrics.get("network_requests", 0) == 0
    assert metrics["cache_reuse_hits"] == 1
    assert metrics["resume_cache_reuse_hits"] == 1
    assert metrics["by_kind_horse_cache_reuse_hits"] == 1
    assert not metrics.get("resume_metadata_only_hits", 0)
    assert session.urls == [url]


def test_expired_resume_is_metadata_not_saved_http(monkeypatch):
    monkeypatch.setattr(pipeline, "_read_resume", lambda key: {"status": "success", "attempts": 9})
    monkeypatch.setattr(pipeline, "_read_cache", lambda url: None)

    async def run():
        with pipeline.fetch_context(job_id="expired"):
            await pipeline.fetch_bytes(Session([Response()]), "https://db.netkeiba.com/race/1/",
                                       resume_key="race:1")
            return pipeline.get_fetch_metrics()

    metrics = asyncio.run(run())
    assert metrics["network_requests"] == 1
    assert metrics["resume_hits"] == 1  # Legacy compatibility, not a saved-request claim.
    assert metrics["by_kind_race_resume_metadata_only_hits"] == 1
    assert not metrics.get("cache_reuse_hits", 0)
    assert not metrics.get("resume_cache_reuse_hits", 0)


def test_plain_cache_hit_counts_only_an_actual_reuse():
    url = "https://race.netkeiba.com/top/calendar.html"

    async def run():
        session = Session([Response()])
        await pipeline.fetch_bytes(session, url)
        with pipeline.fetch_context(job_id="cached-calendar"):
            await pipeline.fetch_bytes(session, url)
            return pipeline.get_fetch_metrics()

    metrics = asyncio.run(run())
    assert metrics["by_kind_calendar_cache_reuse_hits"] == 1
    assert metrics["by_kind_calendar_cache_hits"] == 1
    assert not metrics.get("network_requests", 0)


def test_shared_result_is_not_charged_as_http_to_follower_context():
    async def run():
        entered, gate = asyncio.Event(), asyncio.Event()
        session = Session([Response(entered=entered, gate=gate)])
        url = "https://db.netkeiba.com/horse/result/1/"

        async def fetch(job_id):
            with pipeline.fetch_context(job_id=job_id):
                await pipeline.fetch_bytes(session, url, use_cache=False)
                return pipeline.get_fetch_metrics()

        producer = asyncio.create_task(fetch("producer"))
        await entered.wait()
        follower = asyncio.create_task(fetch("follower"))
        await asyncio.sleep(0)
        gate.set()
        return await producer, await follower, len(session.urls)

    producer, follower, count = asyncio.run(run())
    assert count == 1
    assert producer["by_kind_horse_network_requests"] == 1
    assert not producer.get("dedup_reuse_hits", 0)
    assert follower["by_kind_horse_dedup_waits"] == 1
    assert follower["by_kind_horse_dedup_reuse_hits"] == 1
    assert not follower.get("network_requests", 0)


def test_metrics_reset_is_context_local_and_to_thread_is_attributed():
    async def run():
        pipeline.record_fetch_metric("local_reuse", 7)
        with pipeline.fetch_context(job_id="a"):
            pipeline.record_fetch_metric("local_reuse", 2, url="https://db.netkeiba.com/horse/1/")
            await asyncio.to_thread(pipeline.record_fetch_metric, "local_reuse", 3,
                                    url="https://db.netkeiba.com/horse/1/")
            first = pipeline.get_fetch_metrics(reset=True)
            assert all(value == 0 for value in pipeline.get_fetch_metrics().values())
            with pipeline.fetch_context(job_id="b"):
                assert pipeline.get_fetch_metrics() == {}
                pipeline.record_fetch_metric("local_reuse", 1)
            assert pipeline.get_fetch_metrics()["local_reuse"] == 0
        return first, pipeline.get_fetch_metrics()

    first, process = asyncio.run(run())
    assert first["local_reuse"] == first["by_kind_horse_local_reuse"] == 5
    assert process["local_reuse"] == 13


def test_cancelled_inflight_attempt_is_still_charged_once():
    async def run():
        entered, gate = asyncio.Event(), asyncio.Event()
        with pipeline.fetch_context(job_id="cancelled"):
            task = asyncio.create_task(pipeline.fetch_bytes(
                Session([Response(entered=entered, gate=gate)]),
                "https://db.netkeiba.com/horse/ped/1/", use_cache=False,
            ))
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return pipeline.get_fetch_metrics()

    metrics = asyncio.run(run())
    assert metrics["by_kind_pedigree_network_requests"] == 1
    assert metrics["by_kind_pedigree_cancelled_requests"] == 1
    assert metrics["by_kind_pedigree_request_failure_count"] == 1
    assert not metrics.get("http_success_count", 0)
