from __future__ import annotations

import sqlite3
from pathlib import Path

QUEUE_STATUSES = ("PENDING", "RUNNING", "SUCCESS", "FAILED", "SKIP")


def init_date_queue(db_path: Path) -> None:
    """Create queue table and reset orphan RUNNING tasks to FAILED on startup."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scrape_date_queue (
                task_date TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'PENDING',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_job_id TEXT,
                last_error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            UPDATE scrape_date_queue
               SET status = 'FAILED',
                   last_error = COALESCE(last_error, 'Recovered from stale RUNNING state'),
                   updated_at = CURRENT_TIMESTAMP
             WHERE status = 'RUNNING'
            """
        )
        conn.commit()
    finally:
        conn.close()


def seed_dates(db_path: Path, dates: list[str], force_rescrape: bool = False) -> None:
    """Insert dates as PENDING. If force_rescrape, reset existing statuses to PENDING."""
    if not dates:
        return
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executemany(
            """
            INSERT INTO scrape_date_queue (task_date, status, attempts, updated_at)
            VALUES (?, 'PENDING', 0, CURRENT_TIMESTAMP)
            ON CONFLICT(task_date) DO NOTHING
            """,
            [(d,) for d in dates],
        )
        if force_rescrape:
            conn.executemany(
                """
                UPDATE scrape_date_queue
                   SET status = 'PENDING',
                       last_error = NULL,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE task_date = ?
                """,
                [(d,) for d in dates],
            )
        conn.commit()
    finally:
        conn.close()


def filter_dates_for_run(db_path: Path, dates: list[str], force_rescrape: bool = False) -> list[str]:
    """Return dates that should be processed in this run."""
    if not dates:
        return []
    conn = sqlite3.connect(str(db_path))
    try:
        placeholders = ",".join(["?"] * len(dates))
        rows = conn.execute(
            f"SELECT task_date, status FROM scrape_date_queue WHERE task_date IN ({placeholders})",
            dates,
        ).fetchall()
        status_map = {d: s for d, s in rows}
    finally:
        conn.close()

    picked: list[str] = []
    for d in dates:
        s = status_map.get(d)
        if force_rescrape:
            picked.append(d)
            continue
        if s in (None, "PENDING", "FAILED"):
            picked.append(d)
            continue
    return picked


def mark_date_status(
    db_path: Path,
    task_date: str,
    status: str,
    *,
    job_id: str | None = None,
    error: str | None = None,
    bump_attempts: bool = False,
) -> None:
    if status not in QUEUE_STATUSES:
        raise ValueError(f"invalid queue status: {status}")

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        if bump_attempts:
            conn.execute(
                """
                UPDATE scrape_date_queue
                   SET status = ?,
                       attempts = attempts + 1,
                       last_job_id = COALESCE(?, last_job_id),
                       last_error = ?,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE task_date = ?
                """,
                (status, job_id, error, task_date),
            )
        else:
            conn.execute(
                """
                UPDATE scrape_date_queue
                   SET status = ?,
                       last_job_id = COALESCE(?, last_job_id),
                       last_error = ?,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE task_date = ?
                """,
                (status, job_id, error, task_date),
            )
        conn.commit()
    finally:
        conn.close()


def get_queue_counts(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM scrape_date_queue GROUP BY status"
        ).fetchall()
    finally:
        conn.close()
    out = {s: 0 for s in QUEUE_STATUSES}
    for status, n in rows:
        out[str(status)] = int(n)
    return out
