import requests
from bs4 import BeautifulSoup

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

# 2026年1月のレース（現在2026/1/11なので1/4は過去）
# kaisai_date=20260104と推測
test_dates = [
    ('20260104', '10', '01', '2026/1/4 小倉1R'),
    ('20260104', '06', '01', '2026/1/4 中山1R'),
    ('20260105', '10', '01', '2026/1/5 小倉1R'),
    ('20260105', '06', '01', '2026/1/5 中山1R'),
]

print('='*80)
print('Testing recent races (Jan 2026)')
print('='*80)

for date, venue, race_num, description in test_dates:
    race_id = f'{date}{venue}{race_num}'
    
    url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
    
    print(f'\n{description}')
    print(f'  race_id: {race_id}')
    
    response = requests.get(url, headers=headers)
    print(f'  Status: {response.status_code}')
    
    if response.status_code == 200:
        html = response.content.decode('euc-jp', errors='ignore')
        soup = BeautifulSoup(html, 'html.parser')
        
        title = soup.find('title')
        title_text = title.text if title else ''
        
        cleaned_title = title_text.strip().replace('|', '').replace('-', '').replace('netkeiba', '').strip()
        
        if cleaned_title:
            print(f'  ✓ FOUND! Title: {title_text[:60]}')
            
            # APIテスト
            print(f'\n  API Test:')
            api_url = 'http://localhost:3000/api/netkeiba/race'
            test_user_id = '00000000-0000-0000-0000-000000000000'
            
            try:
                api_response = requests.post(
                    api_url,
                    json={'raceId': race_id, 'userId': test_user_id, 'testOnly': True},
                    timeout=30
                )
                result = api_response.json()
                print(f'  testOnly: {result}')
                
                if result.get('success') or result.get('exists'):
                    api_response = requests.post(
                        api_url,
                        json={'raceId': race_id, 'userId': test_user_id},
                        timeout=30
                    )
                    
                    if api_response.status_code == 200:
                        data = api_response.json()
                        print(f'\n  ✓ Full scrape successful!')
                        print(f'    Race Name: {data.get("raceName")}')
                        print(f'    Results: {len(data.get("results", []))} horses')
                        print(f'\n  {"="*76}')
                        print(f'  SUCCESS! race_id format is correct and working!')
                        print(f'  {"="*76}')
                        break
            except Exception as e:
                print(f'  API Error: {e}')
        else:
            print(f'  ✗ Not found')
