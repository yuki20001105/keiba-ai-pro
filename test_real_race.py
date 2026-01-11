import requests
from bs4 import BeautifulSoup

# 2024年12月28日 中山競馬場 (06) 1レース目
race_id = '202412280601'
url = f'https://race.netkeiba.com/race/shutuba.html?race_id={race_id}'

print(f'Testing race_id: {race_id}')
print(f'URL: {url}')
print()

response = requests.get(url)
print(f'Status: {response.status_code}')

if response.status_code == 200:
    soup = BeautifulSoup(response.text, 'html.parser')
    
    race_name = soup.find('h1', class_='RaceName')
    if race_name:
        print(f'✓ RaceName: {race_name.text.strip()}')
    else:
        print('✗ RaceName not found')
    
    race_data = soup.find('div', class_='RaceData')
    if race_data:
        print(f'✓ RaceData: {race_data.text.strip()}')
    else:
        print('✗ RaceData not found')
    
    shutuba_table = soup.find('table', class_='Shutuba_Table')
    if shutuba_table:
        rows = shutuba_table.find_all('tr')
        print(f'✓ Shutuba_Table: {len(rows)} rows')
    else:
        print('✗ Shutuba_Table not found')
else:
    print('✗ Failed to fetch page')

print('\n' + '='*80)
print('Testing API endpoint')
print('='*80)

# APIテスト
api_url = 'http://localhost:3000/api/netkeiba/race'
test_user_id = '00000000-0000-0000-0000-000000000000'

# Test 1: testOnly
print('\n1. testOnly mode')
response = requests.post(
    api_url,
    json={'raceId': race_id, 'userId': test_user_id, 'testOnly': True}
)
print(f'Status: {response.status_code}')
print(f'Response: {response.json()}')

# Test 2: Full scrape
print('\n2. Full scrape')
response = requests.post(
    api_url,
    json={'raceId': race_id, 'userId': test_user_id}
)
print(f'Status: {response.status_code}')
if response.status_code == 200:
    data = response.json()
    print(f'Success: {data.get("success")}')
    print(f'Race Name: {data.get("raceName")}')
    print(f'Results Count: {len(data.get("results", []))}')
else:
    print(f'Error: {response.text}')
