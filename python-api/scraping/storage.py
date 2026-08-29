"""
SQLite / Supabase 永続化ヘルパー
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections import Counter
from datetime import datetime, timezone
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
        # A zero-row fetch is not proof that no races were held. Older versions
        # wrote no_race=1 after network/HTML failures, so only proven DB coverage
        # may suppress a future request.
        rows = conn.execute(
            "SELECT date FROM scraped_dates WHERE race_count >= ? AND date < ?",
            (min_races, _cutoff),
        ).fetchall()
        conn.close()
        for row in rows:
            result.add(row[0])
    except Exception:
        pass
    return result


def _save_scraped_date_sqlite(
    db_path: Path,
    date: str,
    race_count: int,
    *,
    verified_no_race: bool = False,
) -> None:
    """日付の取得状態を SQLite の scraped_dates テーブルに記録する。
    race_count=0 → no_race=1（開催無し確定）としてマーク。
    """
    try:
        # Fail closed: an empty scrape may mean an upstream error. Only a
        # separately verified calendar result may be stored as no_race.
        no_race = 1 if race_count == 0 and verified_no_race else 0
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


def _normalize_scraped_date(value: object) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))[:8]
    if len(digits) != 8:
        return None
    try:
        datetime.strptime(digits, "%Y%m%d")
    except ValueError:
        return None
    return digits


def reconcile_scraped_dates_from_races(
    db_path: Path,
    *,
    min_races: int = 6,
    apply: bool = False,
) -> dict:
    """Rebuild the coverage ledger from authoritative race rows.

    Only ``scraped_dates`` is updated. Before-images are appended to an audit
    table so every applied change is reversible and attributable to one run.
    """
    run_id = f"coverage-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    protected_tables = (
        "races_ultimate",
        "race_results_ultimate",
        "return_tables_ultimate",
    )

    def _count_rows(conn: sqlite3.Connection, table: str) -> int:
        try:
            return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        except sqlite3.OperationalError:
            return 0

    conn = sqlite3.connect(str(db_path), timeout=60)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=60000")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS scraped_dates (
                date TEXT PRIMARY KEY,
                race_count INTEGER DEFAULT 0,
                no_race INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        protected_before = {table: _count_rows(conn, table) for table in protected_tables}

        observed: Counter[str] = Counter()
        invalid_race_ids: list[str] = []
        rows = conn.execute("SELECT race_id, data FROM races_ultimate").fetchall()
        for race_id, payload_text in rows:
            try:
                payload = json.loads(payload_text or "{}")
            except (TypeError, json.JSONDecodeError):
                payload = {}
            race_date = None
            if isinstance(payload, dict):
                for field in ("race_date", "date", "kaisai_date"):
                    race_date = _normalize_scraped_date(payload.get(field))
                    if race_date:
                        break
            race_date = race_date or _normalize_scraped_date(str(race_id or "")[:8])
            if race_date:
                observed[race_date] += 1
            else:
                invalid_race_ids.append(str(race_id))

        existing = {
            str(date): (int(race_count or 0), int(no_race or 0))
            for date, race_count, no_race in conn.execute(
                "SELECT date, race_count, no_race FROM scraped_dates"
            )
        }
        changes: list[tuple[str, int, int, int, int]] = []
        inserted_dates = 0
        updated_dates = 0
        cleared_no_race_dates = 0
        for date, observed_count in sorted(observed.items()):
            previous = existing.get(date)
            previous_count = previous[0] if previous else 0
            previous_no_race = previous[1] if previous else 0
            if previous is None or previous_count != observed_count or previous_no_race != 0:
                changes.append(
                    (date, 1 if previous is not None else 0, previous_count, previous_no_race, observed_count)
                )
                if previous is None:
                    inserted_dates += 1
                else:
                    updated_dates += 1
                if previous_no_race:
                    cleared_no_race_dates += 1

        report = {
            "run_id": run_id,
            "database": str(db_path.resolve()),
            "mode": "apply" if apply else "dry-run",
            "min_races": int(min_races),
            "race_rows_scanned": len(rows),
            "observed_dates": len(observed),
            "complete_observed_dates": sum(1 for count in observed.values() if count >= min_races),
            "incomplete_observed_dates": sum(1 for count in observed.values() if count < min_races),
            "invalid_race_ids": len(invalid_race_ids),
            "changes_required": len(changes),
            "inserted_dates": inserted_dates,
            "updated_dates": updated_dates,
            "cleared_no_race_dates": cleared_no_race_dates,
            "protected_counts_before": protected_before,
        }
        if not apply:
            conn.rollback()
            return report

        applied_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS scraped_dates_reconcile_audit (
                run_id TEXT NOT NULL,
                date TEXT NOT NULL,
                previous_exists INTEGER NOT NULL,
                previous_race_count INTEGER NOT NULL,
                previous_no_race INTEGER NOT NULL,
                observed_race_count INTEGER NOT NULL,
                applied_at TEXT NOT NULL,
                PRIMARY KEY (run_id, date)
            )"""
        )
        conn.executemany(
            """INSERT INTO scraped_dates_reconcile_audit (
                   run_id, date, previous_exists, previous_race_count,
                   previous_no_race, observed_race_count, applied_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [(run_id, *change, applied_at) for change in changes],
        )
        conn.executemany(
            """INSERT INTO scraped_dates (date, race_count, no_race)
               VALUES (?, ?, 0)
               ON CONFLICT(date) DO UPDATE SET
                   race_count = excluded.race_count,
                   no_race = 0""",
            [(date, count) for date, _exists, _old_count, _old_no_race, count in changes],
        )

        protected_after = {table: _count_rows(conn, table) for table in protected_tables}
        if protected_after != protected_before:
            conn.rollback()
            raise RuntimeError("Protected race table counts changed; reconciliation was rolled back")
        quick_check = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        if quick_check.lower() != "ok":
            conn.rollback()
            raise RuntimeError(f"SQLite quick_check failed: {quick_check}")
        conn.commit()
        report["protected_counts_after"] = protected_after
        report["quick_check"] = quick_check
        report["audit_rows_written"] = len(changes)
        report["ledger_complete_dates_after"] = int(
            conn.execute("SELECT COUNT(*) FROM scraped_dates WHERE race_count >= ?", (min_races,)).fetchone()[0]
        )
        return report
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
