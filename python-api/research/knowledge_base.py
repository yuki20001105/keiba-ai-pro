from __future__ import annotations

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


class ResearchKnowledgeBase:
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
            CREATE TABLE IF NOT EXISTS research_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                source_job_id INTEGER,
                signal_type TEXT NOT NULL,
                feature_family TEXT,
                condition_label TEXT,
                metric TEXT NOT NULL,
                lift REAL NOT NULL,
                confidence REAL NOT NULL,
                evidence_json TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rk_signal_metric ON research_signals(metric)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rk_signal_family ON research_signals(feature_family)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS research_recommendations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                goal_text TEXT,
                recommendation_json TEXT NOT NULL,
                score REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'proposed'
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rk_rec_score ON research_recommendations(score)")
        conn.commit()
        conn.close()

    def add_signal(
        self,
        *,
        source_job_id: int | None,
        signal_type: str,
        metric: str,
        lift: float,
        confidence: float,
        feature_family: str = "",
        condition_label: str = "",
        evidence: dict[str, Any] | None = None,
    ) -> int:
        conn = self._connect()
        cur = conn.execute(
            """
            INSERT INTO research_signals (
                created_at, source_job_id, signal_type, feature_family,
                condition_label, metric, lift, confidence, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _now(),
                (int(source_job_id) if source_job_id is not None else None),
                str(signal_type or ""),
                str(feature_family or ""),
                str(condition_label or ""),
                str(metric or "roi"),
                float(lift),
                float(confidence),
                json.dumps(evidence or {}, ensure_ascii=False, default=str),
            ),
        )
        rid = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return rid

    def list_signals(self, *, metric: str = "", limit: int = 100) -> list[dict[str, Any]]:
        n = max(1, min(int(limit), 1000))
        conn = self._connect()
        if metric:
            rows = conn.execute(
                """
                SELECT id, created_at, source_job_id, signal_type, feature_family,
                       condition_label, metric, lift, confidence, evidence_json
                FROM research_signals
                WHERE metric = ?
                ORDER BY confidence DESC, lift DESC, id DESC
                LIMIT ?
                """,
                (metric, n),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, created_at, source_job_id, signal_type, feature_family,
                       condition_label, metric, lift, confidence, evidence_json
                FROM research_signals
                ORDER BY confidence DESC, lift DESC, id DESC
                LIMIT ?
                """,
                (n,),
            ).fetchall()
        conn.close()

        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                evidence = json.loads(r[9] or "{}")
            except Exception:
                evidence = {}
            out.append(
                {
                    "id": int(r[0]),
                    "created_at": str(r[1] or ""),
                    "source_job_id": (int(r[2]) if r[2] is not None else None),
                    "signal_type": str(r[3] or ""),
                    "feature_family": str(r[4] or ""),
                    "condition_label": str(r[5] or ""),
                    "metric": str(r[6] or ""),
                    "lift": float(r[7] or 0.0),
                    "confidence": float(r[8] or 0.0),
                    "evidence": evidence,
                }
            )
        return out

    def add_recommendation(self, *, goal_text: str, recommendation: dict[str, Any], score: float) -> int:
        conn = self._connect()
        cur = conn.execute(
            """
            INSERT INTO research_recommendations (
                created_at, goal_text, recommendation_json, score, status
            ) VALUES (?, ?, ?, ?, 'proposed')
            """,
            (
                _now(),
                str(goal_text or ""),
                json.dumps(recommendation or {}, ensure_ascii=False, default=str),
                float(score),
            ),
        )
        rid = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return rid

    def list_recommendations(self, *, limit: int = 50) -> list[dict[str, Any]]:
        n = max(1, min(int(limit), 500))
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT id, created_at, goal_text, recommendation_json, score, status
            FROM research_recommendations
            ORDER BY score DESC, id DESC
            LIMIT ?
            """,
            (n,),
        ).fetchall()
        conn.close()
        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                rec = json.loads(r[3] or "{}")
            except Exception:
                rec = {}
            out.append(
                {
                    "id": int(r[0]),
                    "created_at": str(r[1] or ""),
                    "goal_text": str(r[2] or ""),
                    "recommendation": rec,
                    "score": float(r[4] or 0.0),
                    "status": str(r[5] or ""),
                }
            )
        return out
