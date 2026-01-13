"""
バッチスクレイピングテストスクリプト
複数レース並列取得の動作確認
"""
import requests
import time
from db_optimizer import UltimateDatabaseOptimizer


def test_batch_scraping():
    """バッチスクレイピングのテスト"""
    
    print("=" * 80)
    print("バッチスクレイピングテスト")
    print("=" * 80)
    print()
    
    # テスト対象レース（2024年6月1日）
    race_ids = [
        "202406010101",
        "202406010102",
        "202406010103",
        "202406010104",
        "202406010105",
    ]
    
    print(f"対象レース数: {len(race_ids)}")
    print(f"並列数: 2 (メモリ節約のため)")
    print(f"詳細取得: OFF (高速モード)")
    print()
    
    # バッチスクレイピング実行
    start_time = time.time()
    
    try:
        response = requests.post(
            "http://localhost:8001/scrape/ultimate/batch",
            json={
                "race_ids": race_ids,
                "include_details": False,
                "max_workers": 2
            },
            timeout=300
        )
        
        if response.status_code == 200:
            result = response.json()
            
            print("=" * 80)
            print("結果")
            print("=" * 80)
            print()
            
            # 統計情報
            stats = result['stats']
            print(f"✅ 成功: {stats['success']}/{stats['total']}レース")
            print(f"⏱️  所要時間: {stats['elapsed_seconds']}秒")
            print(f"📊 1レースあたり: {stats['avg_seconds_per_race']}秒")
            print(f"🚀 高速化率: 約{stats['speedup_vs_sequential']}倍（逐次処理比）")
            print()
            
            # 失敗したレース
            if result.get('failed'):
                print(f"❌ 失敗: {len(result['failed'])}レース")
                for fail in result['failed']:
                    print(f"   - {fail['race_id']}: {fail['error']}")
                print()
            
            # データベース保存テスト
            if result['results']:
                print("=" * 80)
                print("データベース保存テスト")
                print("=" * 80)
                print()
                
                # バルクインサート
                db = UltimateDatabaseOptimizer("keiba_ultimate.db")
                
                races_data = []
                for race_id, data in result['results'].items():
                    races_data.append({
                        'race_id': race_id,
                        'race_info': data.get('race_info', {}),
                        'results': data.get('results', [])
                    })
                
                save_result = db.save_races_bulk(races_data)
                
                print(f"✅ 保存完了")
                print(f"   - レース数: {save_result['races_saved']}")
                print(f"   - 結果数: {save_result['results_saved']}")
                print(f"   - 所要時間: {save_result['elapsed_seconds']}秒")
                print(f"   - 1レースあたり: {save_result['avg_ms_per_race']}ms")
                print()
                
                # 統計情報
                stats = db.get_stats()
                print("データベース統計:")
                print(f"   - 総レース数: {stats['unique_races']}")
                print(f"   - 総結果数: {stats['total_results']}")
                print(f"   - ファイルサイズ: {stats['file_size_mb']}MB")
                print()
            
            # 総合評価
            total_time = time.time() - start_time
            print("=" * 80)
            print("総合結果")
            print("=" * 80)
            print(f"スクレイピング + 保存: {total_time:.2f}秒")
            print(f"予想削減時間: {(len(race_ids) * 20) - total_time:.0f}秒")
            print("=" * 80)
            
        else:
            print(f"❌ エラー: HTTP {response.status_code}")
            print(response.text)
    
    except requests.exceptions.Timeout:
        print("❌ タイムアウト: 5分以内に完了しませんでした")
    
    except Exception as e:
        print(f"❌ エラー: {e}")


def test_single_race_comparison():
    """単一レーススクレイピングとの比較"""
    
    print("\n\n")
    print("=" * 80)
    print("単一レース vs バッチ比較")
    print("=" * 80)
    print()
    
    race_id = "202406010101"
    
    # 単一レース
    print("【単一レース】")
    start = time.time()
    try:
        response = requests.post(
            "http://localhost:8001/scrape/ultimate",
            json={"race_id": race_id, "include_details": False},
            timeout=60
        )
        single_time = time.time() - start
        print(f"✅ 完了: {single_time:.2f}秒")
    except Exception as e:
        print(f"❌ エラー: {e}")
        single_time = None
    
    print()
    
    # バッチ（1レース）
    print("【バッチ（1レース）】")
    start = time.time()
    try:
        response = requests.post(
            "http://localhost:8001/scrape/ultimate/batch",
            json={
                "race_ids": [race_id],
                "include_details": False,
                "max_workers": 1
            },
            timeout=60
        )
        batch_time = time.time() - start
        print(f"✅ 完了: {batch_time:.2f}秒")
    except Exception as e:
        print(f"❌ エラー: {e}")
        batch_time = None
    
    print()
    
    if single_time and batch_time:
        overhead = batch_time - single_time
        print(f"オーバーヘッド: {overhead:.2f}秒")
        print(f"バッチのメリットは2レース以上で発揮されます")
    
    print("=" * 80)


if __name__ == "__main__":
    print("\n")
    print("🚀 Ultimate版 v2.0 高速化テスト")
    print()
    
    # メインテスト
    test_batch_scraping()
    
    # 比較テスト（オプション）
    # test_single_race_comparison()
