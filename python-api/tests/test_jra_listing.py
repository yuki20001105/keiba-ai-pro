"""Zero-network checks for central-only monthly list reuse and fallback safety."""
import asyncio
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest

from scraping.fetch_pipeline import FetchAccessBlocked
from scraping.jra_listing import fetch_jra_race_ids
from scraping import race_list


@pytest.fixture
def old_date(monkeypatch):
    monkeypatch.setattr(race_list, "_recent_or_future", lambda _: False)


def test_historical_month_resolution_skips_desktop_calls(monkeypatch, old_date):
    month = AsyncMock(return_value=["201605010101", "201605010102"])
    desktop = AsyncMock(side_effect=AssertionError("unnecessary desktop HTTP"))
    monkeypatch.setattr(race_list, "_fetch_mobile_month_race_ids", month)
    monkeypatch.setattr(race_list, "fetch_race_ids", desktop)
    ids, source = asyncio.run(fetch_jra_race_ids("20160105"))
    assert ids == ["201605010101", "201605010102"]
    assert source == "db.sp.netkeiba.com/month-search"
    assert month.await_count == 1
    desktop.assert_not_awaited()


def test_verified_empty_month_is_distinct_from_failed_listing(monkeypatch, old_date):
    monkeypatch.setattr(race_list, "_fetch_mobile_month_race_ids", AsyncMock(return_value=[]))
    ids, source = asyncio.run(fetch_jra_race_ids("20160105"))
    assert ids == []
    assert source.endswith(":verified-empty")


@pytest.mark.parametrize("bad_id", ["201644010101", "201705010101", "201605010199", "invalid"])
def test_mixed_or_invalid_month_cache_does_not_become_jra_evidence(monkeypatch, old_date, bad_id):
    monkeypatch.setattr(race_list, "_fetch_mobile_month_race_ids", AsyncMock(return_value=[bad_id]))
    with pytest.raises(race_list.RaceListUnavailable, match="jra_scope_unconfirmed"):
        asyncio.run(fetch_jra_race_ids("20160105"))


@pytest.mark.parametrize("code", [401, 403, 429])
def test_provider_restriction_never_falls_back(monkeypatch, old_date, code):
    monkeypatch.setattr(race_list, "_fetch_mobile_month_race_ids", AsyncMock(side_effect=FetchAccessBlocked(f"http-{code}")))
    desktop = AsyncMock(side_effect=AssertionError("fallback after block"))
    monkeypatch.setattr(race_list, "fetch_race_ids", desktop)
    with pytest.raises(FetchAccessBlocked):
        asyncio.run(fetch_jra_race_ids("20160105"))
    desktop.assert_not_awaited()


def test_recoverable_month_failure_filters_local_races(monkeypatch, old_date):
    monkeypatch.setattr(race_list, "_fetch_mobile_month_race_ids", AsyncMock(side_effect=RuntimeError("parser unavailable")))
    monkeypatch.setattr(race_list, "fetch_race_ids", AsyncMock(return_value=(["201644010101", "201605010101"], "db.netkeiba.com")))
    assert asyncio.run(fetch_jra_race_ids("20160105")) == (["201605010101"], "db.netkeiba.com")


def test_local_only_fallback_is_not_proof_of_no_central_racing(monkeypatch, old_date):
    monkeypatch.setattr(race_list, "_fetch_mobile_month_race_ids", AsyncMock(side_effect=RuntimeError("parser unavailable")))
    monkeypatch.setattr(race_list, "fetch_race_ids", AsyncMock(return_value=(["201644010101"], "db.netkeiba.com")))
    with pytest.raises(race_list.RaceListUnavailable):
        asyncio.run(fetch_jra_race_ids("20160105"))


def test_recent_card_does_not_use_historical_results(monkeypatch):
    monkeypatch.setattr(race_list, "_recent_or_future", lambda _: True)
    month = AsyncMock(side_effect=AssertionError("historical results for recent card"))
    monkeypatch.setattr(race_list, "_fetch_mobile_month_race_ids", month)
    monkeypatch.setattr(race_list, "fetch_race_ids", AsyncMock(return_value=(["202605010101"], "race.netkeiba.com")))
    assert asyncio.run(fetch_jra_race_ids("20261012")) == (["202605010101"], "race.netkeiba.com")
    month.assert_not_awaited()


def test_two_historical_days_share_one_fully_paginated_month(monkeypatch, old_date):
    parsed = {}
    monkeypatch.setattr(race_list, "get_parsed", lambda _kind, key, _version: parsed.get(key))
    monkeypatch.setattr(race_list, "put_parsed", lambda _kind, key, _version, value, _sources, **_: parsed.update({key: value}))
    html = ('<a href="/race/201606010101/">2016/01/05 中山1R</a>'
            '<a href="/race/201606010201/">2016/01/09 中山1R</a>')
    page = AsyncMock(return_value=(SimpleNamespace(status=200, cache_snapshot={
        "url": race_list.build_mobile_month_url("20160105"),
        "fetched_at": 1, "expires_at": 2,
    }), html))
    monkeypatch.setattr(race_list, "_fetch_list_page", page)
    desktop = AsyncMock(side_effect=AssertionError("unnecessary per-day list call"))
    monkeypatch.setattr(race_list, "fetch_race_ids", desktop)

    async def two_days():
        return [await fetch_jra_race_ids(value) for value in ("20160105", "20160109")]

    results = asyncio.run(two_days())
    assert [value[0] for value in results] == [["201606010101"], ["201606010201"]]
    assert page.await_count == 1
    desktop.assert_not_awaited()


def test_missing_advertised_page_does_not_cache_partial_month(monkeypatch, old_date):
    parsed = {}
    monkeypatch.setattr(race_list, "get_parsed", lambda _kind, key, _version: parsed.get(key))
    monkeypatch.setattr(race_list, "put_parsed", lambda _kind, key, _version, value, _sources, **_: parsed.update({key: value}))
    html = ('<a href="/race/201606010101/">2016/01/05 中山1R</a>'
            '<a href="?pid=race_list&page=2">2</a>')
    page = AsyncMock(side_effect=[(SimpleNamespace(status=200), html), (SimpleNamespace(status=503), "unavailable")])
    monkeypatch.setattr(race_list, "_fetch_list_page", page)
    monkeypatch.setattr(race_list, "fetch_race_ids", AsyncMock(side_effect=race_list.RaceListUnavailable("20160105", [])))
    with pytest.raises(race_list.RaceListUnavailable):
        asyncio.run(fetch_jra_race_ids("20160105"))
    assert parsed == {}
