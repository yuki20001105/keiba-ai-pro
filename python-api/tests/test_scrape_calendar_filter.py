import asyncio
import json
import sqlite3
from datetime import date

from scraping import jobs


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
