"""The normal desktop/mobile ingress shares the strict clock-label rule."""
import asyncio

import pytest

from scraping import race
from scraping.fetch_pipeline import FetchResult
from scraping.mobile_race import parse_mobile_race

RID = "202605010101"


def html(clock_header="後3F", *, mobile=False, duplicate=False):
    table_class = "ResultsByRaceDetail" if mobile else "race_table_01"
    headers = ["着順", "枠番", "馬番", "馬名", "性齢", "斤量", "騎手", "タイム", "着差",
               "通過", "上り指数", "上がり指数", clock_header, "単勝", "人気", "馬体重", "調教師"]
    cells = ["1", "1", "1", '<a href="/horse/2020000001/">テスト</a>', "牡3", "57",
             '<a href="/jockey/00001/">騎手</a>', "1:34.5", "", "1-1", "**", "**", "35.1",
             "2.5", "1", "470(+2)", '<a href="/trainer/00001/">調教師</a>']
    if duplicate:
        headers.append("上がり3F")
        cells.append("36.2")
    return ('<html><head><title>テスト | 2026年1月4日</title></head><body>'
            '<h1>テスト</h1><div class="RaceHeader_Value"><h2>テスト</h2>'
            '<div class="RaceData">10:10発走 芝1600m(左) 晴 良</div></div>'
            '<p class="smalltxt">2026年1月4日 1回東京1日目</p>'
            '<div class="mainrace_data">芝左1600m 天候 晴 馬場 良 発走 10:10</div>'
            f'<table class="{table_class}"><tr>' + ''.join(f'<th>{h}</th>' for h in headers)
            + '</tr><tr>' + ''.join(f'<td>{cell}</td>' for cell in cells) + '</tr></table></body></html>')


async def no_details(*args, **kwargs):
    return {}


@pytest.mark.parametrize("header", ["上り", "上がり", "上り3F", "上がり3F", "後3F"])
def test_normal_desktop_reads_clock_not_paid_index(monkeypatch, header):
    source = html(header)

    async def fetch(session, url, **kwargs):
        return FetchResult(url, url, 200, source.encode(), "fixture", 1), source

    monkeypatch.setattr(race, "fetch_text", fetch)
    monkeypatch.setattr(race, "prefer_mobile_endpoint", lambda *_: False)
    monkeypatch.setattr(race, "is_cloudflare_block", lambda *_: False)
    payload = asyncio.run(race.scrape_race_full(None, RID, date_hint="20260104", quick_mode=True, enrich_horse_details=False))
    assert payload is not None
    assert payload["horses"][0]["last_3f"] == "35.1"


@pytest.mark.parametrize("header", ["上り", "上がり", "上り3F", "上がり3F", "後3F"])
def test_normal_mobile_reads_clock_not_paid_index(header):
    payload = asyncio.run(parse_mobile_race(None, RID, html(header, mobile=True), date_hint="20260104",
                                           quick_mode=True, horse_detail_fetcher=no_details))
    assert payload is not None
    assert payload["horses"][0]["last_3f"] == "35.1"


@pytest.mark.parametrize("mobile", [True, False])
def test_ambiguous_clock_columns_are_not_guessed(monkeypatch, mobile):
    source = html(mobile=mobile, duplicate=True)
    if mobile:
        payload = asyncio.run(parse_mobile_race(None, RID, source, date_hint="20260104", quick_mode=True,
                                               horse_detail_fetcher=no_details))
    else:
        async def fetch(session, url, **kwargs):
            return FetchResult(url, url, 200, source.encode(), "fixture", 1), source
        monkeypatch.setattr(race, "fetch_text", fetch)
        monkeypatch.setattr(race, "prefer_mobile_endpoint", lambda *_: False)
        monkeypatch.setattr(race, "is_cloudflare_block", lambda *_: False)
        payload = asyncio.run(race.scrape_race_full(None, RID, date_hint="20260104", quick_mode=True, enrich_horse_details=False))
    assert payload is not None
    assert payload["horses"][0]["last_3f"] == ""


def test_explicit_result_refresh_does_not_substitute_entry_card(monkeypatch):
    source = html()
    async def fetch(session, url, **kwargs):
        return FetchResult(url, url, 200, source.encode(), "fixture", 1), source
    async def entry(*args, **kwargs):
        raise AssertionError("required result must not take the entry-card path")
    monkeypatch.setattr(race, "_prefer_entry_card_for_target", lambda *_: True)
    monkeypatch.setattr(race, "_entry_card_date_allowed", lambda *_: True)
    monkeypatch.setattr(race, "_scrape_shutuba_fallback", entry)
    monkeypatch.setattr(race, "fetch_text", fetch)
    monkeypatch.setattr(race, "prefer_mobile_endpoint", lambda *_: False)
    monkeypatch.setattr(race, "is_cloudflare_block", lambda *_: False)
    payload = asyncio.run(race.scrape_race_full(None, RID, date_hint="20260104", enrich_horse_details=False, results_required=True))
    assert payload["horses"][0]["finish_position"] == 1
    assert not payload["race_info"].get("_shutuba")


def test_required_unpublished_result_remains_pending(monkeypatch):
    async def fetch(session, url, **kwargs):
        return FetchResult(url, url, 404, b"not available", "fixture", 1), "not available"
    async def entry(*args, **kwargs):
        raise AssertionError("an unavailable required result must stay unavailable")
    monkeypatch.setattr(race, "_prefer_entry_card_for_target", lambda *_: True)
    monkeypatch.setattr(race, "_entry_card_date_allowed", lambda *_: True)
    monkeypatch.setattr(race, "_scrape_shutuba_fallback", entry)
    monkeypatch.setattr(race, "fetch_text", fetch)
    monkeypatch.setattr(race, "prefer_mobile_endpoint", lambda *_: False)
    assert asyncio.run(race.scrape_race_full(None, RID, date_hint="20260104", enrich_horse_details=False, results_required=True)) is None


def test_required_http_200_empty_template_never_substitutes_entry_card(monkeypatch):
    source = '<html><head><title>レース結果 | 1970年1月1日</title></head><body></body></html>'
    requests = []

    async def fetch(session, url, **kwargs):
        requests.append(url)
        return FetchResult(url, url, 200, source.encode(), "fixture", 1), source

    async def entry(*args, **kwargs):
        raise AssertionError("HTTP 200 without a result table must remain pending")

    monkeypatch.setattr(race, "fetch_text", fetch)
    monkeypatch.setattr(race, "prefer_mobile_endpoint", lambda *_: False)
    monkeypatch.setattr(race, "_prefer_entry_card_for_target", lambda *_: False)
    monkeypatch.setattr(race, "_entry_card_date_allowed", lambda *_: True)
    monkeypatch.setattr(race, "_scrape_shutuba_fallback", entry)
    monkeypatch.setattr(race, "is_cloudflare_block", lambda *_: False)
    assert asyncio.run(race.scrape_race_full(None, RID, date_hint="20260104",
        enrich_horse_details=False, results_required=True)) is None
    assert len(requests) == 1


@pytest.mark.parametrize("surface,expected", [("芝", "芝"), ("ダ", "ダート")])
def test_modern_public_result_uses_explicit_header_conditions(monkeypatch, surface, expected):
    source = html().replace('class="race_table_01"', 'class="RaceTable01"')
    start = source.index('<div class="RaceHeader_Value">')
    end = source.index('<table class="RaceTable01">')
    source = (source[:start] + '<div class="RaceData01">09:45発走 / '
              + surface + '1600m (左 外 A) / 天候:晴 / 馬場:良</div>'
              '<div class="RaceData02">1回 東京 1日目 サラ系３歳 未勝利 1頭</div>' + source[end:])

    async def fetch(session, url, **kwargs):
        return FetchResult(url, url, 200, source.encode(), "fixture", 1), source

    monkeypatch.setattr(race, "fetch_text", fetch)
    monkeypatch.setattr(race, "prefer_mobile_endpoint", lambda *_: False)
    monkeypatch.setattr(race, "is_cloudflare_block", lambda *_: False)
    payload = asyncio.run(race.scrape_race_full(None, RID, date_hint="",
        enrich_horse_details=False, results_required=True))
    info = payload["race_info"]
    assert info["distance"] == 1600
    assert info["track_type"] == expected
    assert info["field_condition"] == "良"
    assert info["weather"] == "晴"
    assert info["post_time"] == "09:45"
    assert info["date"] == "20260104"
    assert payload["horses"][0]["last_3f"] == "35.1"
