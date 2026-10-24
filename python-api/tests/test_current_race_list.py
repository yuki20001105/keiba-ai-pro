"""Current-card contract fixtures; these are not captured live-site HTML.

All requests and cache access are mocked. Live HTTP 200 compatibility must be
verified separately; an unknown real layout remains explicitly unconfirmed.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

import pytest

from scraping import race_list
from scraping.fetch_pipeline import FetchAccessBlocked, FetchResult


DAY = "20260919"
RACE = "202606040501"
MAIN = f"https://race.netkeiba.com/top/race_list.html?kaisai_date={DAY}"
SUB = f"https://race.netkeiba.com/top/race_list_sub.html?kaisai_date={DAY}"
MOBILE = f"https://race.sp.netkeiba.com/?kaisai_date={DAY}&pid=race_list"
ORIGINAL_JST_TODAY = race_list._jst_today


def card(race_id=RACE):
    return f'<li class="RaceList_DataItem"><a href="/race/shutuba.html?race_id={race_id}">1R</a></li>'


def page(day=DAY, *, content=None):
    return f'<h1>{day[:4]}年{int(day[4:6])}月{int(day[6:8])}日</h1>' + (content if content is not None else card())


def response(url, status=200, html="", *, final_url=None, source="network"):
    return FetchResult(final_url or url, url, status, html.encode(), source, 1), html


@pytest.fixture(autouse=True)
def isolated_sources(monkeypatch):
    monkeypatch.setattr(race_list, "_jst_today", lambda: date(2026, 9, 19))
    monkeypatch.setattr(race_list, "source_metadata", lambda _url: None)
    monkeypatch.setattr(race_list, "_cached_month_ids", lambda _day: None)
    monkeypatch.setattr(race_list, "put_parsed", lambda *_args, **_kwargs: None)
    async def forbidden(*_args, **_kwargs):
        pytest.fail("Every transport must be explicitly mocked")
    monkeypatch.setattr(race_list, "fetch_text", forbidden)


def test_current_page_is_checked_before_negative_month_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(race_list, "_cached_month_ids", lambda _day: [])
    async def fetch(_session, url, **kwargs):
        calls.append((url, kwargs))
        return response(url, html=page())
    monkeypatch.setattr(race_list, "fetch_text", fetch)
    ids, source = asyncio.run(race_list.fetch_race_ids(DAY))
    assert ids == [RACE]
    assert source == "race.netkeiba.com"
    assert [url for url, _ in calls] == [MAIN]
    assert calls[0][1]["cache_ttl_sec"] == 60


def test_current_page_is_checked_before_positive_month_cache(monkeypatch):
    monkeypatch.setattr(race_list, "_cached_month_ids", lambda _day: ["202606040401"])
    async def fetch(_session, url, **_kwargs):
        return response(url, html=page())
    monkeypatch.setattr(race_list, "fetch_text", fetch)
    assert asyncio.run(race_list.fetch_race_ids(DAY))[0] == [RACE]


def test_unknown_main_shell_uses_date_bound_subfragment(monkeypatch):
    calls = []
    async def fetch(_session, url, **_kwargs):
        calls.append(url)
        return response(url, html='<div id="js-app"></div>' if url == MAIN else card())
    monkeypatch.setattr(race_list, "fetch_text", fetch)
    assert asyncio.run(race_list.fetch_race_ids(DAY))[0] == [RACE]
    assert calls == [MAIN, SUB]


def test_official_mobile_route_requires_date_proof(monkeypatch):
    calls = []
    async def fetch(_session, url, **_kwargs):
        calls.append(url)
        return response(url, html=page()) if url == MOBILE else response(url, 400)
    monkeypatch.setattr(race_list, "fetch_text", fetch)
    ids, source = asyncio.run(race_list.fetch_race_ids(DAY))
    assert ids == [RACE] and source == "race.sp.netkeiba.com"
    assert calls == [MAIN, SUB, MOBILE]


@pytest.mark.parametrize("url,allow_fragment", [(MAIN, False), (MOBILE, False), (SUB, True)])
def test_mismatched_selected_date_never_accepts_cards(url, allow_fragment):
    ids, reason = race_list.extract_current_card_ids(
        page("20260913"), DAY, requested_url=url, final_url=url,
        allow_date_bound_fragment=allow_fragment,
    )
    assert ids == [] and reason == "selected_date_mismatch"


def test_navigation_link_is_not_selected_date_proof():
    html = f'<a href="?kaisai_date={DAY}">today</a>' + card()
    assert race_list.extract_current_card_ids(html, DAY, requested_url=MAIN, final_url=MAIN) == (
        [], "selected_date_unconfirmed",
    )


@pytest.mark.parametrize("proof", [
    f'<input type="hidden" name="kaisai_date" value="{DAY}">',
    f'<li class="Active"><a href="?kaisai_date={DAY}">today</a></li>',
    f'<a aria-current="date" href="?kaisai_date={DAY}">today</a>',
])
def test_selected_date_proof_is_accepted(proof):
    assert race_list.extract_current_card_ids(proof + card(), DAY, requested_url=MAIN, final_url=MAIN)[0] == [RACE]


@pytest.mark.parametrize("final_url", [
    SUB.replace(DAY, "20260913"), SUB.replace("netkeiba.com", "example.test"),
    MAIN, "http://[",
])
def test_subfragment_requires_exact_final_endpoint_and_date(final_url):
    assert race_list.extract_current_card_ids(
        card(), DAY, requested_url=SUB, final_url=final_url, allow_date_bound_fragment=True,
    )[0] == []


def test_non_card_sidebar_links_are_never_used():
    html = page(content=card() + '<aside><a href="/race/shutuba.html?race_id=202606040512">other</a></aside>')
    assert race_list.extract_current_card_ids(html, DAY, requested_url=MAIN, final_url=MAIN)[0] == [RACE]


@pytest.mark.parametrize("content", [
    '<a href="/race/shutuba.html?race_id=202606040501">unscoped</a>',
    card("202506040501"), card("202666040501"), card("202606040599"),
    '<li class="RaceList_DataItem"><a href="http://[">bad</a></li>',
    '<li class="RaceList_DataItem"><a href="/race/2026060405019/">bad</a></li>',
    '<li class="RaceList_DataItem"><a href="/race/202606040501extra">bad</a></li>',
    '<li class="RaceList_DataItem"><a href="?race_id=202606040501">one</a><a href="?race_id=202606040502">two</a></li>',
])
def test_invalid_or_ambiguous_card_list_fails_closed(content):
    assert race_list.extract_current_card_ids(page(content=content), DAY, requested_url=MAIN, final_url=MAIN)[0] == []


def test_recent_missing_month_date_raises_structured_unconfirmed(monkeypatch):
    calls = []
    monkeypatch.setattr(race_list, "_cached_month_ids", lambda _day: [])
    async def fetch(_session, url, **_kwargs):
        calls.append(url)
        return response(url, 400)
    monkeypatch.setattr(race_list, "fetch_text", fetch)
    with pytest.raises(race_list.RaceListUnavailable) as failure:
        asyncio.run(race_list.fetch_race_ids(DAY))
    assert failure.value.reason == "current_race_list_unconfirmed"
    assert failure.value.as_dict()["date_str"] == DAY
    assert len(failure.value.attempts) == 3
    assert all(attempt["status"] == 400 for attempt in failure.value.attempts)
    assert calls == [MAIN, SUB, MOBILE]


@pytest.mark.parametrize("day", [DAY, "20260920", "20270101"])
def test_today_and_future_never_promote_partial_historical_results(monkeypatch, day):
    calls = []
    async def no_month(*_args, **_kwargs):
        pytest.fail("Historical results cannot prove the entire current/future card")
    monkeypatch.setattr(race_list, "_fetch_mobile_month_race_ids", no_month)
    monkeypatch.setattr(race_list, "_cached_month_ids", lambda _day: [RACE])
    async def fetch(_session, url, **_kwargs):
        calls.append(url)
        return response(url, 400)
    monkeypatch.setattr(race_list, "fetch_text", fetch)
    with pytest.raises(race_list.RaceListUnavailable):
        asyncio.run(race_list.fetch_race_ids(day))
    assert len(calls) == 3
    assert all("db.sp.netkeiba.com" not in url for url in calls)


def test_recent_past_positive_historical_result_remains_available_after_current_routes(monkeypatch):
    calls = []
    monkeypatch.setattr(race_list, "_cached_month_ids", lambda _day: [RACE])
    async def fetch(_session, url, **_kwargs):
        calls.append(url)
        return response(url, 400)
    monkeypatch.setattr(race_list, "fetch_text", fetch)
    assert asyncio.run(race_list.fetch_race_ids("20260918")) == ([RACE], "db.sp.netkeiba.com/month-search")
    assert calls == [url.replace(DAY, "20260918") for url in [MAIN, SUB, MOBILE]]


def test_recent_past_missing_month_date_stays_unconfirmed(monkeypatch):
    monkeypatch.setattr(race_list, "_cached_month_ids", lambda _day: [])
    async def fetch(_session, url, **_kwargs):
        return response(url, 400)
    monkeypatch.setattr(race_list, "fetch_text", fetch)
    with pytest.raises(race_list.RaceListUnavailable) as failure:
        asyncio.run(race_list.fetch_race_ids("20260918"))
    assert failure.value.attempts[-1]["reason"] == "target_absent_from_historical_results"


@pytest.mark.parametrize("status", [401, 403, 429])
def test_access_restrictions_stop_all_fallbacks(monkeypatch, status):
    calls = []
    async def fetch(_session, url, **_kwargs):
        calls.append(url)
        return response(url, status)
    monkeypatch.setattr(race_list, "fetch_text", fetch)
    with pytest.raises(FetchAccessBlocked):
        asyncio.run(race_list.fetch_race_ids(DAY))
    assert calls == [MAIN]


def test_access_block_exception_is_not_swallowed(monkeypatch):
    async def fetch(*_args, **_kwargs):
        raise FetchAccessBlocked("provider_blocked")
    monkeypatch.setattr(race_list, "fetch_text", fetch)
    with pytest.raises(FetchAccessBlocked, match="provider_blocked"):
        asyncio.run(race_list.fetch_race_ids(DAY))


def test_month_fallback_does_not_swallow_access_block(monkeypatch):
    async def fetch(_session, url, **_kwargs):
        return response(url, 400)
    async def blocked(*_args, **_kwargs):
        raise FetchAccessBlocked("month_blocked")
    monkeypatch.setattr(race_list, "fetch_text", fetch)
    monkeypatch.setattr(race_list, "_fetch_mobile_month_race_ids", blocked)
    with pytest.raises(FetchAccessBlocked, match="month_blocked"):
        asyncio.run(race_list.fetch_race_ids("20260918"))


def test_short_ttl_bypasses_old_long_lived_body_and_resume(monkeypatch):
    now, calls = 2000.0, []
    monkeypatch.setattr(race_list.time, "time", lambda: now)
    monkeypatch.setattr(race_list, "source_metadata", lambda _url: {
        "url": MAIN, "fetched_at": now - 61, "expires_at": now + 43200,
    })
    async def fetch(_session, url, **kwargs):
        calls.append(kwargs)
        return response(url, html=page())
    monkeypatch.setattr(race_list, "fetch_text", fetch)
    asyncio.run(race_list.fetch_race_ids(DAY))
    assert calls[0]["force_refresh"] is True
    assert calls[0]["cache_ttl_sec"] == 60


def test_fresh_source_can_reuse_body_but_explicit_refresh_cannot(monkeypatch):
    now, calls = 2000.0, []
    monkeypatch.setattr(race_list.time, "time", lambda: now)
    monkeypatch.setattr(race_list, "source_metadata", lambda _url: {
        "url": MAIN, "fetched_at": now - 1, "expires_at": now + 43200,
    })
    async def fetch(_session, url, **kwargs):
        calls.append(kwargs)
        return response(url, html=page())
    monkeypatch.setattr(race_list, "fetch_text", fetch)
    asyncio.run(race_list.fetch_race_ids(DAY))
    asyncio.run(race_list.fetch_race_ids(DAY, force_refresh=True))
    assert [call["force_refresh"] for call in calls] == [False, True]


@pytest.mark.parametrize("source", ["cache", "resume-cache"])
def test_fresh_cached_empty_is_refetched_once_before_fallback(monkeypatch, source):
    calls = []
    monkeypatch.setattr(race_list, "source_metadata", lambda _url: {
        "fetched_at": race_list.time.time() - 1,
    })
    async def fetch(_session, url, **kwargs):
        calls.append((url, kwargs["force_refresh"]))
        if len(calls) == 1:
            return response(url, html=page(content=""), source=source)
        return response(url, html=page())
    monkeypatch.setattr(race_list, "fetch_text", fetch)
    assert asyncio.run(race_list.fetch_race_ids(DAY))[0] == [RACE]
    assert calls == [(MAIN, False), (MAIN, True)]


def test_fresh_network_empty_is_not_refetched(monkeypatch):
    calls = []
    async def fetch(_session, url, **_kwargs):
        calls.append(url)
        return response(url, html=page(content=""))
    monkeypatch.setattr(race_list, "fetch_text", fetch)
    with pytest.raises(race_list.RaceListUnavailable):
        asyncio.run(race_list.fetch_race_ids(DAY))
    assert calls == [MAIN, SUB, MOBILE]


def test_unusable_cache_retry_is_bounded_even_if_transport_returns_cache_again(monkeypatch):
    calls = []
    async def fetch(_session, url, **kwargs):
        calls.append((url, kwargs["force_refresh"]))
        return response(url, html=page(content=""), source="cache")
    monkeypatch.setattr(race_list, "fetch_text", fetch)
    with pytest.raises(race_list.RaceListUnavailable):
        asyncio.run(race_list.fetch_race_ids(DAY))
    assert [url for url, _ in calls] == [MAIN, MAIN, SUB, SUB, MOBILE, MOBILE]
    assert all(calls[index][1] is True for index in [1, 3, 5])


def test_forced_retry_access_restriction_stops_other_routes(monkeypatch):
    calls = []
    async def fetch(_session, url, **_kwargs):
        calls.append(url)
        return response(url, html="", source="cache") if len(calls) == 1 else response(url, 429)
    monkeypatch.setattr(race_list, "fetch_text", fetch)
    with pytest.raises(FetchAccessBlocked):
        asyncio.run(race_list.fetch_race_ids(DAY))
    assert calls == [MAIN, MAIN]


def test_force_refresh_bypasses_month_index_and_every_page(monkeypatch):
    calls = []
    monkeypatch.setattr(race_list, "_cached_month_ids", lambda _day: ["202506010199"])
    async def fetch(_session, url, **kwargs):
        calls.append((url, kwargs))
        if "db.sp.netkeiba.com" not in url:
            return response(url, 400)
        return response(url, html='<a href="/race/202506010101/">2025/01/05</a>')
    monkeypatch.setattr(race_list, "fetch_text", fetch)
    ids, _ = asyncio.run(race_list.fetch_race_ids("20250105", force_refresh=True))
    assert ids == ["202506010101"]
    assert all(kwargs["force_refresh"] for _, kwargs in calls)


def test_historical_month_cache_and_verified_empty_remain_unchanged(monkeypatch):
    monkeypatch.setattr(race_list, "_cached_month_ids", lambda _day: [])
    assert asyncio.run(race_list.fetch_race_ids("20250105")) == (
        [], "db.sp.netkeiba.com/month-search:verified-empty",
    )


@pytest.mark.parametrize("suffix", ["9/", "text/", "text"])
def test_historical_fallback_never_truncates_malformed_race_ids(suffix):
    html = f'<a href="/race/{RACE}{suffix}">2026/09/18</a>'
    assert race_list.extract_mobile_race_entries(html) == []


def test_recent_boundary_matches_fourteen_day_safety_window():
    assert race_list._recent_or_future(date(2026, 9, 5)) is True
    assert race_list._recent_or_future(date(2026, 9, 4)) is False
    assert race_list._recent_or_future(date(2027, 1, 1)) is True


def test_today_is_jst_even_when_utc_still_previous_day(monkeypatch):
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 9, 18, 15, 1, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(race_list, "datetime", Clock)
    assert ORIGINAL_JST_TODAY() == date(2026, 9, 19)
