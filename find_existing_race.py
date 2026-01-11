import requests
from bs4 import BeautifulSoup

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

# 2024年の確実に終わっているレースを試す
# 11月のレースなら確実に結果が出ているはず
test_race_ids = [
    '202411240611',  # 2024/11/24 中山11R（ジャパンカップダート）
    '202411030611',  # 2024/11/03 東京11R（天皇賞（秋））
    '202410270611',  # 2024/10/27 東京11R（天皇賞）
    '202410060611',  # 2024/10/06 中山11R
]

for race_id in test_race_ids:
    url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
    
    print(f'\n{"="*80}')
    print(f'Testing race_id: {race_id}')
    print(f'URL: {url}')
    print(f'{"="*80}')
    
    response = requests.get(url, headers=headers)
    print(f'Status: {response.status_code}')
    
    if response.status_code == 200:
        # EUC-JPでデコード
        html = response.content.decode('euc-jp', errors='ignore')
        soup = BeautifulSoup(html, 'html.parser')
        
        # タイトルを確認
        title = soup.find('title')
        title_text = title.text if title else ''
        print(f'Title: {title_text[:80]}')
        
        # タイトルが空白だけでないかチェック
        if title_text.strip().replace('|', '').replace('-', '').replace('netkeiba', '').strip():
            print('✓ Race exists!')
            
            # RaceName
            race_name = soup.find('h1', class_='RaceName')
            if race_name:
                print(f'✓ RaceName: {race_name.text.strip()}')
            
            # RaceData
            race_data = soup.find('div', class_='RaceData01')
            if race_data:
                print(f'✓ RaceData01: {race_data.text.strip()[:100]}')
            
            # Result table
            result_table = soup.find('table', class_='Result_Table')
            if result_table:
                rows = result_table.find_all('tr')
                print(f'✓ Result_Table: {len(rows)} rows')
            
            # このrace_idを使ってAPIテスト
            print(f'\n--- API Test ---')
            api_url = 'http://localhost:3000/api/netkeiba/race'
            test_user_id = '00000000-0000-0000-0000-000000000000'
            
            api_response = requests.post(
                api_url,
                json={'raceId': race_id, 'userId': test_user_id, 'testOnly': True}
            )
            print(f'testOnly: {api_response.json()}')
            
            break  # 成功したら終了
        else:
            print('✗ Race does not exist (empty title)')
