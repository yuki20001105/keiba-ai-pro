import requests
from bs4 import BeautifulSoup
import re

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

url = 'https://race.netkeiba.com/top/race_list.html?kaisai_date=20251228&current_group=1020260104#racelist_top_a'

print(f'Fetching: {url}\n')

response = requests.get(url, headers=headers)
print(f'Status: {response.status_code}\n')

if response.status_code == 200:
    # EUC-JPでデコード
    html = response.content.decode('euc-jp', errors='ignore')
    soup = BeautifulSoup(html, 'html.parser')
    
    # タイトル
    title = soup.find('title')
    print(f'Title: {title.text if title else "NOT FOUND"}\n')
    
    # race_idを含むすべてのリンクを探す
    print('='*80)
    print('Links with race_id:')
    print('='*80)
    
    race_ids = []
    for link in soup.find_all('a', href=True):
        href = link['href']
        if 'race_id=' in href:
            match = re.search(r'race_id=(\d+)', href)
            if match:
                race_id = match.group(1)
                if race_id not in race_ids:
                    race_ids.append(race_id)
                    link_text = link.text.strip()[:50]
                    print(f'{race_id} ({len(race_id)}桁) - {link_text}')
    
    if race_ids:
        print(f'\n{"="*80}')
        print(f'Found {len(race_ids)} unique race_ids')
        print(f'{"="*80}')
        
        # 最初のrace_idでテスト
        test_race_id = race_ids[0]
        print(f'\nTesting race_id: {test_race_id}')
        
        # result.htmlでテスト
        result_url = f'https://race.netkeiba.com/race/result.html?race_id={test_race_id}'
        print(f'URL: {result_url}')
        
        result_response = requests.get(result_url, headers=headers)
        print(f'Status: {result_response.status_code}')
        
        if result_response.status_code == 200:
            result_html = result_response.content.decode('euc-jp', errors='ignore')
            result_soup = BeautifulSoup(result_html, 'html.parser')
            
            result_title = result_soup.find('title')
            if result_title and result_title.text.strip().replace('|', '').replace('-', '').replace('netkeiba', '').strip():
                print(f'✓ Result page exists!')
                print(f'  Title: {result_title.text}')
                
                # RaceName
                race_name = result_soup.find('h1', class_='RaceName')
                if race_name:
                    print(f'  ✓ RaceName: {race_name.text.strip()}')
                else:
                    print(f'  ✗ RaceName not found')
                
                # APIテスト
                print(f'\n{"="*80}')
                print('API Test')
                print(f'{"="*80}')
                
                api_url = 'http://localhost:3000/api/netkeiba/race'
                test_user_id = '00000000-0000-0000-0000-000000000000'
                
                # testOnly
                print('\n1. testOnly mode')
                api_response = requests.post(
                    api_url,
                    json={'raceId': test_race_id, 'userId': test_user_id, 'testOnly': True},
                    timeout=30
                )
                print(f'Status: {api_response.status_code}')
                print(f'Response: {api_response.json()}')
                
                # Full scrape
                print('\n2. Full scrape')
                api_response = requests.post(
                    api_url,
                    json={'raceId': test_race_id, 'userId': test_user_id},
                    timeout=30
                )
                print(f'Status: {api_response.status_code}')
                if api_response.status_code == 200:
                    data = api_response.json()
                    print(f'Success: {data.get("success")}')
                    print(f'Race Name: {data.get("raceName")}')
                    print(f'Results: {len(data.get("results", []))} horses')
                    print(f'Payouts: {len(data.get("payouts", []))} items')
                else:
                    print(f'Error: {api_response.text[:200]}')
            else:
                print('✗ Result page empty')
    else:
        print('No race_ids found in the page')
