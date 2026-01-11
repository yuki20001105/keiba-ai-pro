"""
Selenium with Microsoft Edge for scraping netkeiba
"""
from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
import time

def get_rendered_html_edge(url, wait_for_selector=None, wait_time=10):
    """
    Seleniumでページを開き、JavaScriptレンダリング後のHTMLを取得
    """
    # Edgeオプション設定（ヘッドレスモード）
    edge_options = Options()
    edge_options.add_argument('--headless')
    edge_options.add_argument('--no-sandbox')
    edge_options.add_argument('--disable-dev-shm-usage')
    edge_options.add_argument('--disable-gpu')
    edge_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
    
    # WebDriverを初期化（自動的にシステムのEdgeDriverを使用）
    driver = webdriver.Edge(options=edge_options)
    
    try:
        # ページを開く
        driver.get(url)
        
        # 指定された要素が表示されるまで待機
        if wait_for_selector:
            try:
                WebDriverWait(driver, wait_time).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, wait_for_selector))
                )
            except:
                print(f"Warning: Selector '{wait_for_selector}' not found within {wait_time}s")
        else:
            time.sleep(3)
        
        # レンダリング後のHTMLを取得
        html = driver.page_source
        
        return html
    
    finally:
        driver.quit()


def scrape_race_with_selenium_edge(race_id):
    """
    Seleniumを使ってレース結果をスクレイピング
    """
    url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
    
    print(f'Fetching: {url}')
    
    # Seleniumでレンダリング後のHTMLを取得
    html = get_rendered_html_edge(url, wait_for_selector='h1.RaceName', wait_time=10)
    
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
    
    # 距離・トラック種別などを抽出
    import re
    distance = None
    track_type = ''
    weather = ''
    field_condition = ''
    
    if race_data_text:
        # 距離
        dist_match = re.search(r'(\d+)m', race_data_text)
        if dist_match:
            distance = int(dist_match.group(1))
        
        # トラック種別
        if '芝' in race_data_text:
            track_type = '芝'
        elif 'ダート' in race_data_text or 'ダ' in race_data_text:
            track_type = 'ダート'
        
        # 天候
        weather_match = re.search(r'天候:([^/\s]+)', race_data_text)
        if weather_match:
            weather = weather_match.group(1).strip()
        
        # 馬場状態
        field_match = re.search(r'馬場:([^/\s]+)', race_data_text)
        if field_match:
            field_condition = field_match.group(1).strip()
    
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
        'distance': distance,
        'track_type': track_type,
        'weather': weather,
        'field_condition': field_condition,
        'results': results,
        'payouts': payouts
    }


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) > 1:
        race_id = sys.argv[1]
    else:
        # 確認済みのrace_id
        race_id = '202606010411'
    
    print(f'Testing with race_id: {race_id}')
    print('=' * 80)
    
    result = scrape_race_with_selenium_edge(race_id)
    
    print(f"\nSuccess: {result.get('success')}")
    if result.get('success'):
        print(f"Race Name: {result.get('race_name')}")
        print(f"Race Data: {result.get('race_data')[:100]}...")
        print(f"Distance: {result.get('distance')}m")
        print(f"Track Type: {result.get('track_type')}")
        print(f"Weather: {result.get('weather')}")
        print(f"Field Condition: {result.get('field_condition')}")
        print(f"Results: {len(result.get('results', []))} horses")
        print(f"Payouts: {len(result.get('payouts', []))} items")
    else:
        print(f"Error: {result.get('error')}")
