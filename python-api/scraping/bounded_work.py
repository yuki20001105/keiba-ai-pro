"""Bounded local acquisition overlap without changing provider HTTP pacing."""
from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Iterable
from typing import TypeVar


_T = TypeVar("_T")
DEFAULT_RACE_CONCURRENCY = 3
MAX_RACE_CONCURRENCY = 4


def race_task_concurrency() -> int:
    """Reserve headroom for the UI/forward collector; never control HTTP rate."""
    try:
        configured = int(os.environ.get("SCRAPE_RACE_CONCURRENCY", DEFAULT_RACE_CONCURRENCY))
    except (TypeError, ValueError, OverflowError):
        configured = DEFAULT_RACE_CONCURRENCY
    return min(MAX_RACE_CONCURRENCY, max(1, configured))


async def drain_acquisition_batch(
    work: Iterable[Awaitable[_T]], *, on_stop: Callable[[], None],
) -> list[_T]:
    """Finish every already-started atomic write before returning any failure.

    Cancelling ``asyncio.to_thread`` cannot cancel its SQLite transaction. A
    sibling failure signals cooperative stop instead, and shielded draining
    keeps its race-save + quality-ledger checkpoint attached to this batch.
    Provider pacing remains owned by the unchanged shared request scheduler.
    """
    failures: list[BaseException] = []

    async def run(item: Awaitable[_T]) -> _T:
        try:
            return await item
        except BaseException as exc:
            failures.append(exc)
            on_stop()
            raise

    pending = asyncio.gather(*(run(item) for item in work), return_exceptions=True)
    cancellation: asyncio.CancelledError | None = None
    while True:
        try:
            results = await asyncio.shield(pending)
            break
        except asyncio.CancelledError as exc:
            # Repeated cancellation must not orphan a thread-backed write.
            cancellation = cancellation or exc
            on_stop()
            if pending.done():
                results = pending.result()
                break
    if cancellation is not None:
        raise cancellation
    if failures:
        # The original access block must not be relabelled as the cooperative
        # stop that a sibling observes afterwards (input ordering is irrelevant).
        raise failures[0]
    return results
