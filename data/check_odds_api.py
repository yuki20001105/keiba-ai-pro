"""
netkeiba の AJAX APIエンドポイントを直接呼び出して過去レースの単勝オッズを取得できるか確認
"""
import asyncio
import sys
import re
import time
sys.path.insert(0, 'python-api')

import aiohttp

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'ja,en;q=0.9',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Referer': 'https://race.netkeiba.com/odds/index.html?type=b1&race_id=202605021201',
    'X-Requested-With': 'XMLHttpRequest',
}


async def try_api(sess: aiohttp.ClientSession, url: str, label: str):
    t0 = time.time()
    try:
        async with sess.get(url) as r:
            body = await r.read()
            elapsed = time.time() - t0
            text = body[:500].decode('utf-8', errors='replace')
            print(f'[{label}] status={r.status} size={len(body)} time={elapsed:.2f}s')
            print(f'  preview: {text[:200]!r}')
    except Exception as e:
        print(f'[{label}] ERROR: {e}')


async def main():
    race_id = '202605021201'
    ts = int(time.time() * 1000)
    async with aiohttp.ClientSession(headers=HEADERS) as sess:
        # 候補1: 実績ページから示されることが多いAJAXエンドポイント
        await try_api(sess, f'https://race.netkeiba.com/api/api_get_jra_odds.html?type=1&race_id={race_id}&_={ts}', 'api_get_jra_odds type=1')
        await asyncio.sleep(1.2)
        # 候補2: typeなし
        await try_api(sess, f'https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={race_id}&_={ts}', 'api_get_jra_odds no type')
        await asyncio.sleep(1.2)
        # 候補3: race_api endpoint
        await try_api(sess, f'https://race.netkeiba.com/race_api/?class=OddsWin&method=get&race_id={race_id}&_={ts}', 'race_api OddsWin')
        await asyncio.sleep(1.2)
        # 候補4: race_api OddsHorseList
        await try_api(sess, f'https://race.netkeiba.com/race_api/?class=OddsHorseList&method=get&race_id={race_id}', 'race_api OddsHorseList')
        await asyncio.sleep(1.2)
        # 候補5: 結果ページAPI
        await try_api(sess, f'https://race.netkeiba.com/race_api/?class=RaceResultInfo&method=get&race_id={race_id}', 'race_api RaceResultInfo')


asyncio.run(main())
