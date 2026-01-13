"""
Supabaseに保存されているUltimateデータを確認してkeiba_ultimate.dbにエクスポート
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client, Client
import sqlite3

# 環境変数読み込み
env_path = Path(__file__).parent / ".env.local"
load_dotenv(env_path)

SUPABASE_URL = os.getenv("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_KEY = os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("✗ Supabase環境変数が設定されていません")
    print(f"  NEXT_PUBLIC_SUPABASE_URL: {'設定済み' if SUPABASE_URL else '未設定'}")
    print(f"  NEXT_PUBLIC_SUPABASE_ANON_KEY: {'設定済み' if SUPABASE_KEY else '未設定'}")
    sys.exit(1)

print(f"\n{'='*80}")
print("【Supabase → SQLite エクスポート】")
print(f"{'='*80}\n")

# Supabase接続
print("→ Supabase接続中...")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
print("✓ 接続成功\n")

# レース数確認（まずスキーマ確認）
print("→ races テーブル確認...")
try:
    # 最初の1件でカラムを確認
    test_response = supabase.table('races').select('*').limit(1).execute()
    if test_response.data:
        print(f"  利用可能なカラム: {list(test_response.data[0].keys())}")
    
    # 全レース取得（race_id, race_nameのみ）
    races_response = supabase.table('races').select('race_id, race_name, created_at').execute()
    races = races_response.data if races_response.data else []
    print(f"  レース数: {len(races)}件")
except Exception as e:
    print(f"  エラー: {e}")
    sys.exit(1)

if len(races) == 0:
    print("\n✗ Supabaseにレースデータがありません")
    print("  → まずdata-collectionページでUltimate modeでスクレイピングしてください")
    sys.exit(0)

# サンプル表示
print("\n最新10レース:")
for race in races[:10]:
    created = race.get('created_at', 'N/A')
    print(f"  {race['race_id']}: {race['race_name']} ({created})")

# entries確認（テーブル名が違う可能性）
print("\n→ テーブル一覧確認...")
# PostgRESTではテーブル一覧を直接取得できないため、試行錯誤で確認
table_names = ['entries', 'results', 'race_results', 'horses', 'payouts', 'race_payouts']
existing_tables = []
for table in table_names:
    try:
        test = supabase.table(table).select('*').limit(1).execute()
        existing_tables.append(table)
        print(f"  ✓ {table} (存在)")
    except Exception as e:
        print(f"  ✗ {table} (不在)")

if 'results' not in existing_tables and 'race_results' not in existing_tables:
    print("\n✗ 馬データテーブルが見つかりません")
    print(f"  存在するテーブル: {existing_tables}")
    sys.exit(0)

# 結果テーブルを確認
results_table = 'race_results' if 'race_results' in existing_tables else 'results'
print(f"\n→ {results_table} テーブル確認...")
results_response = supabase.table(results_table).select('race_id', count='exact').limit(1).execute()
results_count = len(results_response.data) if results_response.data else 0
print(f"  {results_table}カウント: {results_count}件")

# カラム確認
if results_response.data:
    print(f"  利用可能なカラム: {list(results_response.data[0].keys())}")

# payouts確認
print("\n→ payouts テーブル確認...")
try:
    payouts_response = supabase.table('payouts').select('race_id', count='exact').execute()
    payouts_count = payouts_response.count if hasattr(payouts_response, 'count') else 0
    print(f"  払戻数: {payouts_count}件")
except Exception as e:
    print(f"  払戻テーブル: エラー ({e})")

print(f"\n{'='*80}")
print("【エクスポート実行】")
print(f"{'='*80}\n")

# SQLite接続
sys.path.insert(0, str(Path(__file__).parent / "keiba"))
from keiba_ai import db_ultimate

db_path = Path(__file__).parent / "keiba" / "data" / "keiba_ultimate.db"
print(f"→ SQLite接続: {db_path}")
conn = db_ultimate.connect(db_path)
db_ultimate.init_db(conn)
print("✓ スキーマ初期化完了\n")

# レースデータエクスポート
print(f"→ レースデータをエクスポート中...")
exported_races = 0
for race in races:
    race_id = race['race_id']
    
    # レース情報取得
    race_detail = supabase.table('races').select('*').eq('race_id', race_id).single().execute()
    if race_detail.data:
        db_ultimate.upsert_race(conn, race_detail.data)
        exported_races += 1
        
        # エントリー情報取得
        entries = supabase.table('entries').select('*').eq('race_id', race_id).execute()
        if entries.data:
            db_ultimate.upsert_entries(conn, race_id, entries.data)
        
        # 結果情報取得
        results = supabase.table('results').select('*').eq('race_id', race_id).execute()
        if results.data:
            db_ultimate.upsert_results(conn, race_id, results.data)
        
        # 払戻情報取得
        try:
            payouts = supabase.table('payouts').select('*').eq('race_id', race_id).execute()
            if payouts.data:
                db_ultimate.upsert_payouts(conn, race_id, payouts.data)
        except:
            pass
        
        if exported_races % 10 == 0:
            print(f"  {exported_races}/{len(races)} 完了...")

print(f"✓ エクスポート完了: {exported_races}レース\n")

# 確認
cursor = conn.cursor()
cursor.execute("SELECT COUNT(*) FROM races")
races_count = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM entries")
entries_count_local = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM results")
results_count_local = cursor.fetchone()[0]

print(f"{'='*80}")
print("【エクスポート結果】")
print(f"{'='*80}")
print(f"  races: {races_count}件")
print(f"  entries: {entries_count_local}件")
print(f"  results: {results_count_local}件")
print(f"\n✓ keiba_ultimate.dbへのエクスポートが完了しました！")
print(f"  これでUltimate modeでの学習が可能です。\n")

conn.close()
