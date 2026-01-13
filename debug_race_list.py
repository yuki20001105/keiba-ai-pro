#!/usr/bin/env python3
"""レース一覧ページのHTML構造をデバッグ"""
import requests
from bs4 import BeautifulSoup
import re

date = "20240106"
url = f'https://race.netkeiba.com/top/race_list.html?kaisai_date={date}'

print(f"URL: {url}\n")

response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
response.encoding = 'EUC-JP'

soup = BeautifulSoup(response.text, 'html.parser')

print("="*80)
print("【レースIDを含むリンクを探す】")
print("="*80)

# 方法1: race_id パラメータを含むリンク
links = soup.find_all('a', href=re.compile(r'race_id=\d+'))
print(f"\n方法1: race_id パラメータを含むリンク数: {len(links)}")
for link in links[:5]:
    href = link.get('href', '')
    match = re.search(r'race_id=(\d+)', href)
    if match:
        print(f"  - {match.group(1)} : {href}")

# 方法2: すべてのhrefからrace_idを抽出
all_links = soup.find_all('a', href=True)
race_ids = set()
for link in all_links:
    href = link.get('href', '')
    matches = re.findall(r'race_id=(\d{12})', href)
    for m in matches:
        race_ids.add(m)

print(f"\n方法2: 12桁のrace_idを抽出: {len(race_ids)} 件")
for rid in sorted(race_ids)[:10]:
    print(f"  - {rid}")

# 方法3: current_group を確認
groups = re.findall(r'current_group=(\d+)', response.text)
print(f"\n方法3: current_group: {set(groups)}")

# HTMLサンプル
print(f"\n【HTMLサンプル（最初の1000文字）】")
print(response.text[:1000])
