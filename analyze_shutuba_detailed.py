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
    
    # title
    title = soup.find('title')
    print(f'Title: {title.text if title else "NOT FOUND"}')
    
    # メタタグ
    meta_desc = soup.find('meta', attrs={'name': 'description'})
    if meta_desc:
        print(f'Meta description: {meta_desc.get("content", "")}')
    
    # H1, H2, H3タグをすべて表示
    print('\n' + '='*80)
    print('All H1, H2, H3 tags:')
    print('='*80)
    
    for tag_name in ['h1', 'h2', 'h3']:
        tags = soup.find_all(tag_name)
        if tags:
            print(f'\n{tag_name.upper()} tags ({len(tags)}):')
            for tag in tags[:5]:  # 最初の5個
                print(f'  - {tag.text.strip()[:100]}')
                print(f'    Class: {tag.get("class", [])}')
    
    # RaceHeadlineを探す
    print('\n' + '='*80)
    print('RaceHeadline:')
    print('='*80)
    
    race_headline = soup.find(class_='RaceHeadline')
    if race_headline:
        print(race_headline.prettify())
    
    # テーブルを探す
    print('\n' + '='*80)
    print('Tables:')
    print('='*80)
    tables = soup.find_all('table')
    print(f'Found {len(tables)} tables')
    
    for i, table in enumerate(tables[:3]):
        print(f'\nTable {i+1}:')
        print(f'  Class: {table.get("class", [])}')
        rows = table.find_all('tr')
        print(f'  Rows: {len(rows)}')
        if rows and len(rows) > 1:
            first_data_row = rows[1]
            cols = first_data_row.find_all(['td', 'th'])
            print(f'  First data row columns: {len(cols)}')
