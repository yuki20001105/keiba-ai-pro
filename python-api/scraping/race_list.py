"""Read-only netkeiba race-list retrieval.

Race-list requests used to be proxied to a separate port-8001 service. That
service is no longer part of the repository. Keeping this small read-only
operation in the existing scraping package makes local and hosted startup use
the same single FastAPI process.
"""
from __future__ import annotations

import re
from datetime import date as date_type

import aiohttp
from bs4 import BeautifulSoup

from .constants import get_random_headers
from .fetch_pipeline import fetch_text


def extract_race_ids(html: str) -> list[str]:
    """Extract unique 12-digit race IDs in document order."""
    found: list[str] = list(re.findall(r"/race/(\d{12})/", html or ""))

    # Current/future cards use query-string links such as shutuba.html?race_id=.
    soup = BeautifulSoup(html or "", "lxml")
    for anchor in soup.find_all("a", href=True):
        match = re.search(r"(?:[?&]race_id=)(\d{12})(?:&|$)", str(anchor["href"]))
        if match:
            found.append(match.group(1))

    return list(dict.fromkeys(found))


async def fetch_race_ids(date_str: str) -> tuple[list[str], str]:
    """Fetch a race list without writing application data."""
    target_date = date_type(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))
    # Keep date validation explicit, but do not restrict the current list
    # endpoint to recent cards. The legacy db endpoint now returns HTTP 400 for
    # some valid historical dates while race_list_sub still serves them.
    _ = target_date

    timeout = aiohttp.ClientTimeout(total=25, connect=8)
    connector = aiohttp.TCPConnector(limit=2, limit_per_host=1)
    statuses: list[int] = []

    async with aiohttp.ClientSession(
        headers=get_random_headers(), timeout=timeout, connector=connector
    ) as session:
        db_result, db_html = await fetch_text(
            session,
            f"https://db.netkeiba.com/race/list/{date_str}/",
            cache_ttl_sec=12 * 60 * 60,
            resume_key=f"race-list:{date_str}:db",
            min_interval_sec=1.0,
            max_retries=2,
            retry_statuses={429, 500, 503},
            retry_base_sec=2.0,
            retry_jitter_sec=0.6,
            circuit_threshold=3,
            circuit_cooldown_sec=120.0,
        )
        statuses.append(db_result.status)
        if db_result.status == 200:
            race_ids = extract_race_ids(db_html)
            if race_ids:
                return race_ids, "db.netkeiba.com"

        sub_result, sub_html = await fetch_text(
            session,
            (
                "https://race.netkeiba.com/top/race_list_sub.html"
                f"?kaisai_date={date_str}"
            ),
            cache_ttl_sec=30 * 60,
            resume_key=f"race-list:{date_str}:current",
            min_interval_sec=1.0,
            max_retries=2,
            retry_statuses={429, 500, 503},
            retry_base_sec=2.0,
            retry_jitter_sec=0.6,
            circuit_threshold=3,
            circuit_cooldown_sec=120.0,
        )
        statuses.append(sub_result.status)
        if sub_result.status == 200:
            race_ids = extract_race_ids(sub_html)
            if race_ids:
                return race_ids, "race.netkeiba.com"

    unavailable = {403, 429, 500, 502, 503, 504}
    if statuses and all(status in unavailable for status in statuses):
        raise RuntimeError(f"race-list upstream unavailable (HTTP {statuses})")

    # HTTP 400/404 or a successful empty card means no races for that date.
    return [], "netkeiba"
