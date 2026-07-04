import sqlite3, json

# DB テーブル確認
con = sqlite3.connect("keiba/data/keiba_ultimate.db")
tables = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print("テーブル一覧:", [t[0] for t in tables])

# race_results_ultimate のカラム確認
for tbl in [t[0] for t in tables]:
    cols = con.execute(f"PRAGMA table_info({tbl})").fetchall()
    print(f"\n{tbl} columns:", [c[1] for c in cols[:10]])

con.close()
