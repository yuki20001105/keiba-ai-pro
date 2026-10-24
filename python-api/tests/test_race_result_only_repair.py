"""Acquisition regressions use captured-looking local fixtures only, never HTTP."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib

import pytest

from scraping import race
from scraping.fetch_pipeline import FetchResult


RACE_ID = "201606010101"
DATE = "20160105"
DESKTOP = """<html><head><title>Test result | 2016年1月5日</title></head><body>
<h1>3歳未勝利</h1>
<div class="mainrace_data">芝右1600m / 天候 : 晴 / 芝 : 稍 / 発走10:00</div>
<p class="smalltxt">2016年1月5日 1回中山1日目</p>
<table class="race_table_01"><tr>
<th>着順</th><th>枠番</th><th>馬番</th><th>馬名</th><th>性齢</th><th>斤量</th><th>騎手</th>
<th>タイム</th><th>着差</th><th>通過</th><th>上り</th><th>単勝</th><th>人気</th><th>馬体重</th>
<th>調教師</th><th>賞金</th></tr>
<tr><td>1</td><td>1</td><td>1</td><td><a href="/horse/2013000001/">Horse One</a></td>
<td>牡3</td><td>57</td><td><a href="/jockey/00001/">Jockey One</a></td>
<td>1:36.0</td><td></td><td>1-1-1</td><td>35.0</td><td>2.5</td><td>1</td><td>480(+2)</td>
<td><a href="/trainer/00001/">Trainer One</a></td><td>550.0</td></tr>
<tr><td>2(降)</td><td>2</td><td>3</td><td><a href="/horse/2013000003/">Horse Three</a></td>
<td>牡3</td><td>57</td><td><a href="/jockey/00003/">Jockey Three</a></td>
<td>1:35.9</td><td>クビ</td><td>2-2-2</td><td>35.1</td><td>3.0</td><td>2</td><td>490(-2)</td>
<td><a href="/trainer/00003/">Trainer Three</a></td><td>220.0</td></tr></table>
<table><tr><th>単勝</th><td>1</td><td>250円</td><td>1人気</td></tr></table>
</body></html>"""
MOBILE = """<html><head><title>Test result | 2016年1月5日</title></head><body>
<div class="RaceHeader_Value"><h2>3歳未勝利</h2>
<div class="RaceData">10:00発走 ダ1800m(右) 晴 不</div><span>1回中山1日目</span></div>
<table class="table_slide_body ResultsByRaceDetail"><tr>
<th>着順</th><th>枠 番</th><th>馬 番</th><th>馬名</th><th>性齢</th><th>斤量</th><th>騎手</th>
<th>タイム</th><th>着差</th><th>通過</th><th>上がり</th><th>単勝</th><th>人気</th><th>馬体重</th>
<th>調教師</th><th>馬主</th><th>賞金（万円）</th></tr>
<tr><td>2(降)</td><td>1</td><td>1</td><td><a href="https://db.sp.netkeiba.com/horse/2013000001/">Horse One</a></td>
<td>牡3</td><td>57</td><td><a href="https://db.sp.netkeiba.com/jockey/00001/">Jockey One</a></td>
<td>1:55.0</td><td></td><td>1-1-1-1</td><td>38.0</td><td>2.5</td><td>1</td><td>480(+2)</td>
<td><a href="https://db.sp.netkeiba.com/trainer/00001/">Trainer One</a></td><td>Owner</td><td>220.0</td></tr>
</table><table><tr><th>単勝</th><td>2</td><td>300円</td><td>2人気</td></tr></table></body></html>"""


@pytest.fixture
def acquisition_mocks(monkeypatch):
    monkeypatch.setattr(race, "_entry_card_date_allowed", lambda *_args: False)
    monkeypatch.setattr(race, "_prefer_entry_card_for_target", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(race, "prefer_mobile_endpoint", lambda *_args: False)
    monkeypatch.setattr(race, "remember_mobile_endpoint", lambda *_args, **_kwargs: None)
    details = []

    async def detail(_session, horse_id, *_args, **_kwargs):
        details.append(horse_id)
        return {"sire": "Detail Sire", "damsire": "Detail Damsire"}

    monkeypatch.setattr(race, "scrape_horse_detail", detail)
    return details


def _install_fetch(monkeypatch, *, html=DESKTOP, desktop_rejected=False):
    calls = []

    async def fetch(_session, url, **kwargs):
        calls.append((url, kwargs))
        status = 400 if desktop_rejected and "db.netkeiba.com" in url else 200
        body = html if status == 200 else ""
        return FetchResult(url, url, status, body.encode("utf-8"), "network", 1), body

    monkeypatch.setattr(race, "fetch_text", fetch)
    return calls


def test_desktop_result_only_skips_all_horse_details_and_retains_original_evidence(acquisition_mocks, monkeypatch):
    calls = _install_fetch(monkeypatch)
    before = datetime.now(timezone.utc)
    actual = asyncio.run(race.scrape_race_full(None, RACE_ID, DATE, quick_mode=True,
                                             force_refresh=True, enrich_horse_details=False))
    assert len(calls) == 1 and calls[0][1]["force_refresh"] is True
    assert acquisition_mocks == []
    info = actual["race_info"]
    assert info["date"] == DATE and info["distance"] == 1600
    assert info["field_condition"] == "稍重" and info["field_condition_raw"] == "稍"
    demoted = actual["horses"][1]
    assert demoted["finish_position"] == 2 and demoted["finish_position_raw"] == "2(降)"
    assert demoted["finish_annotation"] == "降" and demoted["finish_time"] == "1:35.9"
    assert actual["return_tables"][0]["payout"] == 250
    evidence = actual["_source_evidence"]
    assert evidence["body_sha256"] == hashlib.sha256(DESKTOP.encode("utf-8")).hexdigest()
    assert evidence["source_url"] == f"https://db.netkeiba.com/race/{RACE_ID}/"
    assert evidence["source_date"] == DATE
    assert evidence["observation_time_is_source_event_time"] is False
    assert datetime.fromisoformat(evidence["observed_at_utc"]) >= before
    assert evidence["runner_identity"] == [
        {"horse_number": 1, "horse_id": "2013000001", "horse_name": "Horse One"},
        {"horse_number": 3, "horse_id": "2013000003", "horse_name": "Horse Three"}]


def test_default_signature_still_enriches_each_horse(acquisition_mocks, monkeypatch):
    _install_fetch(monkeypatch)
    actual = asyncio.run(race.scrape_race_full(None, RACE_ID, DATE, quick_mode=True))
    assert sorted(acquisition_mocks) == ["2013000001", "2013000003"]
    assert actual["horses"][0]["sire"] == "Detail Sire"
    assert "_source_evidence" not in actual


def test_source_date_does_not_come_from_callers_date_hint(acquisition_mocks, monkeypatch):
    _install_fetch(monkeypatch)
    actual = asyncio.run(race.scrape_race_full(None, RACE_ID, "20160106", enrich_horse_details=False))
    assert actual["race_info"]["date"] == "20160106"  # compatibility; caller independently checks source proof
    assert actual["_source_evidence"]["source_date"] == DATE


def test_ambiguous_or_absent_displayed_date_is_not_filled_from_hint(acquisition_mocks, monkeypatch):
    html = DESKTOP.replace("<title>Test result | 2016年1月5日</title>", "<title>2016年1月6日</title>")
    _install_fetch(monkeypatch, html=html)
    actual = asyncio.run(race.scrape_race_full(None, RACE_ID, DATE, enrich_horse_details=False))
    assert actual["_source_evidence"]["source_date"] is None


@pytest.mark.parametrize("preferred,reject", [(True, False), (False, True)])
def test_mobile_result_only_routes_skip_horse_http_and_normalize_with_source_evidence(
        acquisition_mocks, monkeypatch, preferred, reject):
    monkeypatch.setattr(race, "prefer_mobile_endpoint", lambda *_args: preferred)
    calls = _install_fetch(monkeypatch, html=MOBILE, desktop_rejected=reject)
    actual = asyncio.run(race.scrape_race_full(None, RACE_ID, DATE, enrich_horse_details=False))
    assert len(calls) == (2 if reject else 1)
    assert acquisition_mocks == []
    assert actual["race_info"]["field_condition"] == "不良"
    assert actual["race_info"]["field_condition_raw"] == "不"
    horse = actual["horses"][0]
    assert horse["finish_position"] == 2 and horse["finish_position_raw"] == "2(降)"
    assert actual["_source_evidence"]["body_sha256"] == hashlib.sha256(MOBILE.encode()).hexdigest()
    assert "db.sp.netkeiba.com" in actual["_source_evidence"]["source_url"]


def test_mobile_default_keeps_detail_enrichment(acquisition_mocks, monkeypatch):
    monkeypatch.setattr(race, "prefer_mobile_endpoint", lambda *_args: True)
    _install_fetch(monkeypatch, html=MOBILE)
    actual = asyncio.run(race.scrape_race_full(None, RACE_ID, DATE))
    assert acquisition_mocks == ["2013000001"]
    assert actual["horses"][0]["sire"] == "Detail Sire"
    assert "_source_evidence" not in actual
