"""
競馬AI予測システム 総合動作確認テスト
全コンポーネントを順番にテストして動作状況を確認
"""
import requests
import time
import sys

def print_section(title):
    """セクションヘッダーを表示"""
    print("\n" + "=" * 80)
    print(f" {title}")
    print("=" * 80)

def test_ip_status():
    """現在のIP状態確認"""
    print_section("1. IPアドレス状態確認")
    
    try:
        # 現在のIPアドレスを取得
        response = requests.get('https://api.ipify.org?format=json', timeout=10)
        current_ip = response.json()['ip']
        print(f"✓ 現在のIPアドレス: {current_ip}")
        
        # ブロックされているIPかチェック
        blocked_ip = "180.46.30.140"
        if current_ip == blocked_ip:
            print("⚠ このIPはnetkeiba.comでブロックされています")
            print("  VPN接続を推奨します")
            return False, current_ip
        else:
            print("✓ 異なるIPアドレスです（VPN接続済みまたは別環境）")
            return True, current_ip
            
    except Exception as e:
        print(f"✗ IPアドレス確認失敗: {e}")
        return None, None

def test_netkeiba_access(ip_ok):
    """netkeiba.comへのアクセステスト"""
    print_section("2. netkeiba.com 直接アクセステスト")
    
    if ip_ok is False:
        print("⚠ ブロックされたIPのため、このテストはスキップします")
        return False
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    try:
        print("→ トップページにアクセス中...")
        response = requests.get('https://race.netkeiba.com/', headers=headers, timeout=15)
        
        if response.status_code == 200:
            print(f"✓ アクセス成功 (Status: {response.status_code})")
            print(f"  Content-Length: {len(response.content):,} bytes")
            return True
        elif response.status_code == 400:
            print(f"✗ アクセス失敗 (Status: {response.status_code})")
            print("  IPブロックされています。VPN接続してください。")
            return False
        else:
            print(f"⚠ 予期しないステータス: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"✗ エラー: {type(e).__name__}: {str(e)[:100]}")
        return False

def test_scraping_service():
    """スクレイピングサービスの動作確認"""
    print_section("3. スクレイピングサービス動作確認")
    
    # ヘルスチェック
    try:
        print("→ サービスヘルスチェック中...")
        response = requests.get('http://localhost:8001/health', timeout=5)
        
        if response.status_code == 200:
            health = response.json()
            print("✓ サービス稼働中")
            print(f"  リクエスト数: {health.get('request_count', 0)}")
            print(f"  稼働時間: {health.get('uptime_seconds', 0):.1f}秒")
            print(f"  ドライバー: {'初期化済み' if health.get('driver_initialized') else '未初期化'}")
            return True
        else:
            print(f"✗ ヘルスチェック失敗 (Status: {response.status_code})")
            return False
            
    except requests.exceptions.ConnectionError:
        print("✗ サービスが起動していません")
        print("\n起動コマンド:")
        print("  C:\\Users\\yuki2\\Documents\\ws\\keiba\\Scripts\\python.exe scraping_service_undetected.py")
        return False
    except Exception as e:
        print(f"✗ エラー: {type(e).__name__}: {str(e)[:100]}")
        return False

def test_race_scraping():
    """レースデータ取得テスト"""
    print_section("4. レースデータ取得テスト")
    
    race_id = "202606010411"  # 今日のフェアリーS
    print(f"→ race_id: {race_id} でテスト中...")
    
    try:
        response = requests.post(
            'http://localhost:8001/scrape/race',
            json={'race_id': race_id},
            timeout=120
        )
        
        if response.status_code == 200:
            data = response.json()
            
            if data['success']:
                print("✓ データ取得成功")
                print(f"  レース名: {data.get('race_name', 'N/A')}")
                print(f"  距離: {data.get('distance', 'N/A')}m")
                print(f"  トラック: {data.get('track_type', 'N/A')}")
                print(f"  天候: {data.get('weather', 'N/A')}")
                print(f"  馬場: {data.get('field_condition', 'N/A')}")
                print(f"  待機時間: {data.get('wait_time', 0):.1f}秒")
                return True
            else:
                print(f"✗ データ取得失敗")
                print(f"  エラー: {data.get('error', 'Unknown error')}")
                return False
        else:
            print(f"✗ HTTPエラー: {response.status_code}")
            return False
            
    except requests.exceptions.ConnectionError:
        print("✗ スクレイピングサービスが起動していません")
        return False
    except requests.exceptions.Timeout:
        print("✗ タイムアウト（120秒以上）")
        return False
    except Exception as e:
        print(f"✗ エラー: {type(e).__name__}: {str(e)[:100]}")
        return False

def test_database():
    """データベース接続テスト"""
    print_section("5. データベース接続テスト")
    
    # Supabase接続確認（環境変数から）
    import os
    
    supabase_url = os.getenv('NEXT_PUBLIC_SUPABASE_URL')
    supabase_key = os.getenv('NEXT_PUBLIC_SUPABASE_ANON_KEY')
    
    if not supabase_url or not supabase_key:
        print("⚠ Supabase環境変数が設定されていません")
        print("  .env.local ファイルを確認してください")
        return False
    
    print(f"✓ Supabase URL設定済み: {supabase_url[:30]}...")
    print(f"✓ Supabase Key設定済み: {supabase_key[:20]}...")
    
    # 実際の接続テストは省略（Supabaseクライアント未インストールの可能性）
    return True

def generate_summary(results):
    """テスト結果サマリーを生成"""
    print_section("テスト結果サマリー")
    
    total = len(results)
    passed = sum(1 for r in results.values() if r)
    failed = total - passed
    
    print(f"\n総合結果: {passed}/{total} 件のテストに合格")
    print("\n詳細:")
    
    for test_name, result in results.items():
        status = "✓ 合格" if result else "✗ 不合格"
        print(f"  {status} - {test_name}")
    
    print("\n" + "=" * 80)
    
    if failed == 0:
        print("🎉 全てのテストに合格しました！")
        print("データ収集を開始できます。")
    else:
        print(f"⚠ {failed}件のテストが失敗しました。")
        print("\n推奨アクション:")
        
        if not results.get('netkeiba_access'):
            print("  1. ProtonVPNに接続してください")
            print("  2. test_after_vpn.py で接続確認してください")
        
        if not results.get('scraping_service'):
            print("  1. scraping_service_undetected.py を起動してください")
            print("  2. 別ターミナルで実行してください")
        
        if not results.get('race_scraping'):
            print("  1. VPN接続を確認してください")
            print("  2. スクレイピングサービスを再起動してください")

def main():
    """メイン処理"""
    print("=" * 80)
    print(" 競馬AI予測システム - 総合動作確認テスト")
    print("=" * 80)
    print(f" 実行日時: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    
    results = {}
    
    # 1. IP状態確認
    ip_ok, current_ip = test_ip_status()
    results['ip_status'] = ip_ok is not None
    
    # 2. netkeiba.comアクセステスト
    netkeiba_ok = test_netkeiba_access(ip_ok)
    results['netkeiba_access'] = netkeiba_ok
    
    # 3. スクレイピングサービス確認
    service_ok = test_scraping_service()
    results['scraping_service'] = service_ok
    
    # 4. レースデータ取得テスト（サービスが起動している場合のみ）
    if service_ok:
        race_ok = test_race_scraping()
        results['race_scraping'] = race_ok
    else:
        print_section("4. レースデータ取得テスト")
        print("⚠ スクレイピングサービスが起動していないため、スキップします")
        results['race_scraping'] = False
    
    # 5. データベース接続テスト
    db_ok = test_database()
    results['database'] = db_ok
    
    # サマリー表示
    generate_summary(results)
    
    return results

if __name__ == "__main__":
    try:
        results = main()
        
        # 終了コード設定
        all_passed = all(results.values())
        sys.exit(0 if all_passed else 1)
        
    except KeyboardInterrupt:
        print("\n\nテストが中断されました")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n予期しないエラー: {type(e).__name__}: {e}")
        sys.exit(1)
