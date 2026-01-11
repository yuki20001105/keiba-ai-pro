"""
レート制限回避のテスト
より保守的な設定でスクレイピングをテスト
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from keiba_ai.config import load_config
from keiba_ai.netkeiba.client import NetkeibaClient
import time

def test_with_delay():
    """待機時間を置いてテスト"""
    cfg = load_config("config.yaml")
    client = NetkeibaClient(cfg.netkeiba, cfg.storage)
    
    # テストレースID（過去の有名なレース）
    test_races = [
        "202312230811",  # 2023年12月23日 有馬記念
    ]
    
    print("=" * 80)
    print("レート制限回避テスト")
    print(f"User-Agent: {cfg.netkeiba.user_agent[:50]}...")
    print(f"スリープ時間: {cfg.netkeiba.min_sleep_sec}-{cfg.netkeiba.max_sleep_sec}秒")
    print("=" * 80)
    print()
    
    for race_id in test_races:
        print(f"レースID: {race_id}")
        url = client.build_url(cfg.netkeiba.shutuba_url.format(race_id=race_id))
        print(f"URL: {url}")
        
        try:
            # キャッシュを使わずに取得
            fr = client.fetch_html(url, cache_kind="shutuba", cache_key=race_id, use_cache=False)
            print(f"ステータスコード: {fr.status_code}")
            print(f"HTMLサイズ: {len(fr.text)} 文字")
            
            if fr.status_code == 200:
                print("✅ 成功！アクセスできました")
                print(f"HTML先頭100文字: {fr.text[:100]}")
            elif fr.status_code == 400:
                print("❌ 400 Bad Request - レースが存在しないか、まだブロックされています")
            elif fr.status_code == 403:
                print("❌ 403 Forbidden - アクセスがブロックされています")
            else:
                print(f"❌ その他のエラー: {fr.status_code}")
                
        except Exception as e:
            print(f"❌ エラー: {e}")
        
        print()
    
    print("=" * 80)
    print("推奨事項:")
    print("1. まだ 400/403 エラーが出る場合は、24時間待ってから再試行")
    print("2. それでもダメなら、'📋 DB登録済みレースから選択' を使用")
    print("3. 本番運用では、キャッシュを最大限活用してリクエスト数を最小化")
    print("=" * 80)

if __name__ == "__main__":
    test_with_delay()
