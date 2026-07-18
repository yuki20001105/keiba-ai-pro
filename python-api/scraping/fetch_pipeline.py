from __future__ import annotations

import asyncio
import json
import random
import sqlite3
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

try:
    from app_config import logger  # type: ignore
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

_CACHE_DB_PATH = Path(__file__).parent.parent.parent / "keiba" / "data" / "fetch_cache.db"
_SUMMARY_JSON_PATH = Path(__file__).parent.parent.parent / "reports" / "fetch_summary.json"

_STATE_LOCK = Lock()
_CACHE_INIT_LOCK = Lock()
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


def _init_cache_db() -> None:
    """Create cache storage on the first write, never merely on import/read."""

    with _CACHE_INIT_LOCK:
        _CACHE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_CACHE_DB_PATH))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
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
            conn.commit()
        finally:
            conn.close()


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
    conn = _open_cache_db_read_only()
    if conn is None:
        return None
    try:
        row = conn.execute(
            """
            SELECT final_url, status, headers_json, body, fetched_at, expires_at
            FROM http_cache
            WHERE normalized_url = ?
            """,
            (normalized_url,),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if not row:
        return None
    expires_at = float(row[5])
    if expires_at < now:
        return None
    return {
        "url": str(row[0]),
        "status": int(row[1]),
        "headers": json.loads(row[2] or "{}"),
        "body": row[3] or b"",
        "fetched_at": float(row[4]),
        "expires_at": expires_at,
    }


def _write_cache(normalized_url: str, final_url: str, status: int, headers: dict[str, str], body: bytes, ttl_sec: float) -> None:
    now = time.time()
    expires_at = now + max(1.0, ttl_sec)
    _init_cache_db()
    conn = sqlite3.connect(str(_CACHE_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        INSERT OR REPLACE INTO http_cache (
            normalized_url, final_url, status, headers_json, body, fetched_at, expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            normalized_url,
            final_url,
            int(status),
            json.dumps(headers, ensure_ascii=False),
            body,
            now,
            expires_at,
        ),
    )
    conn.commit()
    conn.close()


def _read_resume(resume_key: str) -> dict[str, Any] | None:
    conn = _open_cache_db_read_only()
    if conn is None:
        return None
    try:
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
    finally:
        conn.close()
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
    _init_cache_db()
    conn = sqlite3.connect(str(_CACHE_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        INSERT OR REPLACE INTO fetch_resume (
            resume_key, normalized_url, status, source, http_status, attempts, updated_at, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (resume_key, normalized_url, status, source, int(http_status), int(attempts), time.time(), error),
    )
    conn.commit()
    conn.close()


def _metrics_inc(key: str, delta: int = 1) -> None:
    with _STATE_LOCK:
        _METRICS[key] = int(_METRICS.get(key, 0)) + delta


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
) -> FetchResult:
    host = urlsplit(normalized_url).netloc
    last_error: str | None = None

    for attempt in range(1, max(1, max_retries) + 1):
        await _respect_rate_limit(host, min_interval_sec)
        _record_request_start(host)

        try:
            # Keep the legacy call shape for default callers and simple test
            # sessions. Safety-sensitive callers explicitly disable redirects.
            request = session.get(url) if allow_redirects else session.get(url, allow_redirects=False)
            async with request as resp:
                status = int(resp.status)
                headers = {k: v for k, v in resp.headers.items()}
                body, body_too_large = await _read_response_body(resp, max_body_bytes)
                _metrics_inc("network_requests", 1)
                if status == 429:
                    _metrics_inc("status_429", 1)
                elif status == 403:
                    _metrics_inc("status_403", 1)
                elif status == 500:
                    _metrics_inc("status_500", 1)
                elif status == 503:
                    _metrics_inc("status_503", 1)

                if body_too_large:
                    _metrics_inc("body_limit_count", 1)
                    return FetchResult(
                        url=url,
                        normalized_url=normalized_url,
                        status=0,
                        body=b"",
                        source="network-rejected",
                        attempts=attempt,
                        error=f"response-body-too-large:{max_body_bytes}",
                    )

                if status in retry_statuses and attempt < max_retries:
                    _record_failure(host, circuit_threshold, circuit_cooldown_sec)
                    retry_after = _parse_retry_after(headers, max_retry_after_sec)
                    backoff = retry_base_sec * (2 ** (attempt - 1)) + random.uniform(0.0, max(0.0, retry_jitter_sec))
                    wait_sec = max(retry_after, backoff)
                    _metrics_inc("retry_count", 1)
                    _metrics_inc("backoff_count", 1)
                    logger.warning(
                        f"fetch retry status={status} attempt={attempt}/{max_retries} url={url} wait={wait_sec:.2f}s"
                    )
                    await asyncio.sleep(wait_sec)
                    continue

                if status in retry_statuses:
                    _record_failure(host, circuit_threshold, circuit_cooldown_sec)
                else:
                    _record_success(host)

                return FetchResult(
                    url=url,
                    normalized_url=normalized_url,
                    status=status,
                    body=body,
                    source="network",
                    attempts=attempt,
                )
        except asyncio.TimeoutError:
            _metrics_inc("timeout_count", 1)
            last_error = "timeout"
            if attempt < max_retries:
                _record_failure(host, circuit_threshold, circuit_cooldown_sec)
                backoff = retry_base_sec * (2 ** (attempt - 1)) + random.uniform(0.0, max(0.0, retry_jitter_sec))
                _metrics_inc("retry_count", 1)
                _metrics_inc("backoff_count", 1)
                await asyncio.sleep(backoff)
                continue
        except Exception as e:  # pragma: no cover - network stack dependent
            last_error = f"{type(e).__name__}: {e}"
            if attempt < max_retries:
                _record_failure(host, circuit_threshold, circuit_cooldown_sec)
                backoff = retry_base_sec * (2 ** (attempt - 1)) + random.uniform(0.0, max(0.0, retry_jitter_sec))
                _metrics_inc("retry_count", 1)
                _metrics_inc("backoff_count", 1)
                await asyncio.sleep(backoff)
                continue

    return FetchResult(
        url=url,
        normalized_url=normalized_url,
        status=0,
        body=b"",
        source="network-error",
        attempts=max(1, max_retries),
        error=last_error or "network-error",
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
) -> FetchResult:
    normalized_url = _normalize_url(url)

    if retry_statuses is None:
        retry_statuses = {429, 500, 502, 503, 504}

    if resume_key:
        resume_row = await asyncio.to_thread(_read_resume, resume_key)
        if resume_row and str(resume_row.get("status")) == "success" and not force_refresh:
            _metrics_inc("resume_hits", 1)
            return FetchResult(
                url=url,
                normalized_url=normalized_url,
                status=int(resume_row.get("http_status") or 200),
                body=b"",
                source="resume",
                attempts=int(resume_row.get("attempts") or 1),
            )

    if use_cache and not force_refresh:
        cached = await asyncio.to_thread(_read_cache, normalized_url)
        if cached is not None:
            _metrics_inc("cache_hits", 1)
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
            )

    if dry_run:
        _metrics_inc("dry_run_skips", 1)
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

    with _STATE_LOCK:
        inflight = _LOOP_INFLIGHT.setdefault(loop_key, {})
        existing = inflight.get(normalized_url)
        if existing is not None:
            _METRICS["dedup_waits"] = int(_METRICS.get("dedup_waits", 0)) + 1
            waiter = existing
        else:
            waiter = loop.create_future()
            inflight[normalized_url] = waiter

    if existing is not None:
        result = await waiter
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
            await asyncio.to_thread(
                _write_cache,
                normalized_url,
                result.url,
                result.status,
                {},
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

        waiter.set_result(result)
        return result
    except Exception as e:  # pragma: no cover - defensive
        err_result = FetchResult(
            url=url,
            normalized_url=normalized_url,
            status=0,
            body=b"",
            source="pipeline-error",
            attempts=max(1, max_retries),
            error=f"{type(e).__name__}: {e}",
        )
        waiter.set_result(err_result)
        return err_result
    finally:
        with _STATE_LOCK:
            inflight = _LOOP_INFLIGHT.get(loop_key, {})
            inflight.pop(normalized_url, None)


async def fetch_text(session, url: str, **kwargs: Any) -> tuple[FetchResult, str]:
    result = await fetch_bytes(session, url, **kwargs)
    text = ""
    if result.body:
        text = result.body.decode("euc-jp", errors="replace")
    return result, text


def get_fetch_metrics(reset: bool = False) -> dict[str, int]:
    with _STATE_LOCK:
        metrics = {k: int(v) for k, v in _METRICS.items()}
        if reset:
            for key in list(_METRICS.keys()):
                _METRICS[key] = 0
    return metrics


def estimate_fetch_plan(urls: list[str], resume_keys: list[str] | None = None) -> dict[str, Any]:
    unique_urls = list(dict.fromkeys(_normalize_url(u) for u in urls if u))
    cache_hits = 0
    for normalized in unique_urls:
        if _read_cache(normalized) is not None:
            cache_hits += 1

    resume_hits = 0
    if resume_keys:
        for key in resume_keys:
            row = _read_resume(key)
            if row and str(row.get("status")) == "success":
                resume_hits += 1

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
