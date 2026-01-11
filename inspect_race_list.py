"""
race_list.htmlのHTMLを詳細に確認
"""
import requests
import re

kaisai_date = "20240106"
url = f"https://race.netkeiba.com/top/race_list.html?kaisai_date={kaisai_date}"

headers = {'User-Agent': 'Mozilla/5.0'}

response = requests.get(url, headers=headers, timeout=10)
html = response.text

print(f"URL: {url}")
print(f"Status: {response.status_code}")
print(f"HTML Length: {len(html)}")
print("=" * 80)

# race_idの出現回数
race_id_count = html.count('race_id')
print(f"\n'race_id' appears: {race_id_count} times")

# race_idを含むパターンを全て検索
patterns = [
    (r'race_id=(\d+)', "race_id=数字"),
    (r'race/result\.html\?race_id=(\d+)', "race/result.html?race_id=数字"),
    (r'/race/(\d{12})', "/race/12桁数字"),
    (r'href="([^"]*\d{12}[^"]*)"', "href with 12桁数字"),
]

for pattern, description in patterns:
    matches = re.findall(pattern, html)
    print(f"\n{description}: {len(matches)} matches")
    if matches:
        unique = list(set(matches))[:10]
        for m in unique:
            print(f"  - {m}")

# HTMLの一部を表示（race関連）
print("\n" + "=" * 80)
print("HTML snippet (searching for 'RaceList' or 'race'):")
print("=" * 80)

# RaceListを含む行を表示
lines = html.split('\n')
race_lines = [line for line in lines if 'RaceList' in line or 'race_id' in line]

for i, line in enumerate(race_lines[:20]):
    print(f"{i+1}. {line.strip()[:150]}")
