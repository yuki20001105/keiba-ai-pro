"""netkeiba XHR APIエンドポイントを詳細調査"""
import asyncio
import re
import sys
sys.path.insert(0, 'python-api')
import aiohttp

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'ja,en;q=0.9',
}


async def investigate():
    async with aiohttp.ClientSession(headers=HEADERS) as sess:
        # 1) odds_tanfuku.js を読んで API URL を特定
        js_url = 'https://cdn.netkeiba.com/img.race/common/js/race/odds_tanfuku.js?2019110801'
        print(f'=== Fetching {js_url} ===')
        async with sess.get(js_url) as r:
            js = await r.text(encoding='utf-8', errors='replace')
        print(f'JS length: {len(js)}')
        # api_get_jra_odds を含む行を探す
        for line in js.split('\n'):
            if 'api_get_jra_odds' in line or 'api_get' in line.lower() or 'load_odds' in line.lower():
                print(f'  MATCH: {line.strip()[:200]}')
        # ajax/fetch/$.get パターン
        ajax_patterns = re.findall(r'(?:ajax|\.get|\.post|fetch|url)\s*[\(:{][^;]{0,200}', js)
        for p in ajax_patterns[:10]:
            print(f'  AJAX: {p.strip()[:200]}')

        # 2) jquery.odds_update.js も確認
        upd_url = 'https://cdn.netkeiba.com/img.race/common/js/jquery.odds_update.js?2019110801'
        print(f'\n=== Fetching {upd_url} ===')
        async with sess.get(upd_url) as r:
            upd_js = await r.text(encoding='utf-8', errors='replace')
        print(f'JS length: {len(upd_js)}')
        for line in upd_js.split('\n'):
            if 'api_get_jra_odds' in line or 'api_get' in line.lower() or 'odds' in line.lower():
                if 'url' in line.lower() or 'ajax' in line.lower() or 'get' in line.lower():
                    print(f'  MATCH: {line.strip()[:200]}')

        # 3) api_get_jra_odds.html を直接叩いてみる（パラメータ各種試す）
        race_id = '202605051201'
        api_candidates = [
            f'https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={race_id}&type=b1',
            f'https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={race_id}&type=b1&action=update',
            f'https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={race_id}&houken=1',
            f'https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={race_id}',
        ]
        ref_headers = {
            **HEADERS,
            'Referer': f'https://race.netkeiba.com/odds/index.html?type=b1&race_id={race_id}',
            'X-Requested-With': 'XMLHttpRequest',
        }
        print(f'\n=== Testing api_get_jra_odds.html endpoints ===')
        for url in api_candidates:
            try:
                async with sess.get(url, headers=ref_headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    content = await r.read()
                    try:
                        text = content.decode('euc-jp', errors='replace')
                    except Exception:
                        text = content.decode('utf-8', errors='replace')
                    has_decimal = bool(re.search(r'\b\d+\.\d\b', text))
                    print(f'[{r.status}] {url}')
                    print(f'  len={len(text)}, has_decimal={has_decimal}, ct={r.content_type}')
                    if len(text) < 3000:
                        print(f'  content: {text[:800]}')
                    elif has_decimal:
                        nums = re.findall(r'\d+\.\d', text)
                        print(f'  decimal numbers (first 10): {nums[:10]}')
            except Exception as e:
                print(f'  ERROR: {e}')


asyncio.run(investigate())
