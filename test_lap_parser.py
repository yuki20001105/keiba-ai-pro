"""新ラップタイムパーサーの動作テスト"""
import sys, re, asyncio
sys.path.insert(0, 'python-api')
import aiohttp
from bs4 import BeautifulSoup

async def test_lap_parser(race_id: str, distance: int):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36',
    }
    url = f'https://db.netkeiba.com/race/{race_id}/'
    async with aiohttp.ClientSession(headers=headers) as sess:
        async with sess.get(url) as r:
            html = (await r.read()).decode('euc-jp', errors='replace')
    
    soup = BeautifulSoup(html, 'html.parser')
    lap_cumulative = {}
    lap_sectional = {}
    
    _lap_cells = soup.find_all("td", class_="race_lap_cell")
    print(f"race_lap_cell の数: {len(_lap_cells)}")
    for i, c in enumerate(_lap_cells):
        print(f"  [{i}]: {c.get_text(strip=True)[:80]}")
    
    if _lap_cells and distance:
        def _parse_lap_cell(cell_text):
            _t = re.sub(r"\s*\([^)]*\)\s*", "", cell_text).strip()
            _vals = []
            for _tok in re.split(r"[\s\-－]+", _t):
                _tok = _tok.strip()
                if re.match(r"^\d+\.?\d*$", _tok):
                    try: _vals.append(float(_tok))
                    except ValueError: pass
            return _vals

        def _vals_to_dist_dict(vals, dist):
            n = len(vals)
            if n == 0: return {}
            step = 200
            first = dist - (n - 1) * step
            if first <= 0: first = step
            result = {}
            for i, v in enumerate(vals):
                d = first + i * step
                if 100 <= d <= 4000:
                    result[d] = v
            return result

        _sect_vals = _parse_lap_cell(_lap_cells[0].get_text(strip=True))
        print(f"\n取得したsectional値: {_sect_vals}")
        if _sect_vals:
            lap_sectional = _vals_to_dist_dict(_sect_vals, distance)
            print(f"lap_sectional: {lap_sectional}")

        if len(_lap_cells) >= 2:
            _cum_vals = _parse_lap_cell(_lap_cells[1].get_text(strip=True))
            print(f"取得したcumulative値: {_cum_vals}")
            if _cum_vals:
                lap_cumulative = _vals_to_dist_dict(_cum_vals, distance)
                print(f"lap_cumulative: {lap_cumulative}")

asyncio.run(test_lap_parser('202606010701', 1200))
