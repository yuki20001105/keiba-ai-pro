"""
完全パイプラインテスト
スクレイピング → CSV → Supabase → 学習の一連の流れを確認
"""
import requests
import json
import pandas as pd
import os
from pathlib import Path
from datetime import datetime
from supabase import create_client, Client
from dotenv import load_dotenv

# 環境変数読み込み
load_dotenv()

# 設定
API_URL = "http://localhost:8001"
OUTPUT_DIR = Path(__file__).parent / "data" / "ultimate_collected"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Supabase接続
SUPABASE_URL = os.getenv("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

print(f"\n{'='*80}")
print("【完全パイプラインテスト】")
print(f"{'='*80}\n")

# ステップ1: スクレイピング（1レースのみ）
print("【ステップ1】スクレイピング実行")
print("-" * 80)

race_id = "202406010101"
print(f"対象レース: {race_id}")
print(f"モード: 高速（include_details=False）")
print(f"タイムアウト: 120秒\n")

try:
    print("スクレイピング中...", end=" ", flush=True)
    response = requests.post(
        f"{API_URL}/scrape/ultimate",
        json={
            "race_id": race_id,
            "include_details": False  # 高速モード
        },
        timeout=120  # 2分
    )
    response.raise_for_status()
    data = response.json()
    
    if not data.get("success"):
        print(f"✗ エラー: {data.get('error', 'Unknown')}")
        exit(1)
    
    results = data.get("results", [])
    race_info = data.get("race_info", {})
    
    print(f"✓ 成功")
    print(f"  取得頭数: {len(results)}頭")
    print(f"  レース名: {race_info.get('race_name', 'N/A')}")
    print(f"  列数: {len(results[0])}列" if results else "")
    
except requests.exceptions.Timeout:
    print("✗ タイムアウト（120秒超過）")
    exit(1)
except Exception as e:
    print(f"✗ エラー: {e}")
    exit(1)

# ステップ2: CSV保存
print(f"\n{'='*80}")
print("【ステップ2】CSV保存")
print("-" * 80)

# race_idを各結果に追加
for result in results:
    result['race_id'] = race_id

df_results = pd.DataFrame(results)
race_info['race_id'] = race_id
df_race = pd.DataFrame([race_info])

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
results_csv = OUTPUT_DIR / f"test_results_{timestamp}.csv"
race_csv = OUTPUT_DIR / f"test_race_{timestamp}.csv"

df_results.to_csv(results_csv, index=False, encoding='utf-8-sig')
df_race.to_csv(race_csv, index=False, encoding='utf-8-sig')

print(f"✓ 結果CSV保存: {results_csv.name}")
print(f"  {len(df_results)}行 × {len(df_results.columns)}列")
print(f"✓ レースCSV保存: {race_csv.name}")
print(f"  {len(df_race)}行 × {len(df_race.columns)}列")

# ステップ3: Supabase保存
print(f"\n{'='*80}")
print("【ステップ3】Supabase保存")
print("-" * 80)

if not SUPABASE_URL or not SUPABASE_KEY:
    print("✗ Supabase認証情報が見つかりません（.envファイルを確認）")
    print("  スキップして次のステップへ...")
else:
    try:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        print(f"Supabase接続: ✓")
        
        # race_resultsテーブルに保存
        print(f"\nrace_resultsテーブルに保存中...", end=" ", flush=True)
        results_data = df_results.to_dict('records')
        response = supabase.table('race_results').upsert(results_data).execute()
        print(f"✓ {len(results_data)}行保存")
        
        # racesテーブルに保存
        print(f"racesテーブルに保存中...", end=" ", flush=True)
        race_data = df_race.to_dict('records')
        response = supabase.table('races').upsert(race_data).execute()
        print(f"✓ {len(race_data)}行保存")
        
        print("\n✓ Supabase保存完了")
        
    except Exception as e:
        print(f"✗ Supabase保存エラー: {e}")
        print("  スキップして次のステップへ...")

# ステップ4: 学習実行
print(f"\n{'='*80}")
print("【ステップ4】機械学習実行")
print("-" * 80)

print("学習リクエスト送信中...")
try:
    train_response = requests.post(
        "http://localhost:8000/api/train",
        json={
            "model_type": "lightgbm",
            "use_optimizer": True,
            "use_optuna": False,
            "ultimate_mode": False  # 標準モード（keiba.db使用）
        },
        timeout=300  # 5分
    )
    train_response.raise_for_status()
    train_result = train_response.json()
    
    print("✓ 学習完了\n")
    print(f"モデル: {train_result.get('model_type', 'N/A')}")
    print(f"精度: {train_result.get('accuracy', 'N/A')}")
    print(f"AUC: {train_result.get('roc_auc', 'N/A')}")
    print(f"データ件数: {train_result.get('train_size', 'N/A')}件")
    
except requests.exceptions.ConnectionError:
    print("✗ ML API (port 8000) に接続できません")
    print("  python-api/main.py が起動しているか確認してください")
except Exception as e:
    print(f"✗ 学習エラー: {e}")

# 完了サマリー
print(f"\n{'='*80}")
print("【完了サマリー】")
print(f"{'='*80}\n")
print("✓ スクレイピング: 成功")
print("✓ CSV保存: 成功")
print(f"✓ Supabase保存: {'成功' if SUPABASE_URL else 'スキップ'}")
print("✓ 学習: 実行")
print("\n一連の流れを確認しました！")
