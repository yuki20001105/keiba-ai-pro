import requests
from bs4 import BeautifulSoup

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

# 2024年の確実にレースがあった日
# 有馬記念: 12月22日(日) 中山11R
# ジャパンカップ: 11月24日(日) 東京11R
test_dates = [
    ('20241222', '06', '11', '有馬記念 中山11R'),
    ('20241222', '06', '01', '有馬記念当日 中山1R'),
    ('20241124', '05', '11', 'ジャパンカップ 東京11R'),
    ('20241124', '05', '01', 'ジャパンカップ当日 東京1R'),
    ('20241027', '05', '11', '天皇賞(秋) 東京11R'),
]

print('='*80)
print('Testing known G1 race dates')
print('='*80)

for date, venue, race_num, description in test_dates:
    race_id = f'{date}{venue}{race_num}'
    
    url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
    
    print(f'\n{description}')
    print(f'  race_id: {race_id}')
    print(f'  URL: {url}')
    
    response = requests.get(url, headers=headers)
    print(f'  Status: {response.status_code}')
    
    if response.status_code == 200:
        html = response.content.decode('euc-jp', errors='ignore')
        soup = BeautifulSoup(html, 'html.parser')
        
        title = soup.find('title')
        title_text = title.text if title else ''
        
        cleaned_title = title_text.strip().replace('|', '').replace('-', '').replace('netkeiba', '').strip()
        
        if cleaned_title:
            print(f'  ✓ FOUND!')
            print(f'  Title: {title_text[:80]}')
            
            race_name = soup.find('h1', class_='RaceName')
            if race_name:
                print(f'  RaceName: {race_name.text.strip()}')
            
            # このrace_idを使ってAPIテスト
            print(f'\n{"="*80}')
            print(f'API Test with race_id: {race_id}')
            print(f'{"="*80}')
            
            api_url = 'http://localhost:3000/api/netkeiba/race'
            test_user_id = '00000000-0000-0000-0000-000000000000'
            
            try:
                # testOnly
                print('\n1. testOnly mode')
                api_response = requests.post(
                    api_url,
                    json={'raceId': race_id, 'userId': test_user_id, 'testOnly': True},
                    timeout=30
                )
                print(f'  Status: {api_response.status_code}')
                result = api_response.json()
                print(f'  Response: {result}')
                
                if result.get('success') or result.get('exists'):
                    # Full scrape
                    print('\n2. Full scrape')
                    api_response = requests.post(
                        api_url,
                        json={'raceId': race_id, 'userId': test_user_id},
                        timeout=30
                    )
                    print(f'  Status: {api_response.status_code}')
                    
                    if api_response.status_code == 200:
                        data = api_response.json()
                        print(f'  ✓ Success: {data.get("success")}')
                        print(f'  ✓ Race Name: {data.get("raceName")}')
                        print(f'  ✓ Distance: {data.get("distance")}m')
                        print(f'  ✓ Results: {len(data.get("results", []))} horses')
                        print(f'  ✓ Payouts: {len(data.get("payouts", []))} items')
                        
                        print(f'\n{"="*80}')
                        print('✓✓✓ SUCCESS! Data collection is working! ✓✓✓')
                        print(f'{"="*80}')
                        break
                    else:
                        print(f'  ✗ Error: {api_response.text[:200]}')
            except Exception as e:
                print(f'  ✗ API Error: {e}')
        else:
            print(f'  ✗ Race not found (empty title)')
