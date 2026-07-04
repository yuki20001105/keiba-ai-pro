"""jquery.odds_update.js の _getOdds 関数を詳細解析 + 今日のレースでテスト"""
import asyncio
import re
import sys
import json
import time
sys.path.insert(0, 'python-api')
import aiohttp

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'ja,en;q=0.9',
}


async def read_js():
    async with aiohttp.ClientSession(headers=HEADERS) as sess:
        upd_url = 'https://cdn.netkeiba.com/img.race/common/js/jquery.odds_update.js?2019110801'
        async with sess.get(upd_url) as r:
            js = await r.text(encoding='utf-8', errors='replace')

    # _getOdds 関数全体を抽出
    # 関数の開始位置を探す
    start = js.find('var _getOdds')
    if start == -1:
        start = js.find('_getOdds')
    print(f'_getOdds found at pos {start}')
    if start >= 0:
        # 次の関数定義まで抽出（最大2000文字）
        snippet = js[max(0, start-50):start+2000]
        print(snippet)


async def test_today_races():
    """今日(2026/05/31)の実際のレースでAPIをテスト"""
    # 今日のレース候補 (YYYYMMDD + venue_code(2桁) + race_no(2桁))
    # 東京=05, 京都=08, 中京=07 etc.
    today = '20260531'
    test_races = [
        f'{today}0801',  # 京都1R
        f'{today}0802',  # 京都2R
        f'{today}0803',  # 京都3R
        f'{today}0501',  # 東京1R (if applicable)
    ]

    ref = f'https://race.netkeiba.com/odds/index.html?type=b1&race_id={today}0801'
    api_headers = {
        **HEADERS,
        'Referer': ref,
        'X-Requested-With': 'XMLHttpRequest',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
    }

    async with aiohttp.ClientSession() as sess:
        for race_id in test_races:
            # パターン1: シンプルなGET
            url = f'https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={race_id}&type=b1'
            try:
                async with sess.get(url, headers=api_headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    text = await r.text(encoding='utf-8', errors='replace')
                    print(f'[{r.status}] race_id={race_id} type=b1: {text[:200]}')
            except Exception as e:
                print(f'ERROR {race_id}: {e}')
            await asyncio.sleep(0.5)

        # パターン2: action パラメータ付き
        for action in ['init', 'update', '']:
            race_id = f'{today}0801'
            url = f'https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={race_id}&type=b1'
            if action:
                url += f'&action={action}'
            try:
                async with sess.get(url, headers=api_headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    text = await r.text(encoding='utf-8', errors='replace')
                    print(f'\n[action={action!r}] [{r.status}]: {text[:400]}')
            except Exception as e:
                print(f'ERROR action={action}: {e}')
            await asyncio.sleep(0.5)


async def main():
    print('=== 1. Reading jquery.odds_update.js ===')
    await read_js()
    print('\n=== 2. Testing today races API ===')
    await test_today_races()


asyncio.run(main())
