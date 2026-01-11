"""
undetected-chromedriver でnetkeiba.comにアクセステスト
Selenium + undetected-chromedriverはbot検出を回避するための最強ツール
"""
import undetected_chromedriver as uc
import time

def test_netkeiba_with_undetected():
    """undetected-chromedriverでnetkeiba.comをテスト"""
    
    test_urls = [
        ("https://race.netkeiba.com/", "トップページ"),
        ("https://race.netkeiba.com/race/shutuba.html?race_id=202606010411", "出馬表 (race_id: 202606010411)"),
        ("https://race.netkeiba.com/race/result.html?race_id=202412220612", "結果 (race_id: 202412220612 - 有馬記念日)"),
    ]
    
    print("=" * 80)
    print("undetected-chromedriver テスト開始")
    print("=" * 80)
    
    # undetected-chromedriverでブラウザを起動
    options = uc.ChromeOptions()
    # headless=True だと検出される可能性があるのでFalseで実行
    options.headless = False
    
    driver = uc.Chrome(options=options, use_subprocess=False)
    
    try:
        # 各URLをテスト
        for url, description in test_urls:
            print(f"\n[{description}]")
            print(f"URL: {url}")
            
            try:
                # ページにアクセス
                driver.get(url)
                
                # JavaScriptレンダリング待機
                time.sleep(3)
                
                # ページソースを取得
                page_source = driver.page_source
                content_length = len(page_source)
                
                print(f"✓ Content Length: {content_length:,} bytes")
                
                # タイトルを取得
                title = driver.title
                print(f"  Page Title: {title}")
                
                # 現在のURLを確認（リダイレクトされていないか）
                current_url = driver.current_url
                if current_url != url:
                    print(f"  ⚠ リダイレクト: {current_url}")
                
                # HTML内容をチェック
                checks = {
                    'RaceName': 'レース名要素',
                    'RaceList': 'レース一覧',
                    'Result_Table': '結果テーブル',
                    'Shutuba_Table': '出馬表',
                    '<table': 'テーブル要素',
                    'race_id': 'race_id参照',
                    '400 Bad Request': 'エラーページ',
                    '403 Forbidden': '403エラー',
                }
                
                print("\n  HTML要素チェック:")
                found_any = False
                for keyword, label in checks.items():
                    if keyword in page_source:
                        if 'エラー' in label or 'Bad Request' in keyword or 'Forbidden' in keyword:
                            print(f"    ✗ {label} が検出されました")
                        else:
                            print(f"    ✓ {label} が見つかりました")
                            found_any = True
                
                if not found_any and '400 Bad Request' not in page_source and '403 Forbidden' not in page_source:
                    print("    ⚠ 主要要素が見つかりませんでしたが、エラーページでもありません")
                
                # 最初の500文字を表示
                print(f"\n  HTML冒頭 (500文字):")
                print("  " + "-" * 76)
                # 改行を保持しつつインデント
                for line in page_source[:500].split('\n'):
                    print(f"  {line}")
                print("  " + "-" * 76)
                
            except Exception as e:
                print(f"  ✗ エラー: {type(e).__name__}: {str(e)[:200]}")
        
    finally:
        # ブラウザを閉じる
        print("\n\nブラウザを10秒後に閉じます...")
        time.sleep(10)
        driver.quit()
    
    print("\n" + "=" * 80)
    print("テスト完了")
    print("=" * 80)

if __name__ == "__main__":
    test_netkeiba_with_undetected()
