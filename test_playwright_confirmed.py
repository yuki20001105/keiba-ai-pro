"""
Playwrightで実際に存在するrace_idをテスト
"""
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import time

def scrape_with_playwright(race_id):
    """Playwrightでレース結果をスクレイピング"""
    url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
    
    print(f'='*80)
    print(f'Testing race_id: {race_id}')
    print(f'='*80)
    print(f'URL: {url}\n')
    
    with sync_playwright() as p:
        # Chromiumブラウザを起動（ヘッドレスモード）
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # User-Agentを設定
        page.set_extra_http_headers({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
        # ページを開く
        page.goto(url, wait_until='load', timeout=60000)
        
        # JavaScriptの実行を待つ
        time.sleep(5)
        
        # HTMLを取得
        html = page.content()
        
        # ブラウザを閉じる
        browser.close()
    
    # BeautifulSoupでパース
    soup = BeautifulSoup(html, 'html.parser')
    
    # レース名
    race_name = soup.find('h1', class_='RaceName')
    if race_name:
        print(f'✓ Race Name: {race_name.text.strip()}')
    else:
        print('✗ Race Name not found')
    
    # レースデータ
    race_data = soup.find('div', class_='RaceData01')
    if race_data:
        print(f'✓ Race Data: {race_data.text.strip()[:100]}')
    else:
        print('✗ Race Data not found')
    
    # 結果テーブル
    result_table = soup.find('table', class_='Result_Table')
    if result_table:
        rows = result_table.find_all('tr')
        print(f'✓ Result Table: {len(rows)-1} horses')
    else:
        print('✗ Result Table not found')
    
    # 払い戻し
    payout_table = soup.find('table', class_='Payout_Detail_Table')
    if payout_table:
        rows = payout_table.find_all('tr')
        print(f'✓ Payout Table: {len(rows)} items')
    else:
        print('✗ Payout Table not found')
    
    return race_name is not None


if __name__ == '__main__':
    # 実際に存在することが確認されたrace_id
    race_ids = [
        '202606010411',  # 2026/1/11 中山11R フェアリーS(G3) - 確認済み
    ]
    
    for race_id in race_ids:
        success = scrape_with_playwright(race_id)
        print(f'\n{"✓ SUCCESS!" if success else "✗ FAILED"}\n')
