"""クッキー付きでSPページ → API の順で試す"""
import asyncio
import aiohttp
import json
import time


HEADERS_SP = {
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Accept-Language': 'ja,en;q=0.9',
    'X-Requested-With': 'XMLHttpRequest',
}

async def test_with_session_cookie():
    race_id = '202605310801'
    connector = aiohttp.TCPConnector(ssl=False)
    # CookieJar を共有してセッションを維持
    jar = aiohttp.CookieJar()
    async with aiohttp.ClientSession(headers=HEADERS_SP, cookie_jar=jar, connector=connector) as sess:
        # Step1: SP ページを取得してクッキーをもらう
        page_url = f'https://race.sp.netkeiba.com/?pid=odds_view&type=b1&race_id={race_id}'
        async with sess.get(page_url, timeout=aiohttp.ClientTimeout(total=15)) as r:
            _ = await r.read()
            cookies = {c.key: c.value for c in jar}
            print(f'After page fetch: cookies={list(cookies.keys())}, status={r.status}')

        await asyncio.sleep(0.5)

        # Step2: API を叩く（クッキー引き継ぎ）
        api_url = 'https://race.sp.netkeiba.com/'
        for compress in [0, 1]:
            params = {
                'pid': 'api_get_jra_odds',
                'race_id': race_id,
                'type': 'b1',
                'sort': 'ninki',
                'compress': str(compress),
                'output': 'json',
                'input': 'UTF-8',
                'isPremium': '0',
            }
            ref_h = {**HEADERS_SP, 'Referer': page_url}
            async with sess.get(api_url, headers=ref_h, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
                text = await r.text(encoding='utf-8', errors='replace')
                print(f'[cookie+compress={compress}] {text[:300]}')
            await asyncio.sleep(0.3)

        # Step3: 別の日付のレース（過去レース）でも試す
        old_race_id = '202505310801'  # 2025-05-31 京都1R （過去かも）
        print(f'\n=== 別の日付 {old_race_id} ===')
        params_old = {'pid': 'api_get_jra_odds', 'race_id': old_race_id, 'type': 'b1', 'sort': 'ninki', 'compress': '0', 'output': 'json'}
        async with sess.get(api_url, params=params_old, timeout=aiohttp.ClientTimeout(total=10)) as r:
            text = await r.text(encoding='utf-8', errors='replace')
            print(f'  {text[:300]}')

        # Step4: status を "after" に変えてみる（statusパラメータがある場合）
        print('\n=== action パラメータ試し ===')
        for action in ['update', 'init', 'b1']:
            params_act = {'pid': 'api_get_jra_odds', 'race_id': race_id, 'type': 'b1', 'action': action, 'sort': 'ninki', 'compress': '0', 'output': 'json'}
            async with sess.get(api_url, params=params_act, timeout=aiohttp.ClientTimeout(total=10)) as r:
                text = await r.text(encoding='utf-8', errors='replace')
                print(f'  [action={action}] {text[:200]}')
            await asyncio.sleep(0.2)


async def main():
    await test_with_session_cookie()


asyncio.run(main())
