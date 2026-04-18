"""
SQLite / Supabase 永続化ヘルパー
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app_config import logger  # type: ignore

# Supabase 連携は削除済み。データは SQLite (keiba_ultimate.db) のみに保存する。


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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS return_tables_ultimate (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_id TEXT NOT NULL,
                bet_type TEXT NOT NULL,
                combinations TEXT NOT NULL,
                payout INTEGER NOT NULL,
                popularity INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_return_race_id ON return_tables_ultimate (race_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scraped_dates (
                date TEXT PRIMARY KEY,
                race_count INTEGER DEFAULT 0,
                no_race INTEGER DEFAULT 0,
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
    return_tables = race_data.get("return_tables", [])
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
        # ── 払い戻し表 ──
        cur.execute("""
            CREATE TABLE IF NOT EXISTS return_tables_ultimate (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_id TEXT NOT NULL,
                bet_type TEXT NOT NULL,
                combinations TEXT NOT NULL,
                payout INTEGER NOT NULL,
                popularity INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        if overwrite and return_tables:
            cur.execute("DELETE FROM return_tables_ultimate WHERE race_id = ?", (race_id,))
        for rt in return_tables:
            cur.execute(
                """INSERT INTO return_tables_ultimate (race_id, bet_type, combinations, payout, popularity)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    race_id,
                    rt.get("bet_type", ""),
                    rt.get("combinations", ""),
                    rt.get("payout", 0),
                    rt.get("popularity"),
                ),
            )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.warning(f"SQLite 保存失敗 {race_id}: {e}")
        return False


def _save_race_to_ultimate_db(race_data: dict, db_path: Path, overwrite: bool = True) -> bool:
    """スクレイピング結果を keiba_ultimate.db (SQLite) に保存"""
    race_info = race_data["race_info"]
    horses = race_data["horses"]
    race_id = race_info["race_id"]

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
        return False


# ============================================================
# スクレイピング済み日付の管理（SQLite ローカル、SUPABASE不要）
# ============================================================

def _get_scraped_dates_sqlite(db_path: Path, min_races: int = 6) -> set:
    """SQLite の scraped_dates テーブルから取得済み日付を返す。
    race_count >= min_races または no_race=1（開催無し確定）の日付をスキップ対象として返す。
    ⚠️ 直近14日以内の日付は常に再スクレイプ対象とする（当日・直近の誤スキップ防止）。
    テーブルが存在しない場合は空集合を返す（初回起動時など）。
    """
    result: set = set()
    try:
        if not db_path.exists():
            return result
        import datetime as _dt
        _cutoff = (_dt.date.today() - _dt.timedelta(days=14)).strftime("%Y%m%d")
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT date FROM scraped_dates WHERE (race_count >= ? OR no_race = 1) AND date < ?",
            (min_races, _cutoff),
        ).fetchall()
        conn.close()
        for row in rows:
            result.add(row[0])
    except Exception:
        pass
    return result


def _save_scraped_date_sqlite(db_path: Path, date: str, race_count: int) -> None:
    """日付の取得状態を SQLite の scraped_dates テーブルに記録する。
    race_count=0 → no_race=1（開催無し確定）としてマーク。
    """
    try:
        no_race = 1 if race_count == 0 else 0
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS scraped_dates (
                date TEXT PRIMARY KEY,
                race_count INTEGER DEFAULT 0,
                no_race INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
        )
        conn.execute(
            "INSERT OR REPLACE INTO scraped_dates (date, race_count, no_race) VALUES (?, ?, ?)",
            (date, race_count, no_race),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
