import requests
from bs4 import BeautifulSoup
import re

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

# ユーザー提供のURL
url = 'https://race.netkeiba.com/top/?kaisai_date=20251025'

print(f'Fetching: {url}\n')

response = requests.get(url, headers=headers, timeout=10)
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
    
    print(f'\nTotal unique race_ids: {len(race_ids)}')
    
    if race_ids:
        # 最初のrace_idでテスト
        test_race_id = race_ids[0]
        print(f'\n{"="*80}')
        print(f'Testing first race_id: {test_race_id}')
        print(f'{"="*80}')
        
        # result.htmlでテスト
        result_url = f'https://race.netkeiba.com/race/result.html?race_id={test_race_id}'
        print(f'\nURL: {result_url}')
        
        result_response = requests.get(result_url, headers=headers, timeout=10)
        print(f'Status: {result_response.status_code}')
        
        if result_response.status_code == 200:
            result_html = result_response.content.decode('euc-jp', errors='ignore')
            
            # タイトルを確認
            if '<title>' in result_html:
                start = result_html.find('<title>') + 7
                end = result_html.find('</title>')
                result_title = result_html[start:end].strip()
                
                cleaned = result_title.replace('|', '').replace('-', '').replace('netkeiba', '').strip()
                
                if cleaned:
                    print(f'✓ Result page exists!')
                    print(f'  Title: {result_title[:80]}')
                else:
                    print('✗ Result page has empty title')
        
        # shutuba.htmlでもテスト
        shutuba_url = f'https://race.netkeiba.com/race/shutuba.html?race_id={test_race_id}'
        print(f'\nShutuba URL: {shutuba_url}')
        
        shutuba_response = requests.get(shutuba_url, headers=headers, timeout=10)
        print(f'Status: {shutuba_response.status_code}')
        
        if shutuba_response.status_code == 200:
            shutuba_html = shutuba_response.content.decode('euc-jp', errors='ignore')
            
            if '<title>' in shutuba_html:
                start = shutuba_html.find('<title>') + 7
                end = shutuba_html.find('</title>')
                shutuba_title = shutuba_html[start:end].strip()
                
                cleaned = shutuba_title.replace('|', '').replace('-', '').replace('netkeiba', '').strip()
                
                if cleaned:
                    print(f'✓ Shutuba page exists!')
                    print(f'  Title: {shutuba_title[:80]}')
                else:
                    print('✗ Shutuba page has empty title')
    else:
        print('\n✗ No race_ids found in the page')
        
        # HTMLの一部を保存して確認
        with open('top_page_sample.html', 'w', encoding='utf-8') as f:
            f.write(html[:10000])
        print('\nHTML sample saved to top_page_sample.html')
