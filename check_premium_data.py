import sqlite3, json
import pandas as pd

conn = sqlite3.connect('keiba/data/keiba_ultimate.db')

# holding_times_cache のサンプルと年別カバレッジ
print("=== holding_times_cache ===")
row = conn.execute("SELECT race_id, data FROM holding_times_cache LIMIT 1").fetchone()
if row:
    d = json.loads(row[1])
    print("サンプルキー:", list(d.keys())[:5] if isinstance(d, dict) else "リスト型")
    if isinstance(d, dict):
        first_key = list(d.keys())[0]
        print("馬データサンプル:", d[first_key])

# 年別件数
print("\n年別 holding_times_cache 件数:")
rows = conn.execute("SELECT race_id FROM holding_times_cache").fetchall()
years = {}
for r in rows:
    y = r[0][:4]
    years[y] = years.get(y, 0) + 1
for y, c in sorted(years.items()):
    print(f"  {y}: {c:,}レース")

# speed_figures 年別
print("\n年別 speed_figures 件数:")
rows2 = conn.execute("SELECT race_id FROM speed_figures").fetchall()
years2 = {}
for r in rows2:
    y = r[0][:4]
    years2[y] = years2.get(y, 0) + 1
for y, c in sorted(years2.items()):
    print(f"  {y}: {c:,}件")

# training_data 年別
print("\n年別 training_data 件数:")
rows3 = conn.execute("SELECT race_id FROM training_data").fetchall()
years3 = {}
for r in rows3:
    y = r[0][:4]
    years3[y] = years3.get(y, 0) + 1
for y, c in sorted(years3.items()):
    print(f"  {y}: {c:,}件")

conn.close()
