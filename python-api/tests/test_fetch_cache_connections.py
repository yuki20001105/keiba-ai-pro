from __future__ import annotations

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from scraping import fetch_pipeline as fetch


def test_reading_missing_cache_does_not_create_files(monkeypatch, tmp_path):
    path = tmp_path / "not-created" / "cache.db"
    monkeypatch.setattr(fetch, "_CACHE_DB_PATH", path)
    assert fetch._read_cache("url") is None
    assert fetch._read_resume("resume") is None
    assert fetch.get_cached_source_metadata("https://db.netkeiba.com/race/1") is None
    assert not path.parent.exists()


def test_repeated_writes_use_one_connection_and_are_immediately_durable(monkeypatch, tmp_path):
    path = tmp_path / "cache.db"
    monkeypatch.setattr(fetch, "_CACHE_DB_PATH", path)
    original = sqlite3.connect
    connections = []

    def connect(*args, **kwargs):
        connections.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    for index in range(30):
        fetch._write_resume(str(index), "url", "error", "network", 400, 1, None)
        assert fetch._read_resume(str(index))["http_status"] == 400
    assert len(connections) == 1
    with original(path) as other_process:
        assert other_process.execute("SELECT COUNT(*) FROM fetch_resume").fetchone()[0] == 30


def test_freshness_checks_see_external_refresh_expiry_and_deletion(monkeypatch, tmp_path):
    path = tmp_path / "cache.db"
    monkeypatch.setattr(fetch, "_CACHE_DB_PATH", path)
    url = "https://db.netkeiba.com/horse/1/"
    fetch._write_cache(url, url, 200, {}, b"old", 3600)
    stamp = fetch.get_cached_source_metadata(url)
    with sqlite3.connect(path) as external:
        external.execute("UPDATE http_cache SET body = ?, fetched_at = fetched_at + 1", (b"new",))
    assert fetch.get_cached_source_metadata(url)["fetched_at"] == stamp["fetched_at"] + 1
    assert fetch._read_cache(url)["body"] == b"new"
    with sqlite3.connect(path) as external:
        external.execute("UPDATE http_cache SET expires_at = 0")
    assert fetch.get_cached_source_metadata(url) is None
    assert fetch._read_cache(url) is None
    with sqlite3.connect(path) as external:
        external.execute("DELETE FROM http_cache")
    assert fetch.get_cached_source_metadata(url) is None


def test_readonly_connection_upgrades_for_first_write(monkeypatch, tmp_path):
    path = tmp_path / "cache.db"
    monkeypatch.setattr(fetch, "_CACHE_DB_PATH", path)
    fetch._write_cache("url", "url", 200, {}, b"body", 60)
    fetch.close_fetch_cache_connections()
    before = path.read_bytes()
    assert fetch._read_cache("url")["body"] == b"body"
    assert path.read_bytes() == before
    fetch._write_resume("resume", "url", "success", "cache", 200, 1, None)
    assert fetch._read_resume("resume")["status"] == "success"


def test_connection_can_be_shared_by_threadpool_without_lost_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, "_CACHE_DB_PATH", tmp_path / "cache.db")

    def write(index):
        fetch._write_resume(str(index), "url", "error", "network", 400, 1, None)
        return fetch._read_resume(str(index))["normalized_url"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(write, range(80))) == ["url"] * 80


def test_metadata_query_uses_covering_index_without_reading_body(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, "_CACHE_DB_PATH", tmp_path / "cache.db")
    fetch._write_cache("url", "url", 200, {}, b"x" * (1024 * 1024), 60)
    executed = []
    with fetch._cache_connection() as connection:
        connection.set_trace_callback(executed.append)
    try:
        assert fetch.get_cached_source_metadata("url") is not None
    finally:
        with fetch._cache_connection() as connection:
            connection.set_trace_callback(None)
    # Explain the SQL actually issued by the metadata helper, not a separately
    # constructed query that might choose a different index in production.
    query = next(sql for sql in executed if sql.startswith("SELECT status, fetched_at"))
    with fetch._cache_connection() as connection:
        plan = connection.execute("EXPLAIN QUERY PLAN " + query).fetchall()
    assert any("COVERING INDEX" in str(row) for row in plan)


def test_resume_cache_miss_is_read_once_before_real_fetch(monkeypatch):
    reads = []
    monkeypatch.setattr(fetch, "_read_resume", lambda key: reads.append("resume") or {"status": "success"})
    monkeypatch.setattr(fetch, "_read_cache", lambda key: reads.append("cache") or None)
    monkeypatch.setattr(fetch, "_write_cache", lambda *args: None)
    monkeypatch.setattr(fetch, "_write_resume", lambda *args: None)

    async def network(session, url, normalized, **kwargs):
        reads.append("network")
        return fetch.FetchResult(url, normalized, 200, b"fresh", "network", 1)

    monkeypatch.setattr(fetch, "_network_fetch", network)
    result = asyncio.run(fetch.fetch_bytes(object(), "https://db.netkeiba.com/race/1", resume_key="resume"))
    assert result.body == b"fresh"
    assert reads == ["resume", "cache", "network"]


def test_force_refresh_still_bypasses_cached_body_and_resume(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, "_CACHE_DB_PATH", tmp_path / "cache.db")
    url = "https://db.netkeiba.com/race/1"
    fetch._write_cache(url, url, 200, {}, b"cached", 3600)
    fetch._write_resume("resume", url, "success", "network", 200, 1, None)

    async def network(session, source, normalized, **kwargs):
        return fetch.FetchResult(source, normalized, 200, b"fresh", "network", 1)

    monkeypatch.setattr(fetch, "_network_fetch", network)
    result = asyncio.run(fetch.fetch_bytes(object(), url, force_refresh=True, resume_key="resume"))
    assert result.body == b"fresh"
    assert fetch._read_cache(url)["body"] == b"fresh"


def test_cache_connection_pool_is_bounded(monkeypatch, tmp_path):
    for index in range(7):
        monkeypatch.setattr(fetch, "_CACHE_DB_PATH", tmp_path / f"cache-{index}.db")
        fetch._write_resume("resume", "url", "error", "network", 400, 1, None)
    assert len(fetch._CACHE_CONNECTIONS) == 4


def test_replaced_database_reopens_instead_of_writing_old_file(monkeypatch, tmp_path):
    path = tmp_path / "cache.db"
    replacement = tmp_path / "restored.db"
    monkeypatch.setattr(fetch, "_CACHE_DB_PATH", path)
    fetch._write_resume("old", "url", "error", "network", 400, 1, None)
    store = fetch._CACHE_CONNECTIONS[str(path)]
    with sqlite3.connect(replacement) as restored:
        fetch._create_cache_tables(restored)
        restored.execute("INSERT INTO fetch_resume VALUES ('restored','url','success','cache',200,1,1,NULL)")
    restored.close()
    # Windows does not allow replacing an open SQLite file. Simulate an idle
    # maintenance restore with its OS handle closed but the pool entry retained.
    store.connection.close()
    replacement.replace(path)
    assert fetch._read_resume("restored")["status"] == "success"
    assert fetch._read_resume("old") is None
    fetch._write_resume("new", "url", "success", "network", 200, 1, None)
    with sqlite3.connect(path) as restored:
        assert restored.execute("SELECT COUNT(*) FROM fetch_resume").fetchone()[0] == 2


def test_deleted_database_is_not_silently_written_through_stale_handle(monkeypatch, tmp_path):
    path = tmp_path / "cache.db"
    monkeypatch.setattr(fetch, "_CACHE_DB_PATH", path)
    fetch._write_resume("old", "url", "error", "network", 400, 1, None)
    fetch._CACHE_CONNECTIONS[str(path)].connection.close()
    path.unlink()
    assert fetch._read_resume("old") is None
    assert not path.exists()
    fetch._write_resume("new", "url", "success", "network", 200, 1, None)
    assert fetch._read_resume("new")["status"] == "success"


def test_metadata_probe_reads_legacy_cache_without_creating_index(monkeypatch, tmp_path):
    path = tmp_path / "legacy.db"
    monkeypatch.setattr(fetch, "_CACHE_DB_PATH", path)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE http_cache (normalized_url TEXT PRIMARY KEY,status INT,fetched_at REAL,expires_at REAL)")
        connection.execute("INSERT INTO http_cache VALUES ('https://db.netkeiba.com/race/1',200,1,9999999999)")
    before = path.read_bytes()
    assert fetch.get_cached_source_metadata("https://db.netkeiba.com/race/1")["fetched_at"] == 1
    assert path.read_bytes() == before


@pytest.mark.parametrize("resumed", [False, True])
def test_cached_body_keeps_its_snapshot_when_external_refresh_wins_before_return(monkeypatch, tmp_path, resumed):
    path = tmp_path / "cache.db"
    monkeypatch.setattr(fetch, "_CACHE_DB_PATH", path)
    url = "https://db.netkeiba.com/horse/1/"
    original_snapshot = fetch._write_cache(url, url, 200, {}, b"original", 3600)
    if resumed:
        fetch._write_resume("resume", url, "success", "network", 200, 1, None)
    lookup = fetch._lookup_cached_fetch

    def lookup_then_external_refresh(*args):
        saved = lookup(*args)
        # Reproduce a different connection committing after our body SELECT but
        # before the worker result gets delivered to the event-loop caller.
        with sqlite3.connect(path) as external:
            external.execute("UPDATE http_cache SET body = ?, fetched_at = fetched_at + 1", (b"refreshed",))
        external.close()
        return saved

    monkeypatch.setattr(fetch, "_lookup_cached_fetch", lookup_then_external_refresh)
    result = asyncio.run(fetch.fetch_bytes(object(), url, resume_key="resume" if resumed else None))
    assert result.source == ("resume-cache" if resumed else "cache")
    assert result.body == b"original"
    assert result.cache_snapshot == original_snapshot
    assert result.cache_snapshot != fetch.get_cached_source_metadata(url)
    assert fetch._read_cache(url)["body"] == b"refreshed"


def test_network_and_duplicate_keep_committed_body_snapshot_after_external_refresh(monkeypatch, tmp_path):
    path = tmp_path / "cache.db"
    monkeypatch.setattr(fetch, "_CACHE_DB_PATH", path)
    url = "https://db.netkeiba.com/horse/1/"
    write = fetch._write_cache
    written_snapshots = []

    def write_then_external_refresh(*args):
        snapshot = write(*args)
        written_snapshots.append(snapshot)
        with sqlite3.connect(path) as external:
            external.execute("UPDATE http_cache SET body = ?, fetched_at = fetched_at + 1", (b"refreshed",))
        external.close()
        return snapshot

    monkeypatch.setattr(fetch, "_write_cache", write_then_external_refresh)

    async def scenario():
        started = asyncio.Event()
        finish = asyncio.Event()
        calls = []

        async def network(session, source, normalized, **kwargs):
            calls.append(source)
            started.set()
            await finish.wait()
            return fetch.FetchResult(source, normalized, 200, b"original", "network", 1)

        monkeypatch.setattr(fetch, "_network_fetch", network)
        producer = asyncio.create_task(fetch.fetch_bytes(object(), url, force_refresh=True))
        await started.wait()
        duplicate = asyncio.create_task(fetch.fetch_bytes(object(), url, force_refresh=True))
        await asyncio.sleep(0)
        finish.set()
        results = await asyncio.gather(producer, duplicate)
        assert calls == [url]
        assert len(written_snapshots) == 1
        assert all(result.body == b"original" for result in results)
        assert all(result.cache_snapshot == written_snapshots[0] for result in results)
        assert all(result.cache_snapshot != fetch.get_cached_source_metadata(url) for result in results)
        assert fetch._read_cache(url)["body"] == b"refreshed"
        assert not fetch._LOOP_INFLIGHT

    asyncio.run(scenario())


@pytest.mark.parametrize("use_cache", [False, True])
def test_unpersisted_response_never_claims_a_source_snapshot(monkeypatch, use_cache):
    async def network(session, source, normalized, **kwargs):
        return fetch.FetchResult(source, normalized, 200, b"body", "network", 1)

    monkeypatch.setattr(fetch, "_network_fetch", network)
    # Some legacy test/embedding adapters intentionally do not persist writes.
    monkeypatch.setattr(fetch, "_write_cache", lambda *args: None)
    result = asyncio.run(fetch.fetch_bytes(object(), "https://db.netkeiba.com/horse/1/",
                                          use_cache=use_cache, force_refresh=True))
    assert result.body == b"body"
    assert result.cache_snapshot is None
