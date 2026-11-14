"""Synthetic markup shaped like local caches; no copied publisher fixtures or HTTP."""
from __future__ import annotations

import asyncio

import pytest
from bs4 import BeautifulSoup


def _legacy(sectional="12.0 - 11.0 - 12.0", cumulative="12.0 - 23.0 - 35.0 (35.0-35.0)"):
    return f"""<table class="result_table_02" summary="ラップタイム">
    <caption>ラップタイム</caption>
    <tr><th>ラップ</th><td class="race_lap_cell">{sectional}</td></tr>
    <tr><th>ペース</th><td class="race_lap_cell">{cumulative}</td></tr></table>"""


def _parse(html, distance=600):
    from scraping.lap_times import parse_lap_times

    return parse_lap_times(BeautifulSoup(html, "lxml"), race_distance=distance)


def test_scraper_saves_explicit_sectionals_without_mistaking_them_for_cumulative(monkeypatch):
    from scraping import race
    from scraping.fetch_pipeline import FetchResult

    html = """<h1>Test race</h1>
    <div class="mainrace_data">芝右600m / 天候 : 晴 / 芝 : 良 / 発走10:00</div>
    <table class="race_table_01"><tr><th>着順</th><th>枠番</th><th>馬番</th>
    <th>馬名</th><th>性齢</th><th>斤量</th><th>騎手</th><th>タイム</th></tr>
    <tr><td>1</td><td>1</td><td>1</td><td>Test horse</td><td>牡3</td>
    <td>57</td><td>Test jockey</td><td>0:35.0</td></tr></table>""" + _legacy()

    async def fetch(_session, url, **_kwargs):
        return FetchResult(url, url, 200, html.encode(), "cache", 0), html

    monkeypatch.setattr(race, "fetch_text", fetch)
    monkeypatch.setattr(race, "prefer_mobile_endpoint", lambda *_args: False)
    monkeypatch.setattr(race, "_entry_card_date_allowed", lambda *_args: False)
    monkeypatch.setattr(race, "_prefer_entry_card_for_target", lambda *_args, **_kwargs: False)
    actual = asyncio.run(race.scrape_race_full(None, "202601010101", "20260101",
                                             enrich_horse_details=False))
    assert actual["race_info"]["lap_sectional"] == {200: 12.0, 400: 11.0, 600: 12.0}
    assert actual["race_info"]["lap_cumulative"] == {200: 12.0, 400: 23.0, 600: 35.0}
    assert actual["race_info"]["_lap_evidence"]["finish_time_check"] == "matched"


def test_legacy_row_semantics_and_pace_summary_are_distinct():
    actual = _parse(_legacy())
    assert actual["lap_sectional"] == {200: 12.0, 400: 11.0, 600: 12.0}
    assert actual["lap_cumulative"] == {200: 12.0, 400: 23.0, 600: 35.0}
    assert actual["lap_evidence"]["sectional_source"] == "ラップ"
    assert actual["lap_evidence"]["cumulative_source"] == "ペース"


@pytest.mark.parametrize("distance,sectional,cumulative,keys", [
    (700, "7.0 - 12.0 - 11.0 - 12.0", "7.0 - 19.0 - 30.0 - 42.0", [100, 300, 500, 700]),
    (650, "4.0 - 12.0 - 11.0 - 12.0", "4.0 - 16.0 - 27.0 - 39.0", [50, 250, 450, 650]),
    (820, "2.4 - 11.8 - 12.0 - 12.0 - 12.0", "2.4 - 14.2 - 26.2 - 38.2 - 50.2",
     [20, 220, 420, 620, 820]),
])
def test_short_initial_segments_use_cumulative_distance_keys(distance, sectional, cumulative, keys):
    actual = _parse(_legacy(sectional, cumulative), distance)
    assert list(actual["lap_sectional"]) == keys
    assert actual["lap_sectional"][keys[0]] == float(sectional.split(" - ")[0])
    assert actual["lap_cumulative"][keys[-1]] == float(cumulative.split(" - ")[-1])


def test_labeled_distance_columns_align_th_and_td_without_offset():
    actual = _parse("""<table class="Race_HaronTime"><tr><th>距離</th>
    <th>100m</th><th>300m</th><th>500m</th><th>700m</th></tr>
    <tr><th>累計</th><td>7.0</td><td>19.0</td><td>30.0</td><td>42.0</td></tr>
    <tr><td>ラップ</td><td>7.0</td><td>12.0</td><td>11.0</td><td>12.0</td></tr>
    </table>""", 700)
    assert actual["lap_sectional"] == {100: 7.0, 300: 12.0, 500: 11.0, 700: 12.0}


@pytest.mark.parametrize("label,values", [
    ("ラップ", "12.0 - 11.0 - 12.0"),
    ("累計", "12.0 - 23.0 - 35.0"),
])
def test_single_explicit_semantics_can_derive_the_other_representation(label, values):
    actual = _parse(f'<table><caption>ラップタイム</caption><tr><th>{label}</th><td>{values}</td></tr></table>')
    assert actual["lap_sectional"] == {200: 12.0, 400: 11.0, 600: 12.0}
    assert actual["lap_cumulative"] == {200: 12.0, 400: 23.0, 600: 35.0}


def test_distance_can_come_from_unambiguous_race_metadata():
    actual = _parse('<div class="RaceData01">芝右600m / 天候:晴</div>' + _legacy(), None)
    assert actual["lap_cumulative"][600] == 35.0


@pytest.mark.parametrize("html,distance", [
    (_legacy(), None),
    (_legacy(), 800),
    (_legacy("12 - 11 - 12", "12 - 24 - 35"), 600),
    (_legacy("12 - NaN - 12"), 600),
    (_legacy("12 - inf - 12"), 600),
    (_legacy("12 - 0 - 12"), 600),
    (_legacy("12 - -1 - 12"), 600),
    (_legacy("12 - 1 - 12"), 600),
    (_legacy("12 - 11 - 12", "12 - 11 - 35"), 600),
    (_legacy("12 - 11 - 12", "12 - 23 - 35 extra"), 600),
    (_legacy("12 - 11 - 12", "---"), 600),
    ('<table><tr><th>200</th><th>400</th><th>600</th></tr>'
     '<tr><td>12</td><td>23</td><td>35</td></tr></table>', 600),
    ('<table summary="ラップタイム"><tr><th>200</th><th>400</th><th>600</th></tr>'
     '<tr><td>12</td><td>23</td><td>35</td></tr></table>', 600),
    ('<table><tr><th>ラップ</th><td>12 - 11 - 12</td></tr></table>', 600),
    ('<div class="RaceData01">芝800m</div>' + _legacy(), 600),
    ('<div class="RaceData01">障600m</div>' + _legacy(), 600),
    ('<table summary="ラップタイム"><tr><th>距離</th><th>200m</th><th>400m</th><th>800m</th></tr>'
     '<tr><th>累計</th><td>12</td><td>23</td><td>35</td></tr></table>', 800),
])
def test_unknown_incomplete_unrelated_or_inconsistent_data_fails_closed(html, distance):
    actual = _parse(html, distance)
    assert actual["lap_sectional"] == {}
    assert actual["lap_cumulative"] == {}
    assert actual["lap_evidence"] is None


def test_available_finish_time_is_checked_against_total_not_last_sectional():
    result = """<table class="RaceTable01"><tr><th>着順</th><th>タイム</th></tr>
    <tr><td>1</td><td>0:35.0</td></tr><tr><td>2</td><td>0:35.2</td></tr></table>"""
    assert _parse(result + _legacy())["lap_evidence"]["finish_time_check"] == "matched"
    assert _parse(result.replace("0:35.0", "0:38.0").replace("0:35.2", "0:38.2") + _legacy())["lap_sectional"] == {}


def test_conflicting_recognized_lap_tables_do_not_choose_first():
    assert _parse(_legacy() + _legacy("13 - 11 - 12", "13 - 24 - 36"))["lap_sectional"] == {}


def test_duplicate_semantic_rows_and_distance_columns_are_rejected():
    duplicate = _legacy().replace("</table>", "<tr><th>ラップ</th><td>13 - 11 - 12</td></tr></table>")
    assert _parse(duplicate)["lap_sectional"] == {}
    assert _parse("""<table summary="ラップタイム"><tr><th>距離</th>
    <th>200m</th><th>200m</th><th>600m</th></tr>
    <tr><th>累計</th><td>12</td><td>23</td><td>35</td></tr></table>""")["lap_sectional"] == {}


def test_merged_distance_headers_do_not_claim_unambiguous_segment_alignment():
    assert _parse("""<table summary="ラップタイム"><tr><th>距離</th>
    <th colspan="2">200m</th><th>400m</th><th>600m</th></tr>
    <tr><th>累計</th><td>12</td><td>23</td><td>35</td></tr></table>""")["lap_sectional"] == {}


def test_source_pace_summary_must_not_override_inconsistent_cumulative_times():
    assert _parse(_legacy("12 - 11 - 12", "12 - 23 - 40 (35.0-35.0)"))["lap_sectional"] == {}


def test_missing_initial_short_segment_is_not_treated_as_a_longer_first_segment():
    assert _parse(_legacy("11.8 - 12.0 - 12.0 - 12.0", "14.2 - 26.2 - 38.2 - 50.2"),
                  820)["lap_sectional"] == {}


def test_long_race_cumulative_times_are_not_subject_to_a_200_second_cap():
    html = """<table summary="ラップタイム"><tr><th>累計</th>
    <td>40.0 - 80.0 - 120.0 - 160.0 - 3:20.0 - 4:00.0</td></tr></table>"""
    actual = _parse(html, 1200)
    assert actual["lap_sectional"] == {200: 40.0, 400: 40.0, 600: 40.0,
                                      800: 40.0, 1000: 40.0, 1200: 40.0}
    assert actual["lap_cumulative"][1200] == 240.0
