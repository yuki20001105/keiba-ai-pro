"""
Ultimate版データ収集（簡易版）
既知のレースIDを直接スクレイピング
"""
import requests
import json
import pandas as pd
import sqlite3
from pathlib import Path
from datetime import datetime
import time

# 設定
API_URL = "http://localhost:8001"
OUTPUT_DIR = Path(__file__).parent / "data" / "ultimate_collected"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(__file__).parent / "keiba" / "data" / "keiba_ultimate.db"

# 2024年1月6日の既知レースID（中央競馬）
RACE_IDS = [
    "202406010101", "202406010102", "202406010103", "202406010104", "202406010105",
    "202406010106", "202406010107", "202406010108", "202406010109", "202406010110",
    "202406010111", "202406010112",
    "202406010201", "202406010202", "202406010203", "202406010204", "202406010205",
    "202406010206", "202406010207", "202406010208", "202406010209", "202406010210",
    "202406010211", "202406010212",
]

print(f"\n{'='*80}")
print("【Ultimate版データ収集（簡易版）】")
print(f"{'='*80}\n")

print(f"設定:")
print(f"  API: {API_URL}")
print(f"  出力先: {OUTPUT_DIR}")
print(f"  DB: {DB_PATH}")
print(f"  対象: {len(RACE_IDS)}レース")
print()

# Ultimate版スクレイピング
print(f"{'='*80}")
print("【スクレイピング開始】")
print(f"{'='*80}\n")

all_results = []
all_races = []
failed_races = []

for i, race_id in enumerate(RACE_IDS, 1):
    print(f"[{i}/{len(RACE_IDS)}] {race_id} ...", end=" ", flush=True)
    
    try:
        response = requests.post(
            f"{API_URL}/scrape/ultimate",
            json={
                "race_id": race_id,
                "include_details": False  # 高速化のため詳細なし
            },
            timeout=60  # 1分に短縮
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get("success"):
            results = data.get("results", [])
            race_info = data.get("race_info", {})
            
            for result in results:
                result['race_id'] = race_id
                all_results.append(result)
            
            race_info['race_id'] = race_id
            all_races.append(race_info)
            
            print(f"✓ {len(results)}頭")
        else:
            error_msg = data.get('error', 'Unknown')
            print(f"✗ {error_msg}")
            failed_races.append((race_id, error_msg))
        
        # レート制限（10秒/レース - 高速化）
        if i < len(RACE_IDS):
            time.sleep(10)
            
    except requests.exceptions.Timeout:
        print(f"✗ タイムアウト（240秒超過）")
        failed_races.append((race_id, "Timeout"))
    except Exception as e:
        print(f"✗ エラー: {e}")
        failed_races.append((race_id, str(e)))

print(f"\n{'='*80}")
print(f"スクレイピング完了")
print(f"{'='*80}")
print(f"  成功: {len(all_races)}レース")
print(f"  失敗: {len(failed_races)}レース")
print(f"  総頭数: {len(all_results)}頭")

if len(all_results) == 0:
    print("\n✗ データが取得できませんでした")
    import sys
    sys.exit(1)

# CSV保存
print(f"\n{'='*80}")
print("【CSV保存】")
print(f"{'='*80}\n")

df = pd.DataFrame(all_results)
races_df = pd.DataFrame(all_races)

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
results_csv = OUTPUT_DIR / f"results_{timestamp}.csv"
races_csv = OUTPUT_DIR / f"races_{timestamp}.csv"

df.to_csv(results_csv, index=False, encoding='utf-8-sig')
races_df.to_csv(races_csv, index=False, encoding='utf-8-sig')

print(f"✓ 結果CSV: {results_csv.name}")
print(f"  {len(df)}行 × {len(df.columns)}列")
print(f"✓ レースCSV: {races_csv.name}")
print(f"  {len(races_df)}行 × {len(races_df.columns)}列")

# SQLite保存
print(f"\n{'='*80}")
print("【SQLite保存】")
print(f"{'='*80}\n")

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# テーブル作成
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

print("→ データ挿入中...")
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

cursor.execute("SELECT COUNT(*) FROM race_results_ultimate")
results_count = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM races_ultimate")
races_count = cursor.fetchone()[0]

print(f"✓ DB保存完了")
print(f"  race_results_ultimate: {results_count}行")
print(f"  races_ultimate: {races_count}行")

conn.close()

# 統計
print(f"\n{'='*80}")
print("【収集完了】")
print(f"{'='*80}\n")

print(f"✓ データ収集が完了しました！")
print(f"\n統計:")
print(f"  成功レース: {len(all_races)}")
print(f"  総頭数: {len(all_results)}頭")
if len(all_races) > 0:
    print(f"  平均頭数: {len(all_results)/len(all_races):.1f}頭/レース")
print(f"  特徴量: {len(df.columns)}列")

if failed_races:
    print(f"\n失敗レース ({len(failed_races)}件):")
    for race_id, error in failed_races[:5]:
        print(f"  {race_id}: {error}")

print(f"\n次のステップ:")
print(f"  python train_with_ultimate.py")
print()
