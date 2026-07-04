"""
ラップタイムのHTML構造を確認するスクリプト
"""
import asyncio, sys
sys.path.insert(0, 'python-api')
import aiohttp
from bs4 import BeautifulSoup

async def check_lap(race_id: str):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html',
    }
    url = f'https://db.netkeiba.com/race/{race_id}/'
    async with aiohttp.ClientSession(headers=headers) as sess:
        async with sess.get(url) as r:
            html = (await r.read()).decode('euc-jp', errors='replace')
    
    soup = BeautifulSoup(html, 'html.parser')
    
    # ラップタイムを含む要素を探す
    print(f"\n=== {race_id} ===")
    
    # パターン1: "ラップ" テキストを含む要素
    for elem in soup.find_all(string=lambda t: t and 'ラップ' in t):
        parent = elem.parent
        if parent:
            print(f"[ラップ含む要素] {parent.name}: {str(parent)[:200]}")
    
    # パターン2: class に "lap" が含まれる要素
    for elem in soup.find_all(class_=lambda c: c and 'lap' in str(c).lower()):
        print(f"[lap class] {elem.name}.{elem.get('class')}: {str(elem)[:200]}")
    
    # パターン3: "12.1" のような数字パターンを含むDL/DD要素
    import re
    pat = re.compile(r'\d{2}\.\d\s*[－\-]\s*\d{2}\.\d')
    for elem in soup.find_all(['p', 'td', 'dd', 'span', 'div']):
        txt = elem.get_text()
        if pat.search(txt) and len(txt) < 300:
            print(f"[lap pattern] {elem.name}.{elem.get('class')}: {txt[:200]}")
    
    # パターン4: race_table の中でラップが載っている行
    for tbl in soup.find_all('table'):
        for row in tbl.find_all('tr'):
            cells = row.find_all(['th','td'])
            row_text = ' | '.join(c.get_text(strip=True) for c in cells)
            if 'ラップ' in row_text or pat.search(row_text):
                print(f"[TABLE ROW] {row_text[:300]}")

asyncio.run(check_lap('202606010701'))
