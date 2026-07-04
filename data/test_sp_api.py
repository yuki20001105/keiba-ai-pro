"""SP版 api_get_jra_odds エンドポイントを直接テスト + ZLIB解凍"""
import asyncio
import re
import sys
import json
import time
import zlib
import base64
sys.path.insert(0, 'python-api')
import aiohttp

HEADERS_SP = {
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15',
    'Accept-Language': 'ja,en;q=0.9',
    'Referer': 'https://race.sp.netkeiba.com/?pid=odds_view&race_id=202605310801',
}

HEADERS_PC = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'ja,en;q=0.9',
    'Referer': 'https://race.netkeiba.com/odds/index.html?type=b1&race_id=202605310801',
    'X-Requested-With': 'XMLHttpRequest',
}


def zlib_base64_decode(b64_str: str) -> dict:
    """JS の ZLIB.inflateInit + inflate + JSON.parse 相当"""
    raw = base64.b64decode(b64_str)
    # wbits=-15 で raw deflate（ヘッダなし）
    try:
        decompressed = zlib.decompress(raw, -15)
    except Exception:
        # zlibヘッダあり版を試す
        decompressed = zlib.decompress(raw)
    return json.loads(decompressed.decode('utf-8'))


async def test_sp_api():
    """SP APIエンドポイントを全パラメータで試す"""
    race_id = '202605310801'  # 京都1R
    base = 'https://race.sp.netkeiba.com/'

    async with aiohttp.ClientSession(headers=HEADERS_SP) as sess:
        # パターン1: compress=0 (非圧縮JSON)
        params1 = {'pid': 'api_get_jra_odds', 'race_id': race_id, 'type': 'b1', 'sort': 'ninki', 'compress': '0', 'input': 'UTF-8', 'output': 'json'}
        async with sess.get(base, params=params1, timeout=aiohttp.ClientTimeout(total=10)) as r:
            text = await r.text(encoding='utf-8', errors='replace')
            print(f'[compress=0 output=json] status={r.status} len={len(text)}')
            print(f'  content: {text[:500]}')

        await asyncio.sleep(0.5)

        # パターン2: compress=1 (ZLIB圧縮)
        params2 = {'pid': 'api_get_jra_odds', 'race_id': race_id, 'type': 'b1', 'sort': 'ninki', 'compress': '1', 'input': 'UTF-8', 'output': 'json'}
        async with sess.get(base, params=params2, timeout=aiohttp.ClientTimeout(total=10)) as r:
            text = await r.text(encoding='utf-8', errors='replace')
            print(f'\n[compress=1 output=json] status={r.status} len={len(text)}')
            print(f'  content: {text[:500]}')
            if '"data":"' in text and '"data":""' not in text:
                try:
                    data = json.loads(text)
                    if data.get('data'):
                        decoded = zlib_base64_decode(data['data'])
                        print(f'  ZLIB decoded keys: {list(decoded.keys())}')
                        if 'odds' in decoded:
                            print(f'  odds keys: {list(decoded["odds"].keys())}')
                except Exception as e:
                    print(f'  decode error: {e}')

        await asyncio.sleep(0.5)

        # パターン3: output=jsonp (JSONP形式)
        params3 = {'pid': 'api_get_jra_odds', 'race_id': race_id, 'type': 'b1', 'sort': 'ninki', 'compress': '1', 'input': 'UTF-8', 'output': 'jsonp', 'callback': 'cb'}
        async with sess.get(base, params=params3, timeout=aiohttp.ClientTimeout(total=10)) as r:
            text = await r.text(encoding='utf-8', errors='replace')
            print(f'\n[compress=1 output=jsonp] status={r.status} len={len(text)}')
            print(f'  content: {text[:300]}')

        await asyncio.sleep(0.5)

        # パターン4: PC版エンドポイント + 同じパラメータ
        pc_base = 'https://race.netkeiba.com/api/api_get_jra_odds.html'
        pc_params = {'race_id': race_id, 'type': 'b1', 'sort': 'ninki', 'compress': '0', 'output': 'json'}
        async with sess.get(pc_base, headers=HEADERS_PC, params=pc_params, timeout=aiohttp.ClientTimeout(total=10)) as r:
            text = await r.text(encoding='utf-8', errors='replace')
            print(f'\n[PC+compress=0] status={r.status} len={len(text)}')
            print(f'  content: {text[:300]}')


async def speed_test():
    """速度テスト: SP API で今日の全レースを並列取得"""
    today = '20260531'
    # 京都 (venue=08) の12R分
    race_ids = [f'{today}{8:02d}{r:02d}' for r in range(1, 13)]
    base = 'https://race.sp.netkeiba.com/'

    async def fetch_one(sess, race_id):
        params = {'pid': 'api_get_jra_odds', 'race_id': race_id, 'type': 'b1', 'sort': 'ninki', 'compress': '0', 'output': 'json'}
        ref_headers = {**HEADERS_SP, 'Referer': f'https://race.sp.netkeiba.com/?pid=odds_view&race_id={race_id}'}
        async with sess.get(base, headers=ref_headers, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
            return race_id, r.status, await r.text(encoding='utf-8', errors='replace')

    print('\n=== Speed test: 12 races parallel ===')
    start = time.time()
    async with aiohttp.ClientSession() as sess:
        tasks = [fetch_one(sess, rid) for rid in race_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    elapsed = time.time() - start

    print(f'12レース並列: {elapsed:.2f}s')
    for res in results:
        if isinstance(res, Exception):
            print(f'  ERROR: {res}')
        else:
            race_id, status, text = res
            short = text[:150].replace('\n', ' ')
            print(f'  {race_id}: [{status}] {short}')


async def main():
    print('=== Test SP API endpoint ===')
    await test_sp_api()
    await speed_test()


asyncio.run(main())
