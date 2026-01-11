"""
スクレイピングのテストスクリプト
URLとパーサーが正しく動作しているか確認する
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from keiba_ai.config import load_config
from keiba_ai.pipeline_daily import create_prediction_features

def test_scraping(race_id: str):
    """指定したレースIDでスクレイピングをテスト"""
    print(f"=== スクレイピングテスト ===")
    print(f"レースID: {race_id}")
    print()
    
    # 設定を読み込み
    cfg = load_config("config.yaml")
    
    # URLを確認
    from keiba_ai.netkeiba.client import NetkeibaClient
    client = NetkeibaClient(cfg.netkeiba, cfg.storage)
    url = client.build_url(cfg.netkeiba.shutuba_url.format(race_id=race_id))
    print(f"URL: {url}")
    print()
    
    try:
        # HTMLを取得
        print("HTMLを取得中...")
        fr = client.fetch_html(url, cache_kind="shutuba", cache_key=race_id, use_cache=False)
        print(f"ステータスコード: {fr.status_code}")
        print(f"HTMLサイズ: {len(fr.text)} 文字")
        print(f"キャッシュから: {fr.from_cache}")
        print()
        
        # HTMLの最初の500文字を表示
        print("HTML の先頭部分:")
        print("=" * 80)
        print(fr.text[:500])
        print("=" * 80)
        print()
        
        # パースを試行
        print("HTMLをパース中...")
        from keiba_ai.netkeiba.parsers import parse_shutuba_table
        df = parse_shutuba_table(fr.text)
        
        if df is not None and not df.empty:
            print(f"✅ パース成功！")
            print(f"取得データ: {len(df)} 頭")
            print()
            print("カラム:")
            print(df.columns.tolist())
            print()
            print("データの先頭5行:")
            print(df.head())
        else:
            print("❌ パース失敗: データが空です")
            
    except Exception as e:
        print(f"❌ エラー発生: {e}")
        import traceback
        print()
        print("詳細なエラー:")
        print(traceback.format_exc())

if __name__ == "__main__":
    # テストするレースID
    race_id = "202601110501"  # 2026年1月11日 東京1R
    
    if len(sys.argv) > 1:
        race_id = sys.argv[1]
    
    test_scraping(race_id)
