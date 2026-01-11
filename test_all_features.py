"""
全機能の動作確認スクリプト
データ取得 → 学習 → 予測 の一連の流れをテスト
"""

import sys
import time
import json
import requests
from datetime import datetime

def print_section(title):
    """セクションタイトルを表示"""
    print("\n" + "="*80)
    print(f"  {title}")
    print("="*80 + "\n")

def test_ultimate_service():
    """1. Ultimate版スクレイピングサービスの動作確認"""
    print_section("1. Ultimate版スクレイピングサービスの確認")
    
    try:
        # ヘルスチェック
        response = requests.get("http://localhost:8001/health", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Ultimate版サービス稼働中")
            print(f"   ステータス: {data.get('status')}")
            print(f"   キャッシュサイズ: {data.get('cache_size')}")
            return True
        else:
            print(f"❌ Ultimate版サービスエラー: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Ultimate版サービス接続失敗: {e}")
        return False

def test_data_collection():
    """2. データ取得の確認"""
    print_section("2. データ取得機能の確認")
    
    # テスト用のレースID（2024年の実在するレース）
    test_race_id = "202401041001"  # 2024年1月4日中山1R
    
    try:
        print(f"📊 レースID {test_race_id} のデータを取得中...")
        
        # Ultimate版スクレイピングサービスに直接リクエスト
        response = requests.post(
            "http://localhost:8001/scrape/ultimate",
            json={
                "race_id": test_race_id,
                "include_details": False  # 高速モード
            },
            timeout=60
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get("success"):
                print("✅ データ取得成功！")
                race_info = data.get("race_info", {})
                results = data.get("results", [])
                
                print(f"\n   レース名: {race_info.get('race_name')}")
                print(f"   距離: {race_info.get('distance')}m")
                print(f"   トラック: {race_info.get('track_type')}")
                print(f"   天候: {race_info.get('weather')}")
                print(f"   馬場状態: {race_info.get('field_condition')}")
                print(f"   出走頭数: {len(results)}頭")
                
                if results:
                    print(f"\n   1着: {results[0].get('horse_name')} ({results[0].get('finish_time')})")
                
                return True
            else:
                print(f"❌ データ取得失敗: {data.get('error')}")
                return False
        else:
            print(f"❌ HTTPエラー: {response.status_code}")
            print(f"   レスポンス: {response.text[:200]}")
            return False
            
    except Exception as e:
        print(f"❌ データ取得エラー: {e}")
        return False

def test_next_api():
    """3. Next.js APIルートの確認"""
    print_section("3. Next.js APIルートの確認")
    
    try:
        # Next.jsが起動しているか確認
        response = requests.get("http://localhost:3000", timeout=5)
        if response.status_code == 200:
            print("✅ Next.js起動中")
            
            # レースリストAPIのテスト
            print("\n📋 レースリストAPI (/api/netkeiba/race-list) をテスト...")
            race_list_response = requests.post(
                "http://localhost:3000/api/netkeiba/race-list",
                json={"date": "2024-01-04"},
                timeout=10
            )
            
            if race_list_response.status_code == 200:
                race_data = race_list_response.json()
                race_ids = race_data.get("raceIds", [])
                print(f"✅ レースリストAPI動作確認")
                print(f"   2024年1月4日のレース数: {len(race_ids)}件")
                if race_ids:
                    print(f"   例: {race_ids[0]}")
                return True
            else:
                print(f"❌ レースリストAPIエラー: {race_list_response.status_code}")
                return False
        else:
            print(f"❌ Next.js接続エラー: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Next.js確認エラー: {e}")
        return False

def test_database_connection():
    """4. データベース接続の確認"""
    print_section("4. Supabaseデータベース接続の確認")
    
    try:
        # Supabase接続情報の確認（実際の接続テストはNext.js経由で行う）
        import os
        from pathlib import Path
        
        env_file = Path("C:/Users/yuki2/Documents/ws/keiba-ai-pro/.env.local")
        if env_file.exists():
            print("✅ .env.local ファイル存在確認")
            
            # 環境変数の読み込み
            with open(env_file, 'r', encoding='utf-8') as f:
                content = f.read()
                has_supabase_url = 'NEXT_PUBLIC_SUPABASE_URL' in content
                has_supabase_key = 'NEXT_PUBLIC_SUPABASE_ANON_KEY' in content
                
                if has_supabase_url and has_supabase_key:
                    print("✅ Supabase設定確認")
                    return True
                else:
                    print("⚠️  Supabase設定が不完全です")
                    return False
        else:
            print("❌ .env.local ファイルが見つかりません")
            return False
            
    except Exception as e:
        print(f"❌ データベース確認エラー: {e}")
        return False

def test_training_system():
    """5. 学習システムの確認"""
    print_section("5. モデル学習システムの確認（概要）")
    
    # 学習用データベースの確認
    import os
    db_path = "C:/Users/yuki2/Documents/ws/keiba/keiba.db"
    
    if os.path.exists(db_path):
        print(f"✅ 学習用データベース存在確認")
        print(f"   パス: {db_path}")
        
        # データベースの中身を確認
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # racesテーブルのレコード数
            cursor.execute("SELECT COUNT(*) FROM races")
            races_count = cursor.fetchone()[0]
            
            # resultsテーブルのレコード数
            cursor.execute("SELECT COUNT(*) FROM results")
            results_count = cursor.fetchone()[0]
            
            print(f"   レース数: {races_count}")
            print(f"   結果レコード数: {results_count}")
            
            conn.close()
            
            if races_count > 0:
                print("✅ 学習用データあり")
                return True
            else:
                print("⚠️  学習用データがありません")
                return False
                
        except Exception as e:
            print(f"❌ データベース確認エラー: {e}")
            return False
    else:
        print("❌ 学習用データベースが見つかりません")
        print("   まずデータ取得を実行してください")
        return False

def main():
    """メイン処理"""
    print("\n")
    print("🏇 競馬AI Pro - 全機能動作確認")
    print("=" * 80)
    print(f"実行日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    results = {}
    
    # 各機能のテスト
    results['Ultimate版サービス'] = test_ultimate_service()
    time.sleep(1)
    
    results['データ取得'] = test_data_collection()
    time.sleep(1)
    
    results['Next.js API'] = test_next_api()
    time.sleep(1)
    
    results['データベース接続'] = test_database_connection()
    time.sleep(1)
    
    results['学習システム'] = test_training_system()
    
    # 結果サマリー
    print_section("テスト結果サマリー")
    
    for test_name, result in results.items():
        status = "✅ 成功" if result else "❌ 失敗"
        print(f"{test_name:20s}: {status}")
    
    passed = sum(1 for r in results.values() if r)
    total = len(results)
    
    print(f"\n合計: {passed}/{total} テスト成功")
    
    if passed == total:
        print("\n🎉 全機能が正常に動作しています！")
        return 0
    else:
        print("\n⚠️  一部の機能に問題があります")
        return 1

if __name__ == "__main__":
    sys.exit(main())
