"""過去レースの単勝オッズページを静的HTMLで取得できるか確認"""
import asyncio
import sys
import re
sys.path.insert(0, 'python-api')

import aiohttp

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'ja,en;q=0.9',
}


async def check_odds_static(sess: aiohttp.ClientSession, race_id: str):
    url = f'https://race.netkeiba.com/odds/index.html?type=b1&race_id={race_id}'
    async with sess.get(url) as r:
        html = (await r.read()).decode('euc-jp', errors='replace')
    # 新フォーマット
    pat1 = re.compile(r'id="odds-1_(\d+)"[^>]*>([0-9.]+)<', re.IGNORECASE)
    result1 = {m.group(1): float(m.group(2)) for m in pat1.finditer(html)}
    # 旧フォーマット
    pat2 = re.compile(r'id="odds_dl_b1_(\d+)"[^>]*>([0-9.]+)<', re.IGNORECASE)
    result2 = {m.group(1): float(m.group(2)) for m in pat2.finditer(html)}
    # ---パターン
    dash_count = html.count('---')
    print(f'{race_id}: new_fmt={len(result1)}頓 old_fmt={len(result2)}頓 dashes={dash_count} html_len={len(html)}')
    if result1:
        sample = list(result1.items())[:4]
        print(f'  sample: {sample}')
    elif result2:
        sample = list(result2.items())[:4]
        print(f'  sample: {sample}')
    else:
        # ボディスニペットで確認
        odds_area = re.findall(r'<[^>]*odds[^>]*>[^<]*<', html[:3000])
        print(f'  odds-like tags (first 5): {odds_area[:5]}')


async def main():
    async with aiohttp.ClientSession(headers=HEADERS) as sess:
        print('=== 過去レースの単勝オッズページ静的HTML確認 ===')
        # 2026-05-02 のレース（山川第二レース）
        await check_odds_static(sess, '202605021201')
        await asyncio.sleep(1.2)
        await check_odds_static(sess, '202605021203')
        await asyncio.sleep(1.2)
        # 2024年のレースも確認
        await check_odds_static(sess, '202401050101')


asyncio.run(main())
import asyncio
import sys
import re
sys.path.insert(0, 'python-api')

import aiohttp
from bs4 import BeautifulSoup


def analyze_html(html: str, label: str):
    print(f'\n=== {label} (len={len(html)}) ===')
    # テーブルクラス一覧
    tables = re.findall(r'<table[^>]{0,200}>', html)
    print(f'Tables ({len(tables)}):')
    for t in tables[:10]:
        print(' ', t[:120])
    # race_table_01 存在確認
    has_rt01 = 'race_table_01' in html
    has_rt02 = 'result_table_02' in html
    print(f'race_table_01: {has_rt01}, result_table_02: {has_rt02}')
    # BeautifulSoupで着順・オッズが読めるか
    soup = BeautifulSoup(html, 'lxml')
    t01 = soup.find('table', class_='race_table_01')
    if t01:
        rows = t01.find_all('tr')
        headers = [c.get_text(strip=True) for c in rows[0].find_all(['th','td'])] if rows else []
        print(f'race_table_01: {len(rows)} rows, headers={headers[:12]}')
        if len(rows) > 1:
            first_cells = [c.get_text(strip=True) for c in rows[1].find_all('td')]
            print(f'  first data row: {first_cells[:14]}')
    else:
        print('race_table_01: NOT FOUND')
    # race_results_new テーブルも確認
    for cls_name in ['RaceTableAll', 'race_table_new', 'ResultTable', 'HorseList']:
        t = soup.find('table', class_=cls_name) or soup.find('div', class_=cls_name)
        if t:
            print(f'{cls_name}: FOUND')


async def main():
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36',
        'Accept-Language': 'ja,en;q=0.9',
        'Accept': 'text/html,application/xhtml+xml',
    }

    async with aiohttp.ClientSession(headers=headers) as sess:
        # 1. 旧URL (db.netkeiba.com) - 最新レース
        r1 = await sess.get('https://db.netkeiba.com/race/202605021201/')
        html1 = (await r1.read()).decode('euc-jp', errors='replace')
        analyze_html(html1, 'db.netkeiba.com 202605021201 (recent)')
        await asyncio.sleep(1.5)

        # 2. race.netkeiba.com - 最新レース
        r2 = await sess.get('https://race.netkeiba.com/race/result.html?race_id=202605021201')
        html2 = (await r2.read()).decode('euc-jp', errors='replace')
        analyze_html(html2, 'race.netkeiba.com result.html 202605021201 (recent)')
        await asyncio.sleep(1.5)

        # 3. 旧URL (db.netkeiba.com) - 2年前のレース（race_table_01があるはず）
        r3 = await sess.get('https://db.netkeiba.com/race/202401050101/')
        html3 = (await r3.read()).decode('euc-jp', errors='replace')
        analyze_html(html3, 'db.netkeiba.com 202401050101 (old 2024)')


asyncio.run(main())
