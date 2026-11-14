"""Pure, complete-month JRA calendar validation shared by acquisition paths."""
from __future__ import annotations

import calendar
import unicodedata
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from .constants import VENUE_MAP


def _text(node):
    return unicodedata.normalize("NFKC", node.get_text(" ", strip=True) if node else "")


def parse_calendar(body: bytes, year: int, month: int) -> dict[str, list[str]]:
    if not isinstance(body, bytes) or not 0 < len(body) <= 2 * 1024 * 1024:
        raise ValueError("raw-provider-body-required")
    soup = BeautifulSoup(body, "lxml")
    selected = soup.select_one("#cal_select_year option[selected]")
    active = soup.select_one(".CalendarMonth .Active a")
    if (not selected or selected.get("value") != str(year) or not active
            or parse_qs(urlsplit(active.get("href", "")).query) != {"year": [str(year)], "month": [str(month)]}):
        raise ValueError("calendar-month-unproven")
    inverse = {VENUE_MAP[f"{code:02d}"]: f"{code:02d}" for code in range(1, 11)}
    days = {}
    for cell in soup.select("table.Calendar_Table td.RaceCellBox"):
        day = _text(cell.select_one(".Day"))
        if not day:
            continue
        if not day.isdecimal() or int(day) in days:
            raise ValueError("calendar-day-ambiguous")
        venues = [_text(v) for v in cell.select(".JyoName")]
        if len(venues) != len(set(venues)) or any(v not in inverse for v in venues):
            raise ValueError("calendar-venue-invalid")
        if not venues and any(
            parse_qs(urlsplit(anchor.get("href", "")).query).get("kaisai_date")
            for anchor in cell.find_all("a", href=True)
        ):
            raise ValueError("calendar-venue-unconfirmed")
        days[int(day)] = sorted(inverse[v] for v in venues)
    if set(days) != set(range(1, calendar.monthrange(year, month)[1] + 1)):
        raise ValueError("calendar-month-incomplete")
    return {f"{year:04d}{month:02d}{d:02d}": v for d, v in days.items()}
