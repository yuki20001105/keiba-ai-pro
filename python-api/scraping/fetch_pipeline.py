from __future__ import annotations

import asyncio
import json
import random
import re
import sqlite3
import time
from collections import OrderedDict
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
from threading import Lock, RLock
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from .request_pacing import AccessPaused, RequestPacer, page_kind, site_family

try:
    from app_config import logger  # type: ignore
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

_CACHE_DB_PATH = Path(__file__).parent.parent.parent / "keiba" / "data" / "fetch_cache.db"
_REQUEST_PACER = RequestPacer(_CACHE_DB_PATH.with_name("request_pacing.db"))
_FETCH_CONTEXT: ContextVar[dict | None] = ContextVar("fetch_context", default=None)
_SUMMARY_JSON_PATH = (
    Path(__file__).parent.parent.parent
    / "reports"
    / "generated"
    / "runtime"
    / "fetch_summary.json"
)

_STATE_LOCK = Lock()
_CACHE_INIT_LOCK = Lock()
_CACHE_POOL_LOCK = RLock()
_CACHE_CONNECTIONS: OrderedDict[str, "_CacheConnection"] = OrderedDict()
_LAST_REQUEST_TS: dict[str, float] = {}
_CIRCUIT_UNTIL: dict[str, float] = {}
_FAILURE_COUNTS: dict[str, int] = {}
_LOOP_INFLIGHT: dict[int, dict[str, asyncio.Future]] = {}
_METRICS: dict[str, int] = {
    "network_requests": 0,
    "cache_hits": 0,
    "resume_hits": 0,
    "dedup_waits": 0,
    "dry_run_skips": 0,
    "retry_count": 0,
    "backoff_count": 0,
    "circuit_open_count": 0,
    "status_429": 0,
    "status_403": 0,
    "status_500": 0,
    "status_503": 0,
    "timeout_count": 0,
    "body_limit_count": 0,
    "total_timeout_count": 0,
}


@dataclass
class FetchResult:
    url: str
    normalized_url: str
    status: int
    body: bytes
    source: str
    attempts: int
    error: str | None = None
    response_headers: dict[str, str] = field(default_factory=dict)
    # Stamp of this exact body, not a later metadata lookup that may observe a
    # concurrent refresh. None means no persisted source can be attributed.
    cache_snapshot: dict[str, Any] | None = None


class FetchAccessBlocked(RuntimeError):
    """Provider blocked access; callers must not try another URL or host."""


@contextmanager
def fetch_context(*, job_id: str | None = None, on_wait=None):
    metrics: dict[str, int] = {}
    token = _FETCH_CONTEXT.set({"job_id": job_id, "on_wait": on_wait, "metrics": metrics})
    try:
        yield metrics
    finally:
        _FETCH_CONTEXT.reset(token)


def get_fetch_control_status() -> dict:
    return _REQUEST_PACER.status()


def clear_fetch_block() -> None:
    _REQUEST_PACER.clear_block()


def _on_rate_wait(state: dict) -> None:
    context = _FETCH_CONTEXT.get()
    if context and context.get("on_wait"):
        context["on_wait"](state)


class _CacheConnection:
    """One guarded SQLite handle per path; each operation still commits durably."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = RLock()
        self.connection: sqlite3.Connection | None = None
        self.writable = False
        self.identity: tuple[int, int] | None = None

    @contextmanager
    def use(self, *, write: bool):
        with self.lock:
            try:
                info = self.path.stat()
                identity = (info.st_dev, info.st_ino)
            except FileNotFoundError:
                identity = None
            # Restore/replacement must never send reads or writes to an old file.
            if self.connection is not None and self.identity != identity:
                self.connection.close()
                self.connection = None
                self.writable = False
            if not write and identity is None:
                yield None
                return
            if write and self.connection is not None and not self.writable:
                self.connection.close()
                self.connection = None
            if self.connection is None:
                if write:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    connection = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False)
                    try:
                        connection.execute("PRAGMA journal_mode=WAL")
                        _create_cache_tables(connection)
                        connection.commit()
                    except BaseException:
                        connection.close()
                        raise
                    self.writable = True
                else:
                    connection = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True,
                                                 timeout=10, check_same_thread=False)
                    connection.execute("PRAGMA query_only=ON")
                    self.writable = False
                self.connection = connection
                info = self.path.stat()
                self.identity = (info.st_dev, info.st_ino)
            with self.connection:
                yield self.connection

    def close(self):
        with self.lock:
            if self.connection is not None:
                self.connection.close()
                self.connection = None
                self.identity = None


@contextmanager
def _cache_connection(*, write: bool = False):
    key = str(_CACHE_DB_PATH)
    with _CACHE_POOL_LOCK:
        store = _CACHE_CONNECTIONS.get(key)
        if store is None:
            store = _CacheConnection(_CACHE_DB_PATH)
            _CACHE_CONNECTIONS[key] = store
        _CACHE_CONNECTIONS.move_to_end(key)
        # Bound handles even when maintenance/benchmarks switch among many DBs.
        while len(_CACHE_CONNECTIONS) > 4:
            _, evicted = _CACHE_CONNECTIONS.popitem(last=False)
            evicted.close()
        # Pin the store through use so another thread cannot evict/reopen it
        # outside the bounded pool. There is no await/network I/O under this lock.
        with store.use(write=write) as connection:
            yield connection


def close_fetch_cache_connections() -> None:
    """Release owned handles at controlled shutdown or isolated-test teardown."""
    with _CACHE_POOL_LOCK:
        for store in _CACHE_CONNECTIONS.values():
            store.close()
        _CACHE_CONNECTIONS.clear()


def _init_cache_db() -> None:
    """Create cache storage on the first write, never merely on import/read."""
    with _cache_connection(write=True):
        pass


def _create_cache_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS http_cache (
            normalized_url TEXT PRIMARY KEY,
            final_url TEXT NOT NULL,
            status INTEGER NOT NULL,
            headers_json TEXT NOT NULL,
            body BLOB NOT NULL,
            fetched_at REAL NOT NULL,
            expires_at REAL NOT NULL
        )
        """
    )
    # Cover metadata-only freshness probes; timestamps live after the large
    # HTML BLOB in the table record, so probing the table can touch overflow pages.
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_http_cache_freshness
        ON http_cache (normalized_url, status, fetched_at, expires_at)""")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fetch_resume (
            resume_key TEXT PRIMARY KEY,
            normalized_url TEXT NOT NULL,
            status TEXT NOT NULL,
            source TEXT NOT NULL,
            http_status INTEGER NOT NULL,
            attempts INTEGER NOT NULL,
            updated_at REAL NOT NULL,
            error TEXT
        )
        """
    )


def _open_cache_db_read_only() -> sqlite3.Connection | None:
    """Open an existing cache without creating a database or journal files."""

    if not _CACHE_DB_PATH.is_file():
        return None
    try:
        conn = sqlite3.connect(f"{_CACHE_DB_PATH.resolve().as_uri()}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only=ON")
        return conn
    except sqlite3.Error:
        return None


def _normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    query_sorted = urlencode(sorted(query_pairs))
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query_sorted, ""))


def _parse_retry_after(headers: dict[str, str], max_seconds: float | None = None) -> float:
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if not raw:
        return 0.0
    raw = str(raw).strip()
    if raw.isdigit():
        value = max(0.0, float(raw))
        return min(value, max(0.0, max_seconds)) if max_seconds is not None else value
    try:
        dt = parsedate_to_datetime(raw)
        value = max(0.0, dt.timestamp() - time.time())
        return min(value, max(0.0, max_seconds)) if max_seconds is not None else value
    except Exception:
        return 0.0


def _read_cache(normalized_url: str) -> dict[str, Any] | None:
    now = time.time()
    try:
        with _cache_connection() as conn:
            if conn is None:
                return None
            row = conn.execute(
                """
                SELECT final_url, status, headers_json, body, fetched_at, expires_at
                FROM http_cache
                WHERE normalized_url = ? AND status = 200 AND expires_at >= ?
                """,
                (normalized_url, now),
            ).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    expires_at = float(row[5])
    if expires_at < now:
        return None
    if int(row[1]) != 200 or (site_family(normalized_url) == "netkeiba.com" and _looks_like_access_block(row[3] or b"")):
        return None
    return {
        "url": str(row[0]),
        "status": int(row[1]),
        "headers": json.loads(row[2] or "{}"),
        "body": row[3] or b"",
        "fetched_at": float(row[4]),
        "expires_at": expires_at,
    }


def _write_cache(normalized_url: str, final_url: str, status: int, headers: dict[str, str], body: bytes, ttl_sec: float) -> dict[str, Any]:
    now = time.time()
    expires_at = now + max(1.0, ttl_sec)
    with _cache_connection(write=True) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO http_cache (
                normalized_url, final_url, status, headers_json, body, fetched_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized_url, final_url, int(status), json.dumps(headers, ensure_ascii=False),
                body, now, expires_at,
            ),
        )
    # Return only after the transaction commits, retaining this write's stamp
    # even if another process replaces the source before the caller resumes.
    return {"url": normalized_url, "fetched_at": now, "expires_at": expires_at}


def _read_resume(resume_key: str) -> dict[str, Any] | None:
    try:
        with _cache_connection() as conn:
            if conn is None:
                return None
            row = conn.execute(
                """
                SELECT normalized_url, status, source, http_status, attempts, updated_at, error
                FROM fetch_resume
                WHERE resume_key = ?
                """,
                (resume_key,),
            ).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    return {
        "normalized_url": str(row[0]),
        "status": str(row[1]),
        "source": str(row[2]),
        "http_status": int(row[3]),
        "attempts": int(row[4]),
        "updated_at": float(row[5]),
        "error": row[6],
    }


def _write_resume(
    resume_key: str,
    normalized_url: str,
    status: str,
    source: str,
    http_status: int,
    attempts: int,
    error: str | None,
) -> None:
    with _cache_connection(write=True) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO fetch_resume (
                resume_key, normalized_url, status, source, http_status, attempts, updated_at, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (resume_key, normalized_url, status, source, int(http_status), int(attempts), time.time(), error),
        )


def get_cached_source_metadata(url: str) -> dict | None:
    """Validate a parsed snapshot's source without reading its HTML BLOB.

    Reuse the connection, not the freshness stamp: an external refresh/delete or
    expiry must remain visible to every lookup, including within a running job.
    """
    normalized = _normalize_url(url)
    try:
        with _cache_connection() as conn:
            if conn is None:
                return None
            try:
                row = conn.execute(
                    "SELECT status, fetched_at, expires_at FROM http_cache INDEXED BY idx_http_cache_freshness "
                    "WHERE normalized_url = ?", (normalized,),
                ).fetchone()
            except sqlite3.OperationalError as exc:
                # Old caches remain readable without migrating them on a read.
                if "no such index" not in str(exc):
                    raise
                row = conn.execute(
                    "SELECT status, fetched_at, expires_at FROM http_cache WHERE normalized_url = ?",
                    (normalized,),
                ).fetchone()
        if row and row[0] == 200 and row[2] > time.time():
            return {"url": normalized, "fetched_at": row[1], "expires_at": row[2]}
    except sqlite3.Error:
        pass
    return None


def _metrics_inc(key: str, delta: int = 1) -> None:
    with _STATE_LOCK:
        _METRICS[key] = int(_METRICS.get(key, 0)) + delta
        context = _FETCH_CONTEXT.get()
        if context is not None:
            metrics = context["metrics"]
            metrics[key] = int(metrics.get(key, 0)) + delta


def metric_page_kind(url: str) -> str:
    """Use bounded metric keys, separating calendars without changing pacing."""
    if "calendar" in urlsplit(url).path.lower():
        return "calendar"
    return page_kind(url)


def record_fetch_metric(key: str, delta: int = 1, *, url: str | None = None) -> None:
    """Add a job-local/process metric, optionally attributed to a fetched URL.

    The flat integer keys remain additive in persisted monthly/batch summaries.
    ContextVars are propagated by asyncio tasks and asyncio.to_thread; callers
    using an independent executor must explicitly copy their context.
    """
    _metrics_inc(key, delta)
    if url is not None:
        _metrics_inc(f"by_kind_{metric_page_kind(url)}_{key}", delta)


async def _respect_rate_limit(host: str, min_interval_sec: float) -> None:
    wait_sec = 0.0
    with _STATE_LOCK:
        now = time.monotonic()
        circuit_until = _CIRCUIT_UNTIL.get(host, 0.0)
        if circuit_until > now:
            wait_sec = max(wait_sec, circuit_until - now)
        last_ts = _LAST_REQUEST_TS.get(host, 0.0)
        due = last_ts + max(1.0, min_interval_sec)
        if due > now:
            wait_sec = max(wait_sec, due - now)

    if wait_sec > 0:
        await asyncio.sleep(wait_sec)


def _record_request_start(host: str) -> None:
    with _STATE_LOCK:
        _LAST_REQUEST_TS[host] = time.monotonic()


def _record_failure(host: str, circuit_threshold: int, circuit_cooldown_sec: float) -> None:
    with _STATE_LOCK:
        _FAILURE_COUNTS[host] = int(_FAILURE_COUNTS.get(host, 0)) + 1
        if _FAILURE_COUNTS[host] >= max(1, circuit_threshold):
            _CIRCUIT_UNTIL[host] = time.monotonic() + max(5.0, circuit_cooldown_sec)
            _METRICS["circuit_open_count"] = int(_METRICS.get("circuit_open_count", 0)) + 1


def _record_success(host: str) -> None:
    with _STATE_LOCK:
        _FAILURE_COUNTS[host] = 0


async def _network_fetch(
    session,
    url: str,
    normalized_url: str,
    *,
    min_interval_sec: float,
    max_retries: int,
    retry_base_sec: float,
    retry_jitter_sec: float,
    retry_statuses: set[int],
    circuit_threshold: int,
    circuit_cooldown_sec: float,
    allow_redirects: bool,
    max_body_bytes: int | None,
    max_retry_after_sec: float | None,
    request_headers: dict[str, str] | None = None,
) -> FetchResult:
    family = site_family(normalized_url)
    governed = family == "netkeiba.com"
    current_url = url
    attempts = 0
    redirects = 0
    result = FetchResult(url, normalized_url, 0, b"", "network-error", 0)

    for retry in range(max(1, max_retries)):
        while True:
            owner = None
            heartbeat = None
            admission_started = time.monotonic()
            try:
                if governed:
                    owner, waited = await _REQUEST_PACER.acquire(family, min_interval_sec, _on_rate_wait)
                    _metrics_inc("rate_wait_ms", int(waited * 1000))
                else:
                    await _respect_rate_limit(family, min_interval_sec)
                    _record_request_start(family)
            except AccessPaused as exc:
                record_fetch_metric("admission_block_count", url=current_url)
                return FetchResult(current_url, normalized_url, 403, b"", "access-blocked", attempts, str(exc))
            finally:
                # Sum of each task's admission occupancy, including overlapping
                # waits. This is deliberately NOT an end-to-end wall clock.
                record_fetch_metric("pacing_wait_cumulative_ms",
                                    int((time.monotonic() - admission_started) * 1000), url=current_url)
            if governed:
                heartbeat = asyncio.create_task(_REQUEST_PACER.heartbeat(family, owner))

            attempts += 1
            record_fetch_metric("network_requests", url=current_url)
            if retry > 0:
                # Actual requests in a retry round, unlike retry_count which
                # records a planned retry even if cancelled during backoff.
                record_fetch_metric("network_retry_requests", url=current_url)
            started = time.monotonic()
            headers: dict[str, str] = {}
            status = 0
            observed = False
            request_failed = False
            try:
                kwargs: dict[str, Any] = {}
                if governed or not allow_redirects:
                    kwargs["allow_redirects"] = False
                if request_headers:
                    kwargs["headers"] = request_headers
                async with session.get(current_url, **kwargs) as resp:
                    status = int(resp.status)
                    headers = dict(resp.headers)
                    record_fetch_metric(f"status_{status}", url=current_url)
                    request_failed = status >= 400
                    if governed and status in {401, 403, 429}:
                        # Publish before reading/releasing: queued workers must see this too.
                        _REQUEST_PACER.observe(family, kind=page_kind(current_url), status=status,
                                               retry_after=_parse_retry_after(headers),
                                               blocked=f"http-{status}" if status in {401, 403} else "")
                        observed = True
                    if governed and status in {401, 403}:
                        record_fetch_metric("access_block_count", url=current_url)
                        return FetchResult(current_url, normalized_url, status, b"", "access-blocked", attempts, f"http-{status}")
                    body, too_large = await _read_response_body(resp, max_body_bytes)
                    record_fetch_metric("response_bytes", len(body), url=current_url)
                    if too_large:
                        request_failed = True
                        record_fetch_metric("body_limit_count", url=current_url)
                        if governed and not observed:
                            _REQUEST_PACER.observe(family, kind=page_kind(current_url), failure=True)
                        return FetchResult(current_url, normalized_url, 0, b"", "network-rejected", attempts,
                                           f"response-body-too-large:{max_body_bytes}")
                    if governed and status == 200 and _looks_like_access_block(body):
                        _REQUEST_PACER.observe(family, kind=page_kind(current_url), status=status, blocked="access-restricted-page")
                        request_failed = True
                        record_fetch_metric("access_block_count", url=current_url)
                        return FetchResult(current_url, normalized_url, 403, b"", "access-blocked", attempts, "access-restricted-page")
                    if governed and not observed:
                        _REQUEST_PACER.observe(family, kind=page_kind(current_url), status=status,
                                               latency=time.monotonic() - started, circuit_threshold=circuit_threshold,
                                               retry_after=_parse_retry_after(headers),
                                               circuit_cooldown=circuit_cooldown_sec)
                    elif not governed:
                        if status >= 400:
                            _record_failure(family, circuit_threshold, circuit_cooldown_sec)
                        elif 200 <= status < 300:
                            _record_success(family)
                    if 200 <= status < 300:
                        record_fetch_metric("http_success_count", url=current_url)
                    result = FetchResult(current_url, normalized_url, status, body, "network", attempts, response_headers=headers)
            except asyncio.TimeoutError:
                request_failed = True
                record_fetch_metric("timeout_count", url=current_url)
                result = FetchResult(current_url, normalized_url, 0, b"", "network-error", attempts, "timeout")
                if governed and not observed:
                    _REQUEST_PACER.observe(family, kind=page_kind(current_url), failure=True,
                                           circuit_threshold=circuit_threshold, circuit_cooldown=circuit_cooldown_sec)
                elif not governed:
                    _record_failure(family, circuit_threshold, circuit_cooldown_sec)
            except asyncio.CancelledError:
                request_failed = True
                record_fetch_metric("cancelled_requests", url=current_url)
                raise
            except Exception as exc:
                request_failed = True
                record_fetch_metric("transport_error_count", url=current_url)
                result = FetchResult(current_url, normalized_url, 0, b"", "network-error", attempts, f"{type(exc).__name__}: {exc}")
                if governed and not observed:
                    _REQUEST_PACER.observe(family, kind=page_kind(current_url), failure=True,
                                           circuit_threshold=circuit_threshold, circuit_cooldown=circuit_cooldown_sec)
                elif not governed:
                    _record_failure(family, circuit_threshold, circuit_cooldown_sec)
            finally:
                active_ms = int((time.monotonic() - started) * 1000)
                _metrics_inc("response_time_ms", active_ms)
                record_fetch_metric("network_active_cumulative_ms", active_ms, url=current_url)
                if request_failed:
                    record_fetch_metric("request_failure_count", url=current_url)
                try:
                    if heartbeat:
                        heartbeat.cancel()
                        with suppress(asyncio.CancelledError):
                            await heartbeat
                finally:
                    if owner is not None:
                        _REQUEST_PACER.release(family, owner)

            if governed and allow_redirects and result.status in {301, 302, 303, 307, 308}:
                location = headers.get("Location") or headers.get("location")
                target = urljoin(current_url, location or "")
                parsed = urlsplit(target)
                if not location or redirects >= 5 or site_family(target) != family or parsed.scheme not in {"http", "https"} or parsed.username:
                    return FetchResult(current_url, normalized_url, result.status, b"", "network-rejected", attempts, "unsafe-or-excessive-redirect")
                redirects += 1
                record_fetch_metric("redirect_count", url=current_url)
                current_url = target
                continue  # Every redirect needs its own admission and attempt count.
            break

        if (result.status not in retry_statuses and result.status != 0) or retry + 1 >= max_retries:
            return result
        record_fetch_metric("retry_count", url=current_url)
        _metrics_inc("backoff_count")
        backoff = retry_base_sec * (2 ** retry) + random.uniform(0, max(0.0, retry_jitter_sec))
        retry_after = _parse_retry_after(headers, None if governed else max_retry_after_sec)
        # 429 is waited by the shared controller; never hold a network lease while sleeping.
        delay = backoff if governed and result.status == 429 else max(backoff, retry_after)
        while delay > 0:
            if governed:
                _on_rate_wait(_REQUEST_PACER.status(family))
            step = min(1.0, delay)
            await (_REQUEST_PACER.sleep(step) if governed else asyncio.sleep(step))
            delay -= step
    return result


def _looks_like_access_block(body: bytes) -> bool:
    text = _decode_text_body(body[:65536]).lower()
    title = re.search(r"<title[^>]*>(.*?)</title>", text, re.DOTALL)
    title_text = title.group(1) if title else ""
    return any(marker in title_text for marker in ("access denied", "just a moment", "attention required", "アクセス制限")) or any(
        marker in text for marker in ("cf-chl-", "verify you are human", "アクセスが制限されています", "アクセス制限がかかっています", "ただいまアクセスが集中しております")
    )


async def _read_response_body(resp: Any, max_body_bytes: int | None) -> tuple[bytes, bool]:
    """Read at most ``max_body_bytes + 1`` decompressed response bytes."""

    if max_body_bytes is None:
        return bytes(await resp.read()), False

    limit = max(0, int(max_body_bytes))
    content = getattr(resp, "content", None)
    if content is not None and hasattr(content, "iter_chunked"):
        chunks: list[bytes] = []
        size = 0
        async for raw_chunk in content.iter_chunked(min(64 * 1024, limit + 1)):
            chunk = bytes(raw_chunk)
            remaining = (limit + 1) - size
            if remaining <= 0:
                break
            chunks.append(chunk[:remaining])
            size += min(len(chunk), remaining)
            if size > limit:
                return b"", True
        body = b"".join(chunks)
    else:
        # Compatibility fallback for minimal response doubles. Real aiohttp
        # responses take the bounded streaming branch above.
        body = bytes(await resp.read())

    if len(body) > limit:
        return b"", True
    return body, False


def _lookup_cached_fetch(normalized_url: str, resume_key: str | None, use_cache: bool):
    """Read each completion marker/body once in one thread-pool dispatch."""
    resume_row = _read_resume(resume_key) if resume_key else None
    resumed = bool(resume_row and str(resume_row.get("status")) == "success")
    cached = _read_cache(normalized_url) if use_cache or resumed else None
    return resume_row, cached


def _cached_body_snapshot(normalized_url: str, cached: dict[str, Any]) -> dict[str, Any] | None:
    # Legacy/minimal cache doubles may not provide timestamps. Never fill that
    # gap with a second SQL query: it could describe a different response body.
    if "fetched_at" not in cached or "expires_at" not in cached:
        return None
    return {"url": normalized_url, "fetched_at": cached["fetched_at"],
            "expires_at": cached["expires_at"]}


async def fetch_bytes(
    session,
    url: str,
    *,
    cache_ttl_sec: float = 12 * 60 * 60,
    use_cache: bool = True,
    force_refresh: bool = False,
    dry_run: bool = False,
    resume_key: str | None = None,
    min_interval_sec: float = 1.0,
    max_retries: int = 3,
    retry_base_sec: float = 2.0,
    retry_jitter_sec: float = 0.7,
    retry_statuses: set[int] | None = None,
    circuit_threshold: int = 3,
    circuit_cooldown_sec: float = 90.0,
    allow_redirects: bool = True,
    max_body_bytes: int | None = None,
    max_retry_after_sec: float | None = None,
    total_timeout_sec: float | None = None,
    request_headers: dict[str, str] | None = None,
) -> FetchResult:
    normalized_url = _normalize_url(url)

    if retry_statuses is None:
        retry_statuses = {429, 500, 502, 503, 504}

    resume_row, cached = None, None
    if not force_refresh and (resume_key or use_cache):
        resume_row, cached = await asyncio.to_thread(
            _lookup_cached_fetch, normalized_url, resume_key, use_cache,
        )

    if resume_row and str(resume_row.get("status")) == "success":
        # A resume row is completion metadata, not a cached response body.  The
        # old implementation returned HTTP 200 with body=b"" forever after the
        # first successful fetch, which prevented mutable race/odds pages from
        # ever being refreshed.  A resume hit is safe only while the matching
        # HTTP cache entry (including its body and TTL) is still valid.
        if cached is not None:
            _metrics_inc("resume_hits", 1)
            record_fetch_metric("cache_reuse_hits", url=normalized_url)
            record_fetch_metric("resume_cache_reuse_hits", url=normalized_url)
            return FetchResult(
                url=str(cached["url"]),
                normalized_url=normalized_url,
                status=int(cached["status"]),
                body=bytes(cached["body"]),
                source="resume-cache",
                attempts=int(resume_row.get("attempts") or 1),
                response_headers=dict(cached.get("headers") or {}),
                cache_snapshot=_cached_body_snapshot(normalized_url, cached),
            )

    if use_cache and cached is not None:
        record_fetch_metric("cache_hits", url=normalized_url)
        record_fetch_metric("cache_reuse_hits", url=normalized_url)
        if resume_key:
            await asyncio.to_thread(
                _write_resume,
                resume_key,
                normalized_url,
                "success",
                "cache",
                int(cached["status"]),
                1,
                None,
            )
        return FetchResult(
            url=str(cached["url"]),
            normalized_url=normalized_url,
            status=int(cached["status"]),
            body=bytes(cached["body"]),
            source="cache",
            attempts=1,
            response_headers=dict(cached.get("headers") or {}),
            cache_snapshot=_cached_body_snapshot(normalized_url, cached),
        )

    # A resume row proves that a previous request completed, but it does not
    # contain the response body.  Returning an empty body here used to make
    # parsers report missing tables after a successful prior fetch.  Count the
    # resume marker for observability, then continue to a real fetch whenever
    # no usable cached body exists.
    if resume_row and str(resume_row.get("status")) == "success":
        _metrics_inc("resume_hits", 1)
        record_fetch_metric("resume_metadata_only_hits", url=normalized_url)

    if dry_run:
        record_fetch_metric("dry_run_skips", url=normalized_url)
        return FetchResult(
            url=url,
            normalized_url=normalized_url,
            status=0,
            body=b"",
            source="dry-run",
            attempts=0,
        )

    loop = asyncio.get_running_loop()
    loop_key = id(loop)
    # Different safety/timeout/header policies must not share an unrestricted result.
    inflight_key = repr((normalized_url, allow_redirects, max_body_bytes, total_timeout_sec,
                         max_retries, max_retry_after_sec, min_interval_sec,
                         tuple(sorted(retry_statuses)), retry_base_sec, retry_jitter_sec,
                         tuple(sorted((request_headers or {}).items()))))

    with _STATE_LOCK:
        inflight = _LOOP_INFLIGHT.setdefault(loop_key, {})
        existing = inflight.get(inflight_key)
        if existing is not None:
            waiter = existing
        else:
            waiter = loop.create_future()
            inflight[inflight_key] = waiter

    if existing is not None:
        record_fetch_metric("dedup_waits", url=normalized_url)
        # Cancelling a follower must not cancel the producer's shared future.
        result = await asyncio.shield(waiter)
        if result.status == 200 and result.body:
            record_fetch_metric("dedup_reuse_hits", url=normalized_url)
        return result

    try:
        network_fetch = _network_fetch(
            session,
            url,
            normalized_url,
            min_interval_sec=min_interval_sec,
            max_retries=max_retries,
            retry_base_sec=retry_base_sec,
            retry_jitter_sec=retry_jitter_sec,
            retry_statuses=retry_statuses,
            circuit_threshold=circuit_threshold,
            circuit_cooldown_sec=circuit_cooldown_sec,
            allow_redirects=allow_redirects,
            max_body_bytes=max_body_bytes,
            max_retry_after_sec=max_retry_after_sec,
            request_headers=request_headers,
        )
        try:
            if total_timeout_sec is None:
                result = await network_fetch
            else:
                result = await asyncio.wait_for(network_fetch, timeout=max(0.001, float(total_timeout_sec)))
        except asyncio.TimeoutError:
            _metrics_inc("total_timeout_count", 1)
            result = FetchResult(
                url=url,
                normalized_url=normalized_url,
                status=0,
                body=b"",
                source="network-timeout",
                attempts=max(1, max_retries),
                error="total-timeout",
            )

        if use_cache and result.status == 200 and result.body:
            result.cache_snapshot = await asyncio.to_thread(
                _write_cache,
                normalized_url,
                result.url,
                result.status,
                result.response_headers,
                result.body,
                cache_ttl_sec,
            )

        if resume_key:
            await asyncio.to_thread(
                _write_resume,
                resume_key,
                normalized_url,
                "success" if result.status == 200 else "error",
                result.source,
                result.status,
                result.attempts,
                result.error,
            )

        if not waiter.done():
            waiter.set_result(result)
        return result
    except BaseException as exc:
        # Producer cancellation also resolves every follower, rather than leaving
        # a never-completed future. Caller wait/cancel callbacks propagate intact.
        if not waiter.done():
            if isinstance(exc, asyncio.CancelledError):
                waiter.cancel()
            else:
                waiter.set_exception(exc)
                waiter.exception()  # Avoid an unobserved warning when no follower exists.
        raise
    finally:
        with _STATE_LOCK:
            inflight = _LOOP_INFLIGHT.get(loop_key, {})
            inflight.pop(inflight_key, None)
            if not inflight:
                _LOOP_INFLIGHT.pop(loop_key, None)


_CHARSET_RE = re.compile(br"charset\s*=\s*[\"']?\s*([a-zA-Z0-9._-]+)", re.IGNORECASE)


def _decode_text_body(body: bytes) -> str:
    """Decode Japanese race pages without assuming their historical encoding.

    netkeiba currently serves race.netkeiba.com as UTF-8 while older database
    pages and cached fixtures may still be EUC-JP.  Detect an HTML charset first,
    then use strict fallbacks so UTF-8 text is never silently mojibaked as EUC-JP.
    """

    if not body:
        return ""

    head = body[:8192]
    match = _CHARSET_RE.search(head)
    declared = match.group(1).decode("ascii", errors="ignore").lower() if match else ""
    aliases = {
        "utf8": "utf-8",
        "shift_jis": "cp932",
        "shift-jis": "cp932",
        "sjis": "cp932",
        "x-sjis": "cp932",
        "eucjp": "euc-jp",
    }
    candidates = [aliases.get(declared, declared)] if declared else []
    candidates.extend(["utf-8-sig", "euc-jp", "cp932"])

    seen: set[str] = set()
    for encoding in candidates:
        if not encoding or encoding in seen:
            continue
        seen.add(encoding)
        try:
            return body.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def decode_html_body(body: bytes) -> str:
    """Public compatibility wrapper for charset-aware HTML decoding."""
    return _decode_text_body(body)


async def fetch_text(session, url: str, **kwargs: Any) -> tuple[FetchResult, str]:
    result = await fetch_bytes(session, url, **kwargs)
    if result.source == "access-blocked":
        raise FetchAccessBlocked(result.error or "netkeiba-access-blocked")
    return result, _decode_text_body(result.body)


def get_fetch_metrics(reset: bool = False) -> dict[str, int]:
    with _STATE_LOCK:
        context = _FETCH_CONTEXT.get()
        source = context["metrics"] if context is not None else _METRICS
        metrics = {k: int(v) for k, v in source.items()}
        if reset:
            for key in list(source.keys()):
                source[key] = 0
    return metrics


def estimate_fetch_plan(urls: list[str], resume_keys: list[str] | None = None) -> dict[str, Any]:
    unique_urls = list(dict.fromkeys(_normalize_url(u) for u in urls if u))
    resume_by_url: dict[str, str] = {}
    if resume_keys:
        for raw_url, key in zip(urls, resume_keys):
            if raw_url and key:
                resume_by_url.setdefault(_normalize_url(raw_url), key)

    cache_hits = 0
    resume_hits = 0
    for normalized in unique_urls:
        if _read_cache(normalized) is None:
            continue
        resume_key = resume_by_url.get(normalized)
        resume_row = _read_resume(resume_key) if resume_key else None
        if resume_row and str(resume_row.get("status")) == "success":
            resume_hits += 1
        else:
            cache_hits += 1

    estimated_network = max(0, len(unique_urls) - cache_hits - resume_hits)
    return {
        "total_input_urls": len(urls),
        "unique_urls": len(unique_urls),
        "cache_hits": cache_hits,
        "resume_hits": resume_hits,
        "estimated_network_requests": estimated_network,
    }


def write_fetch_summary(summary: dict[str, Any], output_path: Path | None = None) -> Path:
    path = output_path or _SUMMARY_JSON_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
