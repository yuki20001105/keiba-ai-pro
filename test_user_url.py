#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ユーザー提供のURLで直接テスト
"""
import requests
from bs4 import BeautifulSoup

# ユーザーが提供した正確なURL
urls_to_test = [
    "https://race.netkeiba.com/race/shutuba.html?race_id=202606010401&rf=race_list",
    "https://race.netkeiba.com/race/shutuba.html?race_id=202606010401",
]

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}

print("=" * 100)
print("ユーザー提供URLの確認")
print("=" * 100)

for url in urls_to_test:
    print(f"\nURL: {url}")
    print("-" * 100)
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        
        print(f"Status: {response.status_code}")
        print(f"Content-Length: {len(response.content)}")
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # RaceName を探す
            race_name = soup.select_one('h1.RaceName')
            if race_name:
                print(f"✓ RaceName: {race_name.get_text(strip=True)}")
            
            # RaceData01 を探す
            race_data = soup.select_one('.RaceData01')
            if race_data:
                print(f"✓ RaceData01: {race_data.get_text(strip=True)[:100]}")
            
            # 出馬表テーブル
            tables = soup.select('.Shutuba_Table tr')
            print(f"✓ Shutuba table rows: {len(tables)}")
            
            print("\n✓ SUCCESS! This URL works!")
        else:
            print(f"⚠ Status {response.status_code}")
            
    except Exception as e:
        print(f"✗ ERROR: {e}")

print("\n" + "=" * 100)
