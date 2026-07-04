from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scraping.task_models import (
    ScrapeTask,
    TASK_FAILED,
    TASK_PENDING,
    TASK_RETRY,
    TASK_RUNNING,
    TASK_SKIP,
    TASK_SUCCESS,
)


class SQLiteTaskQueue:
    def __init__(self, db_path: Path, *, job_id: str, queue_name: str):
        self.db_path = db_path
        self.job_id = job_id
        self.queue_name = queue_name
        self._init_table()
        self._recover_stale_running()

    def _conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_table(self) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_queue (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    queue_name TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 2,
                    last_error TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_queue_status ON task_queue (job_id, queue_name, status, created_at)"
            )
            conn.commit()
        finally:
            conn.close()

    def _recover_stale_running(self) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                UPDATE task_queue
                   SET status = 'RETRY',
                       updated_at = CURRENT_TIMESTAMP,
                       last_error = COALESCE(last_error, 'Recovered from stale RUNNING state')
                 WHERE job_id = ? AND queue_name = ? AND status = 'RUNNING'
                """,
                (self.job_id, self.queue_name),
            )
            conn.commit()
        finally:
            conn.close()

    def enqueue(self, task: ScrapeTask) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO task_queue (id, job_id, queue_name, task_type, payload_json, status, retry_count, max_attempts, last_error, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    task.task_id,
                    self.job_id,
                    self.queue_name,
                    task.task_type,
                    json.dumps(task.payload, ensure_ascii=False),
                    task.status,
                    int(task.attempts),
                    int(task.max_attempts),
                    task.last_error,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def dequeue(self) -> ScrapeTask | None:
        conn = self._conn()
        try:
            row = conn.execute(
                """
                SELECT id, task_type, payload_json, status, retry_count, max_attempts, last_error
                  FROM task_queue
                 WHERE job_id = ? AND queue_name = ? AND status IN ('PENDING','RETRY')
                 ORDER BY created_at ASC
                 LIMIT 1
                """,
                (self.job_id, self.queue_name),
            ).fetchone()
            if not row:
                return None
            return ScrapeTask(
                task_id=row[0],
                task_type=row[1],
                payload=json.loads(row[2] or "{}"),
                status=row[3],
                attempts=int(row[4] or 0),
                max_attempts=int(row[5] or 2),
                last_error=row[6],
            )
        finally:
            conn.close()

    def requeue(self, task: ScrapeTask) -> None:
        # SQLite queue relies on status transitions; nothing to append.
        return

    def mark_running(self, task: ScrapeTask) -> None:
        task.status = TASK_RUNNING
        task.attempts += 1
        conn = self._conn()
        try:
            conn.execute(
                """
                UPDATE task_queue
                   SET status = ?, retry_count = ?, updated_at = CURRENT_TIMESTAMP
                 WHERE id = ?
                """,
                (task.status, int(task.attempts), task.task_id),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_success(self, task: ScrapeTask) -> None:
        task.status = TASK_SUCCESS
        self._mark_status(task)

    def mark_skip(self, task: ScrapeTask) -> None:
        task.status = TASK_SKIP
        self._mark_status(task)

    def mark_retry(self, task: ScrapeTask, error: str | None = None) -> None:
        task.status = TASK_RETRY
        task.last_error = error
        self._mark_status(task)

    def mark_failed(self, task: ScrapeTask, error: str | None = None) -> None:
        task.status = TASK_FAILED
        task.last_error = error
        self._mark_status(task)

    def _mark_status(self, task: ScrapeTask) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                UPDATE task_queue
                   SET status = ?, retry_count = ?, last_error = ?, updated_at = CURRENT_TIMESTAMP
                 WHERE id = ?
                """,
                (task.status, int(task.attempts), task.last_error, task.task_id),
            )
            conn.commit()
        finally:
            conn.close()

    def has_items(self) -> bool:
        conn = self._conn()
        try:
            row = conn.execute(
                """
                SELECT COUNT(*)
                  FROM task_queue
                 WHERE job_id = ? AND queue_name = ? AND status IN ('PENDING','RETRY')
                """,
                (self.job_id, self.queue_name),
            ).fetchone()
            return int(row[0] if row else 0) > 0
        finally:
            conn.close()

    def stats(self) -> dict[str, int]:
        out = {
            TASK_PENDING: 0,
            TASK_RUNNING: 0,
            TASK_SUCCESS: 0,
            TASK_FAILED: 0,
            TASK_RETRY: 0,
            TASK_SKIP: 0,
        }
        conn = self._conn()
        try:
            rows = conn.execute(
                """
                SELECT status, COUNT(*)
                  FROM task_queue
                 WHERE job_id = ? AND queue_name = ?
                 GROUP BY status
                """,
                (self.job_id, self.queue_name),
            ).fetchall()
            total = 0
            for status, n in rows:
                out[str(status)] = int(n)
                total += int(n)
            out["TOTAL"] = total
            return out
        finally:
            conn.close()
