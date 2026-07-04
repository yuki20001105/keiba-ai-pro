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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rru_race_id ON race_results_ultimate (race_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_return_race_id ON return_tables_ultimate (race_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scraped_dates (
                date TEXT PRIMARY KEY,
                race_count INTEGER DEFAULT 0,
                no_race INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS training_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_id TEXT NOT NULL,
                horse_number INTEGER,
                horse_name TEXT,
                training_date TEXT,
                course TEXT,
                track_condition TEXT,
                rider TEXT,
                time_6f REAL,
                time_5f REAL,
                time_4f REAL,
                time_3f REAL,
                time_1f REAL,
                lap_6f_5f REAL,
                lap_5f_4f REAL,
                lap_4f_3f REAL,
                lap_3f_1f REAL,
                lap_1f_g REAL,
                position TEXT,
                pace TEXT,
                grade TEXT,
                comment TEXT,
                is_last_training INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_training_race_id ON training_data (race_id)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_training_unique ON training_data (race_id, horse_number, training_date, course)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS speed_figures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_id TEXT NOT NULL,
                horse_number INTEGER,
                horse_name TEXT,
                max_index INTEGER,
                avg_5_index INTEGER,
                dist_max_index INTEGER,
                course_max_index INTEGER,
                index_3ago INTEGER,
                index_2ago INTEGER,
                index_last INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_speed_unique ON speed_figures (race_id, horse_number)")
        conn.commit()
        conn.close()
        logger.debug(f"SQLite初期化完了(WAL): {db_path}")
    except Exception as e:
        logger.warning(f"SQLite初期化失敗: {e}")


def _save_race_sqlite_only(race_data: dict, db_path: Path) -> bool:
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
        if return_tables:
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


def _save_race_to_ultimate_db(race_data: dict, db_path: Path) -> bool:
    """スクレイピング結果を keiba_ultimate.db (SQLite) に保存。_save_race_sqlite_only に委譲。"""
    return _save_race_sqlite_only(race_data, db_path)


# ============================================================
# 調教タイムデータの保存
# ============================================================

def _save_training_data(race_id: str, training_records: list, db_path: Path) -> int:
    """調教タイムレコードを SQLite の training_data テーブルに保存する。
    既存レコードは IGNORE（重複スキップ）。
    Returns: 挿入件数
    """
    if not training_records:
        return 0
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS training_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_id TEXT NOT NULL,
                horse_number INTEGER,
                horse_name TEXT,
                training_date TEXT,
                course TEXT,
                track_condition TEXT,
                rider TEXT,
                time_6f REAL,
                time_5f REAL,
                time_4f REAL,
                time_3f REAL,
                time_1f REAL,
                lap_6f_5f REAL,
                lap_5f_4f REAL,
                lap_4f_3f REAL,
                lap_3f_1f REAL,
                lap_1f_g REAL,
                position TEXT,
                pace TEXT,
                grade TEXT,
                comment TEXT,
                is_last_training INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_training_race_id ON training_data (race_id)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_training_unique ON training_data (race_id, horse_number, training_date, course)")

        inserted = 0
        for rec in training_records:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO training_data
                       (race_id, horse_number, horse_name, training_date, course, track_condition,
                        rider, time_6f, time_5f, time_4f, time_3f, time_1f,
                        lap_6f_5f, lap_5f_4f, lap_4f_3f, lap_3f_1f, lap_1f_g,
                        position, pace, grade, comment, is_last_training)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        rec.get("race_id", race_id),
                        rec.get("horse_number"),
                        rec.get("horse_name", ""),
                        rec.get("training_date", ""),
                        rec.get("course", ""),
                        rec.get("track_condition", ""),
                        rec.get("rider", ""),
                        rec.get("time_6f"),
                        rec.get("time_5f"),
                        rec.get("time_4f"),
                        rec.get("time_3f"),
                        rec.get("time_1f"),
                        rec.get("lap_6f_5f"),
                        rec.get("lap_5f_4f"),
                        rec.get("lap_4f_3f"),
                        rec.get("lap_3f_1f"),
                        rec.get("lap_1f_g"),
                        rec.get("position", ""),
                        rec.get("pace", ""),
                        rec.get("grade", ""),
                        rec.get("comment", ""),
                        1 if rec.get("is_last_training") else 0,
                    ),
                )
                inserted += conn.execute("SELECT changes()").fetchone()[0]
            except Exception as _re:
                logger.debug(f"training_data insert skip {race_id}: {_re}")
        conn.commit()
        conn.close()
        return inserted
    except Exception as e:
        logger.warning(f"training_data 保存失敗 {race_id}: {e}")
        return 0


def _save_speed_figures(race_id: str, records: list, db_path: Path) -> int:
    """タイム指数レコードを SQLite の speed_figures テーブルに保存する。
    既存レコードは REPLACE（上書き更新）。
    Returns: 挿入・更新件数
    """
    if not records:
        return 0
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS speed_figures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_id TEXT NOT NULL,
                horse_number INTEGER,
                horse_name TEXT,
                max_index INTEGER,
                avg_5_index INTEGER,
                dist_max_index INTEGER,
                course_max_index INTEGER,
                index_3ago INTEGER,
                index_2ago INTEGER,
                index_last INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_speed_unique ON speed_figures (race_id, horse_number)")

        inserted = 0
        for rec in records:
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO speed_figures
                       (race_id, horse_number, horse_name,
                        max_index, avg_5_index, dist_max_index, course_max_index,
                        index_3ago, index_2ago, index_last)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        rec.get("race_id", race_id),
                        rec.get("horse_number"),
                        rec.get("horse_name", ""),
                        rec.get("max_index"),
                        rec.get("avg_5_index"),
                        rec.get("dist_max_index"),
                        rec.get("course_max_index"),
                        rec.get("index_3ago"),
                        rec.get("index_2ago"),
                        rec.get("index_last"),
                    ),
                )
                inserted += conn.execute("SELECT changes()").fetchone()[0]
            except Exception as _re:
                logger.debug(f"speed_figures insert skip {race_id}: {_re}")
        conn.commit()
        conn.close()
        return inserted
    except Exception as e:
        logger.warning(f"speed_figures 保存失敗 {race_id}: {e}")
        return 0


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
