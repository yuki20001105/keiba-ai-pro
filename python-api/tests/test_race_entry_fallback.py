from __future__ import annotations

import asyncio
from datetime import date

import pytest
from bs4 import BeautifulSoup

from scraping import race
from scraping.fetch_pipeline import FetchAccessBlocked, FetchResult
from scraping.quality import classify_race_quality


RACE_ID = "202606040601"
TARGET = "20260919"
TODAY = date(2026, 9, 19)
UNRESOLVED = "<html><body>Results have not been published." + " " * 600 + "</body></html>"
CARD = """
<html><head>
<title>Entry card | 2026年9月19日</title>
<link rel="canonical" href="https://race.netkeiba.com/race/shutuba.html?race_id=202606040601">
</head><body><h1 class="RaceName">Test race</h1>
<div class="RaceData01">10:00発走 / 芝1600m (右)</div>
<div class="RaceData02">4回 中山 6日目</div>
<table class="Shutuba_Table"><tr>
<td>1</td><td>1</td><td></td><td><a href="/horse/2023100001/">Horse One</a></td>
<td>牡3</td><td>57.0</td><td><a href="/jockey/result/recent/00001/">Jockey</a></td>
<td><a href="/trainer/result/recent/00001/">Trainer</a></td><td>---</td><td>---</td><td>**</td>
</tr></table></body></html>
"""


@pytest.fixture
def current_routing(monkeypatch):
    actual_gate = race._entry_card_date_allowed
    monkeypatch.setattr(race, "_entry_card_date_allowed", lambda rid, hint: actual_gate(rid, hint, today=TODAY))
    monkeypatch.setattr(race, "prefer_mobile_endpoint", lambda _kind: False)
    monkeypatch.setattr(race, "remember_mobile_endpoint", lambda *_args, **_kwargs: None)
    enriched = []

    async def odds(_session, race_id):
        enriched.append(("odds", race_id))
        return {}, {}, "not_published"

    async def detail(_session, horse_id, *_args, **_kwargs):
        enriched.append(("horse", horse_id))
        return {"sire": "Sire", "dam": "Dam", "damsire": "Damsire"}

    monkeypatch.setattr(race, "fetch_tansho_odds_api", odds)
    monkeypatch.setattr(race, "scrape_horse_detail", detail)
    return enriched


def _fetcher(monkeypatch, statuses, *, card=CARD, result_html=UNRESOLVED):
    calls = []
    remaining = list(statuses)

    async def fetch(_session, url, **kwargs):
        calls.append((url, kwargs))
        is_card = "/shutuba.html?" in url
        status = 200 if is_card else remaining.pop(0)
        html = card if is_card else result_html if status == 200 else ""
        return FetchResult(url, url, status, html.encode(), "network", 1), html

    monkeypatch.setattr(race, "fetch_text", fetch)
    return calls


@pytest.mark.parametrize("preferred,statuses", [(False, [400, 404]), (False, [404]), (True, [400, 400])])
def test_missing_result_reaches_verified_entry_card(current_routing, monkeypatch, preferred, statuses):
    monkeypatch.setattr(race, "prefer_mobile_endpoint", lambda _kind: preferred)
    calls = _fetcher(monkeypatch, statuses)
    payload = asyncio.run(race.scrape_race_full(None, RACE_ID, TARGET, force_refresh=True))
    assert payload is not None
    assert len(calls) == len(statuses) + 1
    assert calls[-1][0] == f"https://race.netkeiba.com/race/shutuba.html?race_id={RACE_ID}"
    assert calls[-1][1]["force_refresh"] is True
    assert payload["race_info"]["date"] == TARGET
    horse = payload["horses"][0]
    assert horse["finish_position"] is None and horse["finish_time"] is None
    assert horse["odds"] is None and horse["weight_kg"] is None
    assert horse["_shutuba"] is True
    quality = classify_race_quality(payload)
    assert quality.valid_for_storage
    assert quality.lifecycle == "entry" and not quality.valid_for_date_completion


def test_preferred_mobile_unparsed_success_reaches_entry_card(current_routing, monkeypatch):
    monkeypatch.setattr(race, "prefer_mobile_endpoint", lambda _kind: True)
    calls = _fetcher(monkeypatch, [200])
    payload = asyncio.run(race.scrape_race_full(None, RACE_ID, TARGET))
    assert payload is not None
    assert len(calls) == 2
    assert "db.sp.netkeiba.com" in calls[0][0]
    assert "/shutuba.html?" in calls[1][0]


def test_today_routes_directly_to_verified_entry_card(current_routing, monkeypatch):
    monkeypatch.setattr(race, "_prefer_entry_card_for_target", lambda *_args, **_kwargs: True)
    calls = _fetcher(monkeypatch, [])
    payload = asyncio.run(race.scrape_race_full(None, RACE_ID, TARGET, force_refresh=True))
    assert payload is not None
    assert len(calls) == 1
    assert calls[0][0] == f"https://race.netkeiba.com/race/shutuba.html?race_id={RACE_ID}"
    assert payload["race_info"]["date"] == TARGET


def test_entry_result_only_mode_does_not_fetch_horse_details(current_routing, monkeypatch):
    monkeypatch.setattr(race, "_prefer_entry_card_for_target", lambda *_args, **_kwargs: True)
    calls = _fetcher(monkeypatch, [])
    payload = asyncio.run(race.scrape_race_full(None, RACE_ID, TARGET, enrich_horse_details=False))
    assert len(calls) == 1
    assert current_routing == [("odds", RACE_ID)]
    assert payload["horses"][0]["finish_position"] is None
    assert payload["_source_evidence"]["source_date"] == TARGET
    assert payload["_source_evidence"]["body_sha256"]


@pytest.mark.parametrize(
    ("target", "allowed"),
    [("20260918", False), ("20260919", True), ("20260920", True), ("20261003", True)],
)
def test_direct_entry_route_is_never_used_for_a_past_race(target, allowed):
    assert race._prefer_entry_card_for_target(RACE_ID, target, today=TODAY) is allowed


def test_empty_success_reaches_entry_card_only_for_current_date(current_routing, monkeypatch):
    calls = _fetcher(monkeypatch, [200], result_html="")
    assert asyncio.run(race.scrape_race_full(None, RACE_ID, TARGET)) is not None
    assert len(calls) == 2


def test_current_horse_result_links_keep_actual_horse_id(current_routing, monkeypatch):
    _fetcher(monkeypatch, [404], card=CARD.replace("/horse/2023100001/", "/horse/result/2023100001/"))
    payload = asyncio.run(race.scrape_race_full(None, RACE_ID, TARGET))
    assert payload["horses"][0]["horse_id"] == "2023100001"
    assert ("horse", "2023100001") in current_routing


@pytest.mark.parametrize("date_hint", ["20260901", "20261020", "20260230", "", "2026091", "20250919"])
def test_new_failure_routes_do_not_expand_to_historical_or_unbound_dates(current_routing, monkeypatch, date_hint):
    calls = _fetcher(monkeypatch, [400, 404])
    assert asyncio.run(race.scrape_race_full(None, RACE_ID, date_hint)) is None
    assert len(calls) == 2
    assert not current_routing


def test_mobile_historical_unparsed_response_stays_unresolved(current_routing, monkeypatch):
    monkeypatch.setattr(race, "prefer_mobile_endpoint", lambda _kind: True)
    calls = _fetcher(monkeypatch, [200])
    assert asyncio.run(race.scrape_race_full(None, RACE_ID, "20260901")) is None
    assert len(calls) == 1


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_restrictions_and_server_errors_do_not_trigger_another_route(current_routing, monkeypatch, status):
    calls = _fetcher(monkeypatch, [status])
    assert asyncio.run(race.scrape_race_full(None, RACE_ID, TARGET)) is None
    assert len(calls) == 1
    assert not current_routing


def test_provider_access_pause_propagates_without_fallback(current_routing, monkeypatch):
    calls = []

    async def blocked(_session, url, **_kwargs):
        calls.append(url)
        raise FetchAccessBlocked("http-403")

    monkeypatch.setattr(race, "fetch_text", blocked)
    with pytest.raises(FetchAccessBlocked):
        asyncio.run(race.scrape_race_full(None, RACE_ID, TARGET))
    assert len(calls) == 1 and not current_routing


def test_challenge_html_does_not_trigger_entry_route(current_routing, monkeypatch):
    calls = _fetcher(monkeypatch, [200], result_html="<title>Just a moment</title>cf-chl-" + " " * 600)
    assert asyncio.run(race.scrape_race_full(None, RACE_ID, TARGET)) is None
    assert len(calls) == 1 and not current_routing


@pytest.mark.parametrize("card", [
    CARD.replace("2026年9月19日", "2026年9月20日"),
    CARD.replace("2026年9月19日", "2026年2月30日"),
    CARD.replace("2026年9月19日", "No date"),
    CARD.replace("race_id=202606040601", "race_id=202606040602"),
    CARD.replace("</head>", '<meta property="og:title" content="2026年9月20日"></head>'),
])
def test_wrong_or_unverifiable_card_identity_stops_before_enrichment(current_routing, monkeypatch, card):
    calls = _fetcher(monkeypatch, [404], card=card)
    assert asyncio.run(race.scrape_race_full(None, RACE_ID, TARGET)) is None
    assert len(calls) == 2 and not current_routing


def test_only_selected_date_navigation_can_identify_a_card():
    html = '''<div class="RaceList_DateList">
    <dd><a href="?kaisai_date=20260918">Yesterday</a></dd>
    <dd class="Active"><a href="?kaisai_date=20260919">Today</a></dd></div>'''
    assert race._verified_entry_card_date(BeautifulSoup(html, "lxml"), RACE_ID, TARGET) == TARGET
    assert race._verified_entry_card_date(BeautifulSoup(html.replace('class="Active"', ''), "lxml"), RACE_ID, TARGET) is None


def test_redirected_card_cannot_be_relabelled_as_requested_race(current_routing, monkeypatch):
    async def fetch(_session, url, **_kwargs):
        final_url = url.replace(RACE_ID, "202606040602")
        return FetchResult(final_url, url, 200, CARD.encode(), "network", 1), CARD

    monkeypatch.setattr(race, "fetch_text", fetch)
    assert asyncio.run(race._scrape_shutuba_fallback(None, RACE_ID, TARGET)) is None
    assert not current_routing


@pytest.mark.parametrize("target,allowed", [("20260918", True), ("20260919", True), ("20261003", True), ("20260917", False), ("20261004", False)])
def test_entry_route_window_is_bounded_in_japan_calendar(target, allowed):
    assert race._entry_card_date_allowed(RACE_ID, target, today=TODAY) is allowed
    assert not race._entry_card_date_allowed("202655040601", target, today=TODAY)
