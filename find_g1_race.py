import requests
from bs4 import BeautifulSoup

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

# 2024年のG1レース (確実に存在する)
# 天皇賞(秋) 2024/10/27 東京(05)
# ジャパンカップ 2024/11/24 東京(05)  
test_race_ids = [
    '202411240511',  # 2024/11/24 東京11R (ジャパンカップ)
    '202410270511',  # 2024/10/27 東京11R (天皇賞秋)
    '202410130511',  # 2024/10/13 東京11R (府中牝馬S or 秋華賞)
]

for race_id in test_race_ids:
    url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
    
    print(f'\n{"="*80}')
    print(f'Testing race_id: {race_id}')
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
        print(f'Title: {title_text}')
        
        # 空白とデフォルト文字を削除
        cleaned_title = title_text.strip().replace('|', '').replace('-', '').replace('netkeiba', '').strip()
        
        if cleaned_title:
            print(f'✓ Race exists! Title: {title_text}')
            
            # RaceName
            race_name = soup.find('h1', class_='RaceName')
            if race_name:
                print(f'✓ RaceName: {race_name.text.strip()}')
            else:
                print('✗ RaceName not found')
            
            # RaceData
            race_data = soup.find('div', class_='RaceData01')
            if race_data:
                print(f'✓ RaceData01: {race_data.text.strip()[:100]}')
            else:
                print('✗ RaceData01 not found')
            
            # Result table
            result_table = soup.find('table', class_='Result_Table')
            if result_table:
                rows = result_table.find_all('tr')
                print(f'✓ Result_Table: {len(rows)} rows')
            else:
                print('✗ Result_Table not found')
            
            # このrace_idでAPIテスト
            print(f'\n--- API Test ---')
            api_url = 'http://localhost:3000/api/netkeiba/race'
            test_user_id = '00000000-0000-0000-0000-000000000000'
            
            # testOnly
            api_response = requests.post(
                api_url,
                json={'raceId': race_id, 'userId': test_user_id, 'testOnly': True}
            )
            print(f'testOnly: Status={api_response.status_code}, Response={api_response.json()}')
            
            # Full scrape
            api_response = requests.post(
                api_url,
                json={'raceId': race_id, 'userId': test_user_id}
            )
            print(f'Full scrape: Status={api_response.status_code}')
            if api_response.status_code == 200:
                data = api_response.json()
                print(f'  Success: {data.get("success")}')
                print(f'  Race Name: {data.get("raceName")}')
                print(f'  Results: {len(data.get("results", []))}')
                print(f'  Payouts: {len(data.get("payouts", []))}')
            
            break
        else:
            print('✗ Race does not exist')
