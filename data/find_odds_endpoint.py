"""netkeiba オッズページのXHRエンドポイントを調査するスクリプト"""
import asyncio
import re
import sys
sys.path.insert(0, 'python-api')

import aiohttp

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'ja,en;q=0.9',
    'Referer': 'https://race.netkeiba.com/',
}


async def find_xhr_endpoint():
    race_id = '202605051201'  # 今日の東京1R
    url = f'https://race.netkeiba.com/odds/index.html?type=b1&race_id={race_id}'

    async with aiohttp.ClientSession(headers=HEADERS) as sess:
        async with sess.get(url) as r:
            print(f'status: {r.status}, content-type: {r.content_type}')
            content = await r.read()
            html = content.decode('euc-jp', errors='replace')

    print(f'HTML length: {len(html)}')

    # JS内のURL・ajax系を探す
    patterns = [
        r'load_odds\w*',
        r'OddsExpress\w*',
        r'GetOdds\w*',
        r'/api/odds[^\s\'"]+',
    ]
    for pat in patterns:
        found = re.findall(pat, html, re.IGNORECASE)
        if found:
            print(f'Pattern [{pat}]: {list(set(found))[:5]}')

    # script src タグを全部列挙
    scripts = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
    print(f'\nExternal scripts ({len(scripts)}):')
    for s in scripts:
        print(f'  {s}')

    # inline script の最初の3つ
    inline_scripts = re.findall(r'<script(?![^>]*src)[^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE)
    print(f'\nInline scripts: {len(inline_scripts)}')
    for i, s in enumerate(inline_scripts[:5]):
        stripped = s.strip()
        if stripped:
            print(f'--- inline[{i}] ({len(stripped)} chars, first 800) ---')
            print(stripped[:800])

    # 全HTMLから "load_odds" や netkeiba XHR パターンを探す
    xhr_candidates = re.findall(r'https?://[^\s\'"<>]+odds[^\s\'"<>]+', html, re.IGNORECASE)
    if xhr_candidates:
        print('\nXHR candidates found in HTML:')
        for u in set(xhr_candidates):
            print(f'  {u}')

    # コメントなし HTML から URL 全部
    all_urls = re.findall(r'["\']((https?://race\.netkeiba\.com/[^"\']+))["\']', html)
    if all_urls:
        print('\nrace.netkeiba.com URLs in HTML:')
        for _, u in set(all_urls):
            print(f'  {u}')


async def try_direct_odds(race_id: str):
    """既知の候補URLを直接叩いてみる"""
    candidates = [
        # パターン1: load_odds_tanpuku01.html
        f'https://race.netkeiba.com/odds/load_odds_tanpuku01.html?race_id={race_id}&type=b1',
        # パターン2: express エンドポイント
        f'https://race.netkeiba.com/api/odds.json?type=b1&race_id={race_id}',
        # パターン3: odds_express
        f'https://race.netkeiba.com/odds/odds_express.html?race_id={race_id}&type=1',
        # パターン4: old endpoint
        f'https://race.netkeiba.com/odds/index.html?type=b1&race_id={race_id}&ajax=1',
    ]
    headers_with_ref = {
        **HEADERS,
        'Referer': f'https://race.netkeiba.com/odds/index.html?type=b1&race_id={race_id}',
        'X-Requested-With': 'XMLHttpRequest',
    }
    async with aiohttp.ClientSession(headers=headers_with_ref) as sess:
        for url in candidates:
            try:
                async with sess.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    content = await r.read()
                    try:
                        text = content.decode('euc-jp', errors='replace')
                    except Exception:
                        text = content.decode('utf-8', errors='replace')
                    # オッズ値らしきものが含まれるか確認
                    has_odds = bool(re.search(r'\d+\.\d', text))
                    has_odds_html = bool(re.search(r'odds[-_]', text, re.IGNORECASE))
                    print(f'\n[{r.status}] {url}')
                    print(f'  len={len(text)}, has_decimal={has_odds}, has_odds_pattern={has_odds_html}')
                    if has_odds and len(text) < 50000:
                        # オッズ値っぽい行を表示
                        lines = [l for l in text.split('\n') if re.search(r'\d+\.\d', l)]
                        print(f'  Sample odds lines: {lines[:3]}')
                    elif len(text) < 2000:
                        print(f'  Full content: {text[:500]}')
            except Exception as e:
                print(f'  ERROR {url}: {e}')


if __name__ == '__main__':
    print('=== Phase 1: Find XHR endpoint from page HTML ===')
    asyncio.run(find_xhr_endpoint())
    print('\n=== Phase 2: Try direct candidate URLs ===')
    asyncio.run(try_direct_odds('202605051201'))
