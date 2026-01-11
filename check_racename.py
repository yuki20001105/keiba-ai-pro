"""
実際のHTMLでRaceNameの要素を確認
"""
import requests
from bs4 import BeautifulSoup

race_id = "2024010606010101"
url = f"https://race.netkeiba.com/race/result.html?race_id={race_id}"

headers = {'User-Agent': 'Mozilla/5.0'}

response = requests.get(url, headers=headers, timeout=10)
html = response.text
soup = BeautifulSoup(html, 'html.parser')

print(f"URL: {url}")
print(f"Status: {response.status_code}")
print("=" * 60)

# RaceNameを探す
race_name_elem = soup.find(class_='RaceName')
print(f"\nRaceName element: {race_name_elem}")

if race_name_elem:
    print(f"RaceName text: '{race_name_elem.get_text(strip=True)}'")
else:
    print("RaceName not found")
    
    # 代替パターンを探す
    print("\n\nSearching for alternatives...")
    
    # raceやnameを含むclass
    candidates = soup.find_all(class_=lambda x: x and ('race' in x.lower() or 'name' in x.lower() or 'title' in x.lower()))
    print(f"\nFound {len(candidates)} candidates:")
    for i, elem in enumerate(candidates[:10]):
        text = elem.get_text(strip=True)[:100]
        print(f"  {i+1}. class='{elem.get('class')}': {text}")
