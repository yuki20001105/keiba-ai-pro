"""
Ultimate版データ一括収集スクリプト
20-30レースをスクレイピング → CSV → SQLite → 学習可能に
"""
import requests
import json
import pandas as pd
import sqlite3
from pathlib import Path
from datetime import datetime
import time
import sys

# 設定
API_URL = "http://localhost:8001"
OUTPUT_DIR = Path(__file__).parent / "data" / "ultimate_collected"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(__file__).parent / "keiba" / "data" / "keiba_ultimate.db"

# スクレイピング対象（2024年1月の中央競馬）
# まずは1日分で動作確認
TARGET_DATES = [
    "20240106",  # 土曜（24レース前後）
]

print(f"\n{'='*80}")
print("【Ultimate版データ一括収集】")
print(f"{'='*80}\n")

print(f"設定:")
print(f"  API: {API_URL}")
print(f"  出力先: {OUTPUT_DIR}")
print(f"  DB: {DB_PATH}")
print(f"  対象日: {len(TARGET_DATES)}日分")
print()

# ステップ1: レースID一覧取得
print(f"{'='*80}")
print("【ステップ1】レースID一覧取得")
print(f"{'='*80}\n")

all_race_ids = []
for kaisai_date in TARGET_DATES:
    print(f"→ {kaisai_date} のレース一覧取得中...")
    try:
        response = requests.post(
            f"{API_URL}/scrape/race_list",
            json={"kaisai_date": kaisai_date},
            timeout=180  # 3分に延長
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get("success"):
            races = data.get("races", [])
            print(f"  ✓ {len(races)}レース発見")
            all_race_ids.extend(races)
        else:
            print(f"  ✗ エラー: {data.get('error', 'Unknown')}")
    except Exception as e:
        print(f"  ✗ 取得失敗: {e}")
        continue

print(f"\n総レース数: {len(all_race_ids)}件")
if len(all_race_ids) == 0:
    print("\n✗ レースIDが取得できませんでした")
    print("  → スクレイピングAPIが起動しているか確認してください")
    print("     npm run dev:all")
    sys.exit(1)

# 最初の25レースに制限（時間短縮）
race_ids_to_scrape = all_race_ids[:25]
print(f"スクレイピング対象: {len(race_ids_to_scrape)}レース（最初の25件）\n")

# ステップ2: Ultimate版スクレイピング
print(f"{'='*80}")
print("【ステップ2】Ultimate版スクレイピング")
print(f"{'='*80}\n")

all_results = []
all_races = []
failed_races = []

for i, race_id in enumerate(race_ids_to_scrape, 1):
    print(f"[{i}/{len(race_ids_to_scrape)}] {race_id} スクレイピング中...", end=" ")
    
    try:
        response = requests.post(
            f"{API_URL}/scrape/ultimate",
            json={
                "race_id": race_id,
                "include_details": True  # 詳細情報あり（馬・騎手・調教師）
            },
            timeout=240  # 4分に延長
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get("success"):
            results = data.get("results", [])
            race_info = data.get("race_info", {})
            
            # race_idを各結果に追加
            for result in results:
                result['race_id'] = race_id
                all_results.append(result)
            
            # レース情報も保存
            race_info['race_id'] = race_id
            all_races.append(race_info)
            
            print(f"✓ {len(results)}頭")
        else:
            print(f"✗ エラー: {data.get('error', 'Unknown')}")
            failed_races.append(race_id)
        
        # レート制限回避（15秒/レース）
        if i < len(race_ids_to_scrape):
            time.sleep(15)
            
    except Exception as e:
        print(f"✗ 失敗: {e}")
        failed_races.append(race_id)
        continue

print(f"\n完了: {len(all_results)}頭のデータ取得")
print(f"失敗: {len(failed_races)}レース")
if failed_races:
    print(f"  失敗レース: {', '.join(failed_races[:5])}...")

if len(all_results) == 0:
    print("\n✗ データが取得できませんでした")
    sys.exit(1)

# ステップ3: CSV保存
print(f"\n{'='*80}")
print("【ステップ3】CSV保存")
print(f"{'='*80}\n")

# DataFrameに変換
df = pd.DataFrame(all_results)
races_df = pd.DataFrame(all_races)

# CSV保存
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
results_csv = OUTPUT_DIR / f"results_{timestamp}.csv"
races_csv = OUTPUT_DIR / f"races_{timestamp}.csv"

df.to_csv(results_csv, index=False, encoding='utf-8-sig')
races_df.to_csv(races_csv, index=False, encoding='utf-8-sig')

print(f"✓ 結果CSV: {results_csv}")
print(f"  {len(df)}行 × {len(df.columns)}列")
print(f"✓ レースCSV: {races_csv}")
print(f"  {len(races_df)}行 × {len(races_df.columns)}列")

# カラム確認
print(f"\n取得した特徴量:")
print(f"  基本情報: finish_position, horse_number, horse_name, etc.")
print(f"  ID情報: horse_id, jockey_id, trainer_id")
print(f"  派生特徴量: {len(df.columns)}列")
print(f"\nカラム一覧（最初の30列）:")
for i, col in enumerate(df.columns[:30]):
    print(f"    {i+1:2d}. {col}")
if len(df.columns) > 30:
    print(f"    ... 他{len(df.columns)-30}列")

# ステップ4: SQLite保存
print(f"\n{'='*80}")
print("【ステップ4】SQLite保存")
print(f"{'='*80}\n")

print(f"→ DB接続: {DB_PATH}")

# keiba_ai.db_ultimateモジュールを使わず、直接保存
# （スキーマの問題を回避するため、シンプルなテーブル構造で）

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# シンプルなテーブル作成（全カラムをTEXTで保存）
print("→ テーブル作成...")
cursor.execute("DROP TABLE IF EXISTS race_results_ultimate")
cursor.execute("DROP TABLE IF EXISTS races_ultimate")

# race_results_ultimate（結果データ）
cursor.execute("""
CREATE TABLE race_results_ultimate (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

# races_ultimate（レース情報）
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
for _, row in df.iterrows():
    cursor.execute(
        "INSERT INTO race_results_ultimate (race_id, data) VALUES (?, ?)",
        (row['race_id'], json.dumps(row.to_dict(), ensure_ascii=False))
    )

for _, row in races_df.iterrows():
    cursor.execute(
        "INSERT OR REPLACE INTO races_ultimate (race_id, data) VALUES (?, ?)",
        (row['race_id'], json.dumps(row.to_dict(), ensure_ascii=False))
    )

conn.commit()
print(f"✓ 挿入完了")

# 確認
cursor.execute("SELECT COUNT(*) FROM race_results_ultimate")
results_count = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM races_ultimate")
races_count = cursor.fetchone()[0]

print(f"\nDB保存結果:")
print(f"  race_results_ultimate: {results_count}行")
print(f"  races_ultimate: {races_count}行")

conn.close()

# 最終レポート
print(f"\n{'='*80}")
print("【収集完了】")
print(f"{'='*80}\n")

print(f"✓ Ultimate版データ収集が完了しました！")
print(f"\n統計:")
print(f"  スクレイピング: {len(race_ids_to_scrape)}レース試行")
print(f"  成功: {len(all_races)}レース")
print(f"  失敗: {len(failed_races)}レース")
print(f"  総頭数: {len(all_results)}頭")
print(f"  平均頭数: {len(all_results)/len(all_races):.1f}頭/レース")
print(f"  特徴量: {len(df.columns)}列")
print(f"\n保存先:")
print(f"  CSV: {OUTPUT_DIR}")
print(f"  SQLite: {DB_PATH}")

print(f"\n次のステップ:")
print(f"  1. python train_with_ultimate.py")
print(f"     → Ultimate版特徴量で学習を実行")
print(f"  2. ブラウザで http://localhost:3000/train")
print(f"     → Ultimate mode ONで学習")
print()
