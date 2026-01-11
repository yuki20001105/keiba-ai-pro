import requests

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

# 2023年1月7日（土）のレースを試す
# 中山、東京で開催されている可能性が高い
test_ids = [
    '202301070601',  # 2023/1/7 中山1R
    '202301070501',  # 2023/1/7 東京1R
    '202301080601',  # 2023/1/8 中山1R
    '202301080501',  # 2023/1/8 東京1R
]

for race_id in test_ids:
    url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
    
    print(f'\nTesting: {race_id}')
    print(f'URL: {url}')
    
    response = requests.get(url, headers=headers, timeout=10)
    print(f'Status: {response.status_code}')
    
    if response.status_code == 200:
        html = response.content.decode('euc-jp', errors='ignore')
        
        # タイトルを確認
        if '<title>' in html:
            start = html.find('<title>') + 7
            end = html.find('</title>')
            title = html[start:end].strip()
            
            cleaned = title.replace('|', '').replace('-', '').replace('netkeiba', '').strip()
            
            if cleaned:
                print(f'✓ FOUND! Title: {title[:80]}')
                
                # APIテスト
                print('\nAPI Test:')
                api_response = requests.post(
                    'http://localhost:3000/api/netkeiba/race',
                    json={'raceId': race_id, 'userId': '00000000-0000-0000-0000-000000000000', 'testOnly': True},
                    timeout=30
                )
                print(f'testOnly: {api_response.status_code} - {api_response.json()}')
                break
            else:
                print('✗ Empty title')
