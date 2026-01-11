from __future__ import annotations
import sqlite3
from pathlib import Path
import pandas as pd

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS races (
  race_id TEXT PRIMARY KEY,
  kaisai_date TEXT,
  source TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS entries (
  race_id TEXT,
  horse_id TEXT,
  horse_name TEXT,
  horse_no INTEGER,
  bracket INTEGER,
  sex TEXT,
  age INTEGER,
  handicap REAL,
  jockey_id TEXT,
  jockey_name TEXT,
  trainer_id TEXT,
  trainer_name TEXT,
  weight INTEGER,
  weight_diff INTEGER,
  odds REAL,
  popularity INTEGER,
  raw_json TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (race_id, horse_id)
);

CREATE TABLE IF NOT EXISTS results (
  race_id TEXT,
  horse_id TEXT,
  finish INTEGER,
  time TEXT,
  margin TEXT,
  last3f REAL,
  pass_order TEXT,
  odds REAL,
  popularity INTEGER,
  raw_json TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (race_id, horse_id)
);

CREATE TABLE IF NOT EXISTS models (
  model_id TEXT PRIMARY KEY,
  created_at TEXT,
  target TEXT,
  n_rows INTEGER,
  notes TEXT
);
"""

def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA foreign_keys = ON;")
    return con

def init_db(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA_SQL)
    con.commit()

def upsert_race(con: sqlite3.Connection, race_id: str, kaisai_date: str | None, source: str) -> None:
    con.execute(
        "INSERT OR REPLACE INTO races (race_id, kaisai_date, source) VALUES (?, ?, ?)",
        (race_id, kaisai_date, source),
    )
    con.commit()

def upsert_entries(con: sqlite3.Connection, race_id: str, df: pd.DataFrame) -> None:
    # Ensure required columns exist (best-effort)
    cols = {
        "horse_id": None, "horse_name": None, "horse_no": None, "bracket": None,
        "sex": None, "age": None, "handicap": None, "jockey_id": None, "jockey_name": None,
        "trainer_id": None, "trainer_name": None, "weight": None, "weight_diff": None,
        "odds": None, "popularity": None,
    }
    for k in cols:
        if k not in df.columns:
            df[k] = cols[k]

    rows = []
    for _, r in df.iterrows():
        horse_id = r.get("horse_id")
        # If horse_id is missing, fallback to name (still allow insert, but key becomes NULL => skip)
        if pd.isna(horse_id) or horse_id in (None, ""):
            continue
        rows.append((
            race_id,
            str(horse_id),
            None if pd.isna(r.get("horse_name")) else str(r.get("horse_name")),
            r.get("horse_no"),
            r.get("bracket"),
            None if pd.isna(r.get("sex")) else str(r.get("sex")),
            r.get("age"),
            r.get("handicap"),
            None if pd.isna(r.get("jockey_id")) else str(r.get("jockey_id")),
            None if pd.isna(r.get("jockey_name")) else str(r.get("jockey_name")),
            None if pd.isna(r.get("trainer_id")) else str(r.get("trainer_id")),
            None if pd.isna(r.get("trainer_name")) else str(r.get("trainer_name")),
            r.get("weight"),
            r.get("weight_diff"),
            r.get("odds"),
            r.get("popularity"),
            None,  # raw_json placeholder
        ))

    con.executemany(
        """INSERT OR REPLACE INTO entries
        (race_id, horse_id, horse_name, horse_no, bracket, sex, age, handicap, jockey_id, jockey_name,
         trainer_id, trainer_name, weight, weight_diff, odds, popularity, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows
    )
    con.commit()

def upsert_results(con: sqlite3.Connection, race_id: str, df: pd.DataFrame) -> None:
    cols = {
        "horse_id": None, "finish": None, "time": None, "margin": None,
        "last3f": None, "pass_order": None, "odds": None, "popularity": None,
    }
    for k in cols:
        if k not in df.columns:
            df[k] = cols[k]

    rows = []
    for _, r in df.iterrows():
        horse_id = r.get("horse_id")
        if pd.isna(horse_id) or horse_id in (None, ""):
            continue
        rows.append((
            race_id,
            str(horse_id),
            r.get("finish"),
            None if pd.isna(r.get("time")) else str(r.get("time")),
            None if pd.isna(r.get("margin")) else str(r.get("margin")),
            r.get("last3f"),
            None if pd.isna(r.get("pass_order")) else str(r.get("pass_order")),
            r.get("odds"),
            r.get("popularity"),
            None,  # raw_json placeholder
        ))
    con.executemany(
        """INSERT OR REPLACE INTO results
        (race_id, horse_id, finish, time, margin, last3f, pass_order, odds, popularity, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows
    )
    con.commit()

def load_training_frame(con: sqlite3.Connection) -> pd.DataFrame:
    q = """
    SELECT
      e.race_id, e.horse_id, e.horse_no, e.bracket, e.sex, e.age, e.handicap,
      e.jockey_id, e.trainer_id, e.weight, e.weight_diff, e.odds as entry_odds, e.popularity as entry_popularity,
      r.finish, r.odds as result_odds, r.popularity as result_popularity
    FROM entries e
    JOIN results r
      ON e.race_id = r.race_id AND e.horse_id = r.horse_id
    """
    return pd.read_sql_query(q, con)
