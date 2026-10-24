"""Bounded process transport and exact frozen-parser parity on local fixtures."""
from concurrent.futures import Future
from datetime import datetime
import importlib.util
import os
from pathlib import Path
import sqlite3
import sys
from types import FunctionType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import last3f_cache_processes_v4 as processes
import repair_last3f_gaps_v3 as repair

RID = "202605010101"
URL = f"https://db.sp.netkeiba.com/race/{RID}/"
STAMP = datetime.fromisoformat("2026-10-17T10:00:00+00:00").timestamp()


@pytest.fixture
def cache_fixture(tmp_path):
    path = tmp_path / "synthetic-cache.db"
    body = '''<html><head><meta charset="utf-8"><title>結果 2026年1月4日 東京1R</title></head>
<div class="RaceData02">2頭</div><table class="ResultsByRaceDetail">
<tr><th>着順</th><th>馬番</th><th>馬名</th><th>後3F</th></tr>
<tr><td>1</td><td>1</td><td><a href="/horse/2020000001/">テスト1</a></td><td>35.1</td></tr>
<tr><td>2</td><td>2</td><td><a href="/horse/2020000002/">テスト2</a></td><td>**</td></tr>
</table></html>'''.encode()
    with sqlite3.connect(path) as cache:
        cache.execute("CREATE TABLE http_cache(normalized_url TEXT PRIMARY KEY,status INTEGER,body BLOB,fetched_at REAL)")
        cache.execute("INSERT INTO http_cache VALUES(?,?,?,?)", (URL, 200, body, STAMP))
    target = {"race_id": RID, "race_date": "20260104", "expected_runners": [
        {"horse_number": i, "horse_id": f"202000000{i}", "horse_name": f"テスト{i}"} for i in (1, 2)]}
    return SimpleNamespace(path=path, body=body, target=target)


class FakePool:
    def __init__(self, **kwargs):
        self.kwargs, self.futures, self.closed = kwargs, [], None

    def submit(self, *args):
        future = Future()
        self.futures.append(future)
        return future

    def shutdown(self, **kwargs):
        self.closed = kwargs


@pytest.fixture
def fake_pool(monkeypatch):
    monkeypatch.setattr(processes, "ProcessPoolExecutor", FakePool)
    monkeypatch.setattr(processes, "_check_memory_reserve", lambda: None)


@pytest.mark.parametrize("workers", [0, 4, True, None, 2.5])
def test_worker_limit_rejected_before_start(workers):
    with pytest.raises(ValueError, match="one-through-three"):
        processes.ProcessCacheExecutorV4(max_workers=workers)


def test_spawn_bounded_queue_and_slots_release(fake_pool, cache_fixture):
    with processes.ProcessCacheExecutorV4() as pool:
        assert pool._pool.kwargs["mp_context"].get_start_method() == "spawn"
        futures = [pool.submit(repair.cached_candidate, cache_fixture.target, cache_fixture.path) for _ in range(3)]
        with pytest.raises(RuntimeError, match="bounded-queue-full"):
            pool.submit(repair.cached_candidate, cache_fixture.target, cache_fixture.path)
        futures[0].set_result({})
        pool.submit(repair.cached_candidate, cache_fixture.target, cache_fixture.path)
    assert pool._pool.closed == {"wait": True, "cancel_futures": False}
    with pytest.raises(RuntimeError, match="shut-down"):
        pool.submit(repair.cached_candidate, cache_fixture.target, cache_fixture.path)


def test_only_exact_v3_function_is_accepted(fake_pool, cache_fixture):
    with processes.ProcessCacheExecutorV4() as pool:
        with pytest.raises(ValueError, match="only-pinned-v3"):
            pool.submit(lambda target, path: {}, cache_fixture.target, cache_fixture.path)
        # Private V4 module clones preserve the exact V3 code; no function pickling.
        clone = FunctionType(repair.cached_candidate.__code__, {}, "cached_candidate")
        assert isinstance(pool.submit(clone, cache_fixture.target, cache_fixture.path), Future)


def test_memory_reserve_rejects_before_process_creation(monkeypatch):
    import psutil
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=4 * 1024**3 - 1))
    with pytest.raises(MemoryError, match="four-gib-reserve"):
        processes.ProcessCacheExecutorV4()


def test_source_hash_change_rejects_without_running_pool(monkeypatch):
    monkeypatch.setattr(processes, "PINNED_SOURCES", {"scripts/repair_last3f_gaps_v3.py": "0" * 64})
    with pytest.raises(ValueError, match="source-pin-changed"):
        processes._check_source_pins()


def test_spawn_result_preserves_full_v3_output_and_parent_environment(cache_fixture):
    original = cache_fixture.path.read_bytes()
    environment = {key: os.environ.get(key) for key in processes.THREAD_ENV}
    expected = repair.cached_candidate(cache_fixture.target, cache_fixture.path)
    with processes.ProcessCacheExecutorV4(max_workers=1) as pool:
        actual = pool.submit(repair.cached_candidate, cache_fixture.target, cache_fixture.path).result(timeout=45)
    assert actual == expected
    assert actual["body"] == cache_fixture.body
    assert actual["evidence"]["source"]["source_url"] == URL
    assert [row["last3f_state"] for row in actual["evidence"]["rows"]] == ["valid", "masked"]
    assert cache_fixture.path.read_bytes() == original
    assert environment == {key: os.environ.get(key) for key in processes.THREAD_ENV}
