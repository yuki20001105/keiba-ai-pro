"""過去の確定済みレースでAPIを試す + Playwright取得との比較"""
import asyncio
import aiohttp
import json
import sqlite3
import os

DB = 'keiba/data/keiba_ultimate.db'
BASE = 'https://race.sp.netkeiba.com/'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15',
    'Accept': 'application/json, */*',
    'Accept-Language': 'ja,en;q=0.9',
    'X-Requested-With': 'XMLHttpRequest',
    'Referer': 'https://race.sp.netkeiba.com/?pid=odds_view',
}


def get_recent_race_ids():
    """DBから最近のrace_idを取得"""
    if not os.path.exists(DB):
        return []
    conn = sqlite3.connect(DB)
    # テーブル構造を確認
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    print(f'Tables: {tables[:5]}')
    for t in tables:
        try:
            row = conn.execute(f'SELECT * FROM {t} LIMIT 1').fetchone()
            cols = [d[0] for d in conn.execute(f'SELECT * FROM {t} LIMIT 0').description or []]
            if 'race_id' in cols:
                rows = conn.execute(f'SELECT DISTINCT race_id FROM {t} ORDER BY race_id DESC LIMIT 10').fetchall()
                ids = [r[0] for r in rows]
                print(f'{t}.race_id: {ids}')
        except Exception:
            pass
    conn.close()


async def test_past_races():
    """過去の確定レースでオッズAPIを試す"""
    # 既知の過去race_id (2026年4月前後)
    test_ids = [
        '202604060101',  # 2026-04-06 阪神1R
        '202604050101',  # 2026-04-05
        '202604130801',  # 2026-04-13 京都1R
        '202604260801',  # 2026-04-26
        '202605110801',  # 2026-05-11
        '202605180801',  # 2026-05-18
        '202605250801',  # 2026-05-25 (先週)
        '202605300801',  # 2026-05-30 (昨日)
    ]

    jar = aiohttp.CookieJar()
    async with aiohttp.ClientSession(headers=HEADERS, cookie_jar=jar) as sess:
        # まずSPページを一度取得してクッキーを確保
        async with sess.get('https://race.sp.netkeiba.com/?pid=odds_view&type=b1&race_id=202605300801', timeout=aiohttp.ClientTimeout(total=10)) as r:
            _ = await r.read()
            print(f'Session init: {r.status}')

        for race_id in test_ids:
            params = {'pid': 'api_get_jra_odds', 'race_id': race_id, 'type': 'b1', 'sort': 'ninki', 'compress': '0', 'output': 'json'}
            async with sess.get(BASE, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
                text = await r.text(encoding='utf-8', errors='replace')
                # statusとreasonだけ表示
                try:
                    d = json.loads(text)
                    print(f'{race_id}: status={d.get("status")} reason={d.get("reason")} data_len={len(str(d.get("data","")))}')
                except Exception:
                    print(f'{race_id}: raw={text[:100]}')
            await asyncio.sleep(0.3)


async def main():
    print('=== DB race_ids ===')
    get_recent_race_ids()
    print('\n=== Past race API test ===')
    await test_past_races()


asyncio.run(main())
