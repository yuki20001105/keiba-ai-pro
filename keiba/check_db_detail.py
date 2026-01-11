"""データベースの詳細確認"""
import sqlite3
import pandas as pd

conn = sqlite3.connect('data/keiba.db')

print("=" * 60)
print("データベース確認")
print("=" * 60)

# 基本統計
print("\n【基本統計】")
races = pd.read_sql_query("SELECT COUNT(*) AS n FROM races", conn)["n"].iloc[0]
entries = pd.read_sql_query("SELECT COUNT(*) AS n FROM entries", conn)["n"].iloc[0]
results = pd.read_sql_query("SELECT COUNT(*) AS n FROM results", conn)["n"].iloc[0]
print(f"  レース数: {races}")
print(f"  エントリー数: {entries}")
print(f"  結果数: {results}")

# finish列の分布
print("\n【finish列の分布】")
finish_dist = pd.read_sql_query(
    "SELECT finish, COUNT(*) as count FROM results GROUP BY finish ORDER BY finish",
    conn
)
print(finish_dist.to_string(index=False))

# 最新のレース
print("\n【最新のレース（5件）】")
recent_races = pd.read_sql_query(
    "SELECT race_id FROM races ORDER BY race_id DESC LIMIT 5",
    conn
)
print(recent_races['race_id'].to_list())

# サンプルデータ
print("\n【サンプルデータ（race_id=202401010101）】")
sample = pd.read_sql_query(
    """SELECT r.race_id, e.horse_id, e.horse_name, r.finish, r.odds, r.popularity
       FROM results r
       JOIN entries e ON r.race_id = e.race_id AND r.horse_id = e.horse_id
       WHERE r.race_id = '202401010101'
       ORDER BY CAST(r.finish AS INTEGER)
       LIMIT 5""",
    conn
)
if not sample.empty:
    print(sample.to_string(index=False))
else:
    print("  データなし")

conn.close()
