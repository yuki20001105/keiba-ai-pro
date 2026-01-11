"""
簡潔な機能テスト - VPN接続状態での重要機能のみを検証
"""
import requests
import time

def test_scraping():
    """スクレイピング機能のテスト"""
    print("=" * 80)
    print("■ スクレイピング機能テスト")
    print("=" * 80)
    
    # 1. サービス稼働確認
    print("\n1. サービス稼働確認...")
    try:
        health = requests.get('http://localhost:8001/health', timeout=5)
        if health.status_code == 200:
            print("✓ サービス稼働中")
        else:
            print(f"✗ サービス異常: {health.status_code}")
            return
    except:
        print("✗ サービスが起動していません")
        return
    
    # 2. レースデータ取得テスト（3回）
    print("\n2. レースデータ取得テスト（3回連続）...")
    
    for i in range(3):
        print(f"\n  [{i+1}/3] リクエスト送信中...")
        start = time.time()
        
        try:
            response = requests.post(
                'http://localhost:8001/scrape/race',
                json={'race_id': '202606010411'},
                timeout=120
            )
            
            elapsed = time.time() - start
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('success'):
                    print(f"  ✓ 成功: {data.get('race_name', 'N/A')}")
                    print(f"     距離: {data.get('distance')}m, トラック: {data.get('track_type')}")
                    print(f"     待機時間: {data.get('wait_time', 0):.1f}秒, 処理時間: {elapsed:.1f}秒")
                else:
                    print(f"  ✗ 失敗: {data.get('error', 'Unknown')}")
            else:
                print(f"  ✗ HTTPエラー: {response.status_code}")
                
        except Exception as e:
            print(f"  ✗ エラー: {e}")
    
    # 3. 統計情報確認
    print("\n3. 統計情報確認...")
    try:
        stats = requests.get('http://localhost:8001/stats', timeout=5)
        if stats.status_code == 200:
            data = stats.json()
            print(f"✓ 総リクエスト数: {data.get('request_count')}回")
            print(f"  平均間隔: {data.get('average_interval', 0):.1f}秒")
            print(f"  稼働時間: {data.get('uptime_seconds', 0):.0f}秒")
    except Exception as e:
        print(f"✗ エラー: {e}")
    
    print("\n" + "=" * 80)
    print("テスト完了")
    print("=" * 80)

if __name__ == "__main__":
    test_scraping()
