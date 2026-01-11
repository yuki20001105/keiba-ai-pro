import requests
from bs4 import BeautifulSoup

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

race_id = '202412220612'
url = f'https://race.netkeiba.com/race/shutuba.html?race_id={race_id}'

print(f'Fetching: {url}\n')

response = requests.get(url, headers=headers)
print(f'Status: {response.status_code}\n')

if response.status_code == 200:
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # RaceName
    race_name = soup.find('h1', class_='RaceName')
    print(f'RaceName: {race_name.text.strip() if race_name else "NOT FOUND"}')
    
    # RaceData (発走時刻など)
    race_data = soup.find('div', class_='RaceData01')
    if race_data:
        print(f'RaceData01: {race_data.text.strip()}')
    else:
        # 別のクラス名を試す
        race_data = soup.find('div', class_='RaceData')
        print(f'RaceData: {race_data.text.strip() if race_data else "NOT FOUND"}')
    
    # 出走馬テーブル
    shutuba_table = soup.find('table', class_='Shutuba_Table')
    if shutuba_table:
        rows = shutuba_table.find_all('tr')
        print(f'\nShutuba_Table: {len(rows)} rows')
        
        # 最初の馬の情報を表示
        for row in rows[1:2]:  # ヘッダーをスキップして最初のデータ行
            cols = row.find_all('td')
            print(f'First horse: {len(cols)} columns')
            for i, col in enumerate(cols):
                print(f'  Col {i}: {col.text.strip()[:50]}')
    else:
        print('Shutuba_Table: NOT FOUND')
    
    # すべてのクラス名をリスト
    print('\n' + '='*80)
    print('All classes in the page:')
    print('='*80)
    classes = set()
    for tag in soup.find_all(class_=True):
        classes.update(tag.get('class', []))
    
    for cls in sorted(classes):
        if 'race' in cls.lower() or 'shutuba' in cls.lower():
            print(f'  - {cls}')
