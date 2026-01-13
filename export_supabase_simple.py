"""
Supabaseの既存データを使ってkeiba_ultimate.dbに保存
（新規スクレイピングなし、既存データ活用）
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client, Client
import sqlite3
import json
import pandas as pd
from datetime import datetime

# 環境変数読み込み
env_path = Path(__file__).parent / ".env.local"
load_dotenv(env_path)

SUPABASE_URL = os.getenv("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_KEY = os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
DB_PATH = Path(__file__).parent / "keiba" / "data" / "keiba_ultimate.db"
OUTPUT_DIR = Path(__file__).parent / "data" / "ultimate_collected"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

print(f"\n{'='*80}")
print("【Supabase既存データをSQLiteにエクスポート】")
print(f"{'='*80}\n")

print(f"設定:")
print(f"  Supabase: {SUPABASE_URL}")
print(f"  DB: {DB_PATH}")
print()

# Supabase接続
print("→ Supabase接続中...")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
print("✓ 接続成功\n")

# 全レース取得
print("→ レースデータ取得中...")
races_response = supabase.table('races').select('*').execute()
races = races_response.data if races_response.data else []
print(f"  {len(races)}レース発見\n")

if len(races) == 0:
    print("✗ Supabaseにデータがありません")
    sys.exit(1)

# 全結果取得
print("→ 結果データ取得中...")
results_response = supabase.table('race_results').select('*').execute()
results = results_response.data if results_response.data else []
print(f"  {len(results)}頭分のデータ発見\n")

if len(results) == 0:
    print("✗ race_resultsにデータがありません")
    sys.exit(1)

# DataFrameに変換
print("→ DataFrame変換中...")
df_races = pd.DataFrame(races)
df_results = pd.DataFrame(results)

print(f"  races: {len(df_races)}行 × {len(df_races.columns)}列")
print(f"  race_results: {len(df_results)}行 × {len(df_results.columns)}列")

# カラム確認
print(f"\nrace_resultsのカラム:")
for i, col in enumerate(df_results.columns, 1):
    print(f"  {i:2d}. {col}")

# CSV保存
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
results_csv = OUTPUT_DIR / f"supabase_results_{timestamp}.csv"
races_csv = OUTPUT_DIR / f"supabase_races_{timestamp}.csv"

df_results.to_csv(results_csv, index=False, encoding='utf-8-sig')
df_races.to_csv(races_csv, index=False, encoding='utf-8-sig')

print(f"\n✓ CSV保存完了:")
print(f"  {results_csv.name}")
print(f"  {races_csv.name}")

# SQLite保存
print(f"\n{'='*80}")
print("【SQLite保存】")
print(f"{'='*80}\n")

print(f"→ DB接続: {DB_PATH}")
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# テーブル作成
print("→ テーブル作成...")
cursor.execute("DROP TABLE IF EXISTS race_results_ultimate")
cursor.execute("DROP TABLE IF EXISTS races_ultimate")

cursor.execute("""
CREATE TABLE race_results_ultimate (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE races_ultimate (
    race_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

print("✓ テーブル作成完了")

# データ挿入
print(f"→ データ挿入中...")
for _, row in df_results.iterrows():
    cursor.execute(
        "INSERT INTO race_results_ultimate (race_id, data) VALUES (?, ?)",
        (row['race_id'], json.dumps(row.to_dict(), ensure_ascii=False, default=str))
    )

for _, row in df_races.iterrows():
    cursor.execute(
        "INSERT OR REPLACE INTO races_ultimate (race_id, data) VALUES (?, ?)",
        (row['race_id'], json.dumps(row.to_dict(), ensure_ascii=False, default=str))
    )

conn.commit()

# 確認
cursor.execute("SELECT COUNT(*) FROM race_results_ultimate")
results_count = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM races_ultimate")
races_count = cursor.fetchone()[0]

print(f"✓ DB保存完了")
print(f"  race_results_ultimate: {results_count}行")
print(f"  races_ultimate: {races_count}行")

# レースごとの統計
cursor.execute("""
SELECT race_id, COUNT(*) as horse_count
FROM race_results_ultimate
GROUP BY race_id
ORDER BY horse_count DESC
LIMIT 10
""")
top_races = cursor.fetchall()

print(f"\nレースあたりの頭数（上位10レース）:")
for race_id, count in top_races:
    print(f"  {race_id}: {count}頭")

conn.close()

# 最終レポート
print(f"\n{'='*80}")
print("【エクスポート完了】")
print(f"{'='*80}\n")

print(f"✓ Supabaseデータのエクスポートが完了しました！")
print(f"\n統計:")
print(f"  レース数: {len(df_races)}")
print(f"  総頭数: {len(df_results)}")
if len(df_races) > 0:
    print(f"  平均頭数: {len(df_results)/len(df_races):.1f}頭/レース")

print(f"\n保存先:")
print(f"  CSV: {OUTPUT_DIR}")
print(f"  SQLite: {DB_PATH}")

print(f"\n次のステップ:")
print(f"  python train_with_ultimate.py")
print(f"  → Ultimate mode ONで学習を実行")
print()
