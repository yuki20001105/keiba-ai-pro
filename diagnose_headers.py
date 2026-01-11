#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
netkeiba.com へのアクセス診断スクリプト - ヘッダ調整版
"""
import requests
from bs4 import BeautifulSoup

print("=" * 100)
print("netkeiba.com アクセス診断（ヘッダ調整版）")
print("=" * 100)

# 複数のヘッダパターンを試す
header_patterns = [
    {
        "name": "Minimal headers",
        "headers": {'User-Agent': 'Mozilla/5.0'}
    },
    {
        "name": "With Referer",
        "headers": {
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://race.netkeiba.com/top/',
        }
    },
    {
        "name": "Full browser headers",
        "headers": {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0',
        }
    },
]

test_urls = [
    "https://race.netkeiba.com/top/?kaisai_date=20260111",
    "https://race.netkeiba.com/race/shutuba.html?race_id=202606010401",
]

for pattern in header_patterns:
    print(f"\n\n{'='*100}")
    print(f"Pattern: {pattern['name']}")
    print('='*100)
    
    for url in test_urls:
        print(f"\nURL: {url}")
        print("-" * 100)
        
        try:
            response = requests.get(
                url,
                headers=pattern['headers'],
                timeout=15,
                allow_redirects=True,
                verify=True
            )
            
            print(f"Status: {response.status_code}")
            print(f"Content-Length: {len(response.content)}")
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                race_name = soup.select_one('h1.RaceName')
                if race_name:
                    print(f"✓ RaceName: {race_name.get_text(strip=True)[:80]}")
                else:
                    print("✗ RaceName not found")
                print("✓ SUCCESS!")
            else:
                print(f"⚠ Status {response.status_code}")
                
        except Exception as e:
            print(f"✗ ERROR: {type(e).__name__}: {e}")

print(f"\n\n{'='*100}")
print("診断完了")
print('='*100)
