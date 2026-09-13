"""Reparse valid historical HTML before spending a repair HTTP request."""
from __future__ import annotations

import sqlite3
import time
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable

from scraping import fetch_pipeline
from scraping.quality import classify_race_quality


def has_cached_result(race_id: str) -> bool:
    """Inspect metadata only: never load a large HTML body just to plan work."""
    try:
        with fetch_pipeline._cache_connection() as connection:
            if connection is None:
                return False
            urls = [f"https://{host}/race/{race_id}/" for host in (
                "db.netkeiba.com", "db.sp.netkeiba.com",
            )]
            return connection.execute(
                "SELECT 1 FROM http_cache WHERE normalized_url IN (?,?) "
                "AND status=200 AND expires_at>=? AND length(body)>0 LIMIT 1",
                (*urls, time.time()),
            ).fetchone() is not None
    except sqlite3.Error:
        return False


async def fetch_acquisition_race(
    session: Any,
    race_id: str,
    *,
    date_hint: str,
    fetcher: Callable[..., Awaitable[dict | None]],
    today: date | None = None,
) -> dict | None:
    """Keep live snapshots fresh and use only quality-complete cached history."""
    try:
        target = datetime.strptime(date_hint, "%Y%m%d").date()
        historical = target < (today or date.today()) - timedelta(days=30)
    except ValueError:
        historical = False
    import asyncio

    cache_available = historical and await asyncio.to_thread(has_cached_result, race_id)
    if cache_available:
        candidate = await fetcher(
            session, race_id, date_hint=date_hint, quick_mode=True, force_refresh=False,
        )
        quality = classify_race_quality(candidate)
        if quality.valid_for_date_completion and all(
            value in {"available", "available_with_domain_exceptions"}
            for value in quality.field_states.values()
        ):
            return candidate
    # A partial cached page must not make a repair silently stall forever.
    return await fetcher(
        session, race_id, date_hint=date_hint, quick_mode=True, force_refresh=True,
    )
