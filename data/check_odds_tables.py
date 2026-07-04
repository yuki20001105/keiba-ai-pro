import sqlite3, pandas as pd

db = 'keiba/data/keiba_ultimate.db'
with sqlite3.connect(db) as conn:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]
    print('全テーブル:', tables)
    for t in tables:
        cur.execute(f"PRAGMA table_info({t})")
        cols = [r[1] for r in cur.fetchall()]
        odds_cols = [c for c in cols if 'odds' in c.lower()]
        if odds_cols:
            print(f'  {t}: {odds_cols}')
            cur.execute(f"SELECT {odds_cols[0]} FROM {t} WHERE {odds_cols[0]} IS NOT NULL LIMIT 3")
            print(f'    sample: {cur.fetchall()}')
