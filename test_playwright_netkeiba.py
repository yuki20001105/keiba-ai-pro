"""
Playwright + playwright-stealth でnetkeiba.comにアクセステスト
"""
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
import asyncio

async def test_netkeiba_with_playwright():
    """Playwrightでnetkeiba.comをテスト"""
    
    test_urls = [
        ("https://race.netkeiba.com/", "トップページ"),
        ("https://race.netkeiba.com/race/shutuba.html?race_id=202606010411", "出馬表 (race_id: 202606010411)"),
        ("https://race.netkeiba.com/race/result.html?race_id=202412220612", "結果 (race_id: 202412220612 - 有馬記念日)"),
    ]
    
    print("=" * 80)
    print("Playwright + playwright-stealth テスト開始")
    print("=" * 80)
    
    async with async_playwright() as p:
        # Chromiumブラウザを起動（headless=Falseで実ブラウザ表示）
        browser = await p.chromium.launch(
            headless=False,  # 実ブラウザで動作確認
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox',
            ]
        )
        
        # Stealthインスタンスを作成
        stealth_config = Stealth()
        
        # コンテキストとページを作成
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        
        page = await context.new_page()
        
        # playwright-stealth を適用（bot検出回避）
        await stealth_config.apply_stealth_async(page)
        
        # 各URLをテスト
        for url, description in test_urls:
            print(f"\n[{description}]")
            print(f"URL: {url}")
            
            try:
                # ページにアクセス
                response = await page.goto(url, timeout=30000, wait_until='domcontentloaded')
                
                if response:
                    status = response.status
                    print(f"✓ Status: {status}")
                    
                    # コンテンツを取得
                    await asyncio.sleep(2)  # JavaScriptレンダリング待機
                    content = await page.content()
                    content_length = len(content)
                    
                    print(f"  Content Length: {content_length:,} bytes")
                    
                    # 特定の要素をチェック
                    if status == 200:
                        # タイトルを取得
                        title = await page.title()
                        print(f"  Page Title: {title}")
                        
                        # HTML内容をチェック
                        checks = {
                            'RaceName': 'レース名要素',
                            'RaceList': 'レース一覧',
                            'Result_Table': '結果テーブル',
                            'Shutuba_Table': '出馬表',
                            '<table': 'テーブル要素',
                            'race_id': 'race_id参照',
                        }
                        
                        print("\n  HTML要素チェック:")
                        found_any = False
                        for keyword, label in checks.items():
                            if keyword in content:
                                print(f"    ✓ {label} が見つかりました")
                                found_any = True
                        
                        if not found_any:
                            print("    ✗ 主要要素が見つかりませんでした")
                        
                        # 最初の500文字を表示
                        print(f"\n  HTML冒頭 (500文字):")
                        print("  " + "-" * 76)
                        print("  " + content[:500].replace('\n', '\n  '))
                        print("  " + "-" * 76)
                    else:
                        print(f"  ✗ エラーステータス: {status}")
                        
                else:
                    print("  ✗ レスポンスがありません")
                    
            except Exception as e:
                print(f"  ✗ エラー: {type(e).__name__}: {str(e)[:200]}")
        
        # クリーンアップ
        await context.close()
        await browser.close()
    
    print("\n" + "=" * 80)
    print("テスト完了")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(test_netkeiba_with_playwright())
