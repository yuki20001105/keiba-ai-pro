import sys, sqlite3, json
import pandas as pd
sys.path.insert(0, 'keiba')
sys.path.insert(0, 'python-api')

conn = sqlite3.connect('keiba/data/keiba_ultimate.db')

# races_ultimateの1件サンプルで取得できるフィールド確認
row = conn.execute("SELECT data FROM races_ultimate LIMIT 1").fetchone()
race_data = json.loads(row[0])
print("=== races_ultimate (レース基本情報) ===")
for k, v in race_data.items():
    vstr = str(v)[:60] if v is not None else 'NULL'
    print(f"  {k:<35} = {vstr}")

print()

# race_results_ultimateの1件サンプル
row2 = conn.execute("SELECT data FROM race_results_ultimate LIMIT 1").fetchone()
horse_data = json.loads(row2[0])
print("=== race_results_ultimate (馬別情報) ===")
for k, v in horse_data.items():
    vstr = str(v)[:60] if v is not None else 'NULL'
    print(f"  {k:<35} = {vstr}")

conn.close()
