"""
Ultimate版データベース操作モジュール
90列対応の完全な機能を提供
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
import pandas as pd
from typing import Optional, Dict, Any, List
from datetime import datetime


def get_schema_path() -> Path:
    """スキーマファイルのパスを取得"""
    return Path(__file__).parent / "schema_ultimate.sql"


def connect(db_path: Path | str = None) -> sqlite3.Connection:
    """データベース接続"""
    if db_path is None:
        db_path = Path(__file__).parent.parent / "data" / "keiba_ultimate.db"
    
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA foreign_keys = ON;")
    con.execute("PRAGMA journal_mode = WAL;")
    return con


def init_db(con: sqlite3.Connection) -> None:
    """データベース初期化"""
    schema_path = get_schema_path()
    with open(schema_path, 'r', encoding='utf-8') as f:
        schema_sql = f.read()
    con.executescript(schema_sql)
    con.commit()


# ============================================================
# 1. レース情報の操作
# ============================================================

def upsert_race(con: sqlite3.Connection, race_data: Dict[str, Any]) -> None:
    """レース情報の挿入・更新"""
    sql = """
    INSERT OR REPLACE INTO races (
        race_id, race_name, post_time, track_type, distance, course_direction,
        weather, field_condition, kai, venue, day, race_class, horse_count,
        prize_money, market_entropy, top3_probability, kaisai_date, source
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    con.execute(sql, (
        race_data.get('race_id'),
        race_data.get('race_name'),
        race_data.get('post_time'),
        race_data.get('track_type'),
        race_data.get('distance'),
        race_data.get('course_direction'),
        race_data.get('weather'),
        race_data.get('field_condition'),
        race_data.get('kai'),
        race_data.get('venue'),
        race_data.get('day'),
        race_data.get('race_class'),
        race_data.get('horse_count'),
        race_data.get('prize_money'),
        race_data.get('market_entropy'),
        race_data.get('top3_probability'),
        race_data.get('kaisai_date'),
        race_data.get('source', 'scraping')
    ))
    con.commit()


# ============================================================
# 2. 馬詳細情報の操作
# ============================================================

def upsert_horse_details(con: sqlite3.Connection, horse_data: Dict[str, Any]) -> None:
    """馬詳細情報の挿入・更新"""
    sql = """
    INSERT OR REPLACE INTO horse_details (
        horse_id, horse_name, birth_date, coat_color, owner_name, breeder_name,
        breeding_farm, sale_price, total_prize_money, total_runs, total_wins,
        total_seconds, total_thirds, sire, dam, damsire
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    con.execute(sql, (
        horse_data.get('horse_id'),
        horse_data.get('horse_name'),
        horse_data.get('birth_date'),
        horse_data.get('coat_color'),
        horse_data.get('owner_name'),
        horse_data.get('breeder_name'),
        horse_data.get('breeding_farm'),
        horse_data.get('sale_price'),
        horse_data.get('total_prize_money'),
        horse_data.get('total_runs'),
        horse_data.get('total_wins'),
        horse_data.get('total_seconds'),
        horse_data.get('total_thirds'),
        horse_data.get('sire'),
        horse_data.get('dam'),
        horse_data.get('damsire')
    ))
    con.commit()


def get_horse_details(con: sqlite3.Connection, horse_id: str) -> Optional[Dict[str, Any]]:
    """馬詳細情報の取得"""
    sql = "SELECT * FROM horse_details WHERE horse_id = ?"
    cursor = con.execute(sql, (horse_id,))
    row = cursor.fetchone()
    if row is None:
        return None
    
    columns = [desc[0] for desc in cursor.description]
    return dict(zip(columns, row))


# ============================================================
# 3. 騎手情報の操作
# ============================================================

def upsert_jockey_details(con: sqlite3.Connection, jockey_data: Dict[str, Any]) -> None:
    """騎手詳細情報の挿入・更新"""
    sql = """
    INSERT OR REPLACE INTO jockey_details (
        jockey_id, jockey_name, win_rate, place_rate_top2, show_rate, graded_wins, total_races
    ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """
    con.execute(sql, (
        jockey_data.get('jockey_id'),
        jockey_data.get('jockey_name'),
        jockey_data.get('win_rate'),
        jockey_data.get('place_rate_top2'),
        jockey_data.get('show_rate'),
        jockey_data.get('graded_wins'),
        jockey_data.get('total_races')
    ))
    con.commit()


# ============================================================
# 4. 調教師情報の操作
# ============================================================

def upsert_trainer_details(con: sqlite3.Connection, trainer_data: Dict[str, Any]) -> None:
    """調教師詳細情報の挿入・更新"""
    sql = """
    INSERT OR REPLACE INTO trainer_details (
        trainer_id, trainer_name, win_rate, place_rate_top2, show_rate, total_races
    ) VALUES (?, ?, ?, ?, ?, ?)
    """
    con.execute(sql, (
        trainer_data.get('trainer_id'),
        trainer_data.get('trainer_name'),
        trainer_data.get('win_rate'),
        trainer_data.get('place_rate_top2'),
        trainer_data.get('show_rate'),
        trainer_data.get('total_races')
    ))
    con.commit()


# ============================================================
# 5. エントリー情報の操作
# ============================================================

def upsert_entries(con: sqlite3.Connection, race_id: str, entries_list: List[Dict[str, Any]]) -> None:
    """エントリー情報の一括挿入・更新"""
    sql = """
    INSERT OR REPLACE INTO entries (
        race_id, horse_id, horse_name, horse_no, bracket, sex, age, sex_age,
        handicap, jockey_id, jockey_name, trainer_id, trainer_name,
        weight, weight_diff, weight_kg, weight_change, odds, popularity
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    
    rows = []
    for entry in entries_list:
        if not entry.get('horse_id'):
            continue
        rows.append((
            race_id,
            entry.get('horse_id'),
            entry.get('horse_name'),
            entry.get('horse_no'),
            entry.get('bracket'),
            entry.get('sex'),
            entry.get('age'),
            entry.get('sex_age'),
            entry.get('handicap'),
            entry.get('jockey_id'),
            entry.get('jockey_name'),
            entry.get('trainer_id'),
            entry.get('trainer_name'),
            entry.get('weight'),
            entry.get('weight_diff'),
            entry.get('weight_kg'),
            entry.get('weight_change'),
            entry.get('odds'),
            entry.get('popularity')
        ))
    
    con.executemany(sql, rows)
    con.commit()


# ============================================================
# 6. 結果情報の操作
# ============================================================

def upsert_results(con: sqlite3.Connection, race_id: str, results_list: List[Dict[str, Any]]) -> None:
    """結果情報の一括挿入・更新"""
    sql = """
    INSERT OR REPLACE INTO results (
        race_id, horse_id, finish, bracket_number, horse_number, time, margin,
        last3f, last_3f_rank, pass_order, corner_1, corner_2, corner_3, corner_4,
        odds, popularity
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    
    rows = []
    for result in results_list:
        if not result.get('horse_id'):
            continue
        rows.append((
            race_id,
            result.get('horse_id'),
            result.get('finish'),
            result.get('bracket_number'),
            result.get('horse_number'),
            result.get('time'),
            result.get('margin'),
            result.get('last3f'),
            result.get('last_3f_rank'),
            result.get('pass_order'),
            result.get('corner_1'),
            result.get('corner_2'),
            result.get('corner_3'),
            result.get('corner_4'),
            result.get('odds'),
            result.get('popularity')
        ))
    
    con.executemany(sql, rows)
    con.commit()


# ============================================================
# 7. ラップタイム情報の操作
# ============================================================

def upsert_lap_times(con: sqlite3.Connection, race_id: str, lap_data: Dict[str, Any]) -> None:
    """ラップタイム情報の挿入・更新"""
    sql = """
    INSERT OR REPLACE INTO race_lap_times (
        race_id, lap_200m, lap_400m, lap_600m, lap_800m, lap_1000m, lap_1200m,
        lap_1400m, lap_1600m, lap_1800m, lap_2000m, lap_2200m, lap_2400m,
        lap_sect_200m, lap_sect_400m, lap_sect_600m, lap_sect_800m, lap_sect_1000m,
        lap_sect_1200m, lap_sect_1400m, lap_sect_1600m, lap_sect_1800m, lap_sect_2000m,
        lap_sect_2200m, lap_sect_2400m, pace_diff
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    con.execute(sql, (
        race_id,
        lap_data.get('lap_200m'),
        lap_data.get('lap_400m'),
        lap_data.get('lap_600m'),
        lap_data.get('lap_800m'),
        lap_data.get('lap_1000m'),
        lap_data.get('lap_1200m'),
        lap_data.get('lap_1400m'),
        lap_data.get('lap_1600m'),
        lap_data.get('lap_1800m'),
        lap_data.get('lap_2000m'),
        lap_data.get('lap_2200m'),
        lap_data.get('lap_2400m'),
        lap_data.get('lap_sect_200m'),
        lap_data.get('lap_sect_400m'),
        lap_data.get('lap_sect_600m'),
        lap_data.get('lap_sect_800m'),
        lap_data.get('lap_sect_1000m'),
        lap_data.get('lap_sect_1200m'),
        lap_data.get('lap_sect_1400m'),
        lap_data.get('lap_sect_1600m'),
        lap_data.get('lap_sect_1800m'),
        lap_data.get('lap_sect_2000m'),
        lap_data.get('lap_sect_2200m'),
        lap_data.get('lap_sect_2400m'),
        lap_data.get('pace_diff')
    ))
    con.commit()


# ============================================================
# 8. 過去成績情報の操作
# ============================================================

def upsert_past_performances(con: sqlite3.Connection, race_id: str, horse_id: str, past_data: Dict[str, Any]) -> None:
    """過去成績情報の挿入・更新"""
    sql = """
    INSERT INTO past_performances (
        race_id, horse_id, past_performance_1, past_performance_2, past_performance_3,
        prev_race_date, prev_race_venue, prev_race_distance, prev_race_finish,
        prev_race_weight, distance_change, venue_change
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    con.execute(sql, (
        race_id,
        horse_id,
        past_data.get('past_performance_1'),
        past_data.get('past_performance_2'),
        past_data.get('past_performance_3'),
        past_data.get('prev_race_date'),
        past_data.get('prev_race_venue'),
        past_data.get('prev_race_distance'),
        past_data.get('prev_race_finish'),
        past_data.get('prev_race_weight'),
        past_data.get('distance_change'),
        past_data.get('venue_change')
    ))
    con.commit()


# ============================================================
# 9. 払戻情報の操作
# ============================================================

def upsert_payouts(con: sqlite3.Connection, race_id: str, payouts_list: List[Dict[str, Any]]) -> None:
    """払戻情報の一括挿入"""
    sql = """
    INSERT INTO payouts (race_id, bet_type, combination, payout, popularity)
    VALUES (?, ?, ?, ?, ?)
    """
    
    rows = []
    for payout in payouts_list:
        rows.append((
            race_id,
            payout.get('bet_type'),
            payout.get('combination'),
            payout.get('payout'),
            payout.get('popularity')
        ))
    
    con.executemany(sql, rows)
    con.commit()


# ============================================================
# 10. クエリヘルパー関数
# ============================================================

def get_ml_training_data(con: sqlite3.Connection, limit: Optional[int] = None) -> pd.DataFrame:
    """機械学習用のトレーニングデータを取得"""
    sql = "SELECT * FROM ml_training_data"
    if limit:
        sql += f" LIMIT {limit}"
    return pd.read_sql_query(sql, con)


def get_race_full_data(con: sqlite3.Connection, race_id: str) -> Dict[str, Any]:
    """レースの完全なデータを取得"""
    result = {
        'race_info': None,
        'entries': [],
        'results': [],
        'lap_times': None,
        'payouts': []
    }
    
    # レース基本情報
    sql = "SELECT * FROM races WHERE race_id = ?"
    df = pd.read_sql_query(sql, con, params=(race_id,))
    if len(df) > 0:
        result['race_info'] = df.iloc[0].to_dict()
    
    # エントリー
    sql = "SELECT * FROM entries WHERE race_id = ?"
    result['entries'] = pd.read_sql_query(sql, con, params=(race_id,)).to_dict('records')
    
    # 結果
    sql = "SELECT * FROM results WHERE race_id = ?"
    result['results'] = pd.read_sql_query(sql, con, params=(race_id,)).to_dict('records')
    
    # ラップタイム
    sql = "SELECT * FROM race_lap_times WHERE race_id = ?"
    df = pd.read_sql_query(sql, con, params=(race_id,))
    if len(df) > 0:
        result['lap_times'] = df.iloc[0].to_dict()
    
    # 払戻
    sql = "SELECT * FROM payouts WHERE race_id = ?"
    result['payouts'] = pd.read_sql_query(sql, con, params=(race_id,)).to_dict('records')
    
    return result


def get_database_stats(con: sqlite3.Connection) -> Dict[str, int]:
    """データベースの統計情報を取得"""
    tables = ['races', 'entries', 'results', 'horse_details', 'jockey_details', 
              'trainer_details', 'past_performances', 'race_lap_times', 'payouts']
    
    stats = {}
    for table in tables:
        cursor = con.execute(f"SELECT COUNT(*) FROM {table}")
        stats[table] = cursor.fetchone()[0]
    
    return stats
