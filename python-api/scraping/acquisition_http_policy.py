"""Opt-in safety for ordinary acquisition; frozen transports remain unchanged.

Only the job-owned context opts in. Cache/provenance/metrics and the existing
cross-process pacer stay authoritative; unscoped forward collection delegates
unchanged. A real HTTP 400 is published to that pacer before reading its body
or releasing its lease, and is raised before a parser can choose a fallback.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import math
import sys
from typing import Any

from . import fetch_pipeline as base
from .request_pacing import page_kind, site_family


SAFETY_POLICY_VERSION = "normal-acquisition-safe-v1"
MINIMUM_PROVIDER_INTERVAL_SECONDS = 3.0


@dataclass
class _PolicyState:
    # Child tasks and asyncio.to_thread inherit the same latch. A nested scope
    # must not clear a stop already observed by one of its siblings.
    halt_reason: str = ""


_POLICY: ContextVar[_PolicyState | None] = ContextVar("acquisition_http_policy", default=None)


def acquisition_policy_active() -> bool:
    """Whether this task opted into ordinary acquisition safeguards."""
    return _POLICY.get() is not None


@contextmanager
def acquisition_http_policy():
    current = _POLICY.get()
    if current is not None:
        yield
        return
    token = _POLICY.set(_PolicyState())
    try:
        yield
    finally:
        _POLICY.reset(token)


def acquisition_safety_capabilities() -> dict[str, Any]:
    # Advertise preservation only with the matching implemented storage guard.
    from .storage_preservation import PRESERVATION_POLICY_VERSION

    return {
        "safety_policy_version": SAFETY_POLICY_VERSION,
        "minimum_provider_interval_seconds": MINIMUM_PROVIDER_INTERVAL_SECONDS,
        "halt_http400": True,
        "preserve_saved_values": PRESERVATION_POLICY_VERSION == SAFETY_POLICY_VERSION,
        "max_concurrent_http_requests": 1,
    }


def _ensure_running(state: _PolicyState) -> None:
    if state.halt_reason:
        raise base.FetchAccessBlocked(state.halt_reason)


class _EmptyContent:
    async def iter_chunked(self, _size):
        # Async generator, including when the frozen reader uses a body cap.
        if False:
            yield b""


class _StoppedResponse:
    """Preserve real status/headers/URL, but never consume a rejected body."""
    content = _EmptyContent()

    def __init__(self, response):
        self._response = response

    def __getattr__(self, name):
        return getattr(self._response, name)

    async def read(self):
        return b""


class _ScopedRequest:
    def __init__(self, session, request, url):
        self._session, self._request, self._url = session, request, url

    async def __aenter__(self):
        response = await self._request.__aenter__()
        try:
            if int(response.status) == 400:
                self._session._state.halt_reason = "http-400"
                # The lease is still held by frozen _network_fetch. Publish
                # immediately so other processes cannot start after release.
                # Never clear an earlier provider block or a Retry-After wait.
                pacer = base._REQUEST_PACER
                previous = pacer.status("netkeiba.com").get("blocked_reason")
                pacer.observe(
                    "netkeiba.com", kind=page_kind(self._url), status=400,
                    blocked=previous or "http-400",
                    retry_after=base._parse_retry_after(dict(response.headers)),
                )
                return _StoppedResponse(response)
            return response
        except BaseException:
            # Python does not call __aexit__ when __aenter__ fails. Still close
            # the real response; a cleanup error must not silently disappear.
            try:
                await self._request.__aexit__(*sys.exc_info())
            except BaseException as cleanup_error:
                self._session.cleanup_error = cleanup_error
                raise
            raise

    async def __aexit__(self, *exc):
        try:
            return await self._request.__aexit__(*exc)
        except BaseException as cleanup_error:
            self._session.cleanup_error = cleanup_error
            raise


class ScopedSession:
    """Per-call proxy, never a monkeypatch of aiohttp or the frozen module."""
    def __init__(self, session, state: _PolicyState):
        self._session, self._state = session, state
        self.cleanup_error: BaseException | None = None

    def __getattr__(self, name):
        return getattr(self._session, name)

    def get(self, url, **kwargs):
        _ensure_running(self._state)
        return _ScopedRequest(self, self._session.get(url, **kwargs), url)


def _safe_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    result = dict(kwargs)
    minimum = result.get("min_interval_sec", 1.0)
    if isinstance(minimum, bool) or not isinstance(minimum, (int, float)) or not math.isfinite(minimum):
        raise ValueError("acquisition-minimum-interval-invalid")
    result["min_interval_sec"] = max(MINIMUM_PROVIDER_INTERVAL_SECONDS, minimum)
    if result.get("retry_statuses") is not None:
        result["retry_statuses"] = set(result["retry_statuses"]) - {400}
    return result


async def _call(fetcher, session, url, kwargs):
    state = _POLICY.get()
    if state is None or site_family(url) != "netkeiba.com":
        return await fetcher(session, url, **kwargs)
    _ensure_running(state)
    proxy = ScopedSession(session, state)
    try:
        result = await fetcher(proxy, url, **_safe_kwargs(kwargs))
    except BaseException:
        if proxy.cleanup_error is not None:
            raise proxy.cleanup_error
        raise
    # The frozen fetcher catches transport exceptions. Do not hide a real
    # response-cleanup failure behind its generic network-error envelope.
    if proxy.cleanup_error is not None:
        raise proxy.cleanup_error
    response = result[0] if isinstance(result, tuple) else result
    if response.status == 400:
        # Normally the header observer set this. Covers compatible test/dry
        # transports as well, without treating an error as a valid cache hit.
        state.halt_reason = "http-400"
    _ensure_running(state)
    return result


async def fetch_text(session, url: str, **kwargs):
    return await _call(base.fetch_text, session, url, kwargs)


async def fetch_bytes(session, url: str, **kwargs):
    return await _call(base.fetch_bytes, session, url, kwargs)


async def fetch_source_text(session, url: str, **kwargs):
    # Horse parsing retains the exact existing body-bound source metadata.
    from .parsed_cache import fetch_source_text as collect_source_text

    return await _call(collect_source_text, session, url, kwargs)
