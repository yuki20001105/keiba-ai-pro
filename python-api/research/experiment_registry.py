from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_db() -> Path:
    return _repo_root() / "keiba" / "data" / "experiment_ops.db"


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


class ExperimentOpsStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or _default_db()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS experiment_specs (
                spec_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                spec_hash TEXT NOT NULL,
                spec_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS experiment_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                spec_id TEXT NOT NULL,
                status TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 100,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                worker TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                result_json TEXT,
                FOREIGN KEY(spec_id) REFERENCES experiment_specs(spec_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_expq_status ON experiment_queue(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_expq_priority ON experiment_queue(priority)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS experiment_cache (
                cache_key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
        conn.close()

    def register_spec(self, *, name: str, spec: dict[str, Any], metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        spec_json = json.dumps(spec or {}, ensure_ascii=False, sort_keys=True)
        spec_hash = _sha256_text(spec_json)
        spec_id = f"exp_{spec_hash[:12]}"
        conn = self._connect()
        conn.execute(
            """
            INSERT OR REPLACE INTO experiment_specs (
                spec_id, name, created_at, spec_hash, spec_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                spec_id,
                (name or spec.get("name") or spec.get("experiment", {}).get("name") or spec_id),
                _now(),
                spec_hash,
                spec_json,
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        conn.commit()
        conn.close()
        return {"spec_id": spec_id, "spec_hash": spec_hash}

    def enqueue(self, *, spec_id: str, priority: int = 100) -> int:
        conn = self._connect()
        cur = conn.execute(
            """
            INSERT INTO experiment_queue (
                spec_id, status, priority, created_at, attempts
            ) VALUES (?, 'queued', ?, ?, 0)
            """,
            (spec_id, int(priority), _now()),
        )
        job_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return job_id

    def pop_next_job(self, worker: str = "local-worker") -> dict[str, Any] | None:
        conn = self._connect()
        row = conn.execute(
            """
            SELECT q.id, q.spec_id, s.name, s.spec_json, q.priority, q.attempts
            FROM experiment_queue q
            JOIN experiment_specs s ON s.spec_id = q.spec_id
            WHERE q.status = 'queued'
            ORDER BY q.priority DESC, q.id ASC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            conn.close()
            return None

        job_id = int(row[0])
        conn.execute(
            """
            UPDATE experiment_queue
            SET status = 'running', started_at = ?, worker = ?, attempts = attempts + 1
            WHERE id = ?
            """,
            (_now(), worker, job_id),
        )
        conn.commit()
        conn.close()

        try:
            spec = json.loads(row[3] or "{}")
        except Exception:
            spec = {}

        return {
            "job_id": job_id,
            "spec_id": str(row[1]),
            "name": str(row[2]),
            "spec": spec,
            "priority": int(row[4] or 100),
            "attempts": int(row[5] or 0) + 1,
        }

    def finish_job(
        self,
        *,
        job_id: int,
        status: str,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        if status not in {"completed", "failed", "canceled"}:
            status = "failed"
        conn = self._connect()
        conn.execute(
            """
            UPDATE experiment_queue
            SET status = ?, finished_at = ?, error = ?, result_json = ?
            WHERE id = ?
            """,
            (
                status,
                _now(),
                (error or ""),
                json.dumps(result or {}, ensure_ascii=False, default=str),
                int(job_id),
            ),
        )
        conn.commit()
        conn.close()

    def list_queue(self, *, status: str = "queued", limit: int = 50) -> list[dict[str, Any]]:
        n = max(1, min(int(limit), 500))
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT q.id, q.spec_id, s.name, q.status, q.priority, q.created_at,
                   q.started_at, q.finished_at, q.worker, q.attempts, q.error
            FROM experiment_queue q
            JOIN experiment_specs s ON s.spec_id = q.spec_id
            WHERE q.status = ?
            ORDER BY q.priority DESC, q.id ASC
            LIMIT ?
            """,
            (status, n),
        ).fetchall()
        conn.close()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "job_id": int(r[0]),
                    "spec_id": str(r[1]),
                    "name": str(r[2]),
                    "status": str(r[3]),
                    "priority": int(r[4] or 100),
                    "created_at": str(r[5] or ""),
                    "started_at": str(r[6] or ""),
                    "finished_at": str(r[7] or ""),
                    "worker": str(r[8] or ""),
                    "attempts": int(r[9] or 0),
                    "error": str(r[10] or ""),
                }
            )
        return out

    def list_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        n = max(1, min(int(limit), 500))
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT q.id, q.spec_id, s.name, q.status, q.priority, q.created_at,
                   q.started_at, q.finished_at, q.worker, q.attempts, q.error, q.result_json
            FROM experiment_queue q
            JOIN experiment_specs s ON s.spec_id = q.spec_id
            WHERE q.status IN ('completed', 'failed', 'canceled')
            ORDER BY q.finished_at DESC, q.id DESC
            LIMIT ?
            """,
            (n,),
        ).fetchall()
        conn.close()
        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                res = json.loads(r[11] or "{}")
            except Exception:
                res = {}
            out.append(
                {
                    "job_id": int(r[0]),
                    "spec_id": str(r[1]),
                    "name": str(r[2]),
                    "status": str(r[3]),
                    "priority": int(r[4] or 100),
                    "created_at": str(r[5] or ""),
                    "started_at": str(r[6] or ""),
                    "finished_at": str(r[7] or ""),
                    "worker": str(r[8] or ""),
                    "attempts": int(r[9] or 0),
                    "error": str(r[10] or ""),
                    "result": res,
                }
            )
        return out

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        conn = self._connect()
        row = conn.execute(
            """
            SELECT q.id, q.spec_id, s.name, q.status, q.priority, q.created_at,
                   q.started_at, q.finished_at, q.worker, q.attempts, q.error,
                   q.result_json, s.spec_json
            FROM experiment_queue q
            JOIN experiment_specs s ON s.spec_id = q.spec_id
            WHERE q.id = ?
            """,
            (int(job_id),),
        ).fetchone()
        conn.close()
        if not row:
            return None
        try:
            res = json.loads(row[11] or "{}")
        except Exception:
            res = {}
        try:
            spec = json.loads(row[12] or "{}")
        except Exception:
            spec = {}
        return {
            "job_id": int(row[0]),
            "spec_id": str(row[1]),
            "name": str(row[2]),
            "status": str(row[3]),
            "priority": int(row[4] or 100),
            "created_at": str(row[5] or ""),
            "started_at": str(row[6] or ""),
            "finished_at": str(row[7] or ""),
            "worker": str(row[8] or ""),
            "attempts": int(row[9] or 0),
            "error": str(row[10] or ""),
            "result": res,
            "spec": spec,
        }

    def get_cache(self, cache_key: str) -> dict[str, Any] | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT value_json FROM experiment_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        conn.close()
        if not row:
            return None
        try:
            return json.loads(row[0] or "{}")
        except Exception:
            return None

    def set_cache(self, cache_key: str, value: dict[str, Any]) -> None:
        conn = self._connect()
        conn.execute(
            """
            INSERT OR REPLACE INTO experiment_cache (cache_key, value_json, updated_at)
            VALUES (?, ?, ?)
            """,
            (cache_key, json.dumps(value or {}, ensure_ascii=False, default=str), _now()),
        )
        conn.commit()
        conn.close()
