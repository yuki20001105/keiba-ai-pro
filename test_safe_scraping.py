"""
安全なデータ収集テスト
VPN接続後にこのスクリプトでレート制限付きスクレイピングをテスト
"""
import requests
import time

def test_scraping_service():
    """レート制限機能付きスクレイピングサービスのテスト"""
    
    print("=" * 80)
    print("レート制限機能付きスクレイピングサービステスト")
    print("=" * 80)
    
    # サービスのヘルスチェック
    print("\n[ステップ1] サービスヘルスチェック")
    print("-" * 80)
    try:
        response = requests.get('http://localhost:8001/health', timeout=5)
        health = response.json()
        print(f"✓ サービス稼働中")
        print(f"  リクエスト数: {health.get('request_count', 0)}")
        print(f"  稼働時間: {health.get('uptime_seconds', 0):.1f}秒")
    except requests.exceptions.ConnectionError:
        print("✗ サービスが起動していません")
        print("\n別のターミナルで以下のコマンドを実行してください:")
        print("C:\\Users\\yuki2\\Documents\\ws\\keiba\\Scripts\\python.exe scraping_service_v2.py")
        return
    except Exception as e:
        print(f"✗ エラー: {e}")
        return
    
    # テスト用race_id（過去の実際のレース）
    test_race_ids = [
        ("202412220612", "2024年 有馬記念予定日"),
        ("202412210611", "2024年 中山11R"),
    ]
    
    print("\n[ステップ2] レースデータ取得テスト（レート制限適用）")
    print("-" * 80)
    print("⚠ 各リクエスト間に3〜7秒の待機時間が入ります")
    print()
    
    for i, (race_id, description) in enumerate(test_race_ids, 1):
        print(f"\n[テスト {i}/{len(test_race_ids)}] {description}")
        print(f"race_id: {race_id}")
        
        try:
            start_time = time.time()
            
            # スクレイピングリクエスト
            response = requests.post(
                'http://localhost:8001/scrape/race',
                json={'race_id': race_id},
                timeout=60  # タイムアウトを長めに設定
            )
            
            elapsed = time.time() - start_time
            
            if response.status_code == 200:
                data = response.json()
                
                if data['success']:
                    print(f"✓ 成功（処理時間: {elapsed:.1f}秒）")
                    print(f"  待機時間: {data.get('wait_time', 0):.1f}秒")
                    print(f"  レース名: {data['race_name']}")
                    print(f"  距離: {data.get('distance', 'N/A')}m")
                    print(f"  トラック: {data.get('track_type', 'N/A')}")
                    print(f"  結果データ: {len(data.get('results', []))}頭")
                    print(f"  払い戻し: {len(data.get('payouts', []))}件")
                else:
                    print(f"✗ 失敗: {data.get('error', 'Unknown error')}")
            else:
                print(f"✗ HTTPエラー: {response.status_code}")
                
        except requests.exceptions.Timeout:
            print(f"✗ タイムアウト（60秒以上）")
        except Exception as e:
            print(f"✗ エラー: {type(e).__name__}: {str(e)[:100]}")
    
    # 統計情報を取得
    print("\n[ステップ3] サービス統計")
    print("-" * 80)
    try:
        response = requests.get('http://localhost:8001/stats', timeout=5)
        stats = response.json()
        print(f"✓ 総リクエスト数: {stats['total_requests']}")
        print(f"✓ 平均間隔: {stats['average_interval_seconds']:.1f}秒")
        print(f"✓ レート制限設定: {stats['rate_limit_config']['min_interval']}〜{stats['rate_limit_config']['max_interval']}秒")
    except Exception as e:
        print(f"⚠ 統計取得エラー: {e}")
    
    print("\n" + "=" * 80)
    print("テスト完了")
    print("=" * 80)
    print("\n【結果の評価】")
    print("✓ 全て成功した場合:")
    print("  → データ収集UIから安全にデータ収集を開始できます")
    print("  → ただし、1日あたりの収集数は100レース以下を推奨")
    print("\n✗ 400エラーやIPブロックが出た場合:")
    print("  → VPN接続を確認してください")
    print("  → または別のVPNサーバーに接続し直してください")
    print("\n⚠ 重要な注意:")
    print("  → 大量のデータを一度に収集しないでください")
    print("  → レート制限は自動で適用されますが、無理な使い方は避けてください")

if __name__ == "__main__":
    test_scraping_service()
