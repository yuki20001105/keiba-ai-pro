import requests
from bs4 import BeautifulSoup
import re

print("=" * 100)
print("netkeiba.com から実際のrace_idを抽出")
print("=" * 100)

# トップページでレース一覧を取得
url = "https://race.netkeiba.com/top/"
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

print(f"\nFetching: {url}\n")

response = requests.get(url, headers=headers, timeout=15)
print(f"Status: {response.status_code}")
print(f"Content-Length: {len(response.content)}")

if response.status_code == 200:
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # race_id を含むリンクを探す
    race_links = soup.find_all('a', href=re.compile(r'race_id='))
    print(f"\nFound {len(race_links)} race links")
    
    if race_links:
        print("\n最初の10個のrace_id:")
        for i, link in enumerate(race_links[:10]):
            href = link.get('href', '')
            match = re.search(r'race_id=(\d+)', href)
            if match:
                race_id = match.group(1)
                text = link.get_text(strip=True)
                print(f"  {i+1}. race_id={race_id} : {text}")
        
        # 最初のrace_idでテスト
        if race_links:
            match = re.search(r'race_id=(\d+)', race_links[0].get('href', ''))
            if match:
                test_race_id = match.group(1)
                print(f"\n\nテスト: race_id={test_race_id}")
                test_url = f"https://race.netkeiba.com/race/result.html?race_id={test_race_id}"
                print(f"URL: {test_url}")
                
                test_response = requests.get(test_url, headers=headers, timeout=15)
                print(f"Status: {test_response.status_code}")
                
                if test_response.status_code == 200:
                    test_soup = BeautifulSoup(test_response.text, 'html.parser')
                    race_name = test_soup.select_one('h1.RaceName')
                    if race_name:
                        print(f"✓ RaceName: {race_name.get_text(strip=True)}")
else:
    print(f"\n✗ Failed with status {response.status_code}")
    print("netkeiba.com may have blocked access or the format changed")
