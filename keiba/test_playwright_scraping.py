"""
Playwrightを使ってブラウザ経由でスクレイピングするテストスクリプト
IPブロック回避に有効
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from keiba_ai.config import load_config
from keiba_ai.netkeiba.browser_client import PlaywrightClient
from keiba_ai.netkeiba.parsers import extract_race_ids

def test_playwright_scraping():
    """Playwrightでレース一覧を取得"""
    cfg = load_config(Path("config.yaml"))
    
    print("=" * 70)
    print("Playwrightブラウザモードでスクレイピングテスト")
    print("=" * 70)
    print()
    print("📌 これはブロック回避に有効な方法です：")
    print("  - 実際のブラウザを使用")
    print("  - JavaScriptが動作")
    print("  - より人間らしいアクセスパターン")
    print()
    
    # ブラウザクライアントを作成（headless=Falseでブラウザが表示される）
    with PlaywrightClient(cfg.netkeiba, cfg.storage, headless=True) as client:
        # テスト: 2024年1月1日のレース一覧を取得
        test_date = "20240101"
        url = f"{cfg.netkeiba.base}/top/race_list_sub.html?kaisai_date={test_date}"
        
        print(f"🌐 URL: {url}")
        print()
        
        try:
            result = client.fetch_html(
                url=url,
                cache_kind="list",
                cache_key=test_date,
                use_cache=False  # キャッシュを使わずに実際に取得
            )
            
            print(f"✅ 取得成功！")
            print(f"  - ステータス: {result.status_code}")
            print(f"  - HTMLサイズ: {len(result.text):,} 文字")
            print()
            
            # race_idを抽出
            race_ids = extract_race_ids(result.text)
            
            if race_ids:
                print(f"✅ {len(race_ids)}件のレースIDを発見:")
                for rid in race_ids[:5]:  # 最初の5件を表示
                    print(f"  - {rid}")
                if len(race_ids) > 5:
                    print(f"  ... 他 {len(race_ids) - 5}件")
            else:
                print("⚠️ レースIDが見つかりませんでした")
                print("HTMLの一部を表示:")
                print(result.text[:500])
            
            print()
            print("=" * 70)
            print("✅ テスト完了！")
            print()
            print("💡 使い方:")
            print("  1. requirements.txtに追加: playwright")
            print("  2. インストール:")
            print("     pip install playwright")
            print("     playwright install chromium")
            print("  3. このスクリプトを実行してブロック回避")
            
        except Exception as e:
            print(f"❌ エラー: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    test_playwright_scraping()
