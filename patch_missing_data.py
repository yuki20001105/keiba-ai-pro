"""
patch_missing_data.py
======================
既存 keiba_ultimate.db の不足フィールドだけを差分で補完するスクリプト。

処理内容:
  Phase 0: race_results_ultimate → horse_idが空の行はレースページを再スクレイプし、
                               horse_id/horse_url/corner_positions/horse_weight/
                               last_3f/trainer_id/jockey_id/margin 等を補完
  Phase 1: races_ultimate  → 欠損レース情報（race_name/kai/day/weather/field_condition/
                               race_class/course_direction/lap_cumulative）を再スクレイプ
  Phase 2: race_results_ultimate → 欠損 sire/dam/damsire を /horse/ped/ から取得
  Phase 3: race_results_ultimate → 欠損 prev_race_* を /horse/result/ から取得

実行方法:
  python patch_missing_data.py                     # 全フェーズ実行 (0,1,2,3)
  python patch_missing_data.py --phase 0           # horse_id補完のみ
  python patch_missing_data.py --phase 1           # レースメタのみ
  python patch_missing_data.py --phase 2           # 血統のみ
  python patch_missing_data.py --phase 3           # 前走成绩のみ
  python patch_missing_data.py --phase 0,1,2,3     # 複数指定
  python patch_missing_data.py --dry-run           # DB更新せず件数確認のみ
  python patch_missing_data.py --limit 10          # 先頭10件だけ処理（テスト用）
"""

import asyncio
import json
import re
import sqlite3
import argparse
import time
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup

DB_PATH = Path(__file__).parent / 'keiba' / 'data' / 'keiba_ultimate.db'
SLEEP_INTERVAL = 0.7   # アクセス間隔（秒）
MAX_CONCURRENT = 3     # 同時接続数

# ── ヘルパー ──────────────────────────────────────────────

def load_races_needing_patch():
    """race_name が空、または kai/day/weather が未設定のレース一覧を返す"""
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT race_id, data FROM races_ultimate")
    rows = cur.fetchall()
    conn.close()
    needs = []
    for race_id, data_str in rows:
        d = json.loads(data_str)
        missing = (
            not d.get('race_name') or
            d.get('kai') is None or
            d.get('day') is None or
            not d.get('weather') or
            not d.get('field_condition') or
            not d.get('course_direction')
        )
        if missing:
            needs.append(race_id)
    return needs


def load_horses_needing_bloodline():
    """sire が空のユニーク horse_id 一覧を返す（race_results_ultimate から）"""
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT race_id, data FROM race_results_ultimate")
    rows = cur.fetchall()
    conn.close()
    horse_ids = {}  # horse_id → 最初に見つかった horse_url
    for race_id, data_str in rows:
        d = json.loads(data_str)
        hid = d.get('horse_id', '')
        if not hid:
            continue
        if not d.get('sire'):  # sire が空
            if hid not in horse_ids:
                horse_ids[hid] = d.get('horse_url', '')
    return horse_ids  # {horse_id: horse_url}


def load_horses_needing_prev_race():
    """prev_race_date が空の行を horse_id でグループ化して返す
    Returns: {horse_id: [(rowid, race_id, horse_url), ...]} """
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT rowid, race_id, data FROM race_results_ultimate")
    rows = cur.fetchall()
    conn.close()
    from collections import defaultdict
    horse_map = defaultdict(list)
    for rowid, race_id, data_str in rows:
        d = json.loads(data_str)
        hid = d.get('horse_id', '')
        if not hid:
            continue
        if not d.get('prev_race_date'):
            horse_map[hid].append((rowid, race_id, d.get('horse_url', '')))
    return dict(horse_map)


# ── Phase 0: horse_id補完（レースページ再スクレイプで馬名突き合わせ） ──────────────

def load_races_needing_horse_id():
    """少なくとも1行以上 horse_id が空のレースID一覧を返す"""
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT race_id FROM race_results_ultimate
        WHERE json_extract(data, '$.horse_id') IS NULL
           OR json_extract(data, '$.horse_id') = ''
    """)
    race_ids = [r[0] for r in cur.fetchall()]
    conn.close()
    return race_ids


async def scrape_race_horse_map(session: aiohttp.ClientSession, race_id: str) -> dict:
    """{horse_name: {horse_id, horse_url, corner_positions, ...}} を返す"""
    url = f"https://db.netkeiba.com/race/{race_id}/"
    await asyncio.sleep(SLEEP_INTERVAL)
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                return {}
            html = (await resp.read()).decode('euc-jp', errors='ignore')
    except Exception as e:
        print(f"  [phase0] 取得失敗 {race_id}: {e}")
        return {}

    soup = BeautifulSoup(html, 'html.parser')
    table = soup.find('table', class_='race_table_01')
    if not table:
        return {}

    all_rows = table.find_all('tr')
    if not all_rows:
        return {}

    header_cells = all_rows[0].find_all(['th', 'td'])
    header_texts = [c.get_text(strip=True) for c in header_cells]

    def col_idx(names, default=-1):
        for name in names:
            for i, h in enumerate(header_texts):
                if name in h:
                    return i
        return default

    IDX_FINISH    = col_idx(['着順'], 0)
    IDX_BRACKET   = col_idx(['枠番'], 1)
    IDX_HORSE_NUM = col_idx(['馬番'], 2)
    IDX_HORSE     = col_idx(['馬名'], 3)
    IDX_SEX_AGE   = col_idx(['性齢'], 4)
    IDX_JW        = col_idx(['斤量'], 5)
    IDX_JOCKEY    = col_idx(['騎手'], 6)
    IDX_TIME      = col_idx(['タイム'], 7)
    IDX_MARGIN    = col_idx(['着差'], 8)
    IDX_CORNER    = col_idx(['通過', 'コーナー'], 10)
    IDX_LAST3F    = col_idx(['上り'], 11)
    IDX_ODDS      = col_idx(['単勝'], 9)
    IDX_POP       = col_idx(['人気'], 10)
    IDX_WEIGHT    = col_idx(['馬体重'], 13)
    IDX_TRAINER   = col_idx(['調教師'], 14)
    IDX_PRIZE     = col_idx(['賞金'], 15)

    has_time_index = any('タイム指数' in h or 'ﾀｲﾑ指数' in h for h in header_texts)
    if has_time_index:
        IDX_WEIGHT  = col_idx(['馬体重'], 14)
        IDX_TRAINER = col_idx(['調教師'], 18)
        IDX_PRIZE   = col_idx(['賞金'], 20)

    horse_map = {}
    for row in all_rows[1:]:
        cols = row.find_all('td')
        if len(cols) < 8:
            continue
        try:
            def txt(i):
                return cols[i].get_text(strip=True) if 0 <= i < len(cols) else ''

            def link_href(i):
                a = cols[i].find('a') if 0 <= i < len(cols) else None
                href = a['href'] if a and 'href' in a.attrs else ''
                if href and not href.startswith('http'):
                    href = 'https://db.netkeiba.com' + href
                return href

            def link_text(i):
                a = cols[i].find('a') if 0 <= i < len(cols) else None
                return a.get_text(strip=True) if a else txt(i)

            horse_name = link_text(IDX_HORSE)
            if not horse_name:
                continue

            horse_url  = link_href(IDX_HORSE)
            hid_m = re.search(r'/horse/(\d+)', horse_url)
            horse_id = hid_m.group(1) if hid_m else ''

            jockey_url  = link_href(IDX_JOCKEY)
            jid_m = re.search(r'/jockey/(?:result/recent/)?(\d+)', jockey_url)

            trainer_url = link_href(IDX_TRAINER) if IDX_TRAINER >= 0 else ''
            tid_m = re.search(r'/trainer/(?:result/recent/)?(\d+)', trainer_url)

            corner_str = txt(IDX_CORNER) if IDX_CORNER >= 0 else ''
            cp_list = [int(x) for x in corner_str.split('-') if x.strip().isdigit()]
            n_cp = len(cp_list)

            weight_text = txt(IDX_WEIGHT) if IDX_WEIGHT >= 0 else ''
            weight_kg = weight_change = None
            wm = re.match(r'(\d+)\(([+-]?\d+)\)', weight_text)
            if wm:
                weight_kg     = int(wm.group(1))
                weight_change = int(wm.group(2))

            finish_pos = txt(IDX_FINISH)
            try:
                finish_position = int(finish_pos)
            except ValueError:
                finish_position = finish_pos

            prize_t = txt(IDX_PRIZE) if IDX_PRIZE >= 0 else ''
            try:
                prize_money = float(prize_t.replace(',', '')) * 10000 if prize_t else None
            except ValueError:
                prize_money = None

            last_3f_str = txt(IDX_LAST3F) if IDX_LAST3F >= 0 else ''

            horse_map[horse_name] = {
                'horse_id':              horse_id,
                'horse_url':             horse_url,
                'jockey_id':             jid_m.group(1) if jid_m else '',
                'jockey_url':            jockey_url,
                'trainer_id':            tid_m.group(1) if tid_m else '',
                'trainer_url':           trainer_url,
                'trainer_name':          link_text(IDX_TRAINER) if IDX_TRAINER >= 0 else '',
                'finish_position':       finish_position,
                'margin':                txt(IDX_MARGIN),
                'corner_positions':      corner_str,
                'corner_positions_list': cp_list,
                'corner_1': cp_list[0] if n_cp >= 1 else None,
                'corner_2': cp_list[1] if n_cp >= 2 else None,
                'corner_3': cp_list[2] if n_cp >= 3 else None,
                'corner_4': cp_list[3] if n_cp >= 4 else None,
                'last_3f':               last_3f_str,
                'weight':                weight_text,
                'weight_kg':             weight_kg,
                'weight_change':         weight_change,
                'prize_money':           prize_money,
                'odds':    (lambda s: float(s) if s else None)(txt(IDX_ODDS)),
                'popularity': (lambda s: int(s) if s.isdigit() else None)(txt(IDX_POP)),
            }
        except Exception:
            continue

    # last_3f_rank を再計算
    keys = list(horse_map.keys())
    vals = []
    for name in keys:
        try:
            vals.append(float(horse_map[name]['last_3f']))
        except (ValueError, TypeError):
            vals.append(float('inf'))
    sorted_idx = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0] * len(keys)
    for rank, idx in enumerate(sorted_idx):
        if vals[idx] != float('inf'):
            ranks[idx] = rank + 1
    for i, name in enumerate(keys):
        horse_map[name]['last_3f_rank'] = ranks[i] if ranks[i] > 0 else None

    return horse_map


def update_race_horses_by_name(race_id: str, horse_map: dict) -> int:
    """馬名で突き合わせて race_results_ultimate の欠損フィールドを更新。
    既に horse_id がある行はスキップ。"""
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT rowid, data FROM race_results_ultimate WHERE race_id = ?", (race_id,))
    rows = cur.fetchall()
    updated = 0
    for rowid, data_str in rows:
        d = json.loads(data_str)
        if d.get('horse_id'):   # 既に horse_id あり → スキップ
            continue
        name = d.get('horse_name', '')
        if name not in horse_map:
            continue
        patch = horse_map[name]
        for k, v in patch.items():
            if v is not None and v != '' and not d.get(k):
                d[k] = v
        cur.execute("UPDATE race_results_ultimate SET data = ? WHERE rowid = ?",
                    (json.dumps(d, ensure_ascii=False), rowid))
        updated += 1
    conn.commit()
    conn.close()
    return updated


async def run_phase0(dry_run: bool, limit: int):
    """レースページ再スクレイプで horse_id 空の行を補完"""
    race_ids = load_races_needing_horse_id()
    print(f"\n[LIST] Phase 0: horse_id補完対象 {len(race_ids)} レース")
    if limit:
        race_ids = race_ids[:limit]
        print(f"   → --limit {limit} 適用: {len(race_ids)} 件に絞る")
    if not race_ids:
        print("   [OK] 全レース充填済み - スキップ")
        return

    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT, limit_per_host=2)
    timeout = aiohttp.ClientTimeout(total=30)
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    total_updated_rows = 0
    total_races_done = 0
    async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
        for i, race_id in enumerate(race_ids):
            horse_map = await scrape_race_horse_map(session, race_id)
            if not horse_map:
                if (i + 1) % 20 == 0:
                    print(f"  [{i+1}/{len(race_ids)}] {race_id}: スキップ")
                continue
            if not dry_run:
                n = update_race_horses_by_name(race_id, horse_map)
            else:
                n = f"(dry-run: {len(horse_map)}馬取得)"
            total_updated_rows += (n if isinstance(n, int) else 0)
            total_races_done += 1
            if (i + 1) % 10 == 0 or i < 3:
                sample_hid = next((v['horse_id'] for v in horse_map.values() if v.get('horse_id')), '?')
                print(f"  [{i+1}/{len(race_ids)}] {race_id}: {len(horse_map)}馬取得 sample_id={sample_hid} ({n}行更新)")
    print(f"  [OK] Phase 0 完了: レース={total_races_done}, 更新行数={total_updated_rows}")


# ── Phase 1: レースメタ更新 ────────────────────────────────

VENUE_MAP = {
    '01': '札幌', '02': '函館', '03': '福島', '04': '新潟',
    '05': '東京', '06': '中山', '07': '中京', '08': '京都',
    '09': '阪神', '10': '小倉', '55': '海外',
}


async def patch_race_meta(session: aiohttp.ClientSession, race_id: str) -> dict:
    """race_idのレースページを再スクレイプし、欠損メタフィールドを返す"""
    url = f"https://db.netkeiba.com/race/{race_id}/"
    await asyncio.sleep(SLEEP_INTERVAL)
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                return {}
            html = (await resp.read()).decode('euc-jp', errors='ignore')
    except Exception as e:
        print(f"  [race] 取得失敗 {race_id}: {e}")
        return {}

    soup = BeautifulSoup(html, 'html.parser')

    # race_name
    race_name = ''
    for h1 in soup.find_all('h1'):
        txt = h1.get_text(strip=True)
        if txt:
            race_name = txt
            break

    # info_text
    mainrace_div = soup.find('div', class_='mainrace_data')
    info_text = mainrace_div.get_text(' ') if mainrace_div else ''
    if not info_text:
        for cls in ['p', 'div']:
            s = soup.find(cls, class_='smalltxt')
            if s:
                info_text = s.get_text(' ')
                break
    if not info_text:
        info_text = html[:3000]

    # 距離・トラック
    dist_m = re.search(r'(芝|ダ)[右左直外内障]?\s*(\d+)m', info_text)
    track_type = ('芝' if dist_m.group(1) == '芝' else 'ダート') if dist_m else ''
    distance = int(dist_m.group(2)) if dist_m else None

    # 天候
    weather_m = re.search(r'天候\s*[:/：]\s*([^\s/]+)', info_text)
    weather = weather_m.group(1).strip() if weather_m else ''

    # 馬場状態
    cond_m = (re.search(r'(?:芝|ダート)\s*[:/：]\s*([^\s/\n]+)', info_text) or
              re.search(r'馬場\s*[:/：]\s*([^\s/\n]+)', info_text))
    field_condition = cond_m.group(1).strip() if cond_m else ''

    # smalltxt テキスト
    sp = soup.find('p', class_='smalltxt') or soup.find('div', class_='smalltxt')
    smalltxt_text = sp.get_text(' ') if sp else ''

    # 発走時刻
    pt_m = re.search(r'発走\s*[:/：]?\s*(\d{1,2}:\d{2})', info_text)
    post_time = pt_m.group(1) if pt_m else ''

    # レースクラス
    race_class = ''
    for src in [race_name, smalltxt_text, info_text]:
        if race_class:
            break
        for pat in [r'(G[1-3])', r'(新馬)', r'(未勝利)', r'([1-3]勝クラス)', r'(オープン)', r'(重賞)', r'(リステッド)']:
            cm = re.search(pat, src)
            if cm:
                race_class = cm.group(1)
                break

    # kai / day
    kai_src = smalltxt_text or info_text
    kai_m = re.search(r'(\d+)回', kai_src)
    kai = int(kai_m.group(1)) if kai_m else None
    day_m = re.search(r'(\d+)日目', kai_src)
    day = int(day_m.group(1)) if day_m else None

    # course_direction
    course_direction = ''
    dir_m = re.search(r'[芝ダ](右|左)(外)?', info_text)
    if dir_m:
        course_direction = dir_m.group(1) + (dir_m.group(2) or '')
    elif '直線' in info_text:
        course_direction = '直線'

    # ラップタイム
    lap_cumulative = {}
    lap_sectional = {}
    for tbl in soup.find_all('table'):
        rows = tbl.find_all('tr')
        if len(rows) < 2:
            continue
        hcells = rows[0].find_all(['th', 'td'])
        htexts = [c.get_text(strip=True).replace('\u3000', '').replace(' ', '') for c in hcells]
        dists = []
        for ht in htexts:
            dm = re.match(r'^(\d+)m?$', ht)
            if dm:
                d = int(dm.group(1))
                if 100 <= d <= 4000 and d % 200 == 0:
                    dists.append(d)
        if len(dists) >= 3:
            tcells = rows[1].find_all('td')
            for i, dist in enumerate(dists):
                if i < len(tcells):
                    try:
                        t = float(tcells[i].get_text(strip=True))
                        if 5.0 <= t <= 200.0:
                            lap_cumulative[dist] = t
                    except ValueError:
                        pass
            if lap_cumulative:
                sdists = sorted(lap_cumulative.keys())
                prev = 0.0
                for dist in sdists:
                    lap_sectional[dist] = round(lap_cumulative[dist] - prev, 1)
                    prev = lap_cumulative[dist]
                break

    patch = {}
    if race_name:
        patch['race_name'] = race_name
    if track_type:
        patch['track_type'] = track_type
    if distance:
        patch['distance'] = distance
    if weather:
        patch['weather'] = weather
    if field_condition:
        patch['field_condition'] = field_condition
    if post_time:
        patch['post_time'] = post_time
    if race_class:
        patch['race_class'] = race_class
    if kai is not None:
        patch['kai'] = kai
    if day is not None:
        patch['day'] = day
    if course_direction:
        patch['course_direction'] = course_direction
    if lap_cumulative:
        patch['lap_cumulative'] = lap_cumulative
        patch['lap_sectional'] = lap_sectional

    return patch


# ── Phase 2: 血統更新 ─────────────────────────────────────

async def patch_bloodline(session: aiohttp.ClientSession, horse_id: str) -> dict:
    """/horse/ped/{horse_id}/ から sire/dam/damsire を取得"""
    ped_url = f"https://db.netkeiba.com/horse/ped/{horse_id}/"
    await asyncio.sleep(SLEEP_INTERVAL)
    try:
        async with session.get(ped_url) as resp:
            if resp.status != 200:
                return {}
            html = (await resp.read()).decode('euc-jp', errors='ignore')
    except Exception as e:
        print(f"  [ped] 取得失敗 {horse_id}: {e}")
        return {}

    soup = BeautifulSoup(html, 'html.parser')
    result = {}

    # 現在のnetkeiba HTML: class=b_ml (父), class=b_fml (母)
    # tr[0]の1番目td = 父(sire), tr[half]の1番目td = 母(dam), 2番目td = 母の父(damsire)
    blood_table = soup.find('table', class_='blood_table')
    if blood_table:
        trs = blood_table.find_all('tr')
        half = len(trs) // 2  # 5世代=32行 → half=16
        # sire: tr[0]の最初のtd
        if trs:
            sire_tds = trs[0].find_all('td')
            if sire_tds:
                a = sire_tds[0].find('a')
                if a:
                    result['sire'] = a.get_text(strip=True)
        # dam / damsire: tr[half]のtd
        if half > 0 and len(trs) > half:
            dam_tds = trs[half].find_all('td')
            if dam_tds:
                a = dam_tds[0].find('a')
                if a:
                    result['dam'] = a.get_text(strip=True)
            if len(dam_tds) >= 2:
                a = dam_tds[1].find('a')
                if a:
                    result['damsire'] = a.get_text(strip=True)

    return result


# ── Phase 3: 前走成績更新 ─────────────────────────────────

async def scrape_horse_result_rows(session: aiohttp.ClientSession, horse_id: str) -> list:
    """horse/result/{horse_id}/ の全行をパースして返す
    Returns: [{date_key: 'YYYYMMDD', venue, finish, time, weight, distance, surface}, ...]
             新→旧の時系列順（最新が先頭）
    """
    result_url = f"https://db.netkeiba.com/horse/result/{horse_id}/"
    await asyncio.sleep(SLEEP_INTERVAL)
    try:
        async with session.get(result_url) as resp:
            if resp.status != 200:
                return []
            html = (await resp.read()).decode('euc-jp', errors='ignore')
    except Exception as e:
        print(f"  [result] 取得失敗 {horse_id}: {e}")
        return []

    soup = BeautifulSoup(html, 'html.parser')

    # 成績テーブルを探す
    race_hist_table = None
    for tbl in soup.find_all('table'):
        ths = [th.get_text(strip=True) for th in tbl.find_all('th')]
        if '日付' in ths and ('着順' in ths or '着' in ths):
            race_hist_table = tbl
            break
    if not race_hist_table:
        return []

    # ヘッダー取得
    header_rows = [r for r in race_hist_table.find_all('tr') if r.find('th')]
    if not header_rows:
        return []
    headers = [th.get_text(strip=True) for th in header_rows[0].find_all('th')]
    cidx = {h: i for i, h in enumerate(headers)}
    date_i   = cidx.get('日付', 0)
    venue_i  = cidx.get('開催', 1)
    finish_i = cidx.get('着順', cidx.get('着', -1))
    time_i   = cidx.get('タイム', -1)
    weight_i = cidx.get('馬体重', -1)
    course_i = -1
    for cname in ['距離', 'コース', '芝・距離']:
        if cname in cidx:
            course_i = cidx[cname]
            break
    if course_i == -1:
        course_i = next((cidx[h] for h in headers if 'コース' in h or '距離' in h), -1)

    all_rows = []
    for row in race_hist_table.find_all('tr'):
        if not row.find('td'):
            continue
        cols = row.find_all('td')
        try:
            # 日付を YYYYMMDD に変換 ("2024/06/01" → "20240601")
            date_raw = cols[date_i].get_text(strip=True) if date_i < len(cols) else ''
            date_key = date_raw.replace('/', '').replace('-', '').strip()[:8]
            if not re.match(r'^\d{8}$', date_key):
                continue  # 日付が取れない行はスキップ

            entry = {'date_key': date_key, 'date_str': date_raw}
            if venue_i < len(cols):
                entry['venue'] = cols[venue_i].get_text(strip=True)
            if finish_i != -1 and finish_i < len(cols):
                fin_t = cols[finish_i].get_text(strip=True)
                if re.match(r'^\d+$', fin_t):
                    entry['finish'] = int(fin_t)
            if time_i != -1 and time_i < len(cols):
                t_t = cols[time_i].get_text(strip=True)
                tm = re.match(r'(\d+):(\d+\.\d+)', t_t)
                if tm:
                    entry['time'] = float(tm.group(1)) * 60 + float(tm.group(2))
            if weight_i != -1 and weight_i < len(cols):
                w_t = cols[weight_i].get_text(strip=True)
                w_m = re.match(r'(\d+)', w_t)
                if w_m:
                    entry['weight'] = int(w_m.group(1))
            if course_i != -1 and course_i < len(cols):
                c_t = cols[course_i].get_text(strip=True)
                d_m = re.search(r'(\d{3,4})', c_t)
                if d_m:
                    entry['distance'] = int(d_m.group(1))
                if '芝' in c_t:
                    entry['surface'] = '芝'
                elif 'ダ' in c_t or 'ダート' in c_t:
                    entry['surface'] = 'ダート'
            all_rows.append(entry)
        except Exception:
            continue

    # 新→旧順（日付降順）に整列
    all_rows.sort(key=lambda x: x['date_key'], reverse=True)
    return all_rows


def make_prev_patch(all_rows: list, race_date_yyyymmdd: str) -> dict:
    """race_date_yyyymmdd より前の行だけを使って prev/prev2 を作成
    これにより「当該レース当日以降のデータ混入」を防止する"""
    # race_date_yyyymmdd より厳密に前の行だけ使う
    before = [r for r in all_rows if r.get('date_key', '') < race_date_yyyymmdd]
    patch = {}
    for i, entry in enumerate(before[:2]):
        pfx = 'prev' if i == 0 else 'prev2'
        patch[f'{pfx}_race_date']     = entry.get('date_str', '')
        if 'venue'    in entry: patch[f'{pfx}_race_venue']    = entry['venue']
        if 'finish'   in entry: patch[f'{pfx}_race_finish']   = entry['finish']
        if 'time'     in entry: patch[f'{pfx}_race_time']     = entry['time']
        if 'weight'   in entry: patch[f'{pfx}_race_weight']   = entry['weight']
        if 'distance' in entry: patch[f'{pfx}_race_distance'] = entry['distance']
        if 'surface'  in entry: patch[f'{pfx}_race_surface']  = entry['surface']
    return patch


def update_single_row(rowid: int, patch: dict):
    """指定 rowid の1行だけを更新"""
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT data FROM race_results_ultimate WHERE rowid = ?", (rowid,))
    row = cur.fetchone()
    if row:
        d = json.loads(row[0])
        d.update(patch)
        cur.execute("UPDATE race_results_ultimate SET data = ? WHERE rowid = ?",
                    (json.dumps(d, ensure_ascii=False), rowid))
        conn.commit()
    conn.close()


# ── DB更新 ───────────────────────────────────────────────

def update_race_in_db(race_id: str, patch: dict):
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT data FROM races_ultimate WHERE race_id = ?", (race_id,))
    row = cur.fetchone()
    if row:
        d = json.loads(row[0])
        d.update(patch)
        cur.execute("UPDATE races_ultimate SET data = ? WHERE race_id = ?",
                    (json.dumps(d, ensure_ascii=False), race_id))
        conn.commit()
    conn.close()


def update_horse_in_db(horse_id: str, patch: dict):
    """horse_id に対応する全行を更新"""
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT rowid, data FROM race_results_ultimate WHERE json_extract(data, '$.horse_id') = ?",
                (horse_id,))
    rows = cur.fetchall()
    for rowid, data_str in rows:
        d = json.loads(data_str)
        d.update(patch)
        cur.execute("UPDATE race_results_ultimate SET data = ? WHERE rowid = ?",
                    (json.dumps(d, ensure_ascii=False), rowid))
    conn.commit()
    conn.close()
    return len(rows)


# ── メイン実行 ────────────────────────────────────────────

async def run_phase1(dry_run: bool, limit: int):
    """レースメタの差分更新"""
    race_ids = load_races_needing_patch()
    print(f"\n[LIST] Phase 1: レースメタ更新対象 {len(race_ids)} レース")
    if limit:
        race_ids = race_ids[:limit]
        print(f"   → --limit {limit} 適用: {len(race_ids)} 件に絞る")
    if not race_ids:
        print("   [OK] 全レース充填済み - スキップ")
        return

    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT, limit_per_host=2)
    timeout = aiohttp.ClientTimeout(total=30)
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    updated = 0
    skipped = 0
    async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
        for i, race_id in enumerate(race_ids):
            patch = await patch_race_meta(session, race_id)
            if patch:
                if not dry_run:
                    update_race_in_db(race_id, patch)
                updated += 1
                fields = list(patch.keys())
                print(f"  [{i+1}/{len(race_ids)}] {race_id}: {fields}")
            else:
                skipped += 1
                if (i + 1) % 20 == 0:
                    print(f"  [{i+1}/{len(race_ids)}] {race_id}: スキップ (取得失敗 or 既に充填済)")
    print(f"  [OK] Phase 1 完了: 更新={updated}, スキップ={skipped}")


async def run_phase2(dry_run: bool, limit: int):
    """血統（sire/dam/damsire）の差分更新"""
    horse_ids = load_horses_needing_bloodline()
    print(f"\n[LIST] Phase 2: 血統更新対象 {len(horse_ids)} 頭")
    items = list(horse_ids.items())
    if limit:
        items = items[:limit]
        print(f"   → --limit {limit} 適用: {len(items)} 件に絞る")
    if not items:
        print("   [OK] 全頭充填済み - スキップ")
        return

    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT, limit_per_host=2)
    timeout = aiohttp.ClientTimeout(total=30)
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    updated = 0
    skipped = 0
    async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
        for i, (horse_id, _horse_url) in enumerate(items):
            patch = await patch_bloodline(session, horse_id)
            if patch:
                if not dry_run:
                    n = update_horse_in_db(horse_id, patch)
                else:
                    n = '(dry-run)'
                updated += 1
                if (i + 1) % 10 == 0 or i < 5:
                    print(f"  [{i+1}/{len(items)}] {horse_id}: sire={patch.get('sire')!r} ({n}行更新)")
            else:
                skipped += 1
    print(f"  [OK] Phase 2 完了: 更新={updated}頭, スキップ={skipped}頭")


async def run_phase3(dry_run: bool, limit: int):
    """前走成績（prev_race_*）の差分更新
    【修正】行ごとに race_id 日付でフィルタし、当該レース以前の成績だけを使う。
    同一馬の複数レースでも正しい前走情報がセットされる。
    """
    horse_map = load_horses_needing_prev_race()  # {horse_id: [(rowid, race_id, horse_url), ...]}
    total_rows = sum(len(v) for v in horse_map.values())
    print(f"\n[LIST] Phase 3: 前走成績更新対象 {len(horse_map)} 頭 / {total_rows} 行")
    items = list(horse_map.items())
    if limit:
        items = items[:limit]
        print(f"   → --limit {limit} 適用: {len(items)} 頭に絞る")
    if not items:
        print("   [OK] 全頭充填済み - スキップ")
        return

    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT, limit_per_host=2)
    timeout = aiohttp.ClientTimeout(total=30)
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    updated_horses = 0
    updated_rows = 0
    skipped = 0
    horse_num = 0
    async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
        for horse_id, entries in items:
            horse_num += 1
            # 馬の全成績ページを1回だけスクレイプ
            all_rows = await scrape_horse_result_rows(session, horse_id)
            if not all_rows:
                skipped += 1
                continue

            any_updated = False
            for rowid, race_id, _horse_url in entries:
                # このレース日より「前」の成績だけを prev/prev2 に使う
                race_date = race_id[:8]  # YYYYMMDD
                patch = make_prev_patch(all_rows, race_date)
                if not patch:
                    continue
                if not dry_run:
                    update_single_row(rowid, patch)
                    updated_rows += 1
                else:
                    updated_rows += 1  # dry-run でもカウント
                any_updated = True

            if any_updated:
                updated_horses += 1
                if horse_num % 10 == 0 or horse_num <= 5:
                    sample_entry = entries[0]
                    sample_race_id = sample_entry[1]
                    sample_patch = make_prev_patch(all_rows, sample_race_id[:8])
                    marker = '(dry-run)' if dry_run else ''
                    print(f"  [{horse_num}/{len(items)}] {horse_id}: "
                          f"prev={sample_patch.get('prev_race_date')!r} "
                          f"finish={sample_patch.get('prev_race_finish')} "
                          f"{len(entries)}行更新 {marker}")
            else:
                skipped += 1

    print(f"  [OK] Phase 3 完了: 更新={updated_horses}頭 / {updated_rows}行, スキップ={skipped}頭")


async def main():
    parser = argparse.ArgumentParser(description='keiba_ultimate.db の欠損フィールドを差分補完')
    parser.add_argument('--phase', default='0,1,2,3', help='実行するフェーズ (例: 0,1,2,3 または 1 または 2,3)')
    parser.add_argument('--dry-run', action='store_true', help='DB更新せず件数確認のみ')
    parser.add_argument('--limit', type=int, default=0, help='処理件数上限 (0=無制限、テスト用)')
    args = parser.parse_args()

    phases = [int(p.strip()) for p in args.phase.split(',') if p.strip().isdigit()]
    dry = args.dry_run
    lim = args.limit

    print(f"=== keiba_ultimate.db 差分パッチ ===")
    print(f"  DB: {DB_PATH}")
    print(f"  フェーズ: {phases}")
    print(f"  dry-run: {dry}")
    if lim:
        print(f"  limit: {lim}件")
    print()

    t0 = time.time()

    if 0 in phases:
        await run_phase0(dry_run=dry, limit=lim)
    if 1 in phases:
        await run_phase1(dry_run=dry, limit=lim)
    if 2 in phases:
        await run_phase2(dry_run=dry, limit=lim)
    if 3 in phases:
        await run_phase3(dry_run=dry, limit=lim)

    elapsed = time.time() - t0
    print(f"\n[DONE] 全処理完了: {elapsed:.1f}秒")
    print()
    print("次のステップ:")
    print("  python check_null_rates3.py  # 充填率を再確認")
    print("  python check_features_detail.py  # 特徴量パイプライン確認")


if __name__ == '__main__':
    asyncio.run(main())
