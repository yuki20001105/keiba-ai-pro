"""netkeiba SP ページからオッズを抽出する方法を調査"""
import asyncio
import re
import sys
sys.path.insert(0, 'python-api')
import aiohttp

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15',
    'Accept-Language': 'ja,en;q=0.9',
}

HEADERS_PC = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'ja,en;q=0.9',
}


async def analyze_sp_page():
    race_id = '202605310801'  # 京都1R
    url = f'https://race.sp.netkeiba.com/?pid=odds_view&type=b1&race_id={race_id}'
    ref_headers = {
        **HEADERS,
        'Referer': f'https://race.sp.netkeiba.com/?pid=race_list&race_id={race_id}',
    }

    async with aiohttp.ClientSession(headers=ref_headers) as sess:
        async with sess.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
            content = await r.read()
            text = content.decode('utf-8', errors='replace')

    print(f'SP page len: {len(text)}')

    # 馬番とオッズのペアを探す
    # 単勝オッズはたいてい <td>馬番</td><td>馬名</td><td>オッズ</td> 形式か
    # または特定のクラス名が付く

    # class 属性に "odds" を含む要素
    odds_classes = re.findall(r'class="[^"]*[Oo]dds[^"]*"[^>]*>([^<]+)<', text)
    print(f'\nOdds class elements: {odds_classes[:20]}')

    # id 属性に "odds" を含む要素
    odds_ids = re.findall(r'id="[^"]*[Oo]dds[^"]*"[^>]*>([^<]+)<', text)
    print(f'\nOdds id elements: {odds_ids[:20]}')

    # 馬番 + オッズっぽいパターンを探す (数字.数字)
    # SP版は JS を使わず静的 HTML でオッズを提供している可能性
    # テーブル行を解析
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', text, re.DOTALL)
    print(f'\nTable rows: {len(rows)}')
    for i, row in enumerate(rows[:30]):
        cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.DOTALL)
        cell_texts = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
        cell_texts = [t for t in cell_texts if t]
        # オッズっぽい小数点数値を含む行
        if any(re.search(r'^\d+\.\d$', t) for t in cell_texts):
            print(f'  Row {i}: {cell_texts[:10]}')

    # HTMLの一部を表示して構造を確認
    # "単勝" の周辺を表示
    idx = text.find('単勝')
    if idx >= 0:
        print(f'\n単勝 context (pos {idx}):')
        print(text[max(0, idx-200):idx+500])

    # 馬番(1〜18)とオッズのテーブル
    # よくある形: <span class="RaceHorseName">...</span> + <td>X.X</td>
    print('\n=== Saving HTML for manual inspection ===')
    with open('data/sp_odds_page.html', 'w', encoding='utf-8') as f:
        f.write(text)
    print('Saved to data/sp_odds_page.html')

    # 最もシンプルなパターン: [horse_number, horse_name, odds]
    # UmaBan + Odds パターン
    pattern1 = re.findall(
        r'<td[^>]*class="[^"]*Umaban[^"]*"[^>]*>(\d+)</td>.*?<td[^>]*>(\d+\.\d)</td>',
        text, re.DOTALL
    )
    print(f'\nPattern1 (Umaban+odds): {pattern1[:10]}')

    # odds_list のような特定の class
    odds_list = re.findall(r'<li[^>]*>.*?(\d+)\s*番.*?(\d+\.\d).*?</li>', text, re.DOTALL)
    print(f'\nList pattern: {odds_list[:10]}')


async def test_multiple_today_races():
    """今日の全レースでSP APIが使えるか確認（速度テスト）"""
    today = '20260531'
    race_ids = [f'{today}0{8:02d}{r:02d}' for r in range(1, 13)]  # 京都1-12R
    race_ids += [f'{today}0{5:02d}{r:02d}' for r in range(1, 7)]  # 東京1-6R（あれば）

    ref_headers = {
        **HEADERS_PC,
        'Referer': 'https://race.sp.netkeiba.com/',
    }

    import time
    async with aiohttp.ClientSession(headers=ref_headers) as sess:
        start = time.time()
        # 全レース並列取得テスト
        tasks = []
        for race_id in race_ids[:6]:  # まず6レースで試す
            url = f'https://race.sp.netkeiba.com/?pid=odds_view&type=b1&race_id={race_id}'
            tasks.append(sess.get(url, timeout=aiohttp.ClientTimeout(total=10)))

        responses = await asyncio.gather(*tasks, return_exceptions=True)
        elapsed = time.time() - start
        print(f'\n6レース並列取得: {elapsed:.2f}s')

        for race_id, resp in zip(race_ids[:6], responses):
            if isinstance(resp, Exception):
                print(f'  {race_id}: ERROR {resp}')
                continue
            async with resp as r:
                content = await r.read()
                text = content.decode('utf-8', errors='replace')
                # オッズ値を数える（小数点数値）
                nums = re.findall(r'\b\d{1,4}\.\d\b', text)
                # 1.0-999.9 の範囲で単勝オッズらしいもの
                odds_like = [float(n) for n in nums if 1.0 <= float(n) <= 999.0]
                print(f'  {race_id}: status={r.status} len={len(text)} odds-like={odds_like[:8]}')


async def main():
    print('=== 1. Analyze SP page structure ===')
    await analyze_sp_page()
    print('\n=== 2. Parallel speed test ===')
    await test_multiple_today_races()


asyncio.run(main())
