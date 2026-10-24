"""Read-only netkeiba race-list retrieval.

Race-list requests used to be proxied to a separate port-8001 service. That
service is no longer part of the repository. Keeping this small read-only
operation in the existing scraping package makes local and hosted startup use
the same single FastAPI process.
"""
from __future__ import annotations

import re
import time
from datetime import date as date_type, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urlsplit

import aiohttp
from bs4 import BeautifulSoup

from .constants import get_random_headers
from .fetch_pipeline import FetchAccessBlocked
from .acquisition_http_policy import fetch_text
from .parsed_cache import get_parsed, parser_version, put_parsed, source_metadata
from .race_inventory import RaceListSource


_JRA_VENUE_CODES = tuple(f"{value:02d}" for value in range(1, 11))
_PARSER_VERSION = parser_version(__file__)
_JST = timezone(timedelta(hours=9))
CURRENT_LIST_TTL_SECONDS = 60
RECENT_LIST_DAYS = 14


class RaceListUnavailable(RuntimeError):
    """An unconfirmed current card is not evidence that there are no races."""

    def __init__(self, date_str: str, attempts: list[dict], *, reason: str = "current_race_list_unconfirmed"):
        self.date_str = date_str
        self.reason = reason
        self.attempts = [dict(attempt) for attempt in attempts]
        super().__init__(f"{reason}: {date_str}")

    def as_dict(self) -> dict:
        return {"reason": self.reason, "date_str": self.date_str, "attempts": self.attempts}


def _jst_today() -> date_type:
    return datetime.now(_JST).date()


def _recent_or_future(target: date_type) -> bool:
    return target >= _jst_today() - timedelta(days=RECENT_LIST_DAYS)


async def _fetch_list_page(session, url: str, *, ttl: int, resume_key: str,
                           force_refresh: bool = False):
    # cache_ttl_sec controls new writes, not the age of a previously cached
    # response. Explicitly bypass legacy long-lived bodies AND resume hits.
    existing = source_metadata(url)
    refresh = force_refresh or existing is None or existing["fetched_at"] + ttl <= time.time()
    result, html = await fetch_text(
        session, url, cache_ttl_sec=ttl, force_refresh=refresh,
        resume_key=resume_key, min_interval_sec=1.0, max_retries=2,
        retry_statuses={429, 500, 503}, retry_base_sec=2.0, retry_jitter_sec=0.6,
        circuit_threshold=3, circuit_cooldown_sec=120.0,
    )
    # Real transport already raises this. Also enforce it for alternative
    # transports: never try another endpoint after an explicit restriction.
    if result.status in (401, 403, 429):
        raise FetchAccessBlocked(f"race-list:http-{result.status}")
    return result, html


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
        race_match = re.search(r"/race/(\d{12})(?:/|$|[?#])", str(anchor["href"]))
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
    session: aiohttp.ClientSession, date_str: str, target_date: date_type,
    *, force_refresh: bool = False,
) -> list[str]:
    """Resolve one date by fully scanning the same-provider mobile month index.

    All advertised pages must be readable.  Returning an incomplete list would
    incorrectly freeze a partial day as authoritative, so any failed page makes
    this fallback fail closed.
    """
    month_key = f"{target_date.year:04d}{target_date.month:02d}"
    cached = None if force_refresh else _cached_month_ids(date_str)
    if cached is not None:
        return cached
    sources: dict[str, dict] = {}
    today = _jst_today()
    current_month = target_date.year == today.year and target_date.month == today.month
    source_ttl = 5 * 60 if current_month or _recent_or_future(target_date) else 24 * 60 * 60

    async def fetch_page(page: int) -> str:
        url = build_mobile_month_url(date_str, page)
        result, html = await _fetch_list_page(
            session, url, ttl=source_ttl,
            force_refresh=force_refresh,
            resume_key=f"race-list-mobile:{month_key}:page:{page}",
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


def _selected_dates(soup: BeautifulSoup) -> set[str]:
    """Read selected/declared page dates, never unselected navigation links."""
    dates: set[str] = set()
    for item in soup.select('input[name="kaisai_date"], select[name="kaisai_date"] option[selected]'):
        value = str(item.get("value", ""))
        if re.fullmatch(r"\d{8}", value):
            dates.add(value)
    for item in soup.select('[data-kaisai-date], [data-kaisai_date]'):
        value = str(item.get("data-kaisai-date") or item.get("data-kaisai_date") or "")
        if re.fullmatch(r"\d{8}", value):
            dates.add(value)
    for selected in soup.select('.Active, .active, .Selected, .selected, [aria-current="date"], [aria-current="page"]'):
        anchors = [selected] if selected.name == "a" else selected.find_all("a", href=True)
        for anchor in anchors:
            try:
                values = parse_qs(urlsplit(str(anchor.get("href", ""))).query).get("kaisai_date", [])
            except ValueError:
                continue
            dates.update(value for value in values if re.fullmatch(r"\d{8}", value))
    for heading in soup.find_all(["title", "h1", "h2", "h3"]):
        for year, month, day in re.findall(
            r"(\d{4})\s*(?:年|/|-)\s*(\d{1,2})\s*(?:月|/|-)\s*(\d{1,2})(?:日)?",
            heading.get_text(" ", strip=True),
        ):
            try:
                dates.add(date_type(int(year), int(month), int(day)).strftime("%Y%m%d"))
            except ValueError:
                continue
    return dates


def extract_current_card_ids(
    html: str, date_str: str, *, requested_url: str, final_url: str,
    allow_date_bound_fragment: bool = False,
) -> tuple[list[str], str]:
    """Parse only scoped cards with explicit date/endpoint evidence.

    A race-list subfragment has no date heading, so its existing endpoint
    contract is bound to both the requested and final URL's date. Main/mobile
    pages additionally need a selected date or a dated title/heading. Unknown
    HTTP-200 shells do not become authoritative empty-day responses.
    """
    try:
        requested, final = urlsplit(requested_url), urlsplit(final_url)
    except ValueError:
        return [], "response_date_or_endpoint_mismatch"
    if (final.scheme != "https" or final.hostname != requested.hostname
            or (final.path or "/") != (requested.path or "/")
            or parse_qs(final.query).get("kaisai_date") != [date_str]
            or parse_qs(requested.query).get("kaisai_date") != [date_str]):
        return [], "response_date_or_endpoint_mismatch"
    if requested.hostname == "race.sp.netkeiba.com" and parse_qs(final.query).get("pid") != ["race_list"]:
        return [], "response_date_or_endpoint_mismatch"
    soup = BeautifulSoup(html or "", "lxml")
    selected_dates = _selected_dates(soup)
    if selected_dates and selected_dates != {date_str}:
        return [], "selected_date_mismatch"
    fragment = (allow_date_bound_fragment and requested.hostname == "race.netkeiba.com"
                and requested.path == "/top/race_list_sub.html")
    if not selected_dates and not fragment:
        return [], "selected_date_unconfirmed"
    cards = soup.select(".RaceList_DataItem")
    if not cards:
        return [], "current_cards_unconfirmed"
    target = date_type(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))
    found: list[str] = []
    allowed_hosts = {None, "race.netkeiba.com", "race.sp.netkeiba.com", "db.netkeiba.com", "db.sp.netkeiba.com"}
    for card in cards:
        ids: set[str] = set()
        for anchor in card.find_all("a", href=True):
            try:
                link = urlsplit(str(anchor["href"]))
            except ValueError:
                return [], "current_card_id_invalid"
            if link.hostname not in allowed_hosts or link.scheme not in {"", "https"}:
                continue
            values = parse_qs(link.query).get("race_id", [])
            values.extend(re.findall(r"/race/(\d{12})(?:/|$)", link.path))
            for value in values:
                if not _valid_jra_race_id(value, target):
                    return [], "current_card_id_invalid"
                ids.add(value)
        if len(ids) != 1:
            return [], "current_card_identity_unconfirmed"
        found.extend(ids)
    return list(dict.fromkeys(found)), "verified_current_cards"


async def _fetch_recent_race_ids(
    session, date_str: str, target_date: date_type, *, force_refresh: bool,
) -> tuple[list[str], str]:
    attempts: list[dict] = []
    candidates = [
        (f"https://race.netkeiba.com/top/race_list.html?kaisai_date={date_str}", "current-main", False),
        (f"https://race.netkeiba.com/top/race_list_sub.html?kaisai_date={date_str}", "current", True),
        (f"https://race.sp.netkeiba.com/?kaisai_date={date_str}&pid=race_list", "current-mobile", False),
    ]
    for url, key, fragment in candidates:
        # A short-lived cached empty/shell response still must not prevent an
        # immediate user retry from discovering newly published cards. Refresh
        # that same URL once through the shared pacer; never double-fetch a
        # fresh network response or loop indefinitely on an unknown layout.
        for refresh_attempt in range(2):
            try:
                result, html = await _fetch_list_page(
                    session, url, ttl=CURRENT_LIST_TTL_SECONDS,
                    resume_key=f"race-list:{date_str}:{key}",
                    force_refresh=force_refresh or refresh_attempt == 1,
                )
            except FetchAccessBlocked:
                raise
            except (aiohttp.ClientError, TimeoutError) as exc:
                attempts.append({"url": url, "status": 0, "reason": type(exc).__name__})
                break
            if result.status != 200:
                attempts.append({"url": url, "status": result.status, "reason": "upstream_http_error"})
                break
            ids, reason = extract_current_card_ids(
                html, date_str, requested_url=url,
                final_url=str(getattr(result, "url", "") or getattr(result, "normalized_url", "")),
                allow_date_bound_fragment=fragment,
            )
            attempts.append({"url": url, "status": 200, "reason": reason})
            if ids:
                return ids, RaceListSource(
                    urlsplit(url).hostname, fetched=result, parser=_PARSER_VERSION,
                )
            if getattr(result, "source", "network") == "network":
                break

    # Today's historical results may contain only already-finished races.
    # Never freeze such a subset as a complete current/future day's card.
    if target_date >= _jst_today():
        raise RaceListUnavailable(date_str, attempts)

    # A positive historical result for an earlier exact day remains useful.
    # An absent key in a recent-month result is NOT proof of no racing.
    try:
        ids = await _fetch_mobile_month_race_ids(
            session, date_str, target_date, force_refresh=force_refresh,
        )
    except FetchAccessBlocked:
        raise
    except (aiohttp.ClientError, TimeoutError, RuntimeError) as exc:
        attempts.append({"url": build_mobile_month_url(date_str), "status": 0,
                         "reason": "historical_month_unconfirmed", "error_type": type(exc).__name__})
    else:
        if ids:
            return ids, "db.sp.netkeiba.com/month-search"
        attempts.append({"url": build_mobile_month_url(date_str), "status": 200,
                         "reason": "target_absent_from_historical_results"})
    raise RaceListUnavailable(date_str, attempts)


async def fetch_race_ids(date_str: str, *, force_refresh: bool = False) -> tuple[list[str], str]:
    """Fetch a race list without writing application data."""
    target_date = date_type(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))
    recent = _recent_or_future(target_date)
    cached = None if recent or force_refresh else _cached_month_ids(date_str)
    if cached is not None:
        source = "db.sp.netkeiba.com/month-search"
        return cached, source if cached else f"{source}:verified-empty"

    timeout = aiohttp.ClientTimeout(total=25, connect=8)
    connector = aiohttp.TCPConnector(limit=2, limit_per_host=1)

    async with aiohttp.ClientSession(
        headers=get_random_headers(), timeout=timeout, connector=connector
    ) as session:
        if recent:
            return await _fetch_recent_race_ids(
                session, date_str, target_date, force_refresh=force_refresh,
            )
        db_result, db_html = await fetch_text(
            session,
            f"https://db.netkeiba.com/race/list/{date_str}/",
            cache_ttl_sec=12 * 60 * 60,
            force_refresh=force_refresh,
            resume_key=f"race-list:{date_str}:db",
            min_interval_sec=1.0,
            max_retries=2,
            retry_statuses={429, 500, 503},
            retry_base_sec=2.0,
            retry_jitter_sec=0.6,
            circuit_threshold=3,
            circuit_cooldown_sec=120.0,
        )
        if db_result.status in (401, 403, 429):
            raise FetchAccessBlocked(f"race-list:http-{db_result.status}")
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
            force_refresh=force_refresh,
            resume_key=f"race-list:{date_str}:current",
            min_interval_sec=1.0,
            max_retries=2,
            retry_statuses={429, 500, 503},
            retry_base_sec=2.0,
            retry_jitter_sec=0.6,
            circuit_threshold=3,
            circuit_cooldown_sec=120.0,
        )
        if sub_result.status in (401, 403, 429):
            raise FetchAccessBlocked(f"race-list:http-{sub_result.status}")
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
        mobile_ids = await _fetch_mobile_month_race_ids(
            session, date_str, target_date, force_refresh=force_refresh,
        )
        if mobile_ids:
            return mobile_ids, "db.sp.netkeiba.com/month-search"
        return [], "db.sp.netkeiba.com/month-search:verified-empty"
