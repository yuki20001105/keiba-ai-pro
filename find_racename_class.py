"""
RaceNameの正しいクラス名を探す
"""
import requests
from bs4 import BeautifulSoup

race_id = "202606010401"
url = f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"

headers = {'User-Agent': 'Mozilla/5.0'}

response = requests.get(url, headers=headers, timeout=10)
soup = BeautifulSoup(response.text, 'html.parser')

print(f"URL: {url}")
print("=" * 80)

# レース名を含む可能性のある全ての要素を探す
candidates = [
    ('div', 'RaceName'),
    ('h1', None),
    ('div', 'RaceTitle'),
    ('div', 'Race_Name'),
    ('span', 'RaceName'),
]

print("\nレース名候補:")
print("-" * 80)
for tag, class_name in candidates:
    if class_name:
        elem = soup.find(tag, class_=class_name)
    else:
        elem = soup.find(tag)
    
    if elem:
        text = elem.get_text(strip=True)
        print(f"{tag} class={class_name}: {text[:100]}")

# 全てのh1, h2タグを表示
print("\n全てのh1/h2タグ:")
print("-" * 80)
for h in soup.find_all(['h1', 'h2']):
    print(f"<{h.name} class='{h.get('class')}'> {h.get_text(strip=True)[:100]}")

# RaceData01が取得できているので、その周辺を確認
race_data = soup.find('div', class_='RaceData01')
if race_data:
    print("\nRaceData01の前の要素:")
    print("-" * 80)
    prev = race_data.find_previous_sibling()
    if prev:
        print(f"<{prev.name} class='{prev.get('class')}'> {prev.get_text(strip=True)[:100]}")
    
    parent = race_data.parent
    if parent:
        print(f"\nParent: <{parent.name} class='{parent.get('class')}'>")
        for child in parent.children:
            if hasattr(child, 'name') and child.name:
                print(f"  Child: <{child.name} class='{child.get('class')}'> {child.get_text(strip=True)[:60]}")
