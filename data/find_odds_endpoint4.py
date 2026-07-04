"""netkeiba API のパラメータ全組合せテストと s.apiUrl 特定"""
import asyncio
import re
import sys
sys.path.insert(0, 'python-api')
import aiohttp

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'ja,en;q=0.9',
}


async def find_s_apiurl():
    """odds_update.js の s.apiUrl を探す"""
    async with aiohttp.ClientSession(headers=HEADERS) as sess:
        url = 'https://cdn.netkeiba.com/img.race/common/js/jquery.odds_update.js?2019110801'
        async with sess.get(url) as r:
            js = await r.text(encoding='utf-8', errors='replace')

    # s.apiUrl を探す
    for line in js.split('\n'):
        if 'apiUrl' in line or 'api_url' in line.lower() or 'apiurl' in line.lower():
            print(f'apiUrl: {line.strip()[:200]}')

    # settings オブジェクト初期化を探す
    settings_start = js.find('var s =')
    if settings_start == -1:
        settings_start = js.find('settings')
    print(f'\ns/settings found at pos: {settings_start}')
    if settings_start >= 0:
        print(js[settings_start:settings_start + 500])


async def test_with_pid():
    """pid パラメータを含めてテスト"""
    today = '20260531'
    race_id = f'{today}0801'
    
    ref = f'https://race.netkeiba.com/odds/index.html?type=b1&race_id={race_id}'
    api_headers = {
        **HEADERS,
        'Referer': ref,
        'X-Requested-With': 'XMLHttpRequest',
        'Accept': '*/*',
    }

    # netkeiba のページから init 呼び出し時の実際の URL を確認するため
    # アクティブなレース情報ページをフェッチして、レース一覧から "before" レースを探す
    # まず今日のレーストップページをチェック
    async with aiohttp.ClientSession(headers=api_headers) as sess:
        # 京都の当日レース一覧
        page_url = f'https://race.netkeiba.com/top/race_list.html?kaisai_date={today}'
        async with sess.get(page_url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            content = await r.read()
            text = content.decode('euc-jp', errors='replace')
            print(f'Race list: [{r.status}] len={len(text)}')
            # race_id を抽出
            race_ids = re.findall(r'race_id=(\d{12})', text)
            print(f'Race IDs found: {list(set(race_ids))[:10]}')

        # pid パラメータを含めてテスト
        candidates = [
            # pid なし
            {'race_id': race_id, 'type': 'b1'},
            # pid あり
            {'pid': 'api_get_jra_odds', 'race_id': race_id, 'type': 'b1', 'input': 'UTF-8', 'output': 'json'},
            # output=json
            {'race_id': race_id, 'type': 'b1', 'output': 'json'},
            # sort あり
            {'race_id': race_id, 'type': 'b1', 'sort': 'ninki', 'output': 'json'},
            # action=init + pid
            {'pid': 'api_get_jra_odds', 'race_id': race_id, 'type': 'b1', 'action': 'init', 'output': 'json'},
        ]

        base_url = 'https://race.netkeiba.com/api/api_get_jra_odds.html'
        for params in candidates:
            try:
                async with sess.get(base_url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    text = await r.text(encoding='utf-8', errors='replace')
                    print(f'\nParams {params}: [{r.status}] {text[:300]}')
            except Exception as e:
                print(f'ERROR {params}: {e}')
            await asyncio.sleep(0.3)


async def test_jra_race_page():
    """JRA公式や netkeiba SP からオッズを取得できるか"""
    today = '20260531'
    race_id = f'{today}0801'

    api_headers = {
        **HEADERS,
        'Referer': f'https://race.netkeiba.com/odds/index.html?type=b1&race_id={race_id}',
    }

    async with aiohttp.ClientSession(headers=api_headers) as sess:
        # SP版試す
        sp_url = f'https://race.sp.netkeiba.com/?pid=odds_view&type=b1&race_id={race_id}'
        async with sess.get(sp_url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            content = await r.read()
            text = content.decode('utf-8', errors='replace')
            print(f'SP page: [{r.status}] len={len(text)}')
            # オッズ値を探す
            odds_matches = re.findall(r'(\d+\.\d)', text)
            if odds_matches:
                print(f'SP odds values: {odds_matches[:20]}')
            else:
                print('No odds found in SP page')
            # span id を探す
            spans = re.findall(r'id="odds-\d+[^"]*"', text)
            print(f'SP odds spans: {spans[:5]}')


async def main():
    print('=== 1. Find s.apiUrl in JS ===')
    await find_s_apiurl()
    print('\n=== 2. Test with pid parameter ===')
    await test_with_pid()
    print('\n=== 3. Test SP page ===')
    await test_jra_race_page()


asyncio.run(main())
