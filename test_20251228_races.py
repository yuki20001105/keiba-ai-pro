import requests
from bs4 import BeautifulSoup

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

# 2024年12月28日のrace_idを推測
# 場コード: 10=小倉, 06=中山, 05=東京
# kaisai_date=20241228
date = '20241228'
venues = [
    ('10', '小倉'),
    ('06', '中山'),
    ('05', '東京'),
    ('04', '新潟'),
    ('09', '阪神'),
]

print('='*80)
print('Testing race_ids for 2024/12/28')
print('='*80)

for venue_code, venue_name in venues:
    race_num = '01'  # 1レース目
    race_id = f'{date}{venue_code}{race_num}'
    
    url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
    
    print(f'\n{venue_name} ({venue_code}): race_id={race_id}')
    print(f'URL: {url}')
    
    response = requests.get(url, headers=headers)
    print(f'Status: {response.status_code}')
    
    if response.status_code == 200:
        html = response.content.decode('euc-jp', errors='ignore')
        soup = BeautifulSoup(html, 'html.parser')
        
        title = soup.find('title')
        title_text = title.text if title else ''
        
        # タイトルが空白だけでないかチェック
        cleaned_title = title_text.strip().replace('|', '').replace('-', '').replace('netkeiba', '').strip()
        
        if cleaned_title:
            print(f'✓ FOUND! Title: {title_text}')
            
            # RaceName
            race_name = soup.find('h1', class_='RaceName')
            if race_name:
                print(f'✓ RaceName: {race_name.text.strip()}')
            
            # APIテスト
            print(f'\n{"="*80}')
            print('API Test')
            print(f'{"="*80}')
            
            api_url = 'http://localhost:3000/api/netkeiba/race'
            test_user_id = '00000000-0000-0000-0000-000000000000'
            
            # testOnly
            print('\n1. testOnly mode')
            try:
                api_response = requests.post(
                    api_url,
                    json={'raceId': race_id, 'userId': test_user_id, 'testOnly': True},
                    timeout=30
                )
                print(f'Status: {api_response.status_code}')
                print(f'Response: {api_response.json()}')
                
                # Full scrape
                print('\n2. Full scrape')
                api_response = requests.post(
                    api_url,
                    json={'raceId': race_id, 'userId': test_user_id},
                    timeout=30
                )
                print(f'Status: {api_response.status_code}')
                if api_response.status_code == 200:
                    data = api_response.json()
                    print(f'✓ Success: {data.get("success")}')
                    print(f'✓ Race Name: {data.get("raceName")}')
                    print(f'✓ Results: {len(data.get("results", []))} horses')
                    print(f'✓ Payouts: {len(data.get("payouts", []))} items')
                    
                    print(f'\n{"="*80}')
                    print('SUCCESS! Data collection working!')
                    print(f'{"="*80}')
                else:
                    print(f'✗ Error: {api_response.text[:200]}')
            except Exception as e:
                print(f'✗ API Error: {e}')
            
            break
        else:
            print('✗ Race not found')
