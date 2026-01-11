import requests
from bs4 import BeautifulSoup
import re

headers = {'User-Agent': 'Mozilla/5.0'}
url = "https://race.netkeiba.com/top/race_list.html?kaisai_date=20240106"

response = requests.get(url, headers=headers)
print(f'Status: {response.status_code}\n')

if response.status_code == 200:
    # EUC-JPでデコード
    html = response.content.decode('euc-jp', errors='ignore')
    soup = BeautifulSoup(html, 'html.parser')
    
    # race_idを含むすべてのリンク
    print('All links with race_id:')
    links_found = 0
    for link in soup.find_all('a', href=True):
        href = link['href']
        if 'race_id=' in href:
            links_found += 1
            print(f'  {href[:150]}')
            
            # race_idを抽出
            match = re.search(r'race_id=(\d+)', href)
            if match:
                race_id = match.group(1)
                print(f'    -> race_id: {race_id} ({len(race_id)} digits)')
            
            if links_found >= 10:
                break
    
    if links_found == 0:
        print('  No links with race_id found')
        
        # 代わりにすべてのリンクの最初の10個を表示
        print('\nFirst 10 links in the page:')
        all_links = soup.find_all('a', href=True)[:10]
        for link in all_links:
            href = link['href']
            text = link.text.strip()[:30]
            print(f'  {text}: {href[:100]}')
