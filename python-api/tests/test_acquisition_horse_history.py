import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest

from scraping import fetch_pipeline, horse, parsed_cache
from scraping.acquisition_horse_history import HISTORY_INVENTORY_VERSION, enrichment_repair_races, prepare_history
from scraping.acquisition_http_policy import acquisition_http_policy
from scraping.fetch_pipeline import FetchAccessBlocked
from scraping.saved_horse_reuse import acquisition_horse_reuse

HORSE_ID = "2022104614"
PROFILE = f"""<html><link rel="canonical" href="https://db.netkeiba.com/horse/{HORSE_ID}/">
<table class="db_prof_table"><tr><th>通算成績</th><td>3戦1勝</td></tr>
<tr><th>生年月日</th><td>2022年1月1日</td></tr></table><div id="horse_results_box"></div></html>"""
TABLE = """<table><tr><th>日付</th><th>開催</th><th>着順</th><th>タイム</th><th>距離</th></tr>
<tr><td>2026/01/01</td><td>京都</td><td>1</td><td>2:00.0</td><td>芝2000</td></tr>
<tr><td>2025/12/28</td><td>中山</td><td>2</td><td>2:30.0</td><td>芝2500</td></tr>
<tr><td>2025/10/26</td><td>京都</td><td>3</td><td>3:00.0</td><td>芝3000</td></tr></table>"""
RESPONSE = json.dumps({"status": "OK", "data": TABLE})
PEDIGREE = {"sire": "父", "dam": "母", "damsire": "母父"}


def test_ajax_history_is_cut_before_current_race_but_original_source_counts_are_kept():
    html, audit = prepare_history(PROFILE, RESPONSE, HORSE_ID, "20251228")
    assert "2026/01/01" not in html and "2025/12/28" not in html
    assert "2025/10/26" in html
    assert audit["strictly_prior_rows"] == 1
    assert audit["excluded_current_or_future_rows"] == 2
    assert audit["coverage_matches_published_career_count"]
    _, first = prepare_history(PROFILE, RESPONSE, HORSE_ID, "20250101")
    assert first["strictly_prior_rows"] == 0
    assert first["status"] == "validated"


def test_static_tables_and_invalid_dates_cannot_bypass_identity_or_cutoff():
    static = PROFILE.replace('<div id="horse_results_box"></div>', TABLE)
    _, audit = prepare_history(static, None, HORSE_ID, "20251228")
    assert audit["strictly_prior_rows"] == 1
    with pytest.raises(ValueError, match="child-conflict"):
        prepare_history(static, None, "2022104615", "20251228")
    with pytest.raises(ValueError, match="date-invalid"):
        prepare_history(PROFILE, RESPONSE.replace("2025/10/26", "2025/13/26"), HORSE_ID, "20251228")


def test_prior_race_inventory_is_complete_only_with_verified_strictly_prior_links():
    linked = TABLE.replace("<td>2025/10/26</td>",
                           '<td><a href="/race/202508040701/">2025/10/26</a></td>')
    _, audit = prepare_history(PROFILE, json.dumps({"status": "OK", "data": linked}), HORSE_ID, "20251228")
    assert audit["strictly_prior_race_inventory"] == [
        {"race_id": "202508040701", "race_date": "20251026", "scope": "jra", "start_status": "started"}]
    assert audit["prior_race_links_complete"]
    assert not audit["prior_results_imported"]
    _, unlinked = prepare_history(PROFILE, RESPONSE, HORSE_ID, "20251228")
    assert not unlinked["prior_race_links_complete"]
    with pytest.raises(ValueError, match="race-link-identity-invalid"):
        prepare_history(PROFILE, json.dumps({"status": "OK", "data": linked.replace(
            "202508040701", "202408040701")}), HORSE_ID, "20251228")


def test_published_career_count_excludes_explicit_cancellations_not_unknown_missing_finishes():
    extra = '<tr><td>2024/01/01</td><td>東京</td><td>取</td><td></td><td>芝2000</td></tr>'
    response = json.dumps({"status": "OK", "data": TABLE.replace("</table>", extra + "</table>")},
                          ensure_ascii=False)
    _, audit = prepare_history(PROFILE, response, HORSE_ID, "20251228")
    assert audit["coverage_matches_published_career_count"]
    assert audit["explicit_not_started_rows"] == 1
    assert audit["strictly_prior_started_rows"] == 1
    assert audit["unknown_prior_start_status_rows"] == 0
    _, unknown = prepare_history(PROFILE, response.replace(">取<", "><"), HORSE_ID, "20251228")
    assert not unknown["coverage_matches_published_career_count"]
    assert unknown["unknown_prior_start_status_rows"] == 1


@pytest.fixture
def acquisition(tmp_path, monkeypatch):
    parsed_cache.close_cache_connections()
    fetch_pipeline.close_fetch_cache_connections()
    monkeypatch.setattr(parsed_cache, "_CACHE_PATH", tmp_path / "parsed.db")
    monkeypatch.setattr(fetch_pipeline, "_CACHE_DB_PATH", tmp_path / "http.db")
    monkeypatch.setattr(horse, "_PEDIGREE_DB_PATH", tmp_path / "pedigree.db")
    monkeypatch.setattr(horse, "_get_pedigree_sqlite", lambda _: PEDIGREE.copy())
    yield tmp_path / "unused.db"
    parsed_cache.close_cache_connections()
    fetch_pipeline.close_fetch_cache_connections()


def test_frontend_job_scope_uses_public_ajax_and_does_not_reuse_another_dates_history(acquisition, monkeypatch):
    calls = []

    async def fetch(_session, url, **kwargs):
        calls.append(url)
        body = RESPONSE if "ajax_horse_results" in url else PROFILE
        normalized = fetch_pipeline._normalize_url(url)
        snapshot = fetch_pipeline._write_cache(normalized, url, 200, {}, body.encode(), 3600)
        return fetch_pipeline.FetchResult(
            url, normalized, 200, body.encode(), "network", 1, cache_snapshot=snapshot,
        ), body

    monkeypatch.setattr(fetch_pipeline, "fetch_text", fetch)

    async def scenario(day):
        with acquisition_http_policy(), acquisition_horse_reuse(acquisition, day, force_refresh=True):
            return await horse.scrape_horse_detail(None, HORSE_ID, quick_mode=True)

    earlier = asyncio.run(scenario("20251228"))
    assert earlier["prev_race_date"] == "2025/10/26"
    later = asyncio.run(scenario("20260102"))
    assert later["prev_race_date"] == "2026/01/01"
    assert earlier["_career_history_source"]["target_date"] == "20251228"
    assert all("/horse/result/" not in url for url in calls)
    assert sum("ajax_horse_results" in url for url in calls) == 2
    before = len(calls)
    assert asyncio.run(scenario("20260102"))["prev_race_date"] == "2026/01/01"
    assert len(calls) == before


def test_bad_ajax_preserves_profile_but_does_not_invent_previous_results(acquisition, monkeypatch):
    async def fetch(_session, url, **kwargs):
        return SimpleNamespace(status=200, normalized_url=url, cache_snapshot=None), (
            '{"status":"NG","data":""}' if "ajax_horse_results" in url else PROFILE)
    monkeypatch.setattr(horse, "fetch_text", fetch)
    async def scenario():
        with acquisition_http_policy(), acquisition_horse_reuse(acquisition, "20251228", force_refresh=True):
            return await horse.scrape_horse_detail(None, HORSE_ID, quick_mode=True)
    result = asyncio.run(scenario())
    assert result["horse_birth_date"] == "2022年1月1日"
    assert result["_career_history_source"]["status"] == "unresolved"
    assert "prev_race_date" not in result


def test_wrong_profile_never_supplies_another_horses_fields_or_triggers_ajax(acquisition, monkeypatch):
    calls = []
    async def fetch(_session, url, **kwargs):
        calls.append(url)
        return SimpleNamespace(status=200, normalized_url=url, cache_snapshot=None), PROFILE.replace(
            HORSE_ID, "2022104615")
    monkeypatch.setattr(horse, "fetch_text", fetch)
    async def scenario():
        with acquisition_http_policy(), acquisition_horse_reuse(acquisition, "20251228", force_refresh=True):
            return await horse.scrape_horse_detail(None, HORSE_ID, quick_mode=True)
    result = asyncio.run(scenario())
    assert "horse_birth_date" not in result
    assert result["_career_history_source"]["status"] == "unresolved"
    assert len(calls) == 1


def test_provider_stop_propagates_without_fallback(acquisition, monkeypatch):
    calls = []
    async def fetch(_session, url, **kwargs):
        calls.append(url)
        if "ajax_horse_results" in url:
            raise FetchAccessBlocked("test-provider-block")
        return SimpleNamespace(status=200, normalized_url=url, cache_snapshot=None), PROFILE
    monkeypatch.setattr(horse, "fetch_text", fetch)
    async def scenario():
        with acquisition_http_policy(), acquisition_horse_reuse(acquisition, "20251228", force_refresh=True):
            await horse.scrape_horse_detail(None, HORSE_ID, quick_mode=True)
    with pytest.raises(FetchAccessBlocked, match="test-provider-block"):
        asyncio.run(scenario())
    assert len(calls) == 2


@pytest.mark.parametrize("wrong_child", [False, True])
def test_job_pedigree_enrichment_requires_child_and_parent_link_identity(acquisition, monkeypatch, wrong_child):
    from test_pedigree_identity import page
    monkeypatch.setattr(horse, "_get_pedigree_sqlite", lambda _: None)
    blood = page().replace("2020000001", HORSE_ID if not wrong_child else "2022104615")
    async def fetch(_session, url, **kwargs):
        body = blood if "/ped/" in url else RESPONSE if "ajax_horse_results" in url else PROFILE
        return SimpleNamespace(status=200, normalized_url=url, cache_snapshot=None), body
    monkeypatch.setattr(horse, "fetch_text", fetch)
    async def scenario():
        with acquisition_http_policy(), acquisition_horse_reuse(acquisition, "20251228", force_refresh=True):
            return await horse.scrape_horse_detail(None, HORSE_ID, quick_mode=True)
    result = asyncio.run(scenario())
    if wrong_child:
        assert "sire" not in result and "_pedigree_source" not in result
    else:
        assert result["sire_id"] == "2010000001"
        assert result["damsire_id"] == "000a010a17"
        assert result["damsire"] == "Sevres Rose\u00a0(愛)"


def test_repair_mode_revisits_settled_races_with_missing_pedigree_or_unaudited_history(tmp_path):
    database = tmp_path / "raw.db"
    ids = ["202506050811", "202506050810", "202506050809"]
    valid = {"horse_id": HORSE_ID, **PEDIGREE, "_career_history_source": {
        "status": "validated", "target_date": "20251228",
        "coverage_matches_published_career_count": True, "strictly_prior_rows": 0,
        "history_inventory_version": HISTORY_INVENTORY_VERSION, "prior_race_links_complete": True,
    }}
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE race_results_ultimate(race_id TEXT,data TEXT)")
        db.execute("CREATE TABLE races_ultimate(race_id TEXT,data TEXT)")
        for race_id, item in zip(ids, [valid, {**valid, "sire": "unknown_local"}, PEDIGREE]):
            db.execute("INSERT INTO race_results_ultimate VALUES(?,?)", (race_id, json.dumps(item)))
            db.execute("INSERT INTO races_ultimate VALUES(?,?)", (race_id, json.dumps({"race_class": "1勝クラス"})))
    assert enrichment_repair_races(database, ids, "20251228") == set(ids[1:])
    with sqlite3.connect(database) as db:
        db.execute("UPDATE races_ultimate SET data=? WHERE race_id=?",
                   (json.dumps({"race_class": "不明"}), ids[0]))
    assert enrichment_repair_races(database, ids, "20251228") == set(ids)


def test_stale_future_history_is_not_inherited_when_validated_past_history_is_empty():
    from scraping.storage_preservation import PreservationConflict, merge_acquisition_payload
    info = {"race_id": "202506050811", "date": "20251228"}
    identity = {"horse_id": HORSE_ID, "horse_number": 1}
    old = {**identity, "prev_race_date": "2026/01/01", "prev_race_finish": 1,
           "prev2_race_date": "2025/12/28", "horse_birth_date": "2022年1月1日"}
    fresh = {**identity, "_career_history_source": {
        "status": "validated", "target_date": "20251228", "strictly_prior_rows": 0,
        "coverage_matches_published_career_count": True,
    }}
    merged, _ = merge_acquisition_payload(info, [old], {"race_info": info, "horses": [fresh]})
    row = merged["horses"][0]
    assert not any(key.startswith(("prev_race_", "prev2_race_")) for key in row)
    assert row["horse_birth_date"] == old["horse_birth_date"]
    fresh["_career_history_source"]["target_date"] = "20260101"
    with pytest.raises(PreservationConflict, match="history-target-date-conflict"):
        merge_acquisition_payload(info, [old], {"race_info": info, "horses": [fresh]})
