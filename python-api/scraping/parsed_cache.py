"""Reusable parsed snapshots, valid only while all HTTP inputs are unchanged.

No HTML or application records are duplicated here. The source cache remains
the authority for freshness; refreshing or expiring any source invalidates its
derived value. Reads never create a database.
"""
from __future__ import annotations

import hashlib
import atexit
import json
import sqlite3
import time
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from threading import RLock
from typing import Any

from . import fetch_pipeline

_CACHE_PATH = Path(__file__).resolve().parents[2] / "keiba" / "data" / "parsed_fetch_cache.db"
_SOURCES: ContextVar[dict[str, dict] | None] = ContextVar("parsed_http_sources", default=None)
_LOCK = RLock()
_CONNECTION_LIMIT = 4
_MEMORY_LIMIT = 2048
_MEMORY_BYTE_LIMIT = 16 * 1024 * 1024
_MEMORY_BYTES = 0
_CONNECTIONS: OrderedDict[str, tuple[sqlite3.Connection, bool, tuple[int, int]]] = OrderedDict()
_INITIALIZED: dict[str, sqlite3.Connection] = {}
_DATA_VERSIONS: dict[str, int] = {}
_MEMORY: OrderedDict[tuple[str, str, str, str], tuple[str, list[dict], float]] = OrderedDict()


def _memory_size(key, row) -> int:
    # Conservative UTF-32 payload/key accounting plus source-dict overhead.
    return (len(row[0]) + sum(len(str(item)) for item in key)) * 4 + len(row[1]) * 1024 + 256


def _forget_memory(key) -> None:
    global _MEMORY_BYTES
    row = _MEMORY.pop(key, None)
    if row is not None:
        _MEMORY_BYTES -= _memory_size(key, row)


def _drop_connection(path_key: str) -> None:
    entry = _CONNECTIONS.pop(path_key, None)
    if entry:
        entry[0].close()
    _INITIALIZED.pop(path_key, None)
    _DATA_VERSIONS.pop(path_key, None)
    for key in [key for key in _MEMORY if key[0] == path_key]:
        _forget_memory(key)


def close_cache_connections() -> None:
    """Release bounded cache handles, e.g. when shutting down or isolating tests."""
    with _LOCK:
        for path_key in list(_CONNECTIONS):
            _drop_connection(path_key)


@contextmanager
def cached_sqlite_connection(path: Path, *, writable: bool = False):
    """Reuse short SQLite operations across threads; readers never create files.

    The lock is held only during local SQL. No HTTP or parsing may run inside
    this context. Replaced/deleted files and a read-to-write upgrade reopen the
    handle, avoiding a stale database or an ever-growing connection registry.
    """
    path = Path(path)
    path_key = str(path.resolve())
    with _LOCK:
        try:
            stat = path.stat()
            identity = (stat.st_dev, stat.st_ino)
        except FileNotFoundError:
            identity = None
        entry = _CONNECTIONS.get(path_key)
        if entry and (entry[2] != identity or (writable and entry[1])):
            _drop_connection(path_key)
            entry = None
        if not entry:
            if identity is None and not writable:
                yield None
                return
            if writable:
                path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
                conn.execute("PRAGMA journal_mode=WAL")
            else:
                conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True,
                                       timeout=10, check_same_thread=False)
                conn.execute("PRAGMA query_only=ON")
            stat = path.stat()
            entry = (conn, not writable, (stat.st_dev, stat.st_ino))
            _CONNECTIONS[path_key] = entry
            while len(_CONNECTIONS) > _CONNECTION_LIMIT:
                _drop_connection(next(iter(_CONNECTIONS)))
        _CONNECTIONS.move_to_end(path_key)
        try:
            yield entry[0]
        except BaseException:
            if entry[0].in_transaction:
                entry[0].rollback()
            raise


def _remember_memory(key, row) -> None:
    global _MEMORY_BYTES
    _forget_memory(key)
    size = _memory_size(key, row)
    if size > _MEMORY_BYTE_LIMIT:
        return
    _MEMORY[key] = row
    _MEMORY_BYTES += size
    _MEMORY.move_to_end(key)
    while len(_MEMORY) > _MEMORY_LIMIT or _MEMORY_BYTES > _MEMORY_BYTE_LIMIT:
        _forget_memory(next(iter(_MEMORY)))


def _check_data_version(conn: sqlite3.Connection, path_key: str) -> None:
    version = int(conn.execute("PRAGMA data_version").fetchone()[0])
    previous = _DATA_VERSIONS.get(path_key)
    if previous is not None and previous != version:
        # A different process/connection updated snapshots or endpoint choices.
        for key in [key for key in _MEMORY if key[0] == path_key]:
            _forget_memory(key)
    _DATA_VERSIONS[path_key] = version


def parser_version(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_metadata(url: str) -> dict | None:
    """Read freshness stamps without loading the potentially large HTML BLOB."""
    return fetch_pipeline.get_cached_source_metadata(url)


@contextmanager
def collect_sources():
    sources: dict[str, dict] = {}
    token = _SOURCES.set(sources)
    try:
        yield sources
    finally:
        _SOURCES.reset(token)


async def fetch_source_text(session, url: str, **kwargs):
    fetched, html = await fetch_pipeline.fetch_text(session, url, **kwargs)
    sources = _SOURCES.get()
    if sources is not None and fetched.status == 200:
        # This stamp was read/written together with this response's body. A
        # fresh database lookup could instead identify another job's refresh.
        metadata = getattr(fetched, "cache_snapshot", None)
        source_url = metadata["url"] if metadata else fetched.normalized_url
        previous = sources.get(source_url)
        if metadata is not None and (previous is None or previous == metadata):
            sources[source_url] = dict(metadata)
        else:
            # An untracked successful source must prevent promotion, even if
            # another input has metadata. Two versions of one input must also
            # remain uncacheable rather than relabeling the earlier fields.
            sources[source_url] = {"url": source_url, "fetched_at": 0, "expires_at": 0}
    return fetched, html


def _ensure_schema(conn: sqlite3.Connection) -> None:
    path_key = str(_CACHE_PATH.resolve())
    if _INITIALIZED.get(path_key) is not conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS parsed_snapshots (
            kind TEXT NOT NULL, cache_key TEXT NOT NULL, version TEXT NOT NULL,
            payload TEXT NOT NULL, sources TEXT NOT NULL, expires_at REAL NOT NULL,
            PRIMARY KEY (kind, cache_key)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS endpoint_preferences (
            kind TEXT PRIMARY KEY, expires_at REAL NOT NULL
        )""")
        conn.commit()
        _INITIALIZED[path_key] = conn


def get_parsed(kind: str, key: str, version: str, *,
               expected_sources: dict[str, dict] | None = None) -> Any | None:
    try:
        path_key = str(_CACHE_PATH.resolve())
        memory_key = (path_key, kind, key, version)
        with cached_sqlite_connection(_CACHE_PATH) as conn:
            if conn is None:
                return None
            _check_data_version(conn, path_key)
            row = _MEMORY.get(memory_key)
            if row is None:
                stored = conn.execute(
                    "SELECT payload, sources, expires_at FROM parsed_snapshots "
                    "WHERE kind = ? AND cache_key = ? AND version = ? AND expires_at > ?",
                    (kind, key, version, time.time()),
                ).fetchone()
                if stored is None:
                    return None
                row = (stored[0], json.loads(stored[1]), stored[2])
                _remember_memory(memory_key, row)
            else:
                _MEMORY.move_to_end(memory_key)
            if row[2] <= time.time():
                _forget_memory(memory_key)
                return None
        sources = row[1]
        # A caller holding HTML may only reuse the parse for that exact body,
        # even if a newer parsed snapshot is now current in the shared cache.
        if expected_sources is not None and {
            item["url"]: item for item in sources
        } != expected_sources:
            return None
        if not sources or any(source_metadata(item["url"]) != item for item in sources):
            return None
        # JSON decoding returns a fresh value: callers may safely update it.
        payload = json.loads(row[0])
        recorder = getattr(fetch_pipeline, "record_fetch_metric", None)
        if recorder:
            recorder("parsed_cache_hits")
        return payload
    except (sqlite3.Error, ValueError, KeyError, TypeError, OSError):
        return None


def put_parsed(kind: str, key: str, version: str, payload: Any,
               sources: dict[str, dict], *, max_age_sec: float) -> None:
    if not sources:
        return
    now = time.time()
    expires_at = min(
        min(source["expires_at"], source["fetched_at"] + max_age_sec)
        for source in sources.values()
    )
    if expires_at <= now:
        return
    try:
        payload_json = json.dumps(payload, ensure_ascii=False)
        source_list = [dict(source) for source in sources.values()]
        if any(source_metadata(item["url"]) != item for item in source_list):
            return
        # If a source changes after this check, keep the captured stamp: reads
        # will miss. Never associate already-parsed fields with a newer body.
        with cached_sqlite_connection(_CACHE_PATH, writable=True) as conn:
            _ensure_schema(conn)
            path_key = str(_CACHE_PATH.resolve())
            _check_data_version(conn, path_key)
            conn.execute(
                "INSERT OR REPLACE INTO parsed_snapshots VALUES (?, ?, ?, ?, ?, ?)",
                (kind, key, version, payload_json,
                 json.dumps(source_list, sort_keys=True), expires_at),
            )
            conn.commit()
            for old_key in [item for item in _MEMORY if item[:3] == (path_key, kind, key)]:
                _forget_memory(old_key)
            _remember_memory((path_key, kind, key, version),
                             (payload_json, source_list, expires_at))
    except (sqlite3.Error, ValueError, TypeError, OSError):
        pass  # Cache availability must never determine acquisition correctness.


def prefer_mobile_endpoint(kind: str) -> bool:
    try:
        with cached_sqlite_connection(_CACHE_PATH) as conn:
            return bool(conn and conn.execute(
                "SELECT 1 FROM endpoint_preferences WHERE kind = ? AND expires_at > ?",
                (kind, time.time()),
            ).fetchone())
    except (sqlite3.Error, OSError):
        return False


def remember_mobile_endpoint(kind: str, *, valid: bool = True) -> None:
    """Called only after HTTP 400 followed by a validated mobile response."""
    try:
        with cached_sqlite_connection(_CACHE_PATH, writable=True) as conn:
            _ensure_schema(conn)
            if valid:
                conn.execute("INSERT OR REPLACE INTO endpoint_preferences VALUES (?, ?)",
                             (kind, time.time() + 24 * 60 * 60))
            else:
                conn.execute("DELETE FROM endpoint_preferences WHERE kind = ?", (kind,))
            conn.commit()
    except (sqlite3.Error, OSError):
        pass


atexit.register(close_cache_connections)
