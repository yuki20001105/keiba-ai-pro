#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
netkeiba.com へのアクセス診断スクリプト
複数のURLに対してHTTPリクエストを送り、ステータス/レスポンスヘッダ/本文を確認
"""
import requests
import sys
from bs4 import BeautifulSoup

print("=" * 100)
print("netkeiba.com アクセス診断")
print("=" * 100)

targets = [
    ("https://race.netkeiba.com/", "トップページ"),
    ("https://race.netkeiba.com/top/", "トップ"),
    ("https://race.netkeiba.com/top/?kaisai_date=20260111", "開催日指定"),
    ("https://race.netkeiba.com/race/shutuba.html?race_id=202606010401", "出馬表（race_id指定）"),
    ("https://race.netkeiba.com/race/result.html?race_id=202606010401", "結果（race_id指定）"),
]

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
    'Cache-Control': 'no-cache',
    'Pragma': 'no-cache',
}

session = requests.Session()
session.headers.update(headers)

for url, description in targets:
    print(f"\n[{description}]")
    print(f"URL: {url}")
    print("-" * 100)
    
    try:
        response = session.get(url, timeout=15, allow_redirects=True)
        
        # ステータスとサイズ
        print(f"Status: {response.status_code}")
        print(f"Content-Length: {len(response.content)} bytes")
        print(f"Final URL: {response.url}")
        
        # リダイレクト履歴
        if response.history:
            print(f"Redirects: {' -> '.join([str(h.status_code) for h in response.history])}")
        
        # 重要なレスポンスヘッダ
        important_headers = ['server', 'content-type', 'set-cookie', 'location', 'x-cache', 'cf-ray', 'cf-cache-status']
        for header_name in important_headers:
            if header_name in response.headers:
                val = response.headers[header_name][:150]
                print(f"{header_name}: {val}")
        
        # ボディをチェック
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # レース名の確認
            race_name = soup.select_one('h1.RaceName')
            if race_name:
                print(f"✓ RaceName found: {race_name.get_text(strip=True)[:80]}")
            else:
                print("✗ RaceName not found")
            
            # RaceData01の確認
            race_data = soup.select_one('.RaceData01')
            if race_data:
                print(f"✓ RaceData01 found: {race_data.get_text(strip=True)[:80]}")
            else:
                print("✗ RaceData01 not found")
            
            # テーブルの確認
            shutuba_tables = soup.select('.Shutuba_Table')
            result_tables = soup.select('.Race_Result_Table')
            print(f"Shutuba tables: {len(shutuba_tables)}, Result tables: {len(result_tables)}")
            
            # 404チェック
            if '404' in response.text or 'not found' in response.text.lower():
                print("⚠ 404 error found in response body")
        else:
            print(f"⚠ Non-200 status: showing first 200 chars of body")
            print(f"Body: {response.text[:200]}")
            
    except requests.exceptions.Timeout:
        print("✗ TIMEOUT")
    except requests.exceptions.ConnectionError as e:
        print(f"✗ CONNECTION ERROR: {e}")
    except Exception as e:
        print(f"✗ ERROR: {type(e).__name__}: {e}")

print("\n" + "=" * 100)
print("診断完了")
print("=" * 100)
