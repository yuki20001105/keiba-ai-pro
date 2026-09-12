"""SQLite persistence for asynchronous model-training jobs."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "keiba" / "data" / "train_jobs.db"
_LOCK = threading.RLock()


def init_train_jobs_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS train_jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                progress TEXT NOT NULL DEFAULT '',
                pct INTEGER NOT NULL DEFAULT 0,
                result TEXT DEFAULT 'null',
                error TEXT DEFAULT 'null',
                request_json TEXT DEFAULT 'null',
                owner_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(train_jobs)").fetchall()
        }
        if "owner_id" not in columns:
            conn.execute("ALTER TABLE train_jobs ADD COLUMN owner_id TEXT")
        conn.commit()


def persist_train_job(
    job_id: str,
    job: dict[str, Any],
    request_payload: dict[str, Any] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    init_train_jobs_db(db_path)
    request_json = json.dumps(request_payload, ensure_ascii=False, default=str) if request_payload is not None else None
    with _LOCK, sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO train_jobs (
                job_id, status, progress, pct, result, error, request_json, owner_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, COALESCE(?, 'null'), ?, CURRENT_TIMESTAMP)
            ON CONFLICT(job_id) DO UPDATE SET
                status = excluded.status,
                progress = excluded.progress,
                pct = excluded.pct,
                result = excluded.result,
                error = excluded.error,
                request_json = CASE
                    WHEN excluded.request_json = 'null' THEN train_jobs.request_json
                    ELSE excluded.request_json
                END,
                owner_id = COALESCE(excluded.owner_id, train_jobs.owner_id),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                job_id,
                str(job.get("status", "unknown")),
                str(job.get("progress", "")),
                int(job.get("pct", 0) or 0),
                json.dumps(job.get("result"), ensure_ascii=False, default=str),
                json.dumps(job.get("error"), ensure_ascii=False, default=str),
                request_json,
                str(job["owner_id"]) if job.get("owner_id") else None,
            ),
        )
        conn.commit()


def load_train_job(job_id: str, db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any] | None:
    init_train_jobs_db(db_path)
    with _LOCK, sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT status, progress, pct, result, error, owner_id FROM train_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    if row is None:
        return None
    job = {
        "status": str(row[0]),
        "progress": str(row[1] or ""),
        "pct": int(row[2] or 0),
        "result": json.loads(row[3] or "null"),
        "error": json.loads(row[4] or "null"),
    }
    # Pre-owner records remain readable by migration tooling, but HTTP status
    # handlers fail closed because they require a matching non-empty owner.
    if row[5]:
        job["owner_id"] = str(row[5])
    return job


def mark_interrupted_train_jobs(db_path: Path = DEFAULT_DB_PATH) -> int:
    """Fail closed after a backend restart instead of showing stale progress."""
    init_train_jobs_db(db_path)
    message = "Backend restarted before the training job completed. Start a new job."
    with _LOCK, sqlite3.connect(str(db_path)) as conn:
        cursor = conn.execute(
            """
            UPDATE train_jobs
            SET status = 'error', progress = ?, error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE status IN ('queued', 'running')
            """,
            (message, json.dumps(message)),
        )
        conn.commit()
        return int(cursor.rowcount or 0)
