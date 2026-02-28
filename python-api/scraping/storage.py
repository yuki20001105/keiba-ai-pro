"""
SQLite / Supabase 永続化ヘルパー
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app_config import logger  # type: ignore


def _init_sqlite_db(db_path: Path) -> None:
    """WALモード設定 + テーブル事前作成（毎レースのDDL重複を削減）"""
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS races_ultimate (
                race_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS race_results_ultimate (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_id TEXT,
                data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()
        logger.debug(f"SQLite初期化完了(WAL): {db_path}")
    except Exception as e:
        logger.warning(f"SQLite初期化失敗: {e}")


def _save_race_sqlite_only(race_data: dict, db_path: Path, overwrite: bool = True) -> bool:
    """スクレイピング結果を SQLite のみに保存（Supabase 非対応）"""
    race_info = race_data["race_info"]
    horses = race_data["horses"]
    race_id = race_info["race_id"]
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS races_ultimate (
                race_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute(
            "INSERT OR REPLACE INTO races_ultimate (race_id, data) VALUES (?, ?)",
            (race_id, json.dumps(race_info, ensure_ascii=False)),
        )
        cur.execute("""
            CREATE TABLE IF NOT EXISTS race_results_ultimate (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_id TEXT,
                data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        if overwrite:
            cur.execute("DELETE FROM race_results_ultimate WHERE race_id = ?", (race_id,))
        for h in horses:
            cur.execute(
                "INSERT INTO race_results_ultimate (race_id, data) VALUES (?, ?)",
                (race_id, json.dumps(h, ensure_ascii=False)),
            )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.warning(f"SQLite 保存失敗 {race_id}: {e}")
        return False


def _save_race_to_ultimate_db(race_data: dict, db_path: Path, overwrite: bool = True) -> bool:
    """スクレイピング結果を keiba_ultimate.db と Supabase の両方に保存"""
    # ローカル import で循環依存回避
    from app_config import SUPABASE_ENABLED, save_race_to_supabase  # type: ignore

    race_info = race_data["race_info"]
    horses = race_data["horses"]
    race_id = race_info["race_id"]
    supabase_ok = False

    if SUPABASE_ENABLED:
        try:
            save_race_to_supabase(race_data)
            supabase_ok = True
        except Exception as e:
            logger.warning(f"Supabase 保存失敗 {race_id}: {e}")

    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS races_ultimate (
                race_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute(
            "INSERT OR REPLACE INTO races_ultimate (race_id, data) VALUES (?, ?)",
            (race_id, json.dumps(race_info, ensure_ascii=False)),
        )
        cur.execute("""
            CREATE TABLE IF NOT EXISTS race_results_ultimate (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_id TEXT,
                data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        if overwrite:
            cur.execute("DELETE FROM race_results_ultimate WHERE race_id = ?", (race_id,))
        for h in horses:
            cur.execute(
                "INSERT INTO race_results_ultimate (race_id, data) VALUES (?, ?)",
                (race_id, json.dumps(h, ensure_ascii=False)),
            )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.warning(f"SQLite 保存失敗 {race_id}: {e}")
        return supabase_ok
