import requests
from bs4 import BeautifulSoup

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

race_id = '202412220612'
url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'

print(f'Fetching: {url}\n')

response = requests.get(url, headers=headers)
print(f'Status: {response.status_code}\n')

if response.status_code == 200:
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # RaceName
    race_name = soup.find('h1', class_='RaceName')
    print(f'RaceName (h1): {race_name.text.strip() if race_name else "NOT FOUND"}')
    
    # RaceData
    race_data = soup.find('div', class_='RaceData01')
    if race_data:
        print(f'RaceData01: {race_data.text.strip()}')
    
    # 結果テーブル
    result_table = soup.find('table', class_='Result_Table')
    if result_table:
        rows = result_table.find_all('tr')
        print(f'\nResult_Table: {len(rows)} rows')
        
        # 最初の馬の情報を表示
        if len(rows) > 1:
            first_row = rows[1]
            cols = first_row.find_all('td')
            print(f'First horse: {len(cols)} columns')
            
            # 列の内容を表示
            print('\nColumn contents:')
            for i, col in enumerate(cols[:10]):
                text = col.text.strip()[:50]
                print(f'  {i}: {text}')
    
    # PayBackテーブル
    payback_table = soup.find('table', class_='Payout_Detail_Table')
    if payback_table:
        rows = payback_table.find_all('tr')
        print(f'\nPayout_Detail_Table: {len(rows)} rows')
    
    print('\n' + '='*80)
    print('API Test:')
    print('='*80)
    
    # APIテスト
    api_url = 'http://localhost:3000/api/netkeiba/race'
    test_user_id = '00000000-0000-0000-0000-000000000000'
    
    # testOnly
    print('\n1. testOnly mode')
    api_response = requests.post(
        api_url,
        json={'raceId': race_id, 'userId': test_user_id, 'testOnly': True}
    )
    print(f'Status: {api_response.status_code}')
    print(f'Response: {api_response.json()}')
    
    # Full scrape
    print('\n2. Full scrape')
    api_response = requests.post(
        api_url,
        json={'raceId': race_id, 'userId': test_user_id}
    )
    print(f'Status: {api_response.status_code}')
    if api_response.status_code == 200:
        data = api_response.json()
        print(f'Success: {data.get("success")}')
        print(f'Race Name: {data.get("raceName")}')
        print(f'Results Count: {len(data.get("results", []))}')
        print(f'Payouts Count: {len(data.get("payouts", []))}')
    else:
        print(f'Error: {api_response.text}')
