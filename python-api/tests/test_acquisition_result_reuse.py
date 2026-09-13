import asyncio
import sqlite3
import time
from datetime import date

from scraping import fetch_pipeline, result_reuse
from scraping.quality import classify_race_quality


def complete_race():
    return {
        "race_info": {"race_id": "202001010101", "date": "20200104", "venue": "Tokyo", "distance": 1600},
        "horses": [{
            "horse_id": "2017000001", "horse_name": "Horse", "horse_number": 1,
            "finish_position": 1, "finish_time": "1:34.1", "odds": 2.0,
            "popularity": 1, "weight_kg": 470, "sire": "Sire", "dam": "Dam", "damsire": "Damsire",
        }],
        "return_tables": [{"bet_type": "単勝", "combinations": "1", "payout": 200}],
    }


def test_historical_result_reuses_all_fields_after_quality_check(monkeypatch):
    calls = []
    data = complete_race()
    monkeypatch.setattr(result_reuse, "has_cached_result", lambda _: True)

    async def fetcher(*args, **kwargs):
        calls.append(kwargs["force_refresh"])
        return data

    result = asyncio.run(result_reuse.fetch_acquisition_race(
        object(), "202001010101", date_hint="20200104", fetcher=fetcher, today=date(2026, 9, 13),
    ))
    assert result == data
    assert classify_race_quality(result).valid_for_date_completion
    assert calls == [False]


def test_incomplete_cache_is_refreshed_without_lowering_quality(monkeypatch):
    calls = []
    data = complete_race()
    partial = complete_race()
    partial["horses"][0]["finish_time"] = None
    monkeypatch.setattr(result_reuse, "has_cached_result", lambda _: True)

    async def fetcher(*args, **kwargs):
        calls.append(kwargs["force_refresh"])
        return data if kwargs["force_refresh"] else partial

    result = asyncio.run(result_reuse.fetch_acquisition_race(
        object(), "202001010101", date_hint="20200104", fetcher=fetcher,
    ))
    assert calls == [False, True]
    assert result == data


def test_today_bypasses_historical_cache(monkeypatch):
    def unexpected(_):
        raise AssertionError("Live result must not use historical cache decision")

    monkeypatch.setattr(result_reuse, "has_cached_result", unexpected)
    calls = []

    async def fetcher(*args, **kwargs):
        calls.append(kwargs["force_refresh"])
        return complete_race()

    asyncio.run(result_reuse.fetch_acquisition_race(
        object(), "202601010101", date_hint="20260913", fetcher=fetcher, today=date(2026, 9, 13),
    ))
    assert calls == [True]


def test_cache_planning_is_read_only_and_respects_expiry(tmp_path, monkeypatch):
    path = tmp_path / "cache.db"
    monkeypatch.setattr(fetch_pipeline, "_CACHE_DB_PATH", path)
    assert not result_reuse.has_cached_result("202001010101")
    assert not path.exists()
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE http_cache (normalized_url TEXT, status INTEGER, expires_at REAL, body BLOB)")
        conn.execute("INSERT INTO http_cache VALUES (?,200,?,?)", (
            "https://db.sp.netkeiba.com/race/202001010101/", time.time() + 60, b"<html>race</html>",
        ))
    before = path.read_bytes()
    assert result_reuse.has_cached_result("202001010101")
    assert path.read_bytes() == before
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE http_cache SET expires_at=0")
    assert not result_reuse.has_cached_result("202001010101")


def test_planning_reuses_handle_and_rechecks_body_quality(tmp_path, monkeypatch):
    path = tmp_path / "cache.db"
    monkeypatch.setattr(fetch_pipeline, "_CACHE_DB_PATH", path)
    url = "https://db.sp.netkeiba.com/race/202001010101/"
    fetch_pipeline._write_cache(url, url, 200, {}, b"result", 60)
    original_connect = sqlite3.connect

    def unexpected_connect(*args, **kwargs):
        raise AssertionError("Cache planning must reuse its existing connection")

    monkeypatch.setattr(sqlite3, "connect", unexpected_connect)
    for _ in range(5):
        assert result_reuse.has_cached_result("202001010101")
    with original_connect(path) as external:
        external.execute("UPDATE http_cache SET body=?", (b"",))
    assert not result_reuse.has_cached_result("202001010101")
    with original_connect(path) as external:
        external.execute("UPDATE http_cache SET body=?, status=403", (b"blocked",))
    assert not result_reuse.has_cached_result("202001010101")
