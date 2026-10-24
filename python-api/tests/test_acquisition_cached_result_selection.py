"""Real local HTML parsing and temporary caches; never provider or live DB I/O."""
from __future__ import annotations

import asyncio
import hashlib
import sqlite3
import time

import pytest

from scraping import acquisition_http_policy as policy
from scraping import fetch_pipeline as fetch
from scraping import race, result_reuse
from test_race_result_only_repair import DATE, DESKTOP, MOBILE, RACE_ID


DESKTOP_URL = f"https://db.netkeiba.com/race/{RACE_ID}/"
MOBILE_URL = f"https://db.sp.netkeiba.com/race/{RACE_ID}/"


class Response:
    def __init__(self, status=200, body=DESKTOP):
        self.status, self.headers = status, {}
        self.body = body.encode("utf-8")
        self.reads = self.exits = 0

    async def __aenter__(self):
        return self

    async def read(self):
        self.reads += 1
        return self.body

    async def __aexit__(self, *_exc):
        self.exits += 1
        return False


class Session:
    def __init__(self, response=None):
        self.response, self.calls = response, []

    def get(self, url, **_kwargs):
        self.calls.append(url)
        if self.response is None:
            raise AssertionError("No network is needed for a valid selected result cache")
        if len(self.calls) > 1:
            raise AssertionError("No second HTTP or cross-host fallback is permitted")
        return self.response


@pytest.fixture(autouse=True)
def isolated_result_cache(tmp_path, monkeypatch):
    # The shared fixture isolates the cross-process pacer. This fixture also
    # isolates HTTP body/resume state and avoids the global endpoint store.
    monkeypatch.setattr(fetch, "_CACHE_DB_PATH", tmp_path / "http-cache.db")
    monkeypatch.setattr(race, "prefer_mobile_endpoint", lambda _kind: False)
    preferences = []
    monkeypatch.setattr(race, "remember_mobile_endpoint", lambda *a, **kw: preferences.append((a, kw)))
    yield preferences
    fetch.close_fetch_cache_connections()


def store(url, html, *, ttl=60, status=200):
    return fetch._write_cache(url, url, status, {}, html.encode("utf-8"), ttl)


def parse(session, *, force_refresh=False, scoped=True):
    async def run():
        if scoped:
            with policy.acquisition_http_policy():
                return await race.scrape_race_full(
                    session, RACE_ID, DATE, quick_mode=True,
                    force_refresh=force_refresh, enrich_horse_details=False,
                )
        return await race.scrape_race_full(
            session, RACE_ID, DATE, quick_mode=True,
            force_refresh=force_refresh, enrich_horse_details=False,
        )

    return asyncio.run(run())


@pytest.mark.parametrize("preferred_mobile,cached_mobile", [(False, True), (True, False)])
def test_selects_available_other_host_cache_and_parses_original_identities_without_http(
        monkeypatch, isolated_result_cache, preferred_mobile, cached_mobile):
    monkeypatch.setattr(race, "prefer_mobile_endpoint", lambda _kind: preferred_mobile)
    url, html = (MOBILE_URL, MOBILE) if cached_mobile else (DESKTOP_URL, DESKTOP)
    metadata = store(url, html)
    session = Session()
    with fetch.fetch_context(job_id="offline-cache") as metrics:
        actual = parse(session)
    assert session.calls == []
    assert metrics.get("network_requests", 0) == 0
    assert metrics["cache_reuse_hits"] == 1
    assert isolated_result_cache == [], "Per-race cache selection must not alter global preferences"
    evidence = actual["_source_evidence"]
    assert evidence["source_url"] == url
    assert evidence["transport_source"] in {"cache", "resume-cache"}
    assert evidence["body_sha256"] == hashlib.sha256(html.encode("utf-8")).hexdigest()
    assert evidence["source_date"] == DATE
    expected = [(1, "2013000001", "Horse One")]
    if not cached_mobile:
        expected.append((3, "2013000003", "Horse Three"))
    assert [(h["horse_number"], h["horse_id"], h["horse_name"]) for h in actual["horses"]] == expected
    assert evidence["runner_identity"] == [
        {"horse_number": number, "horse_id": hid, "horse_name": name} for number, hid, name in expected
    ]
    assert fetch._read_cache(url)["fetched_at"] == metadata["fetched_at"]


@pytest.mark.parametrize("preferred_mobile", [False, True])
def test_two_valid_caches_keep_existing_preference_without_http(monkeypatch, preferred_mobile):
    store(DESKTOP_URL, DESKTOP)
    store(MOBILE_URL, MOBILE)
    monkeypatch.setattr(race, "prefer_mobile_endpoint", lambda _kind: preferred_mobile)
    session = Session()
    actual = parse(session)
    assert session.calls == []
    assert actual["_source_evidence"]["source_url"] == (MOBILE_URL if preferred_mobile else DESKTOP_URL)


def test_expired_or_rejected_preferred_cache_uses_other_valid_cache_without_http(monkeypatch):
    store(DESKTOP_URL, DESKTOP)
    store(MOBILE_URL, MOBILE)
    with sqlite3.connect(fetch._CACHE_DB_PATH) as connection:
        connection.execute("UPDATE http_cache SET expires_at=0 WHERE normalized_url=?", (MOBILE_URL,))
    monkeypatch.setattr(race, "prefer_mobile_endpoint", lambda _kind: True)
    session = Session()
    assert parse(session)["_source_evidence"]["source_url"] == DESKTOP_URL
    assert session.calls == []
    store(MOBILE_URL, MOBILE, status=403)
    assert result_reuse.cached_result_url(RACE_ID, prefer_mobile=True) == DESKTOP_URL


def test_force_refresh_bypasses_both_caches_and_preserves_original_endpoint_selection(monkeypatch):
    store(MOBILE_URL, MOBILE)
    store(DESKTOP_URL, DESKTOP)
    monkeypatch.setattr(race, "cached_result_url", lambda *_a, **_kw: pytest.fail("Forced refresh must not select cache"))
    response = Response()
    session = Session(response)
    actual = parse(session, force_refresh=True)
    assert session.calls == [DESKTOP_URL]
    assert response.reads == response.exits == 1
    assert actual["_source_evidence"]["transport_source"] == "network"


def test_unscoped_consumers_keep_their_original_url_and_do_not_read_selector(monkeypatch):
    store(MOBILE_URL, MOBILE)
    monkeypatch.setattr(race, "cached_result_url", lambda *_a, **_kw: pytest.fail("Unscoped consumers must remain unchanged"))
    session = Session(Response())
    actual = parse(session, scoped=False)
    assert session.calls == [DESKTOP_URL]
    assert actual["_source_evidence"]["source_url"] == DESKTOP_URL


def test_400_during_forced_refresh_never_tries_even_an_available_mobile_cache(isolated_result_cache):
    store(MOBILE_URL, MOBILE)
    response = Response(400, "must not read")
    session = Session(response)
    with pytest.raises(fetch.FetchAccessBlocked, match="http-400"):
        parse(session, force_refresh=True)
    assert session.calls == [DESKTOP_URL]
    assert response.reads == 0 and response.exits == 1
    assert isolated_result_cache == []
    assert fetch._REQUEST_PACER.status()["blocked_reason"] == "http-400"
    # Even a fresh invocation with valid cached HTML must observe the shared
    # block before sending any new HTTP; no access-state clearing is allowed.
    with policy.acquisition_http_policy():
        with pytest.raises(fetch.FetchAccessBlocked, match="http-400"):
            asyncio.run(policy.fetch_text(session, DESKTOP_URL, force_refresh=True))
    assert session.calls == [DESKTOP_URL]


def test_selector_does_not_create_a_database_or_read_body_bytes(monkeypatch):
    assert result_reuse.cached_result_url(RACE_ID) is None
    assert not fetch._CACHE_DB_PATH.exists()
    store(MOBILE_URL, MOBILE)
    before = fetch._CACHE_DB_PATH.read_bytes()
    monkeypatch.setattr(fetch, "_read_cache", lambda *_a: pytest.fail("Metadata selection must not load HTML"))
    assert result_reuse.cached_result_url(RACE_ID) == MOBILE_URL
    assert fetch._CACHE_DB_PATH.read_bytes() == before
    with sqlite3.connect(fetch._CACHE_DB_PATH) as connection:
        connection.execute("UPDATE http_cache SET expires_at=?", (time.time() - 1,))
    assert result_reuse.cached_result_url(RACE_ID) is None


def test_worker_reuse_gate_accepts_real_cached_parser_without_any_http(monkeypatch):
    store(DESKTOP_URL, DESKTOP)
    session = Session()
    details = []

    async def complete_details(_session, horse_id, *_args, **_kwargs):
        details.append(horse_id)
        return {"sire": "Sire", "dam": "Dam", "damsire": "Damsire"}

    monkeypatch.setattr(race, "scrape_horse_detail", complete_details)

    async def run():
        with policy.acquisition_http_policy():
            return await result_reuse.fetch_acquisition_race(
                session, RACE_ID, date_hint=DATE, fetcher=race.scrape_race_full,
            )

    actual = asyncio.run(run())
    assert session.calls == []
    assert sorted(details) == ["2013000001", "2013000003"]
    assert result_reuse.classify_race_quality(actual).valid_for_date_completion
    assert [h["horse_id"] for h in actual["horses"]] == ["2013000001", "2013000003"]
