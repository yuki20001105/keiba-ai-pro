"""
VPN接続後の確認テスト
ProtonVPN接続後にこのスクリプトを実行してください
"""
import requests
import time

def main():
    print("=" * 80)
    print("VPN接続後の確認テスト")
    print("=" * 80)
    
    # 1. 現在のIPアドレスを確認
    print("\n[ステップ1] IPアドレス確認")
    print("-" * 80)
    try:
        response = requests.get('https://api.ipify.org?format=json', timeout=10)
        current_ip = response.json()['ip']
        print(f"✓ 現在のIPアドレス: {current_ip}")
        
        if current_ip == "180.46.30.140":
            print("⚠ 警告: IPアドレスが変わっていません！")
            print("   ProtonVPNに接続してから、このスクリプトを実行してください。")
            return
        else:
            print("✓ IPアドレスが変更されました（VPN接続成功）")
    except Exception as e:
        print(f"✗ IPアドレス確認エラー: {e}")
        return
    
    # 2. netkeiba.comのトップページにアクセス
    print("\n[ステップ2] netkeiba.com トップページテスト")
    print("-" * 80)
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
    }
    
    try:
        response = requests.get('https://race.netkeiba.com/', headers=headers, timeout=15)
        status = response.status_code
        content_length = len(response.content)
        
        print(f"Status Code: {status}")
        print(f"Content Length: {content_length:,} bytes")
        
        if status == 200:
            print("✓ トップページアクセス成功！")
            
            # HTMLの内容を確認
            if 'netkeiba' in response.text.lower():
                print("✓ netkeiba.comのコンテンツを確認")
            
        elif status == 400:
            print("✗ まだ400エラーです。別のVPNサーバーを試してください。")
            return
        elif status == 403:
            print("✗ 403 Forbidden。このVPNのIPもブロックされている可能性があります。")
            return
        else:
            print(f"⚠ 予期しないステータス: {status}")
            
    except requests.exceptions.Timeout:
        print("✗ タイムアウト。VPN接続が遅い可能性があります。")
        return
    except Exception as e:
        print(f"✗ エラー: {type(e).__name__}: {str(e)[:100]}")
        return
    
    # 3. レース情報ページのテスト
    print("\n[ステップ3] レース情報ページテスト")
    print("-" * 80)
    
    test_race_ids = [
        ("202606010411", "出馬表テスト用"),
        ("202412220612", "2024年有馬記念予定日"),
    ]
    
    for race_id, description in test_race_ids:
        print(f"\n{description} (race_id: {race_id})")
        url = f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
        
        try:
            time.sleep(2)  # 間隔を空ける
            response = requests.get(url, headers=headers, timeout=15)
            status = response.status_code
            content_length = len(response.content)
            
            print(f"  URL: {url}")
            print(f"  Status: {status}, Length: {content_length:,} bytes")
            
            if status == 200:
                content = response.text
                
                # 重要な要素をチェック
                checks = {
                    'race_id': False,
                    'RaceName': False,
                    '<table': False,
                }
                
                for keyword in checks.keys():
                    if keyword in content:
                        checks[keyword] = True
                
                found_count = sum(checks.values())
                print(f"  HTML要素: {found_count}/{len(checks)} 個見つかりました")
                
                if found_count >= 2:
                    print("  ✓ レース情報の取得に成功！")
                else:
                    print("  ⚠ 一部の情報が不足しています")
                    
            elif status == 400:
                print("  ✗ 400 Bad Request")
            elif status == 404:
                print("  ⚠ 404 Not Found (このrace_idが存在しない可能性)")
            else:
                print(f"  ⚠ Status: {status}")
                
        except Exception as e:
            print(f"  ✗ エラー: {type(e).__name__}: {str(e)[:80]}")
    
    # 4. 結果サマリー
    print("\n" + "=" * 80)
    print("テスト完了")
    print("=" * 80)
    print("\n【次のステップ】")
    print("✓ アクセスが成功した場合:")
    print("  → scraping_service.py を起動してデータ収集を開始できます")
    print("  → ただし、今後は各リクエスト間に3〜7秒の間隔を空けてください")
    print("\n✗ まだ400エラーが出る場合:")
    print("  → ProtonVPNで別の国のサーバーに接続してみてください")
    print("  → または有料プロキシサービスの利用を検討してください")
    print("\n⚠ 重要:")
    print("  → 大量のリクエストを短時間に送ると再度ブロックされます")
    print("  → レート制限を守った実装が必須です")

if __name__ == "__main__":
    main()
