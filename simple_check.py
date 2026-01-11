"""
シンプルにページタイトルとh1を確認
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

# タイトル
title = soup.find('title')
print(f"\\nPage title: {title.get_text() if title else 'None'}")

# h1
h1_tags = soup.find_all('h1')
print(f"\\nH1 tags: {len(h1_tags)}")
for i, h1 in enumerate(h1_tags):
    print(f"  {i+1}. {h1.get('class')}: {h1.get_text(strip=True)[:50]}")

# レース結果テーブルがあるか
result_table = soup.find('table')
print(f"\\nFirst table found: {result_table is not None}")

if result_table:
    rows = result_table.find_all('tr')
    print(f"Table has {len(rows)} rows")

# エラーメッセージがあるか
error_msgs = soup.find_all(string=lambda x: x and 'エラー' in str(x) or 'error' in str(x).lower())
print(f"\\nError messages: {len(error_msgs)}")
if error_msgs:
    for msg in error_msgs[:3]:
        print(f"  - {str(msg)[:100]}")

# 404や存在しないページか
if '404' in html or 'not found' in html.lower():
    print("\\n>>> Page NOT FOUND")
else:
    print("\\n>>> Page EXISTS")
