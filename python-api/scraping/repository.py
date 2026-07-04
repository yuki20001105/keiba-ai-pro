from __future__ import annotations

import sqlite3
from pathlib import Path

from scraping.storage import (
    _save_race_sqlite_only,
    _save_scraped_date_sqlite,
)


class ScrapingRepository:
    """SQLite access boundary for scraping pipeline."""

    def __init__(self, db_path: Path):
        self.db_path = db_path

    def race_exists(self, race_id: str) -> bool:
        try:
            conn = sqlite3.connect(str(self.db_path))
            row = conn.execute(
                "SELECT 1 FROM race_results_ultimate WHERE race_id = ? LIMIT 1", (race_id,)
            ).fetchone()
            conn.close()
            return row is not None
        except Exception:
            return False

    def save_race(self, race_data: dict) -> bool:
        return _save_race_sqlite_only(race_data, self.db_path)

    def mark_scraped_date(self, date: str, race_count: int) -> None:
        _save_scraped_date_sqlite(self.db_path, date, race_count)
