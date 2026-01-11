import requests
from bs4 import BeautifulSoup

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# 既知の存在するrace_idを試す（2024年の有馬記念など）
# 有馬記念は12月の最終週、中山競馬場
test_race_ids = [
    '202412220612',  # 2024/12/22 中山12R（有馬記念の可能性）
    '202412210612',  # 2024/12/21 中山12R
    '202412150612',  # 2024/12/15 中山12R
    '202412080612',  # 2024/12/08 中山12R
    '202412010601',  # 2024/12/01 中山1R
]

for race_id in test_race_ids:
    url = f'https://race.netkeiba.com/race/shutuba.html?race_id={race_id}'
    print(f'\nTesting race_id: {race_id}')
    print(f'URL: {url}')
    
    response = requests.get(url, headers=headers)
    print(f'Status: {response.status_code}')
    
    if response.status_code == 200:
        soup = BeautifulSoup(response.text, 'html.parser')
        race_name = soup.find('h1', class_='RaceName')
        if race_name:
            print(f'✓ SUCCESS! RaceName: {race_name.text.strip()}')
            
            # このrace_idでAPIテスト
            print(f'\n{"="*80}')
            print(f'Testing API with race_id: {race_id}')
            print(f'{"="*80}')
            
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
            else:
                print(f'Error: {api_response.text}')
            
            break  # 成功したら終了
    else:
        print('✗ Race not found')
