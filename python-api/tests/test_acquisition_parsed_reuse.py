from __future__ import annotations

import asyncio
import sqlite3
import json
import threading
import time
from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest
from bs4 import BeautifulSoup

from scraping import fetch_pipeline, horse, parsed_cache, race_list


HORSE_HTML = """<html><body>
<table class="db_prof_table">
<tr><th>生年月日</th><td>2020年3月1日</td></tr>
<tr><th>馬主</th><td>所有者</td></tr>
<tr><th>生産者</th><td>生産者</td></tr>
<tr><th>産地</th><td>牧場</td></tr>
<tr><th>通算成績</th><td>5戦2勝</td></tr>
<tr><th>獲得賞金(中央)</th><td>2,000万円</td></tr>
</table>
<table><tr><th>日付</th><th>開催</th><th>着順</th><th>タイム</th><th>馬体重</th><th>距離</th></tr>
<tr><td>2025/01/05</td><td>中山</td><td>2</td><td>1:55.0</td><td>480(+2)</td><td>ダ1800</td></tr>
<tr><td>2024/12/01</td><td>京都</td><td>3</td><td>1:34.5</td><td>478(-2)</td><td>芝1600</td></tr>
</table></body></html>"""
PEDIGREE = {"sire": "父", "dam": "母", "damsire": "母父"}
ORIGINAL_PEDIGREE_READER = horse._get_pedigree_sqlite


@pytest.fixture(autouse=True)
def isolated_caches(tmp_path, monkeypatch):
    parsed_cache.close_cache_connections()
    fetch_pipeline.close_fetch_cache_connections()
    monkeypatch.setattr(fetch_pipeline, "_CACHE_DB_PATH", tmp_path / "http.db")
    monkeypatch.setattr(parsed_cache, "_CACHE_PATH", tmp_path / "parsed.db")
    monkeypatch.setattr(horse, "_PEDIGREE_DB_PATH", tmp_path / "pedigree.db")
    monkeypatch.setattr(horse, "_get_pedigree_sqlite", lambda _horse_id: dict(PEDIGREE))
    yield
    parsed_cache.close_cache_connections()
    fetch_pipeline.close_fetch_cache_connections()


def successful_source(url, body, ttl=3600):
    normalized = fetch_pipeline._normalize_url(url)
    snapshot = fetch_pipeline._write_cache(normalized, url, 200, {}, body.encode(), ttl)
    return fetch_pipeline.FetchResult(url, normalized, 200, body.encode(), "network", 1,
                                      cache_snapshot=snapshot), body


def cached_source(url, cached):
    normalized = fetch_pipeline._normalize_url(url)
    snapshot = {"url": normalized, "fetched_at": cached["fetched_at"],
                "expires_at": cached["expires_at"]}
    return fetch_pipeline.FetchResult(url, normalized, 200, cached["body"], "cache", 1,
                                      cache_snapshot=snapshot), cached["body"].decode()


def test_parsed_snapshot_isolated_copy_and_invalidates_on_any_source_refresh():
    urls = ["https://db.netkeiba.com/horse/result/2020000001/",
            "https://db.netkeiba.com/horse/ped/2020000001/"]
    for url in urls:
        successful_source(url, "<table>source</table>")
    sources = {url: parsed_cache.source_metadata(url) for url in urls}
    parsed_cache.put_parsed("test", "key", "v1", {"nested": {"score": 3}}, sources, max_age_sec=300)
    first = parsed_cache.get_parsed("test", "key", "v1")
    assert first == {"nested": {"score": 3}}
    first["nested"]["score"] = 99
    assert parsed_cache.get_parsed("test", "key", "v1") == {"nested": {"score": 3}}
    assert parsed_cache.get_parsed("test", "key", "v2") is None
    with sqlite3.connect(fetch_pipeline._CACHE_DB_PATH) as conn:
        conn.execute("UPDATE http_cache SET fetched_at = fetched_at + 0.1 WHERE normalized_url = ?", (urls[1],))
    assert parsed_cache.get_parsed("test", "key", "v1") is None


def test_parsed_snapshot_does_not_extend_source_age_or_cache_untracked_input():
    url = "https://db.netkeiba.com/horse/result/2020000001/"
    successful_source(url, "ok")
    with sqlite3.connect(fetch_pipeline._CACHE_DB_PATH) as conn:
        conn.execute("UPDATE http_cache SET fetched_at = ?", (time.time() - 600,))
    sources = {url: parsed_cache.source_metadata(url)}
    parsed_cache.put_parsed("test", "old", "v1", {"ok": True}, sources, max_age_sec=300)
    assert parsed_cache.get_parsed("test", "old", "v1") is None
    sources["missing"] = {"url": "missing", "fetched_at": 0, "expires_at": 0}
    parsed_cache.put_parsed("test", "missing", "v1", {"ok": True}, sources, max_age_sec=3600)
    assert parsed_cache.get_parsed("test", "missing", "v1") is None


def test_collected_sources_keep_response_stamp_and_reject_mixed_versions(monkeypatch):
    url = "https://db.netkeiba.com/horse/result/2020000001/"
    old = successful_source(url, "old")
    new = successful_source(url, "new")
    assert old[0].cache_snapshot != new[0].cache_snapshot
    responses = iter([old, new, old])
    async def fake_fetch(*_args, **_kwargs):
        return next(responses)
    monkeypatch.setattr(fetch_pipeline, "fetch_text", fake_fetch)
    async def scenario():
        with parsed_cache.collect_sources() as sources:
            await parsed_cache.fetch_source_text(None, url)
            assert sources == {url: old[0].cache_snapshot}
            await parsed_cache.fetch_source_text(None, url)
            await parsed_cache.fetch_source_text(None, url)
        return sources
    sources = asyncio.run(scenario())
    assert sources == {url: {"url": url, "fetched_at": 0, "expires_at": 0}}
    parsed_cache.put_parsed("test", "mixed", "v1", {"old": True}, sources, max_age_sec=300)
    assert not parsed_cache._CACHE_PATH.exists()


def test_old_response_cannot_reuse_or_overwrite_new_parsed_component(monkeypatch):
    url = "https://db.netkeiba.com/horse/result/2020000001/"
    old = successful_source(url, HORSE_HTML)
    new_html = HORSE_HTML.replace("所有者", "新しい所有者")
    new = successful_source(url, new_html)
    parsed_new = horse._parse_horse_source_html(new_html, "2020000001")
    parsed_cache.put_parsed("horse-html", url, horse._PARSER_VERSION, parsed_new,
                           {url: new[0].cache_snapshot}, max_age_sec=300)
    assert parsed_cache.get_parsed("horse-html", url, horse._PARSER_VERSION,
                                    expected_sources={url: old[0].cache_snapshot}) is None
    async def fake_fetch(*_args, **_kwargs):
        return old
    monkeypatch.setattr(fetch_pipeline, "fetch_text", fake_fetch)
    result = asyncio.run(horse._scrape_horse_detail_uncached(None, "2020000001"))
    assert result["horse_owner"] == "所有者"
    assert parsed_cache.get_parsed("horse-html", url, horse._PARSER_VERSION) == parsed_new


def test_source_refresh_during_worker_parse_cannot_promote_old_fields(monkeypatch):
    url = "https://db.netkeiba.com/horse/result/2020000001/"
    old = successful_source(url, HORSE_HTML)
    new_html = HORSE_HTML.replace("所有者", "新しい所有者")
    started = threading.Event()
    release = threading.Event()
    original_parser = horse._parse_horse_source_html
    def paused_parser(html, horse_id):
        started.set()
        assert release.wait(timeout=5)
        return original_parser(html, horse_id)
    async def fake_fetch(_session, requested_url, **_kwargs):
        return cached_source(requested_url, fetch_pipeline._read_cache(requested_url))
    monkeypatch.setattr(fetch_pipeline, "fetch_text", fake_fetch)
    monkeypatch.setattr(horse, "_parse_horse_source_html", paused_parser)
    async def scenario():
        task = asyncio.create_task(horse.scrape_horse_detail(None, "2020000001"))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            refreshed = successful_source(url, new_html)
            assert refreshed[0].cache_snapshot != old[0].cache_snapshot
        finally:
            release.set()
        return await task
    first = asyncio.run(scenario())
    assert first["horse_owner"] == "所有者"
    # Neither component nor complete horse may be stamped with the new body.
    assert not parsed_cache._CACHE_PATH.exists()
    second = asyncio.run(horse.scrape_horse_detail(None, "2020000001"))
    assert second["horse_owner"] == "新しい所有者"
    assert second == dict(first, horse_owner="新しい所有者")


def test_untracked_response_does_not_reuse_or_promote_current_cached_source(monkeypatch):
    url = "https://db.netkeiba.com/horse/result/2020000001/"
    successful_source(url, HORSE_HTML.replace("所有者", "別の所有者"))
    async def fake_fetch(*_args, **_kwargs):
        return fetch_pipeline.FetchResult(url, url, 200, HORSE_HTML.encode(), "network", 1), HORSE_HTML
    monkeypatch.setattr(fetch_pipeline, "fetch_text", fake_fetch)
    first = asyncio.run(horse.scrape_horse_detail(None, "2020000001"))
    assert first["horse_owner"] == "所有者"
    assert not parsed_cache._CACHE_PATH.exists()


def test_memory_hit_reuses_connection_but_external_updates_and_version_changes_invalidate():
    url = "https://db.netkeiba.com/horse/result/2020000001/"
    successful_source(url, "source")
    sources = {url: parsed_cache.source_metadata(url)}
    parsed_cache.put_parsed("test", "key", "v1", {"value": "first"}, sources, max_age_sec=300)
    with parsed_cache.cached_sqlite_connection(parsed_cache._CACHE_PATH) as conn:
        first_connection = conn
        statements = []
        conn.set_trace_callback(statements.append)
    for _ in range(10):
        assert parsed_cache.get_parsed("test", "key", "v1") == {"value": "first"}
    with parsed_cache.cached_sqlite_connection(parsed_cache._CACHE_PATH) as conn:
        assert conn is first_connection
        conn.set_trace_callback(None)
    assert not any("SELECT payload" in statement for statement in statements)
    with sqlite3.connect(parsed_cache._CACHE_PATH) as external:
        external.execute("UPDATE parsed_snapshots SET payload=?", (json.dumps({"value": "external"}),))
    assert parsed_cache.get_parsed("test", "key", "v1") == {"value": "external"}
    parsed_cache.put_parsed("test", "key", "v2", {"value": "parser update"}, sources, max_age_sec=300)
    assert parsed_cache.get_parsed("test", "key", "v1") is None
    assert parsed_cache.get_parsed("test", "key", "v2") == {"value": "parser update"}
    with sqlite3.connect(parsed_cache._CACHE_PATH) as external:
        external.execute("UPDATE parsed_snapshots SET expires_at=0")
    assert parsed_cache.get_parsed("test", "key", "v2") is None


def test_memory_and_connection_pools_are_bounded_and_cache_miss_is_read_only(monkeypatch, tmp_path):
    assert parsed_cache.get_parsed("test", "missing", "v1") is None
    assert not parsed_cache._CACHE_PATH.exists()
    monkeypatch.setattr(parsed_cache, "_MEMORY_LIMIT", 2)
    monkeypatch.setattr(parsed_cache, "_MEMORY_BYTE_LIMIT", 10000)
    url = "https://db.netkeiba.com/horse/result/2020000001/"
    successful_source(url, "source")
    sources = {url: parsed_cache.source_metadata(url)}
    for index in range(4):
        parsed_cache.put_parsed("test", str(index), "v1", {"value": index}, sources, max_age_sec=300)
    assert len(parsed_cache._MEMORY) == 2
    assert 0 < parsed_cache._MEMORY_BYTES <= 10000
    parsed_cache.put_parsed("test", "oversized", "v1", {"value": "x" * 10000}, sources, max_age_sec=300)
    assert all(key[2] != "oversized" for key in parsed_cache._MEMORY)
    assert parsed_cache.get_parsed("test", "0", "v1") == {"value": 0}
    for index in range(parsed_cache._CONNECTION_LIMIT + 2):
        with parsed_cache.cached_sqlite_connection(tmp_path / f"bounded-{index}.db", writable=True):
            pass
    assert len(parsed_cache._CONNECTIONS) == parsed_cache._CONNECTION_LIMIT
    parsed_cache.close_cache_connections()
    assert not parsed_cache._CONNECTIONS and not parsed_cache._MEMORY
    assert parsed_cache._MEMORY_BYTES == 0


def test_pedigree_connection_reuse_preserves_external_corrections():
    assert ORIGINAL_PEDIGREE_READER("2020000001") is None
    assert not horse._PEDIGREE_DB_PATH.exists()
    horse._save_pedigree_sqlite("2020000001", **PEDIGREE)
    with parsed_cache.cached_sqlite_connection(horse._PEDIGREE_DB_PATH) as conn:
        initial_connection = conn
    for _ in range(5):
        assert ORIGINAL_PEDIGREE_READER("2020000001") == PEDIGREE
    with parsed_cache.cached_sqlite_connection(horse._PEDIGREE_DB_PATH) as conn:
        assert conn is initial_connection
    with sqlite3.connect(horse._PEDIGREE_DB_PATH) as external:
        external.execute("UPDATE pedigree_cache SET sire=?", ("corrected",))
    assert ORIGINAL_PEDIGREE_READER("2020000001")["sire"] == "corrected"


def test_bounded_html_workers_preserve_results_without_blocking_event_loop(monkeypatch):
    barrier = threading.Barrier(2)
    parser_threads = set()
    thread_lock = threading.Lock()
    original_parser = horse._parse_horse_source_html
    def parallel_parser(html, horse_id):
        with thread_lock:
            parser_threads.add(threading.get_ident())
        barrier.wait(timeout=3)
        return original_parser(html, horse_id)
    async def fake_fetch(_session, url, **_kwargs):
        return successful_source(url, HORSE_HTML)
    monkeypatch.setattr(horse, "_parse_horse_source_html", parallel_parser)
    monkeypatch.setattr(fetch_pipeline, "fetch_text", fake_fetch)
    async def scenario():
        tasks = [asyncio.create_task(horse.scrape_horse_detail(None, f"202000000{index}"))
                 for index in range(1, 5)]
        # A task can yield to the caller while the CPU parser is still running.
        await asyncio.sleep(0)
        return await asyncio.gather(*tasks)
    results = asyncio.run(scenario())
    assert all(result == results[0] for result in results)
    assert results[0]["prev2_race_distance"] == 1600
    assert len(parser_threads) == 2
    assert threading.get_ident() not in parser_threads


@pytest.mark.parametrize("html", [
    """<html><body><div>5戦2勝 獲得賞金 1,200万円 牡 黒鹿毛</div>
        <script>999戦999勝 獲得賞金 999万円</script><style>999戦999勝</style>
        <table><tr><th>日<span>付</span></th><th>開催</th><th>着順</th><th>タイム</th><th>距離</th></tr>
        <tr><td>2025/01/05</td><td>中山</td><td>2</td><td>1:55.0</td><td>ダ1800</td></tr>
        <tr><td>2024/12/01</td><td>京都</td><td>3</td><td>1:34.5</td><td>芝1600</td></tr>
        <tr><td>2024/11/01</td><td>東京</td><td>1</td><td>1:35.0</td><td>芝1600</td></tr>
        <tr><th>生年月日</th><td>2020年3月1日</td></tr>
        </table></body></html>""",
    """<table><tr><td><table><tr><th>日付</th><th>着順</th></tr>
        <tr><td>2025/01/05</td><td>2</td></tr><tr><td>2024/12/01</td><td>3</td></tr>
        <tr><td>2024/11/01</td><td>1</td></tr></table></td></tr></table>""",
    """<table class="blood_table"><tr><th>日付</th><th>着順</th></tr>
        <tr><td><a>父</a></td><td>2</td></tr><tr><td>ancestor</td></tr>
        <tr><td><a>母</a></td><td><a>母父</a></td></tr><tr><td>ancestor</td></tr></table>""",
    """<html><div>牡 鹿毛 3戦1勝</div><template>999戦999勝</template>
        <table><tr><th>生年月日</th><td>2020年3月1日</td></tr>
        <tr><th>性別</th><td>牡 鹿毛</td></tr></table></html>""",
    """<html><body><div>牝 黒鹿毛</div><table>
        <tr><th>馬主</th><td>A&amp;B&nbsp;所有者</td></tr>
        <tr><th>生産者</th><td>&#x5317;海道&#160;牧場</td></tr>
        <tr><th>日付</th><th>開催</th><th>着順</th><th>タイム</th></tr>
        <tr><td>2025/01/05</td><td>中山&nbsp;</td><td>2</td><td>1:55.0</td></tr>
        </table></body></html>""",
    """<table><tr><th>日付</th><th>着順</th><th>タイム</th></tr>
        <tr><td>2025/01/05</td><td>2</td><td>1:55.0</td></tr>
        <tr><td>2024/12/01</td><td>3</td><td>1:34.5</td></tr>
        <tr><td>2024/11/01</td><td>1</td><td>7戦3勝 獲得賞金 2,500万円</td></tr>
        </table>""",
    """<html><body><table><tr><th>生年月日<td>2020年3月1日
        <tr><th>馬主<td><b>A&amp;B</b><tr><th>産地<td>北海道""",
    "<html><body>not a result page</body></html>",
    "",
])
def test_narrow_horse_tree_matches_legacy_fields_text_and_coat(monkeypatch, html):
    optimized = horse._parse_horse_source_html(html, "2020000001")
    narrow, _page_text = horse._horse_source_soup(html)
    legacy = BeautifulSoup(html, "lxml", parse_only=horse.HTML_STRAINER)
    with monkeypatch.context() as patch:
        patch.setattr(horse, "_horse_source_soup", lambda _html: (legacy, legacy.get_text()))
        expected = horse._parse_horse_source_html(html, "2020000001")
    assert optimized == expected
    assert horse.extract_coat_color(narrow, html) == horse.extract_coat_color(legacy, html)


def test_narrow_horse_tree_falls_back_on_lxml_parse_error(monkeypatch):
    def parser_error(*_args, **_kwargs):
        raise horse.etree.ParserError("unsupported document")
    monkeypatch.setattr(horse.etree, "HTML", parser_error)
    result = horse._parse_horse_source_html(HORSE_HTML, "2020000001")
    assert result["fields"]["prev_race_finish"] == 2
    assert result["fields"]["horse_total_prize_money"] == 20000000


def test_horse_reuse_preserves_every_field_and_bypasses_fetch_and_parse(monkeypatch):
    calls = []
    async def fake_fetch(_session, url, **_kwargs):
        calls.append(url)
        return successful_source(url, HORSE_HTML)
    monkeypatch.setattr(fetch_pipeline, "fetch_text", fake_fetch)
    baseline = asyncio.run(horse._scrape_horse_detail_uncached(None, "2020000001"))
    first = asyncio.run(horse.scrape_horse_detail(None, "2020000001"))
    assert first == baseline
    assert first["prev2_race_distance"] == 1600
    assert first["horse_total_prize_money"] == 20000000
    before = len(calls)
    def fail_parse(*_args, **_kwargs):
        raise AssertionError("a valid parsed snapshot must not parse HTML again")
    monkeypatch.setattr(horse, "BeautifulSoup", fail_parse)
    again = asyncio.run(horse.scrape_horse_detail(None, "2020000001"))
    assert again == baseline
    again["horse_owner"] = "caller mutation"
    assert asyncio.run(horse.scrape_horse_detail(None, "2020000001")) == baseline
    assert len(calls) == before


def test_horse_request_modes_and_changed_pedigree_do_not_share_stale_output(monkeypatch):
    calls = []
    async def fake_fetch(_session, url, **_kwargs):
        calls.append(url)
        return successful_source(url, HORSE_HTML)
    monkeypatch.setattr(fetch_pipeline, "fetch_text", fake_fetch)
    asyncio.run(horse.scrape_horse_detail(None, "2020000001", quick_mode=False))
    asyncio.run(horse.scrape_horse_detail(None, "2020000001", quick_mode=True))
    assert len(calls) == 2
    changed = dict(PEDIGREE, sire="修正された父")
    result = asyncio.run(horse.scrape_horse_detail(None, "2020000001", pedigree_cache={"2020000001": changed}))
    assert len(calls) == 3
    assert result["sire"] == changed["sire"]


def test_incomplete_horse_results_remain_retryable(monkeypatch):
    calls = []
    async def fake_fetch(_session, url, **_kwargs):
        calls.append(url)
        return successful_source(url, "<html>no results table</html>" if len(calls) == 1 else HORSE_HTML)
    monkeypatch.setattr(fetch_pipeline, "fetch_text", fake_fetch)
    first = asyncio.run(horse.scrape_horse_detail(None, "2020000001"))
    second = asyncio.run(horse.scrape_horse_detail(None, "2020000001"))
    assert "prev_race_date" not in first
    assert second["prev_race_finish"] == 2
    assert len(calls) == 2


def test_partial_html_parse_reuse_preserves_pedigree_failures_and_every_output_field(monkeypatch):
    profile_html = """<html><table class="db_prof_table">
        <tr><th>生年月日</th><td>2020年3月1日</td></tr>
        <tr><th>馬主</th><td>所有者</td></tr>
        <tr><th>通算成績</th><td>5戦2勝</td></tr>
        </table></html>"""
    pedigree_calls = []
    parse_calls = []
    original_parser = horse._parse_horse_source_html
    monkeypatch.setattr(horse, "_get_pedigree_sqlite", lambda _: None)

    def counted_parser(html, horse_id):
        parse_calls.append(horse_id)
        return original_parser(html, horse_id)

    async def fake_fetch(_session, url, **_kwargs):
        if "/ped/" in url:
            pedigree_calls.append(url)
            return fetch_pipeline.FetchResult(url, url, 400, b"", "network", 1), ""
        cached = fetch_pipeline._read_cache(fetch_pipeline._normalize_url(url))
        if cached:
            return cached_source(url, cached)
        return successful_source(url, profile_html)

    monkeypatch.setattr(horse, "_parse_horse_source_html", counted_parser)
    monkeypatch.setattr(fetch_pipeline, "fetch_text", fake_fetch)
    expected = {"horse_birth_date": "2020年3月1日", "horse_owner": "所有者",
                "horse_total_runs": 5, "horse_total_wins": 2}
    for _ in range(3):
        result = asyncio.run(horse.scrape_horse_detail(None, "2020000001"))
        assert result == expected
        result["horse_owner"] = "caller mutation"
    # Three acquisitions still attempt the same failed enrichment three times;
    # only pure extraction of the identical source HTML was reused.
    assert len(pedigree_calls) == 3
    assert len(parse_calls) == 1
    assert not horse._complete_detail(expected)
    # Refreshing even a still-unexpired HTTP source invalidates the component.
    successful_source("https://db.netkeiba.com/horse/result/2020000001/", HORSE_HTML)
    refreshed = asyncio.run(horse.scrape_horse_detail(None, "2020000001"))
    assert refreshed["prev_race_finish"] == 2
    assert len(parse_calls) == 2
    assert len(pedigree_calls) == 4


def test_partial_component_cache_does_not_hide_newly_available_pedigree(monkeypatch):
    monkeypatch.setattr(horse, "_get_pedigree_sqlite", lambda _: None)
    pedigree_calls = []
    async def fake_fetch(_session, url, **_kwargs):
        if "/ped/" in url:
            pedigree_calls.append(url)
            if len(pedigree_calls) == 1:
                return fetch_pipeline.FetchResult(url, url, 400, b"", "network", 1), ""
            return successful_source(url, """<table class="blood_table">
                <tr><td><a>父</a></td></tr><tr><td>ancestor</td></tr>
                <tr><td><a>母</a></td><td><a>母父</a></td></tr>
                <tr><td>ancestor</td></tr></table>""")
        cached = fetch_pipeline._read_cache(fetch_pipeline._normalize_url(url))
        if cached:
            return cached_source(url, cached)
        return successful_source(url, HORSE_HTML)
    monkeypatch.setattr(fetch_pipeline, "fetch_text", fake_fetch)
    first = asyncio.run(horse.scrape_horse_detail(None, "2020000001"))
    assert "sire" not in first
    second = asyncio.run(horse.scrape_horse_detail(None, "2020000001"))
    assert second == dict(first, **PEDIGREE)
    assert len(pedigree_calls) == 2


@pytest.mark.parametrize("horse_id", ["2020000001", "B202000001"])
def test_restriction_terminates_horse_fallbacks(monkeypatch, horse_id):
    calls = []
    async def blocked(_session, url, **_kwargs):
        calls.append(url)
        raise fetch_pipeline.FetchAccessBlocked("HTTP 403")
    monkeypatch.setattr(fetch_pipeline, "fetch_text", blocked)
    with pytest.raises(fetch_pipeline.FetchAccessBlocked):
        asyncio.run(horse.scrape_horse_detail(None, horse_id))
    assert len(calls) == 1


def test_validated_400_fallback_is_reused_but_unrecognized_200_is_not(monkeypatch):
    calls = []
    async def fake_fetch(_session, url, **_kwargs):
        calls.append(url)
        if "db.netkeiba.com" in url:
            return fetch_pipeline.FetchResult(url, url, 400, b"", "network", 1), ""
        return successful_source(url, HORSE_HTML)
    monkeypatch.setattr(fetch_pipeline, "fetch_text", fake_fetch)
    first = asyncio.run(horse.scrape_horse_detail(None, "2020000001"))
    second = asyncio.run(horse.scrape_horse_detail(None, "2020000002"))
    assert first == second
    assert calls == [
        "https://db.netkeiba.com/horse/result/2020000001/",
        "https://db.sp.netkeiba.com/horse/result/2020000001/",
        "https://db.sp.netkeiba.com/horse/result/2020000002/",
    ]
    parsed_cache.remember_mobile_endpoint("horse-result", valid=False)
    async def unrecognized(_session, url, **_kwargs):
        status = 400 if "db.netkeiba.com" in url else 200
        return fetch_pipeline.FetchResult(url, url, status, b"<html>oops</html>", "network", 1), "<html>oops</html>"
    monkeypatch.setattr(fetch_pipeline, "fetch_text", unrecognized)
    asyncio.run(horse.scrape_horse_detail(None, "2020000003"))
    assert not parsed_cache.prefer_mobile_endpoint("horse-result")


def month_page(day, race_id, next_page=None):
    pager = f'<a href="/?pid=race_list&amp;page={next_page}">next</a>' if next_page else ""
    return f'<a href="/race/{race_id}/">2025/01/{day:02d} 中山</a>{pager}'


def test_month_reuse_requires_every_page_and_follows_later_pagination(monkeypatch):
    calls = []
    pages = {1: month_page(5, "202506010101", 2),
             2: month_page(6, "202506010201", 3),
             3: month_page(7, "202506010301")}
    async def fake_fetch(_session, url, **_kwargs):
        page = int(parse_qs(urlparse(url).query)["page"][0])
        calls.append(page)
        return successful_source(url, pages[page])
    monkeypatch.setattr(race_list, "fetch_text", fake_fetch)
    first = asyncio.run(race_list._fetch_mobile_month_race_ids(None, "20250105", date(2025, 1, 5)))
    second = asyncio.run(race_list.fetch_race_ids("20250107"))
    assert first == ["202506010101"]
    assert second == (["202506010301"], "db.sp.netkeiba.com/month-search")
    assert calls == [1, 2, 3]


def test_month_page_refresh_does_not_relabel_pre_refresh_entries(monkeypatch):
    async def fake_fetch(_session, url, **_kwargs):
        old = successful_source(url, month_page(5, "202506010101"))
        successful_source(url, month_page(6, "202506010201"))
        return old
    monkeypatch.setattr(race_list, "fetch_text", fake_fetch)
    first = asyncio.run(race_list._fetch_mobile_month_race_ids(None, "20250105", date(2025, 1, 5)))
    assert first == ["202506010101"]
    assert race_list._cached_month_ids("20250105") is None
    assert not parsed_cache._CACHE_PATH.exists()


@pytest.mark.parametrize("last_status,last_html", [(503, ""), (200, "<html>error page</html>")])
def test_partial_or_unrecognized_month_never_becomes_complete_cache(monkeypatch, last_status, last_html):
    async def fake_fetch(_session, url, **_kwargs):
        page = int(parse_qs(urlparse(url).query)["page"][0])
        if page == 1:
            return successful_source(url, month_page(5, "202506010101", 2))
        return fetch_pipeline.FetchResult(url, url, last_status, last_html.encode(), "network", 1), last_html
    monkeypatch.setattr(race_list, "fetch_text", fake_fetch)
    with pytest.raises(RuntimeError):
        asyncio.run(race_list._fetch_mobile_month_race_ids(None, "20250105", date(2025, 1, 5)))
    assert race_list._cached_month_ids("20250105") is None
