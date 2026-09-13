from __future__ import annotations

import asyncio
from datetime import date, timedelta
import json
import sqlite3
from types import SimpleNamespace

import pytest

from scraping import horse, parsed_cache
from scraping.saved_horse_reuse import (
    acquisition_horse_reuse, compatible_saved_pedigree, load_saved_horse_evidence,
)


HORSE_ID = "2020000001"
PEDIGREE = {"sire": "父", "dam": "母", "damsire": "母父"}


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "ultimate.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE races_ultimate(race_id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE race_results_ultimate(id INTEGER PRIMARY KEY, race_id TEXT, data TEXT,
                                              created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE INDEX idx_horse ON race_results_ultimate(json_extract(data,'$.horse_id'));
            CREATE TABLE scrape_date_completeness(
                race_date TEXT PRIMARY KEY, expected_race_ids_json TEXT, expected_race_count INTEGER,
                saved_race_ids_json TEXT, complete_race_ids_json TEXT, missing_race_ids_json TEXT,
                quarantined_race_ids_json TEXT, status TEXT, updated_at TEXT);
        """)
    return path


def save(path, race_id="202501010001", day="20250101", *, horse_id=HORSE_ID, **changes):
    info = {"race_id": race_id, "date": day, "distance": 1600, "venue": "東京", "surface": "芝"}
    payload = {"race_id": race_id, "horse_id": horse_id, **PEDIGREE,
               "finish_position": 2, "finish_time": "1:35.2", "weight_kg": 480,
               "horse_owner": "現在の所有者", "horse_total_runs": 99, **changes}
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT OR REPLACE INTO races_ultimate VALUES (?,?)", (race_id, json.dumps(info)))
        connection.execute("INSERT INTO race_results_ultimate(race_id,data) VALUES (?,?)",
                           (race_id, json.dumps(payload)))


def evidence(path, target="20250105"):
    with acquisition_horse_reuse(path, target):
        return load_saved_horse_evidence(HORSE_ID)


def coverage(path, *, start=date(2025, 1, 1), target=date(2025, 1, 5)):
    with sqlite3.connect(path) as connection:
        day = start
        while day < target:
            text = day.strftime("%Y%m%d")
            expected = [row[0] for row in connection.execute(
                "SELECT race_id FROM races_ultimate WHERE json_extract(data,'$.date')=?", (text,),
            )]
            payload = json.dumps(expected)
            connection.execute(
                "INSERT OR REPLACE INTO scrape_date_completeness VALUES (?,?,?,?,?,?,?,?,?)",
                (text, payload, len(expected), payload, payload, "[]", "[]", "complete", "2026-01-01"),
            )
            day += timedelta(days=1)


def test_read_only_opt_in_missing_database_and_force_bypass(database, tmp_path):
    save(database)
    before = database.read_bytes()
    assert load_saved_horse_evidence(HORSE_ID) is None
    with acquisition_horse_reuse(database, "20250105", force_refresh=True):
        assert load_saved_horse_evidence(HORSE_ID) is None
    with acquisition_horse_reuse(database, "20250230"):
        assert load_saved_horse_evidence(HORSE_ID) is None
    missing = tmp_path / "does-not-exist.db"
    with acquisition_horse_reuse(missing, "20250105"):
        assert load_saved_horse_evidence(HORSE_ID) is None
    assert not missing.exists()
    assert database.read_bytes() == before


def test_saved_pedigree_has_provenance_and_does_not_copy_current_profile(database):
    save(database)
    item = evidence(database)
    assert item.pedigree == PEDIGREE
    assert item.metadata["pedigree"]["source_race_ids"] == ["202501010001"]
    assert item.metadata["pedigree"]["source_count"] == 1
    assert len(item.metadata["source_hash"]) == 64
    history = item.metadata["history"]
    assert "horse_owner" not in history["fields"]
    assert "horse_total_runs" not in history["fields"]
    assert history["can_replace_provider_detail"] is False
    assert history["audit_only"] is True


@pytest.mark.parametrize("bad_name", ["父Father(米)", "父(米)", "unknown_local", "", "父\x00"])
def test_ambiguous_or_missing_name_falls_back(database, bad_name):
    save(database, sire=bad_name)
    assert evidence(database).pedigree is None


def test_conflicting_partial_source_is_not_ignored(database):
    save(database)
    save(database, "202501020001", "20250102", sire="", dam="別の母")
    item = evidence(database)
    assert item.pedigree is None
    assert item.metadata["pedigree"]["reason"] == "conflicting_saved_pedigree"


def test_only_outer_spacing_and_nfkc_are_normalized(database):
    save(database, sire=" 父 ", dam="母", damsire="母父")
    save(database, "202501020001", "20250102")
    assert evidence(database).pedigree == PEDIGREE


def test_current_complete_pedigree_keeps_authority_and_partial_must_agree(database):
    save(database)
    item = evidence(database)
    assert compatible_saved_pedigree(item, None) == PEDIGREE
    assert compatible_saved_pedigree(item, {"sire": "父"}) == PEDIGREE
    assert compatible_saved_pedigree(item, {"sire": "違う父"}) is None
    assert compatible_saved_pedigree(item, {**PEDIGREE, "sire": "修正済みの父"}) is None


def test_source_edits_deletion_target_and_ledger_changes_invalidate_token(database):
    save(database)
    save(database, "202501030001", "20250103")
    first = evidence(database)
    assert first.cache_token != evidence(database, "20250106").cache_token
    coverage(database)
    second = evidence(database)
    assert first.cache_token != second.cache_token
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM race_results_ultimate WHERE race_id='202501030001'")
    third = evidence(database)
    assert second.cache_token != third.cache_token
    save(database, "202501030001", "20250103", dam="修正母")
    assert evidence(database).pedigree is None


@pytest.mark.parametrize("failure", ["index", "malformed", "orphan"])
def test_unusable_source_never_triggers_unbounded_scan(database, failure):
    save(database)
    with sqlite3.connect(database) as connection:
        if failure == "index":
            connection.execute("DROP INDEX idx_horse")
        elif failure == "malformed":
            connection.execute("UPDATE races_ultimate SET data='not-json'")
        else:
            connection.execute("DELETE FROM races_ultimate")
    assert evidence(database) is None


def test_source_row_limit_falls_back(database, monkeypatch):
    from scraping import saved_horse_reuse
    save(database)
    save(database, "202501020001", "20250102")
    monkeypatch.setattr(saved_horse_reuse, "_MAX_HORSE_ROWS", 1)
    assert evidence(database) is None


@pytest.mark.parametrize("table", ["races_ultimate", "race_results_ultimate"])
@pytest.mark.parametrize("wrong_id", [None, "202501999999"])
def test_source_json_identity_must_match_sql_attribution(database, table, wrong_id):
    save(database)
    with sqlite3.connect(database) as connection:
        connection.execute(f"UPDATE {table} SET data=json_set(data,'$.race_id',?)", (wrong_id,))
    assert evidence(database) is None


def test_unparseable_source_date_prevents_history_coverage_claim(database):
    save(database)
    save(database, "202501030001", "20250103")
    save(database, "202501040001", "not-a-date")
    coverage(database)
    history = evidence(database).metadata["history"]
    assert history["coverage_complete"] is False
    assert history["reason"] == "history_contains_unresolved_result"
    assert history["fields"]["prev_race_date"] == "2025/01/03"


def test_history_is_strictly_before_target_and_coverage_is_not_assumed(database):
    save(database)
    save(database, "202501030001", "20250103", finish_position=3, finish_time=94.5)
    save(database, "202501050001", "20250105", finish_position=1)
    save(database, "202501090001", "20250109", finish_position=1)
    history = evidence(database).metadata["history"]
    assert history["available_prior_count"] == 2
    assert history["fields"]["prev_race_date"] == "2025/01/03"
    assert history["fields"]["prev_race_finish"] == 3
    assert history["fields"]["prev2_race_time"] == pytest.approx(95.2)
    assert history["source_race_ids"] == ["202501030001", "202501010001"]
    assert history["coverage_complete"] is False
    coverage(database)
    history = evidence(database).metadata["history"]
    assert history["coverage_complete"] is True
    assert history["can_replace_provider_detail"] is False


@pytest.mark.parametrize("failure", ["missing", "pending", "quarantined", "bad_json", "false_empty"])
def test_coverage_must_be_complete_and_consistent_on_every_day(database, failure):
    save(database)
    save(database, "202501030001", "20250103")
    coverage(database)
    with sqlite3.connect(database) as connection:
        if failure == "missing":
            connection.execute("DELETE FROM scrape_date_completeness WHERE race_date='20250102'")
        elif failure == "pending":
            connection.execute("UPDATE scrape_date_completeness SET status='pending' WHERE race_date='20250102'")
        elif failure == "quarantined":
            connection.execute("UPDATE scrape_date_completeness SET quarantined_race_ids_json='[\"x\"]'")
        elif failure == "bad_json":
            connection.execute("UPDATE scrape_date_completeness SET expected_race_ids_json='null'")
        else:
            connection.execute("UPDATE scrape_date_completeness SET expected_race_ids_json='[]', "
                               "expected_race_count=0, complete_race_ids_json='[]' WHERE race_date='20250101'")
    assert evidence(database).metadata["history"]["coverage_complete"] is False


def test_missing_or_conflicting_prior_result_is_not_coverage_proof(database):
    save(database)
    save(database, "202501030001", "20250103")
    save(database, "202501040001", "20250104", finish_time="")
    coverage(database)
    assert evidence(database).metadata["history"]["reason"] == "history_contains_unresolved_result"
    save(database, "202501030001", "20250103", finish_position=9)
    history = evidence(database).metadata["history"]
    assert history["reason"] == "conflicting_history"
    assert history["fields"] == {}


def test_thread_and_nested_context_isolation(database):
    save(database)
    async def scenario():
        with acquisition_horse_reuse(database, "20250105"):
            first = await asyncio.to_thread(load_saved_horse_evidence, HORSE_ID)
            with acquisition_horse_reuse(database, "20250106"):
                second = await asyncio.to_thread(load_saved_horse_evidence, HORSE_ID)
            third = await asyncio.to_thread(load_saved_horse_evidence, HORSE_ID)
        assert load_saved_horse_evidence(HORSE_ID) is None
        return first, second, third
    first, second, third = asyncio.run(scenario())
    assert first.cache_token == third.cache_token != second.cache_token


@pytest.fixture
def isolated_horse(monkeypatch, tmp_path):
    parsed_cache.close_cache_connections()
    monkeypatch.setattr(parsed_cache, "_CACHE_PATH", tmp_path / "parsed.db")
    monkeypatch.setattr(horse, "_PEDIGREE_DB_PATH", tmp_path / "pedigree.db")
    monkeypatch.setattr(horse, "_get_pedigree_sqlite", lambda _: None)
    monkeypatch.setattr(horse, "_save_pedigree_sqlite", lambda *_: None)
    yield
    parsed_cache.close_cache_connections()


HORSE_HTML = """<table class="db_prof_table">
<tr><th>馬主</th><td>現在の所有者</td></tr><tr><th>通算成績</th><td>99戦30勝</td></tr></table>
<table><tr><th>日付</th><th>開催</th><th>着順</th><th>タイム</th><th>距離</th></tr>
<tr><td>2026/09/01</td><td>東京</td><td>1</td><td>1:35.0</td><td>芝1600</td></tr>
<tr><td>2026/08/01</td><td>東京</td><td>2</td><td>1:36.0</td><td>芝1600</td></tr></table>"""
PED_HTML = """<table class="blood_table"><tr><td><a>父</a></td></tr>
<tr><td><a>母</a></td><td><a>母父</a></td></tr></table>"""


def test_jra_reuses_pedigree_but_preserves_all_provider_fields(database, isolated_horse, monkeypatch):
    save(database)
    calls = []
    async def fetch(_session, url, **_kwargs):
        calls.append(url)
        return SimpleNamespace(status=200, normalized_url=url, cache_snapshot=None), (
            PED_HTML if "/ped/" in url else HORSE_HTML)
    monkeypatch.setattr(horse, "fetch_text", fetch)
    baseline = asyncio.run(horse.scrape_horse_detail(None, HORSE_ID, quick_mode=True))
    assert len(calls) == 2
    calls.clear()
    with acquisition_horse_reuse(database, "20250105"):
        optimized = asyncio.run(horse.scrape_horse_detail(None, HORSE_ID, quick_mode=True))
    assert len(calls) == 1 and "/result/" in calls[0]
    metadata = optimized.pop("_acquisition_reuse")
    assert optimized == baseline  # no profile field dropped or changed
    assert metadata["pedigree"]["reused"] is True
    assert metadata["history"]["fields"]["prev_race_date"] == "2025/01/01"
    assert optimized["prev_race_date"] == "2026/09/01"


def test_nar_quick_path_reuses_verified_pedigree_without_network(database, isolated_horse, monkeypatch):
    nar_id = "B2020000001"
    save(database, horse_id=nar_id)
    async def forbidden(*_args, **_kwargs):
        pytest.fail("verified NAR pedigree should not make an HTTP request")
    monkeypatch.setattr(horse, "fetch_text", forbidden)
    with acquisition_horse_reuse(database, "20250105"):
        result = asyncio.run(horse.scrape_horse_detail(None, nar_id, quick_mode=True))
    assert {key: result[key] for key in PEDIGREE} == PEDIGREE
    assert result["_acquisition_reuse"]["pedigree"]["reused"] is True


def test_cached_horse_still_gets_fresh_date_specific_audit(database, isolated_horse, monkeypatch):
    save(database)
    keys = []
    raw = {**PEDIGREE, "prev_race_date": "2026/09/01", "horse_total_runs": 99}
    def cached(_kind, key, _version):
        keys.append(key)
        return dict(raw)
    monkeypatch.setattr(horse, "get_parsed", cached)
    with acquisition_horse_reuse(database, "20250105"):
        first = asyncio.run(horse.scrape_horse_detail(None, HORSE_ID))
    with acquisition_horse_reuse(database, "20250106"):
        second = asyncio.run(horse.scrape_horse_detail(None, HORSE_ID))
    assert keys[0] != keys[1]
    assert first["_acquisition_reuse"]["target_date"] == "20250105"
    assert second["_acquisition_reuse"]["target_date"] == "20250106"
    assert first["horse_total_runs"] == second["horse_total_runs"] == 99


def test_authoritative_pedigree_reuses_base_parse_across_dates_and_ledger_changes(
    database, isolated_horse, monkeypatch,
):
    save(database)
    save(database, "202501030001", "20250103")
    monkeypatch.setattr(horse, "_get_pedigree_sqlite", lambda _: dict(PEDIGREE))
    keys, calls, cache = [], [], {}
    def get(_kind, key, _version):
        keys.append(key)
        return dict(cache[key]) if key in cache else None
    def put(_kind, key, _version, payload, _sources, **_kwargs):
        cache[key] = dict(payload)
    async def fetch(*_args, **_kwargs):
        calls.append(True)
        return {**PEDIGREE, "horse_owner": "現在の所有者", "horse_total_runs": 99,
                "prev_race_date": "2026/09/01", "prev_race_finish": 1,
                "prev_race_time": 95.0, "prev_race_distance": 1600}
    monkeypatch.setattr(horse, "get_parsed", get)
    monkeypatch.setattr(horse, "put_parsed", put)
    monkeypatch.setattr(horse, "_scrape_horse_detail_uncached", fetch)
    with acquisition_horse_reuse(database, "20250105"):
        first = asyncio.run(horse.scrape_horse_detail(None, HORSE_ID))
    coverage(database, target=date(2025, 1, 6))
    with acquisition_horse_reuse(database, "20250106"):
        second = asyncio.run(horse.scrape_horse_detail(None, HORSE_ID))
    assert keys[0] == keys[1]
    assert len(json.loads(keys[0])) == 4
    assert len(calls) == 1
    assert first["_acquisition_reuse"]["target_date"] == "20250105"
    assert second["_acquisition_reuse"]["target_date"] == "20250106"
    assert first["_acquisition_reuse"]["history"]["coverage_complete"] is False
    assert second["_acquisition_reuse"]["history"]["coverage_complete"] is True
    assert first["horse_total_runs"] == second["horse_total_runs"] == 99
    assert all("_acquisition_reuse" not in payload for payload in cache.values())


def test_rejected_saved_source_cannot_reuse_previously_injected_payload(
    database, isolated_horse, monkeypatch,
):
    save(database)
    keys, calls, cache = [], [], {}
    def get(_kind, key, _version):
        keys.append(key)
        return dict(cache[key]) if key in cache else None
    def put(_kind, key, _version, payload, _sources, **_kwargs):
        cache[key] = dict(payload)
    async def fetch(*_args, **_kwargs):
        calls.append(True)
        return {**PEDIGREE, "dam": "母" if len(calls) == 1 else "再取得母",
                "prev_race_date": "2026/09/01", "prev_race_finish": 1,
                "prev_race_time": 95.0, "prev_race_distance": 1600}
    monkeypatch.setattr(horse, "get_parsed", get)
    monkeypatch.setattr(horse, "put_parsed", put)
    monkeypatch.setattr(horse, "_scrape_horse_detail_uncached", fetch)
    with acquisition_horse_reuse(database, "20250105"):
        first = asyncio.run(horse.scrape_horse_detail(None, HORSE_ID))
    save(database, "202501030001", "20250103", dam="矛盾する母")
    with acquisition_horse_reuse(database, "20250105"):
        second = asyncio.run(horse.scrape_horse_detail(None, HORSE_ID))
    assert list(map(lambda key: len(json.loads(key)), keys)) == [5, 4]
    assert len(calls) == 2
    assert first["_acquisition_reuse"]["pedigree"]["reused"] is True
    assert second["_acquisition_reuse"]["pedigree"]["reused"] is False
    assert second["dam"] == "再取得母"


def test_forced_acquisition_keeps_original_fetch_fallback(database, isolated_horse, monkeypatch):
    save(database)
    calls = []
    async def fetch(_session, url, **_kwargs):
        calls.append(url)
        return SimpleNamespace(status=200, normalized_url=url, cache_snapshot=None), (
            PED_HTML if "/ped/" in url else HORSE_HTML)
    monkeypatch.setattr(horse, "fetch_text", fetch)
    with acquisition_horse_reuse(database, "20250105", force_refresh=True):
        result = asyncio.run(horse.scrape_horse_detail(None, HORSE_ID, quick_mode=True))
    assert len(calls) == 2
    assert "_acquisition_reuse" not in result
