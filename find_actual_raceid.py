import requests
from bs4 import BeautifulSoup
import json

# 2024年12月のカレンダーを取得
year = 2024
month = 12
url = f'https://race.netkeiba.com/top/calendar.html?year={year}&month={month}'

print(f'Fetching calendar: {url}')
response = requests.get(url)
print(f'Status: {response.status_code}\n')

if response.status_code == 200:
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # 開催日のリンクを探す（実際にレースがある日）
    race_links = soup.find_all('a', href=lambda x: x and 'race_list.html' in x)
    
    print(f'Found {len(race_links)} race days\n')
    
    if race_links:
        # 最初の開催日を取得
        first_link = race_links[0]
        href = first_link['href']
        print(f'First race day link: {href}')
        
        # race_list.htmlからrace_idを取得
        if 'kaisai_date=' in href:
            kaisai_date = href.split('kaisai_date=')[1].split('&')[0]
            print(f'Kaisai date: {kaisai_date}')
            
            # race_list.htmlを取得
            race_list_url = f'https://race.netkeiba.com{href}' if not href.startswith('http') else href
            print(f'\nFetching race list: {race_list_url}')
            
            list_response = requests.get(race_list_url)
            print(f'Status: {list_response.status_code}')
            
            if list_response.status_code == 200:
                list_soup = BeautifulSoup(list_response.text, 'html.parser')
                
                # race_idを含むリンクを探す
                race_links = list_soup.find_all('a', href=lambda x: x and 'race_id=' in x)
                
                if race_links:
                    first_race_link = race_links[0]
                    race_href = first_race_link['href']
                    print(f'\nFirst race link: {race_href}')
                    
                    if 'race_id=' in race_href:
                        race_id = race_href.split('race_id=')[1].split('&')[0]
                        print(f'\n{"="*80}')
                        print(f'Found actual race_id: {race_id}')
                        print(f'{"="*80}')
                        
                        # このrace_idでテスト
                        test_url = f'https://race.netkeiba.com/race/shutuba.html?race_id={race_id}'
                        print(f'\nTesting: {test_url}')
                        
                        test_response = requests.get(test_url)
                        print(f'Status: {test_response.status_code}')
                        
                        if test_response.status_code == 200:
                            test_soup = BeautifulSoup(test_response.text, 'html.parser')
                            race_name = test_soup.find('h1', class_='RaceName')
                            if race_name:
                                print(f'✓ RaceName: {race_name.text.strip()}')
                            else:
                                print('✗ RaceName not found')
