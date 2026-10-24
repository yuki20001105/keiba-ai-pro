"""Lossless result-header parsing; fixture-only, no real acquisition."""
from __future__ import annotations

import asyncio

import pytest

from scraping import race
from scraping.fetch_pipeline import FetchResult


@pytest.fixture
def parse_header(monkeypatch):
    monkeypatch.setattr(race, "_entry_card_date_allowed", lambda *_args: False)
    monkeypatch.setattr(race, "_prefer_entry_card_for_target", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(race, "prefer_mobile_endpoint", lambda *_args: False)
    monkeypatch.setattr(race, "remember_mobile_endpoint", lambda *_args, **_kwargs: None)

    async def no_detail(*_args, **_kwargs):
        raise AssertionError("result-only fixture must not enrich horses")

    monkeypatch.setattr(race, "scrape_horse_detail", no_detail)

    def parse(course):
        # Observed 201804030101 source header: 障芝 外-内2850m.
        # Synthetic identities and results keep this regression self-contained.
        html = f"""<html><head><title>Race | 2018年8月11日</title></head><body>
        <h1>Fixture</h1><div class="mainrace_data">{course} / 天候 : 晴 / 芝 : 良 / 発走10:00</div>
        <p class="smalltxt">2018年8月11日 2回新潟5日目</p>
        <table class="race_table_01"><tr><th>着順</th><th>枠番</th><th>馬番</th><th>馬名</th>
        <th>性齢</th><th>斤量</th><th>騎手</th><th>タイム</th><th>単勝</th><th>人気</th><th>馬体重</th></tr>
        <tr><td>1</td><td>1</td><td>1</td><td><a href="/horse/2014000001/">Fixture Horse</a></td>
        <td>牡4</td><td>60</td><td>Fixture Jockey</td><td>3:04.0</td><td>3.0</td><td>1</td><td>480(+2)</td>
        </tr></table></body></html>"""

        async def fetch(_session, url, **_kwargs):
            return FetchResult(url, url, 200, html.encode(), "network", 1), html

        monkeypatch.setattr(race, "fetch_text", fetch)
        payload = asyncio.run(race.scrape_race_full(None, "201804030101", "20180811", enrich_horse_details=False))
        return payload["race_info"]

    return parse


@pytest.mark.parametrize("header,distance", [
    ("障芝 外-内2850m", 2850), ("障芝 外-内 2850m", 2850),
    ("障芝3000m", 3000), ("障害3200m", 3200), ("障3200m", 3200),
    ("障ダート右2930m", 2930), ("障ダ左3000m", 3000), ("障害芝右外3300ｍ", 3300),
    ("障芝 ダート3170m", 3170), ("障芝 ダート2910m", 2910), ("障芝 ダート3000m", 3000),
])
def test_explicit_jump_surface_and_course_notation_stays_obstacle(parse_header, header, distance):
    info = parse_header(header)
    assert info["track_type"] == "障害" and info["distance"] == distance
    assert not info.get("_invalid_distance")


@pytest.mark.parametrize("header,surface,distance", [
    ("芝右1200m", "芝", 1200), ("芝右 外1600m", "芝", 1600),
    ("ダート左1800m", "ダート", 1800), ("ダ右1400m", "ダート", 1400),
])
def test_preexisting_flat_notation_is_unchanged(parse_header, header, surface, distance):
    info = parse_header(header)
    assert (info["track_type"], info["distance"]) == (surface, distance)


@pytest.mark.parametrize("header", ["障芝 外-内Xm", "障芝 未知2850m", "障芝 未知 2850m",
                                   "障芝 外→内2850m", "障芝 外→内 2850m", "障芝 ダー未知 3000m",
                                   "障芝 ダート 未知 3000m", "未詳2850m"])
def test_unknown_or_absent_jump_distance_is_not_guessed(parse_header, header):
    info = parse_header(header)
    assert info["distance"] == 0 and info.get("_invalid_distance") is True
