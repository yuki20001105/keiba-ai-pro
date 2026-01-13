"""
Supabaseのデータを確認
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client, Client

env_path = Path(__file__).parent / ".env.local"
load_dotenv(env_path)

SUPABASE_URL = os.getenv("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_KEY = os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")

print(f"\n{'='*80}")
print("【Supabaseデータ確認】")
print(f"{'='*80}\n")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# races
print("→ racesテーブル:")
races = supabase.table('races').select('*').limit(5).execute()
if races.data:
    print(f"  件数: {len(races.data)}")
    print(f"  カラム: {list(races.data[0].keys())}")
    print(f"  サンプル: {races.data[0]}")

# race_results
print("\n→ race_resultsテーブル:")
results = supabase.table('race_results').select('*').limit(5).execute()
if results.data:
    print(f"  件数: {len(results.data)}")
    print(f"  カラム: {list(results.data[0].keys())}")
    print(f"  サンプル: {results.data[0]}")

# race_payouts
print("\n→ race_payoutsテーブル:")
payouts = supabase.table('race_payouts').select('*').limit(5).execute()
if payouts.data:
    print(f"  件数: {len(payouts.data)}")
    print(f"  カラム: {list(payouts.data[0].keys())}")
    print(f"  サンプル: {payouts.data[0]}")

# 統計
print(f"\n{'='*80}")
print("【データ統計】")
print(f"{'='*80}")

# レース総数
races_all = supabase.table('races').select('race_id').execute()
print(f"  総レース数: {len(races_all.data)}件")

# 結果総数
results_all = supabase.table('race_results').select('race_id').execute()
print(f"  総結果数: {len(results_all.data)}頭")

# 払戻総数
payouts_all = supabase.table('race_payouts').select('race_id').execute()
print(f"  総払戻数: {len(payouts_all.data)}件")

# レースあたりの平均頭数
if len(races_all.data) > 0:
    avg_horses = len(results_all.data) / len(races_all.data)
    print(f"\n  平均頭数/レース: {avg_horses:.1f}頭")

print(f"\n{'='*80}\n")
