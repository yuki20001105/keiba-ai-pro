"""Read-only netkeiba race-list retrieval.

Race-list requests used to be proxied to a separate port-8001 service. That
service is no longer part of the repository. Keeping this small read-only
operation in the existing scraping package makes local and hosted startup use
the same single FastAPI process.
"""
from __future__ import annotations

import re
import time
from datetime import date as date_type
from urllib.parse import urlencode

import aiohttp
from bs4 import BeautifulSoup

from .constants import get_random_headers
from .fetch_pipeline import fetch_text
from .parsed_cache import get_parsed, parser_version, put_parsed, source_metadata
from .race_inventory import RaceListSource


_JRA_VENUE_CODES = tuple(f"{value:02d}" for value in range(1, 11))
_PARSER_VERSION = parser_version(__file__)


def _cached_month_ids(date_str: str) -> list[str] | None:
    index = get_parsed("month-race-index", date_str[:6], _PARSER_VERSION)
    return index.get(date_str, []) if index is not None else None


def build_mobile_month_url(date_str: str, page: int = 1) -> str:
    """Build the mobile DB month-search URL restricted to JRA venues."""
    target_date = date_type(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))
    if page < 1:
        raise ValueError("page must be at least 1")

    params: list[tuple[str, str | int]] = [
        ("pid", "race_list"),
        ("start_year", target_date.year),
        ("start_mon", target_date.month),
        ("end_year", target_date.year),
        ("end_mon", target_date.month),
        ("track[]", 1),
        ("track[]", 2),
        ("track[]", 3),
        ("sort", "date"),
    ]
    params.extend(("jyo[]", code) for code in _JRA_VENUE_CODES)
    params.append(("page", page))
    return f"https://db.sp.netkeiba.com/?{urlencode(params)}"


def extract_mobile_race_entries(html: str) -> list[tuple[str, str]]:
    """Extract ``(YYYYMMDD, race_id)`` pairs from a mobile month result."""
    entries: list[tuple[str, str]] = []
    soup = BeautifulSoup(html or "", "lxml")
    for anchor in soup.find_all("a", href=True):
        race_match = re.search(r"/race/(\d{12})/?", str(anchor["href"]))
        if not race_match:
            continue
        date_match = re.search(
            r"(\d{4})/(\d{1,2})/(\d{1,2})", anchor.get_text(" ", strip=True)
        )
        if not date_match:
            continue
        normalized_date = (
            f"{int(date_match.group(1)):04d}"
            f"{int(date_match.group(2)):02d}"
            f"{int(date_match.group(3)):02d}"
        )
        entries.append((normalized_date, race_match.group(1)))
    return list(dict.fromkeys(entries))


def extract_mobile_max_page(html: str) -> int:
    """Return the highest pagination number advertised by a month result."""
    pages = [1]
    soup = BeautifulSoup(html or "", "lxml")
    for anchor in soup.find_all("a", href=True):
        match = re.search(r"(?:[?&])page=(\d+)(?:&|$)", str(anchor["href"]))
        if match:
            pages.append(int(match.group(1)))
    return max(pages)


def _valid_jra_race_id(race_id: str, target_date: date_type) -> bool:
    return (
        len(race_id) == 12
        and race_id.isdigit()
        and race_id[:4] == f"{target_date.year:04d}"
        and race_id[4:6] in _JRA_VENUE_CODES
        and 1 <= int(race_id[-2:]) <= 12
    )


async def _fetch_mobile_month_race_ids(
    session: aiohttp.ClientSession, date_str: str, target_date: date_type
) -> list[str]:
    """Resolve one date by fully scanning the same-provider mobile month index.

    All advertised pages must be readable.  Returning an incomplete list would
    incorrectly freeze a partial day as authoritative, so any failed page makes
    this fallback fail closed.
    """
    month_key = f"{target_date.year:04d}{target_date.month:02d}"
    cached = _cached_month_ids(date_str)
    if cached is not None:
        return cached
    sources: dict[str, dict] = {}
    current_month = target_date.year == date_type.today().year and target_date.month == date_type.today().month
    source_ttl = 5 * 60 if current_month else 24 * 60 * 60

    async def fetch_page(page: int) -> str:
        url = build_mobile_month_url(date_str, page)
        existing = source_metadata(url)
        result, html = await fetch_text(
            session,
            url,
            cache_ttl_sec=source_ttl,
            force_refresh=bool(existing and existing["fetched_at"] + source_ttl <= time.time()),
            resume_key=f"race-list-mobile:{month_key}:page:{page}",
            min_interval_sec=1.0,
            max_retries=2,
            retry_statuses={429, 500, 503},
            retry_base_sec=2.0,
            retry_jitter_sec=0.6,
            circuit_threshold=3,
            circuit_cooldown_sec=120.0,
        )
        if result.status != 200:
            raise RuntimeError(
                f"mobile race-list page {page} unavailable (HTTP {result.status})"
            )
        metadata = getattr(result, "cache_snapshot", None)
        sources[url] = dict(metadata) if metadata else {"url": url, "fetched_at": 0, "expires_at": 0}
        return html

    first_html = await fetch_page(1)
    page_count = extract_mobile_max_page(first_html)
    if page_count > 100:
        raise RuntimeError(f"mobile race-list pagination is implausible: {page_count}")

    entries: list[tuple[str, str]] = []
    page = 1
    html = first_html
    while page <= page_count:
        page_entries = extract_mobile_race_entries(html)
        if not page_entries:
            # An unrecognized HTTP 200 is not proof of an empty month.
            raise RuntimeError(f"mobile race-list page {page} has no verified race entries")
        if any(
            entry_date[:6] != month_key or not _valid_jra_race_id(race_id, target_date)
            for entry_date, race_id in page_entries
        ):
            raise RuntimeError(f"mobile race-list page {page} has inconsistent race entries")
        try:
            for entry_date, _race_id in page_entries:
                date_type(int(entry_date[:4]), int(entry_date[4:6]), int(entry_date[6:8]))
        except ValueError as exc:
            raise RuntimeError(f"mobile race-list page {page} has invalid dates") from exc
        if page > 1 and not set(page_entries).difference(entries):
            raise RuntimeError(f"mobile race-list page {page} repeats an earlier page")
        entries.extend(page_entries)
        page_count = max(page_count, extract_mobile_max_page(html))
        if page_count > 100:
            raise RuntimeError(f"mobile race-list pagination is implausible: {page_count}")
        page += 1
        if page <= page_count:
            html = await fetch_page(page)

    index: dict[str, list[str]] = {}
    for entry_date, race_id in entries:
        ids = index.setdefault(entry_date, [])
        if race_id not in ids:
            ids.append(race_id)
    # Current month inventories change as race cards are published. Their
    # derived index expires quickly, even if its backing HTML lives longer.
    put_parsed("month-race-index", month_key, _PARSER_VERSION, index, sources,
               max_age_sec=source_ttl)

    race_ids = [
        race_id
        for entry_date, race_id in entries
        if entry_date == date_str and _valid_jra_race_id(race_id, target_date)
    ]
    return list(dict.fromkeys(race_ids))


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
    cached = _cached_month_ids(date_str)
    if cached is not None:
        source = "db.sp.netkeiba.com/month-search"
        return cached, source if cached else f"{source}:verified-empty"

    timeout = aiohttp.ClientTimeout(total=25, connect=8)
    connector = aiohttp.TCPConnector(limit=2, limit_per_host=1)

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
        if db_result.status == 200:
            race_ids = extract_race_ids(db_html)
            if race_ids:
                return race_ids, RaceListSource(
                    "db.netkeiba.com", fetched=db_result, parser=_PARSER_VERSION,
                )

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
        if sub_result.status == 200:
            race_ids = extract_race_ids(sub_html)
            if race_ids:
                return race_ids, RaceListSource(
                    "race.netkeiba.com", fetched=sub_result, parser=_PARSER_VERSION,
                )

        # Both desktop endpoints currently return HTTP 400 for some valid
        # historical dates.  The mobile DB month search remains available and
        # exposes pagination plus the displayed race date, so it can safely
        # establish the complete date-specific JRA ID list.
        mobile_ids = await _fetch_mobile_month_race_ids(session, date_str, target_date)
        if mobile_ids:
            return mobile_ids, "db.sp.netkeiba.com/month-search"
        return [], "db.sp.netkeiba.com/month-search:verified-empty"
