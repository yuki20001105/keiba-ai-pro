"""
Playwrightを使ってnetkeibaからデータをスクレイピング
JavaScriptレンダリング後のHTMLを確実に取得
"""
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import time

def scrape_race_with_playwright(race_id, timeout=30000):
    """
    Playwrightを使ってレース結果をスクレイピング
    
    Args:
        race_id: レースID（12桁）
        timeout: タイムアウト（ミリ秒）
    
    Returns:
        dict: レース情報
    """
    url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
    
    print(f'Fetching: {url}')
    
    with sync_playwright() as p:
        # ブラウザを起動（ヘッドレスモード）
        browser = p.chromium.launch(headless=True)
        
        # 新しいページを開く
        page = browser.new_page()
        
        # User-Agentを設定
        page.set_extra_http_headers({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
        try:
            # ページを開く
            page.goto(url, timeout=timeout, wait_until='domcontentloaded')
            
            # h1.RaceNameが表示されるまで待機
            try:
                page.wait_for_selector('h1.RaceName', timeout=10000)
            except:
                # タイムアウトした場合でも続行
                print('Warning: RaceName not found within 10s')
            
            # 追加で少し待機（JavaScriptの実行完了を待つ）
            time.sleep(2)
            
            # レンダリング後のHTMLを取得
            html = page.content()
            
        finally:
            browser.close()
    
    # BeautifulSoupでパース
    soup = BeautifulSoup(html, 'html.parser')
    
    # レース名を取得
    race_name_elem = soup.find('h1', class_='RaceName')
    race_name = race_name_elem.text.strip() if race_name_elem else None
    
    if not race_name:
        return {
            'success': False,
            'error': 'レース名が取得できませんでした'
        }
    
    # レースデータを取得
    race_data_elem = soup.find('div', class_='RaceData01')
    race_data_text = race_data_elem.text.strip() if race_data_elem else ''
    
    # 結果テーブルを取得
    result_table = soup.find('table', class_='Result_Table')
    results = []
    
    if result_table:
        rows = result_table.find_all('tr')
        for row in rows[1:]:  # ヘッダー行をスキップ
            cols = row.find_all('td')
            if len(cols) >= 10:
                result = {
                    'finish_position': cols[0].text.strip(),
                    'bracket_number': cols[1].text.strip(),
                    'horse_number': cols[2].text.strip(),
                    'horse_name': cols[3].text.strip(),
                    'sex_age': cols[4].text.strip(),
                    'jockey_weight': cols[5].text.strip(),
                    'jockey_name': cols[6].text.strip(),
                    'finish_time': cols[7].text.strip(),
                    'margin': cols[8].text.strip(),
                    'odds': cols[9].text.strip(),
                }
                results.append(result)
    
    # 払い戻しテーブルを取得
    payout_table = soup.find('table', class_='Payout_Detail_Table')
    payouts = []
    
    if payout_table:
        rows = payout_table.find_all('tr')
        for row in rows:
            cols = row.find_all('td')
            if len(cols) >= 3:
                payout = {
                    'type': cols[0].text.strip(),
                    'numbers': cols[1].text.strip(),
                    'amount': cols[2].text.strip(),
                }
                payouts.append(payout)
    
    return {
        'success': True,
        'race_name': race_name,
        'race_data': race_data_text,
        'results': results,
        'payouts': payouts
    }


if __name__ == '__main__':
    import sys
    
    # テスト用race_id
    # 2023年の確実に終わっているレースを使用
    test_ids = [
        '202301070601',  # 2023/1/7 中山1R
        '202301080601',  # 2023/1/8 中山1R
        '202301070501',  # 2023/1/7 東京1R
    ]
    
    if len(sys.argv) > 1:
        race_id = sys.argv[1]
        test_ids = [race_id]
    
    for race_id in test_ids:
        print(f'\n{"="*80}')
        print(f'Testing race_id: {race_id}')
        print(f'{"="*80}')
        
        result = scrape_race_with_playwright(race_id)
        
        print(f"\nSuccess: {result.get('success')}")
        if result.get('success'):
            print(f"Race Name: {result.get('race_name')}")
            print(f"Results: {len(result.get('results', []))} horses")
            print(f"Payouts: {len(result.get('payouts', []))} items")
            
            if result.get('results'):
                print(f"\n✓✓✓ DATA COLLECTION WORKING! ✓✓✓")
                print(f"We can now scrape race data from netkeiba!")
            break
        else:
            print(f"Error: {result.get('error')}")
            print("Trying next race_id...")
