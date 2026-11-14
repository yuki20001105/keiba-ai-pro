import asyncio
import calendar
import json
import sqlite3
from datetime import date

import pytest

from scraping import jobs
from scraping.fetch_pipeline import FetchResult


def calendar_html(year=2025, month=1, venues=None):
    venues = {4: ["京都"], 5: ["京都"]} if venues is None else venues
    cells = []
    for day in range(1, calendar.monthrange(year, month)[1] + 1):
        names = "".join(f'<span class="JyoName">{name}</span>' for name in venues.get(day, []))
        link = (f'<a href="?kaisai_date={year:04d}{month:02d}{day:02d}">{names}</a>'
                if names else "")
        cells.append(f'<td class="RaceCellBox"><span class="Day">{day}</span>{link}</td>')
    return (
        '<meta charset="utf-8">'
        f'<select id="cal_select_year"><option value="{year}" selected>{year}</option></select>'
        f'<div class="CalendarMonth"><span class="Active"><a href="?year={year}&month={month}">'
        f'{month}</a></span></div><table class="Calendar_Table"><tr>'
        + "".join(cells) + "</tr></table>"
    )


def fetch_calendar(monkeypatch, html, status=200):
    async def fetch(_session, url, **_kwargs):
        return FetchResult(url, url, status, html.encode("utf-8"), "network", 1), html

    monkeypatch.setattr(jobs, "fetch_text", fetch)
    return asyncio.run(jobs._fetch_race_days_for_month(2025, 1))


@pytest.mark.parametrize("html", [
    '<a href="?kaisai_date=20250104">4</a>',
    calendar_html().replace('<td class="RaceCellBox"><span class="Day">31</span></td>', ""),
    calendar_html(2026, 1),
    calendar_html(2025, 2),
    calendar_html().replace("京都", "不明競馬場"),
], ids=["navigation-only", "missing-day", "wrong-year", "wrong-month", "unknown-venue"])
def test_main_calendar_rejects_unproven_or_incomplete_month(monkeypatch, html):
    assert fetch_calendar(monkeypatch, html) is None


def test_main_calendar_ignores_navigation_outside_selected_month(monkeypatch):
    html = calendar_html() + '<a href="?kaisai_date=20250201">翌月</a>'
    assert fetch_calendar(monkeypatch, html) == ["20250104", "20250105"]


def test_main_calendar_accepts_only_a_fully_verified_empty_month(monkeypatch):
    assert fetch_calendar(monkeypatch, calendar_html(venues={})) == []


def test_main_calendar_race_links_without_recognized_venues_are_not_empty(monkeypatch):
    html = calendar_html().replace('class="JyoName"', 'class="ChangedVenueLayout"')
    assert fetch_calendar(monkeypatch, html) is None


def test_main_calendar_http_failure_is_not_an_empty_month(monkeypatch):
    assert fetch_calendar(monkeypatch, calendar_html(), status=503) is None


def test_recent_and_future_dates_defer_empty_confirmation() -> None:
    today = date(2026, 9, 13)

    assert jobs._is_recent_or_future_race_date("20260913", today=today)
    assert jobs._is_recent_or_future_race_date("20260901", today=today)
    assert jobs._is_recent_or_future_race_date("20260920", today=today)
    assert not jobs._is_recent_or_future_race_date("20260829", today=today)


def test_calendar_builder_returns_only_scheduled_dates(monkeypatch) -> None:
    async def fake_month(year: int, month: int):
        assert (year, month) == (2025, 1)
        return ["20250104", "20250105", "20250201"]

    monkeypatch.setattr(jobs, "_fetch_race_days_for_month", fake_month)
    result = asyncio.run(
        jobs._build_race_dates_from_calendar("20250101", "20250131")
    )
    assert result == ["20250104", "20250105"]


def test_calendar_builder_falls_back_if_any_month_is_unavailable(monkeypatch) -> None:
    async def fake_month(year: int, month: int):
        return ["20250104"] if month == 1 else None

    monkeypatch.setattr(jobs, "_fetch_race_days_for_month", fake_month)
    result = asyncio.run(
        jobs._build_race_dates_from_calendar("20250101", "20250228")
    )
    assert result is None


def test_existing_race_dates_protects_saved_dates_from_no_race_classification(
    tmp_path,
) -> None:
    db_path = tmp_path / "keiba.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE races_ultimate (race_id TEXT, data TEXT)")
        conn.executemany(
            "INSERT INTO races_ultimate (race_id, data) VALUES (?, ?)",
            [
                ("202501040101", json.dumps({"race_date": "2025-01-04"})),
                ("202501050101", json.dumps({"date": "20250105"})),
                ("broken", "not-json"),
            ],
        )

    assert jobs._existing_race_dates(
        db_path,
        ["20250104", "20250106"],
    ) == {"20250104"}
