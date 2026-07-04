from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_store_path() -> Path:
    return _repo_root() / "keiba" / "data" / "feature_store.db"


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _md5_text(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _schema_hash(columns: list[str]) -> str:
    return _md5_text("|".join(sorted(columns)))


def _load_rules(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        obj = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _git_hash() -> str:
    try:
        cp = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_repo_root()),
            capture_output=True,
            text=True,
            check=False,
        )
        out = (cp.stdout or "").strip()
        return out or "unknown"
    except Exception:
        return "unknown"


class FeatureStoreManager:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or _default_store_path()
        self.rules_path = Path(__file__).with_name("validation_rules.yaml")
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
            CREATE TABLE IF NOT EXISTS feature_sets (
                version_id TEXT PRIMARY KEY,
                feature_set_name TEXT NOT NULL,
                target TEXT NOT NULL,
                model_id TEXT,
                created_at TEXT NOT NULL,
                git_hash TEXT,
                schema_hash TEXT,
                source_hash TEXT,
                row_count INTEGER NOT NULL,
                feature_count INTEGER NOT NULL,
                quality_score REAL NOT NULL,
                validation_error_count INTEGER NOT NULL,
                metadata_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feature_store_rows (
                version_id TEXT NOT NULL,
                race_id TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                feature_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(version_id, race_id, entity_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fs_version ON feature_store_rows(version_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fs_race ON feature_store_rows(race_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feature_registry (
                feature_name TEXT PRIMARY KEY,
                dtype TEXT,
                first_seen_version TEXT,
                last_seen_version TEXT,
                null_rate REAL,
                min_value REAL,
                max_value REAL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feature_lineage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version_id TEXT NOT NULL,
                source_table TEXT NOT NULL,
                transform_hash TEXT,
                validation_hash TEXT,
                notes TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feature_quality (
                version_id TEXT PRIMARY KEY,
                score REAL NOT NULL,
                stats_json TEXT NOT NULL,
                issues_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
        conn.close()

    def _validate(self, df: pd.DataFrame, feature_columns: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        rules = _load_rules(self.rules_path)
        issues: list[dict[str, Any]] = []
        stats: dict[str, Any] = {"features": {}, "row_count": int(len(df))}

        for col in feature_columns:
            if col not in df.columns:
                continue
            s = df[col]
            null_rate = float(s.isna().mean()) if len(s) > 0 else 0.0
            col_stat = {"null_rate": null_rate}
            if pd.api.types.is_numeric_dtype(s):
                col_stat["min"] = float(s.min(skipna=True)) if s.notna().any() else None
                col_stat["max"] = float(s.max(skipna=True)) if s.notna().any() else None
                col_stat["mean"] = float(s.mean(skipna=True)) if s.notna().any() else None
                col_stat["std"] = float(s.std(skipna=True)) if s.notna().any() else None
            stats["features"][col] = col_stat

        ranges = rules.get("ranges") if isinstance(rules.get("ranges"), dict) else {}
        for col, rule in ranges.items():
            if col not in df.columns or not isinstance(rule, dict):
                continue
            s = pd.to_numeric(df[col], errors="coerce")
            vmin = rule.get("min")
            vmax = rule.get("max")
            if isinstance(vmin, (int, float)):
                bad = int((s < float(vmin)).fillna(False).sum())
                if bad > 0:
                    issues.append({"feature": col, "type": "min_violation", "count": bad, "threshold": float(vmin)})
            if isinstance(vmax, (int, float)):
                bad = int((s > float(vmax)).fillna(False).sum())
                if bad > 0:
                    issues.append({"feature": col, "type": "max_violation", "count": bad, "threshold": float(vmax)})

        required_non_null = rules.get("required_non_null") if isinstance(rules.get("required_non_null"), list) else []
        for col in required_non_null:
            if col not in df.columns:
                issues.append({"feature": str(col), "type": "missing_required_column", "count": int(len(df))})
                continue
            miss = int(df[col].isna().sum())
            if miss > 0:
                issues.append({"feature": str(col), "type": "required_non_null_violation", "count": miss})

        return issues, stats

    def _quality_score(self, stats: dict[str, Any], issues: list[dict[str, Any]]) -> float:
        feature_stats = stats.get("features") if isinstance(stats.get("features"), dict) else {}
        if not feature_stats:
            return 0.0
        avg_null = 0.0
        n = 0
        for val in feature_stats.values():
            if isinstance(val, dict):
                avg_null += float(val.get("null_rate", 0.0))
                n += 1
        avg_null = (avg_null / float(n)) if n > 0 else 1.0

        score = 100.0
        score -= avg_null * 60.0
        score -= min(float(len(issues)) * 2.0, 40.0)
        if score < 0.0:
            score = 0.0
        if score > 100.0:
            score = 100.0
        return round(score, 2)

    def materialize_from_training_frame(
        self,
        *,
        df: pd.DataFrame,
        feature_columns: list[str],
        target: str,
        model_id: str,
        training_date_from: str | None,
        training_date_to: str | None,
        feature_set_name: str = "default",
        source_hash: str = "",
    ) -> dict[str, Any]:
        if "race_id" not in df.columns:
            raise ValueError("race_id column is required for feature materialization")

        cols = [c for c in feature_columns if c in df.columns]
        if not cols:
            cols = [
                c for c in df.columns
                if c not in {target, "race_id", "horse_id", "jockey_id", "trainer_id", "owner_id"}
            ]

        entity_col = "horse_id" if "horse_id" in df.columns else None
        if entity_col is None and "horse_number" in df.columns:
            entity_col = "horse_number"

        frame = df[["race_id"] + ([entity_col] if entity_col else []) + cols].copy()
        if entity_col:
            frame["entity_id"] = frame[entity_col].astype(str)
        else:
            frame["entity_id"] = frame.index.astype(str)

        issues, stats = self._validate(frame, cols)
        score = self._quality_score(stats, issues)

        version_id = datetime.utcnow().strftime("%Y%m%d%H%M%S") + "_" + _md5_text(model_id)[:8]
        schema = _schema_hash(cols)
        git = _git_hash()
        created_at = _utcnow()

        metadata = {
            "training_date_from": training_date_from,
            "training_date_to": training_date_to,
            "model_id": model_id,
            "feature_columns": cols,
            "entity_column": entity_col,
        }

        conn = self._connect()
        conn.execute(
            """
            INSERT INTO feature_sets (
                version_id, feature_set_name, target, model_id, created_at,
                git_hash, schema_hash, source_hash, row_count, feature_count,
                quality_score, validation_error_count, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                feature_set_name,
                target,
                model_id,
                created_at,
                git,
                schema,
                source_hash,
                int(len(frame)),
                int(len(cols)),
                float(score),
                int(len(issues)),
                json.dumps(metadata, ensure_ascii=False),
            ),
        )

        rows: list[tuple[str, str, str, str, str]] = []
        for rec in frame[["race_id", "entity_id"] + cols].to_dict(orient="records"):
            race_id = str(rec.get("race_id", ""))
            entity_id = str(rec.get("entity_id", ""))
            feature_json = {k: rec.get(k) for k in cols}
            rows.append(
                (
                    version_id,
                    race_id,
                    entity_id,
                    json.dumps(feature_json, ensure_ascii=False, default=str),
                    created_at,
                )
            )

        conn.executemany(
            """
            INSERT OR REPLACE INTO feature_store_rows (
                version_id, race_id, entity_id, feature_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )

        for col in cols:
            s = frame[col]
            dtype = str(s.dtype)
            null_rate = float(s.isna().mean()) if len(s) > 0 else 0.0
            min_value = float(pd.to_numeric(s, errors="coerce").min(skipna=True)) if pd.to_numeric(s, errors="coerce").notna().any() else None
            max_value = float(pd.to_numeric(s, errors="coerce").max(skipna=True)) if pd.to_numeric(s, errors="coerce").notna().any() else None
            first_row = conn.execute(
                "SELECT first_seen_version FROM feature_registry WHERE feature_name = ?",
                (col,),
            ).fetchone()
            first_seen = str(first_row[0]) if first_row and first_row[0] else version_id
            conn.execute(
                """
                INSERT OR REPLACE INTO feature_registry (
                    feature_name, dtype, first_seen_version, last_seen_version,
                    null_rate, min_value, max_value, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    col,
                    dtype,
                    first_seen,
                    version_id,
                    null_rate,
                    min_value,
                    max_value,
                    created_at,
                ),
            )

        conn.execute(
            """
            INSERT INTO feature_lineage (
                version_id, source_table, transform_hash, validation_hash, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                "race_results_ultimate",
                source_hash,
                _md5_text(json.dumps(_load_rules(self.rules_path), ensure_ascii=False, sort_keys=True)),
                "materialized_from_training_frame",
                created_at,
            ),
        )

        conn.execute(
            """
            INSERT OR REPLACE INTO feature_quality (
                version_id, score, stats_json, issues_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                version_id,
                float(score),
                json.dumps(stats, ensure_ascii=False, default=str),
                json.dumps(issues, ensure_ascii=False),
                created_at,
            ),
        )

        conn.commit()
        conn.close()

        return {
            "version_id": version_id,
            "quality_score": float(score),
            "row_count": int(len(frame)),
            "feature_count": int(len(cols)),
            "validation_error_count": int(len(issues)),
            "git_hash": git,
            "schema_hash": schema,
        }

    def list_feature_sets(self, limit: int = 20) -> list[dict[str, Any]]:
        n = max(1, min(int(limit), 200))
        conn = self._connect()
        cur = conn.execute(
            """
            SELECT version_id, feature_set_name, target, model_id, created_at,
                   git_hash, schema_hash, row_count, feature_count,
                   quality_score, validation_error_count
            FROM feature_sets
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (n,),
        )
        rows = cur.fetchall()
        conn.close()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "version_id": str(r[0]),
                    "feature_set_name": str(r[1]),
                    "target": str(r[2]),
                    "model_id": str(r[3] or ""),
                    "created_at": str(r[4]),
                    "git_hash": str(r[5] or ""),
                    "schema_hash": str(r[6] or ""),
                    "row_count": int(r[7] or 0),
                    "feature_count": int(r[8] or 0),
                    "quality_score": float(r[9] or 0.0),
                    "validation_error_count": int(r[10] or 0),
                }
            )
        return out

    def get_latest_quality(self) -> dict[str, Any] | None:
        conn = self._connect()
        row = conn.execute(
            """
            SELECT fs.version_id, fs.created_at, fs.quality_score, fs.validation_error_count,
                   fq.stats_json, fq.issues_json
            FROM feature_sets fs
            LEFT JOIN feature_quality fq ON fq.version_id = fs.version_id
            ORDER BY fs.created_at DESC
            LIMIT 1
            """
        ).fetchone()
        conn.close()
        if not row:
            return None
        return {
            "version_id": str(row[0]),
            "created_at": str(row[1]),
            "quality_score": float(row[2] or 0.0),
            "validation_error_count": int(row[3] or 0),
            "stats": json.loads(row[4] or "{}"),
            "issues": json.loads(row[5] or "[]"),
        }

    def evaluate_gate(self, min_score: float = 95.0, max_validation_errors: int = 0) -> dict[str, Any]:
        latest = self.get_latest_quality()
        if not latest:
            return {
                "allow_training": False,
                "reasons": ["no_feature_set_available"],
            }
        reasons: list[str] = []
        score = float(latest.get("quality_score", 0.0))
        errs = int(latest.get("validation_error_count", 0))
        if score < float(min_score):
            reasons.append(f"quality_score {score:.2f} < {float(min_score):.2f}")
        if errs > int(max_validation_errors):
            reasons.append(f"validation_error_count {errs} > {int(max_validation_errors)}")
        return {
            "allow_training": len(reasons) == 0,
            "reasons": reasons,
            "latest": latest,
            "thresholds": {
                "min_score": float(min_score),
                "max_validation_errors": int(max_validation_errors),
            },
        }
