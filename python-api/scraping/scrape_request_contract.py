"""Bounded date-range contract shared by scrape HTTP and worker paths."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta


MAX_SCRAPE_RANGE_DAYS = 31
SCRAPE_TARGETS_PER_DAY = 2
MAX_SCRAPE_TARGETS = MAX_SCRAPE_RANGE_DAYS * SCRAPE_TARGETS_PER_DAY

_DATE_FORMATS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\d{8}$"), "%Y%m%d"),
    (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "%Y-%m-%d"),
    (re.compile(r"^\d{4}/\d{2}/\d{2}$"), "%Y/%m/%d"),
)


def parse_scrape_date(value: object) -> date:
    """Parse only the three explicit legacy formats without coercion."""

    if type(value) is not str:
        raise ValueError("scrape date must be a string")
    for pattern, fmt in _DATE_FORMATS:
        if pattern.fullmatch(value) is None:
            continue
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError as exc:
            raise ValueError("scrape date is not a real calendar date") from exc
    raise ValueError("scrape date must use YYYYMMDD, YYYY-MM-DD, or YYYY/MM/DD")


def validate_scrape_date_range(start_date: object, end_date: object) -> tuple[date, date, int]:
    """Return a validated inclusive range or fail closed before scheduling work."""

    start = parse_scrape_date(start_date)
    end = parse_scrape_date(end_date)
    inclusive_days = (end - start).days + 1
    if inclusive_days < 1:
        raise ValueError("scrape start_date must not be after end_date")
    if inclusive_days > MAX_SCRAPE_RANGE_DAYS:
        raise ValueError(f"scrape date range must not exceed {MAX_SCRAPE_RANGE_DAYS} days")
    return start, end, inclusive_days


def build_bounded_scrape_dates(start_date: object, end_date: object) -> list[str]:
    """Build the bounded canonical date list used by the legacy worker."""

    start, _end, inclusive_days = validate_scrape_date_range(start_date, end_date)
    return [(start + timedelta(days=offset)).strftime("%Y%m%d") for offset in range(inclusive_days)]
