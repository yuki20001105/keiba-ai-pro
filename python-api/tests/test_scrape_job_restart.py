import json
import sqlite3
from pathlib import Path

from scraping.jobs import mark_interrupted_scrape_jobs


def test_running_scrape_job_is_marked_interrupted_after_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "scrape_jobs.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE scrape_jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                progress TEXT DEFAULT '{}',
                result TEXT DEFAULT 'null',
                error TEXT DEFAULT 'null',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT INTO scrape_jobs (job_id, status) VALUES (?, ?)",
            ("scrape-1", "running"),
        )

    assert mark_interrupted_scrape_jobs(db_path) == 1
    with sqlite3.connect(str(db_path)) as conn:
        status, error = conn.execute(
            "SELECT status, error FROM scrape_jobs WHERE job_id = ?",
            ("scrape-1",),
        ).fetchone()

    assert status == "error"
    assert "restarted" in json.loads(error)
