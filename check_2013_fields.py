import sys, sqlite3, json
import pandas as pd
import numpy as np
sys.path.insert(0, 'keiba')

conn = sqlite3.connect('keiba/data/keiba_ultimate.db')

# 2013-2018データで全フィールド一覧（実際に存在するキーを確認）
rows = conn.execute("""
    SELECT data FROM race_results_ultimate
    WHERE CAST(substr(race_id,1,4) AS INTEGER) BETWEEN 2013 AND 2018
    LIMIT 200
""").fetchall()
all_keys = set()
for r in rows:
    d = json.loads(r[0])
    all_keys.update(d.keys())

print("=== 2013-2018 race_results_ultimate の全フィールド ===")
for k in sorted(all_keys):
    print(f"  {k}")

print("\n=== 前走情報フィールドの詳細確認 (5000サンプル) ===")
rows2 = conn.execute("""
    SELECT data FROM race_results_ultimate
    WHERE CAST(substr(race_id,1,4) AS INTEGER) BETWEEN 2013 AND 2018
    LIMIT 5000
""").fetchall()
records = [json.loads(r[0]) for r in rows2]
df = pd.DataFrame(records)

prev_cols = [c for c in df.columns if 'prev' in c.lower() or 'last_race' in c.lower()]
print("前走系フィールド:", prev_cols if prev_cols else "なし")

# horse_total 系
total_cols = [c for c in df.columns if 'total' in c.lower() or 'prize' in c.lower()]
print("通算成績フィールド:", total_cols)

for col in total_cols:
    val = df[col]
    non_null = val.notna().sum()
    rate = non_null / len(df) * 100
    print(f"  {col}: {rate:.1f}% 取得")

conn.close()
