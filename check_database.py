import sys
sys.path.insert(0, r"C:\Users\yuki2\Documents\ws\keiba-ai-pro")

from supabase import create_client
import os

# Supabase設定を読み込み
env_file = "C:/Users/yuki2/Documents/ws/keiba-ai-pro/.env.local"
supabase_url = None
supabase_key = None

with open(env_file, 'r', encoding='utf-8') as f:
    for line in f:
        if line.startswith('NEXT_PUBLIC_SUPABASE_URL='):
            supabase_url = line.split('=', 1)[1].strip()
        elif line.startswith('NEXT_PUBLIC_SUPABASE_ANON_KEY='):
            supabase_key = line.split('=', 1)[1].strip()

if not supabase_url or not supabase_key:
    print("❌ Supabase設定が見つかりません")
    sys.exit(1)

print("=== Supabaseデータベース確認 ===\n")

# Supabaseクライアント作成
supabase = create_client(supabase_url, supabase_key)

# racesテーブルのレコード数
try:
    response = supabase.table('races').select('race_id', count='exact').execute()
    races_count = response.count if hasattr(response, 'count') else len(response.data)
    print(f"✅ racesテーブル: {races_count} レコード")
except Exception as e:
    print(f"❌ racesテーブル確認エラー: {e}")
    races_count = 0

# race_resultsテーブルのレコード数
try:
    response = supabase.table('race_results').select('id', count='exact').execute()
    results_count = response.count if hasattr(response, 'count') else len(response.data)
    print(f"✅ race_resultsテーブル: {results_count} レコード")
except Exception as e:
    print(f"❌ race_resultsテーブル確認エラー: {e}")
    results_count = 0

# 最新のレースを表示
if races_count > 0:
    try:
        response = supabase.table('races') \
            .select('race_id, race_name, race_date') \
            .order('race_date', desc=True) \
            .limit(5) \
            .execute()
        
        print("\n最新のレース（上位5件）:")
        for race in response.data:
            print(f"  - {race.get('race_date')}: {race.get('race_name')} (ID: {race.get('race_id')})")
    except Exception as e:
        print(f"❌ 最新レース取得エラー: {e}")

print(f"\n合計データ: {races_count} レース, {results_count} 結果レコード")

if races_count == 0:
    print("\n⚠️ データがありません。データ取得を実行してください。")
else:
    print("\n✅ データベースにデータが存在します。")
