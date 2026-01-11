"""
IP状態別の動作確認テスト
通常IP → VPN推奨のフロー確認
"""
import requests

def test_ip_flow():
    """IP状態に応じたフローのテスト"""
    
    print("=" * 80)
    print(" IP状態別動作確認テスト")
    print("=" * 80)
    
    # 現在のIP状態確認
    print("\n[ステップ1] 現在のIP状態")
    print("-" * 80)
    
    try:
        current_ip_response = requests.get('https://api.ipify.org?format=json', timeout=10)
        current_ip = current_ip_response.json()['ip']
        print(f"現在のIPアドレス: {current_ip}")
        
        # ブロックされたIPかチェック
        blocked_ip = "180.46.30.140"
        if current_ip == blocked_ip:
            print("⚠ このIPはnetkeiba.comでブロックされています")
            ip_status = "BLOCKED"
        else:
            print("✓ VPN接続済みまたは別環境のIPです")
            ip_status = "OK"
            
    except Exception as e:
        print(f"✗ IP確認エラー: {e}")
        ip_status = "ERROR"
    
    # netkeiba.comへの直接アクセステスト
    print("\n[ステップ2] netkeiba.com 直接アクセステスト")
    print("-" * 80)
    
    try:
        response = requests.get(
            'https://race.netkeiba.com/',
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'},
            timeout=15
        )
        
        if response.status_code == 200 and len(response.content) > 10000:
            print(f"✓ アクセス成功 (Status: {response.status_code}, Size: {len(response.content):,} bytes)")
            access_status = "SUCCESS"
        elif response.status_code == 400:
            print(f"✗ アクセスブロック (Status: 400)")
            print("  → VPN接続が必要です")
            access_status = "BLOCKED"
        else:
            print(f"⚠ 予期しないレスポンス (Status: {response.status_code}, Size: {len(response.content)} bytes)")
            access_status = "UNKNOWN"
            
    except Exception as e:
        print(f"✗ アクセスエラー: {type(e).__name__}")
        access_status = "ERROR"
    
    # スクレイピングサービスの動作確認
    print("\n[ステップ3] スクレイピングサービスの動作フロー")
    print("-" * 80)
    
    try:
        # ヘルスチェック
        health_response = requests.get('http://localhost:8001/health', timeout=5)
        
        if health_response.status_code != 200:
            print("✗ スクレイピングサービスが起動していません")
            print("\n必要なアクション:")
            print("  C:\\Users\\yuki2\\Documents\\ws\\keiba\\Scripts\\python.exe scraping_service_undetected.py")
            return
        
        print("✓ スクレイピングサービス稼働中")
        
        # レースデータ取得テスト
        print("\n→ レースデータ取得を試行中...")
        scrape_response = requests.post(
            'http://localhost:8001/scrape/race',
            json={'race_id': '202606010411'},
            timeout=120
        )
        
        if scrape_response.status_code == 200:
            data = scrape_response.json()
            
            if data['success']:
                print("✓ データ取得成功")
                print(f"  レース名: {data.get('race_name', 'N/A')}")
            else:
                error_msg = data.get('error', '')
                print(f"✗ データ取得失敗")
                print(f"  エラー: {error_msg}")
                
                # VPN推奨メッセージのチェック
                if 'VPN' in error_msg or 'ブロック' in error_msg:
                    print("\n📌 サービスの判定:")
                    print("  → 通常IPでアクセス試行")
                    print("  → IPブロックを検出")
                    print("  → VPN接続を推奨")
                    service_flow = "RECOMMEND_VPN"
                else:
                    service_flow = "OTHER_ERROR"
        else:
            print(f"✗ HTTPエラー: {scrape_response.status_code}")
            service_flow = "HTTP_ERROR"
            
    except requests.exceptions.ConnectionError:
        print("✗ スクレイピングサービスが起動していません")
        service_flow = "SERVICE_DOWN"
    except Exception as e:
        print(f"✗ エラー: {type(e).__name__}: {str(e)[:100]}")
        service_flow = "ERROR"
    
    # 結果サマリー
    print("\n" + "=" * 80)
    print(" テスト結果サマリー")
    print("=" * 80)
    
    print(f"\n現在の状態:")
    print(f"  IPアドレス: {current_ip}")
    print(f"  IP状態: {ip_status}")
    print(f"  netkeiba直接アクセス: {access_status}")
    
    print(f"\n推奨される動作フロー:")
    if ip_status == "BLOCKED" or access_status == "BLOCKED":
        print("  1. ❌ 通常IPでアクセス → ブロック検出")
        print("  2. ⚠️  VPN接続を推奨メッセージ表示")
        print("  3. ✅ ユーザーがVPN接続")
        print("  4. ✅ 再度データ収集実行")
    elif ip_status == "OK" and access_status == "SUCCESS":
        print("  1. ✅ 通常IPでアクセス成功")
        print("  2. ✅ そのままデータ収集実行")
        print("  （VPN不要）")
    
    print("\n" + "=" * 80)

if __name__ == "__main__":
    test_ip_flow()
