import requests
from bs4 import BeautifulSoup

# 過去の確実に存在するrace_id（2024年有馬記念）
url = "https://race.netkeiba.com/race/result.html?race_id=202406050811"

print(f"Testing URL: {url}")
print("=" * 80)

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

response = requests.get(url, headers=headers)
print(f"Status: {response.status_code}")
print(f"HTML Length: {len(response.content)}")

if response.status_code == 200:
    soup = BeautifulSoup(response.content, 'html.parser')
    
    # RaceNameを探す
    race_name = soup.select_one('h1.RaceName')
    if race_name:
        print(f"\n✓ Race Name: {race_name.get_text(strip=True)}")
    else:
        print(f"\n✗ RaceName not found")
    
    # RaceDataを探す
    race_data = soup.select_one('.RaceData01')
    if race_data:
        print(f"✓ Race Data: {race_data.get_text(strip=True)}")
    else:
        print(f"✗ RaceData01 not found")
    
    # 出馬表を探す
    shutuba_table = soup.select('.Shutuba_Table')
    print(f"✓ Shutuba tables found: {len(shutuba_table)}")
    
    print("\n✓ SUCCESS! Data can be scraped with simple HTTP requests")
else:
    print(f"\n✗ Failed with status {response.status_code}")
