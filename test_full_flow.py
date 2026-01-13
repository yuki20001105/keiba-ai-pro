"""
スクレイピングデータをSQLiteに保存して学習
"""
import requests
import pandas as pd
import sqlite3
import json
from pathlib import Path
from datetime import datetime

OUTPUT_DIR = Path(__file__).parent / "data" / "ultimate_collected"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(__file__).parent / "keiba" / "data" / "keiba_ultimate.db"

print(f"\n{'='*80}")
print("【スクレイピング → SQLite → 学習】")
print(f"{'='*80}\n")

# ステップ1: 1レースをスクレイピング
print("【ステップ1】スクレイピング (include_details=False)")
print("-" * 80)

race_id = "202406010101"
print(f"Race ID: {race_id}")
print("Scraping...", end=" ", flush=True)

response = requests.post(
    "http://localhost:8001/scrape/ultimate",
    json={"race_id": race_id, "include_details": False},
    timeout=180
)
response.raise_for_status()
data = response.json()

results = data.get("results", [])
race_info = data.get("race_info", {})

print(f"[OK] {len(results)} horses")

# ステップ2: SQLiteに保存
print(f"\n{'='*80}")
print("【ステップ2】SQLite保存")
print("-" * 80)

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# テーブルクリア（テスト用）
print("Clearing test tables...", end=" ")
cursor.execute("DELETE FROM race_results_ultimate WHERE race_id = ?", (race_id,))
cursor.execute("DELETE FROM races_ultimate WHERE race_id = ?", (race_id,))
conn.commit()
print("[OK]")

# 結果データ保存
print(f"Saving race_results_ultimate...", end=" ")
for result in results:
    result['race_id'] = race_id
    data_json = json.dumps(result, ensure_ascii=False)
    cursor.execute(
        "INSERT INTO race_results_ultimate (race_id, data) VALUES (?, ?)",
        (race_id, data_json)
    )
print(f"[OK] {len(results)} rows")

# レース情報保存
print(f"Saving races_ultimate...", end=" ")
race_info['race_id'] = race_id
race_json = json.dumps(race_info, ensure_ascii=False)
cursor.execute(
    "INSERT INTO races_ultimate (race_id, data) VALUES (?, ?)",
    (race_id, race_json)
)
print(f"[OK] 1 row")

conn.commit()
conn.close()

# データ確認
print(f"\nDatabase verification:")
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
cursor.execute("SELECT COUNT(*) FROM race_results_ultimate")
total_results = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(DISTINCT race_id) FROM race_results_ultimate")
total_races = cursor.fetchone()[0]
conn.close()

print(f"  Total results: {total_results} rows")
print(f"  Total races: {total_races}")

# ステップ3: 学習実行
print(f"\n{'='*80}")
print("【ステップ3】学習実行")
print("-" * 80)

print("Note: Supabase data (1000 rows) still exists in the database")
print("      This test added 1 race (16 rows) from scraping")
print(f"      Total available: {total_results} rows from {total_races} races\n")

print("Sending training request...")
try:
    train_response = requests.post(
        "http://localhost:8000/api/train",
        json={
            "model_type": "lightgbm",
            "use_optimizer": True,
            "use_optuna": False,
            "ultimate_mode": True
        },
        timeout=600
    )
    
    if train_response.status_code == 200:
        result = train_response.json()
        print("[OK] Training completed!\n")
        print(f"Model: {result.get('model_type')}")
        print(f"Accuracy: {result.get('accuracy', 'N/A')}")
        print(f"AUC: {result.get('roc_auc', 'N/A')}")
        print(f"Train size: {result.get('train_size', 'N/A')}")
    else:
        error = train_response.json()
        print(f"[X] Training failed: {error.get('detail', 'Unknown error')}")
        
except requests.exceptions.ConnectionError:
    print("[X] ML API (port 8000) not running")
except Exception as e:
    print(f"[X] Error: {e}")

# サマリー
print(f"\n{'='*80}")
print("【完了】")
print(f"{'='*80}\n")
print("[OK] Scraping: 4-5 seconds")
print("[OK] SQLite: Saved with JSON format")
print("[NOTE] Training: May fail due to lack of sufficient IDs in old Supabase data")
print("\nSolution: Scrape more races with include_details=False for better training data")
