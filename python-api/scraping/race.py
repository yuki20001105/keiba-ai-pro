"""
レーススクレイピング: netkeiba.com から単一レースの完全データを取得する。
"""

import asyncio
import gc
import re
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup

from scraping.constants import HTML_STRAINER, VENUE_MAP
from scraping.horse import scrape_horse_detail

try:
    from app_config import SUPABASE_ENABLED, logger  # type: ignore
except ImportError:
    import logging
    SUPABASE_ENABLED = False
    logger = logging.getLogger(__name__)

try:
    from supabase_client import get_pedigree_cache_batch  # type: ignore
    _SUPABASE_RACE_OK = True
except ImportError:
    _SUPABASE_RACE_OK = False

    def get_pedigree_cache_batch(ids):  # type: ignore
        return {}


async def scrape_race_full(
    session, race_id: str, date_hint: str = "", quick_mode: bool = False
) -> Optional[dict]:
    """
    単一レースの完全データを netkeiba.com から取得。
    race_results_ultimate / races_ultimate 形式で返す。
    date_hint: YYYYMMDD 形式の日付（リストページから判明した場合に渡す）
    quick_mode: True=毛色SPフォールバックをスキップして高速化（バックフィルAPIで後処理）
    """
    _quick_mode = quick_mode

    url = f"https://db.netkeiba.com/race/{race_id}/"
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                logger.warning(f"HTTP {resp.status}: {url}")
                return None
            content = await resp.read()
            html = content.decode("euc-jp", errors="ignore")
    except Exception as e:
        logger.error(f"取得エラー {race_id}: {e}")
        return None

    soup = BeautifulSoup(html, "lxml", parse_only=HTML_STRAINER)
    _smalltxt_p = soup.find("p", class_="smalltxt")

    # ---- レース基本情報 ----
    race_name = ""
    for h1 in soup.find_all("h1"):
        txt = h1.get_text(strip=True)
        if txt:
            race_name = txt
            break

    mainrace_div = soup.find("div", class_="mainrace_data")
    if mainrace_div:
        info_text = mainrace_div.get_text(" ")
    else:
        smalltxt = _smalltxt_p or soup.find("div", class_="smalltxt")
        info_text = smalltxt.get_text(" ") if smalltxt else html[:3000]

    # ---- 距離・芝/ダート ----
    dist_m = re.search(r"(芝|ダ)[右左直外内障]{0,3}\s*(\d+)[mｍ]", info_text)
    if dist_m:
        track_type = "芝" if dist_m.group(1) == "芝" else "ダート"
        distance = int(dist_m.group(2))
    else:
        _vc_tmp = race_id[4:6]
        if _vc_tmp == "65":
            track_type = "ばんえい"
            banei_m = re.search(r"ばんえい\s*(\d+)", info_text) or re.search(r"(\d{3})\s*m", info_text)
            distance = int(banei_m.group(1)) if banei_m else 200
        else:
            track_type = ""
            distance = 0

    # ---- 天候 ----
    weather_m = re.search(r"天候\s*[:/：]\s*([^\s/]+)", info_text)
    weather = weather_m.group(1).strip() if weather_m else ""

    # ---- 馬場状態 ----
    cond_m = re.search(r"(?:芝|ダート)\s*[:/：]\s*([^\s/]+)", info_text) or re.search(
        r"馬場\s*[:/：]\s*([^\s/]+)", info_text
    )
    field_condition = cond_m.group(1).strip() if cond_m else ""

    venue_code = race_id[4:6]
    venue = VENUE_MAP.get(venue_code, venue_code)

    # ---- 日付 ----
    date_str = date_hint
    if not date_str:
        smalltxt_p = _smalltxt_p
        if smalltxt_p:
            stxt = smalltxt_p.get_text()
            sdm = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", stxt)
            if sdm:
                date_str = f"{sdm.group(1)}{int(sdm.group(2)):02d}{int(sdm.group(3)):02d}"
    if not date_str:
        body_date_m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", html)
        if body_date_m:
            date_str = (
                f"{body_date_m.group(1)}{int(body_date_m.group(2)):02d}{int(body_date_m.group(3)):02d}"
            )
    if not date_str:
        candidate = race_id[:8]
        try:
            datetime.strptime(candidate, "%Y%m%d")
            date_str = candidate
        except ValueError:
            date_str = ""
    logger.debug(f"race_id={race_id} 日付={date_str}")

    # ---- 発走時刻 ----
    post_time = ""
    pt_m = re.search(r"発走\s*[:/：]?\s*(\d{1,2}:\d{2})", info_text)
    if pt_m:
        post_time = pt_m.group(1)

    # ---- レースクラス ----
    race_class = ""
    smalltxt_text = ""
    if _smalltxt_p:
        smalltxt_text = _smalltxt_p.get_text(" ")
    for src in [race_name, smalltxt_text, info_text]:
        if race_class:
            break
        for pat in [
            r"(G[1-3])",
            r"(新馬)",
            r"(未勝利)",
            r"([1-3]勝クラス)",
            r"(オープン|OP)",
            r"(重賞)",
            r"\b([ABC][1-3])\b",
            r"[（(]([ABC][1-3]?)[）)]",
        ]:
            cm = re.search(pat, src)
            if cm:
                race_class = cm.group(1)
                break
    if race_class == "OP":
        race_class = "オープン"

    # ---- 開催回・日目 ----
    kai = None
    day = None
    kai_src = smalltxt_text or info_text
    kai_m = re.search(r"(\d+)回", kai_src)
    if kai_m:
        kai = int(kai_m.group(1))
    day_m = re.search(r"(\d+)日目", kai_src)
    if day_m:
        day = int(day_m.group(1))

    # ---- コース方向 ----
    course_direction = ""
    dir_m = re.search(r"[芝ダ](右|左)([外内])?", info_text)
    if dir_m:
        course_direction = dir_m.group(1) + (dir_m.group(2) or "")
    elif "直線" in info_text:
        course_direction = "直線"
    elif race_id[4:6] == "65":
        course_direction = "直線"

    # ---- 結果テーブル ----
    table = soup.find("table", class_="race_table_01")
    if not table:
        logger.warning(f"race_table_01 not found: {race_id}")
        return None

    all_rows = table.find_all("tr")
    if not all_rows:
        return None

    header_row = all_rows[0]
    header_cells = header_row.find_all(["th", "td"])
    header_texts = [c.get_text(strip=True) for c in header_cells]

    def col_idx(names, default=-1):
        for name in names:
            for i, h in enumerate(header_texts):
                if name in h:
                    return i
        return default

    IDX_FINISH = col_idx(["着順"], 0)
    IDX_BRACKET = col_idx(["枠番"], 1)
    IDX_HORSE_NUM = col_idx(["馬番"], 2)
    IDX_HORSE = col_idx(["馬名"], 3)
    IDX_SEX_AGE = col_idx(["性齢"], 4)
    IDX_JW = col_idx(["斤量"], 5)
    IDX_JOCKEY = col_idx(["騎手"], 6)
    IDX_TIME = col_idx(["タイム"], 7)
    IDX_MARGIN = col_idx(["着差"], 8)
    IDX_CORNER = col_idx(["通過", "コーナー"], 10)
    IDX_LAST3F = col_idx(["上り"], 11)
    IDX_ODDS = col_idx(["単勝"], 9)
    IDX_POP = col_idx(["人気"], 10)
    IDX_WEIGHT = col_idx(["馬体重"], 13)
    IDX_TRAINER = col_idx(["調教師"], 14)
    IDX_PRIZE = col_idx(["賞金"], 15)

    has_time_index = any("ﾀｲﾑ指数" in h or "タイム指数" in h for h in header_texts)
    if has_time_index:
        IDX_CORNER = col_idx(["通過", "コーナー"], 10)
        IDX_LAST3F = col_idx(["上り"], 11)
        IDX_ODDS = col_idx(["単勝"], 12)
        IDX_POP = col_idx(["人気"], 13)
        IDX_WEIGHT = col_idx(["馬体重"], 14)
        IDX_TRAINER = col_idx(["調教師"], 18)
        IDX_PRIZE = col_idx(["賞金"], 20)

    logger.debug(f"テーブルヘッダー({race_id}): {header_texts}")

    horse_rows = all_rows[1:]
    num_horses = len(horse_rows)
    horses = []

    for row in horse_rows:
        cols = row.find_all("td")
        if len(cols) < 8:
            continue
        try:
            def txt(i, _cols=cols):
                return _cols[i].get_text(strip=True) if i < len(_cols) else ""

            def link_href(i, _cols=cols):
                a = _cols[i].find("a") if i < len(_cols) else None
                href = a["href"] if a and "href" in a.attrs else ""
                if href and not href.startswith("http"):
                    href = "https://db.netkeiba.com" + href
                return href

            def link_text(i, _cols=cols):
                a = _cols[i].find("a") if i < len(_cols) else None
                return a.get_text(strip=True) if a else txt(i)

            finish_pos = txt(IDX_FINISH)
            try:
                finish_position = int(finish_pos)
            except ValueError:
                finish_position = finish_pos

            bracket_t = txt(IDX_BRACKET)
            bracket_number = int(bracket_t) if bracket_t.isdigit() else None

            horse_num_t = txt(IDX_HORSE_NUM)
            horse_number = int(horse_num_t) if horse_num_t.isdigit() else None

            horse_name = link_text(IDX_HORSE)
            horse_url = link_href(IDX_HORSE)
            horse_id_m = re.search(r"/horse/([A-Za-z0-9]+)", horse_url)
            horse_id = horse_id_m.group(1) if horse_id_m else ""

            sex_age = txt(IDX_SEX_AGE)
            sex = sex_age[0] if sex_age else ""
            age_m = re.search(r"\d+", sex_age)
            age = int(age_m.group()) if age_m else None

            jw_t = txt(IDX_JW)
            jockey_weight = float(jw_t) if jw_t else None

            jockey_name = link_text(IDX_JOCKEY)
            jockey_url = link_href(IDX_JOCKEY)
            jockey_id_m = re.search(r"/jockey/(?:result/recent/)?([A-Za-z0-9]+)", jockey_url)
            jockey_id = jockey_id_m.group(1) if jockey_id_m else ""

            finish_time = txt(IDX_TIME)
            margin = txt(IDX_MARGIN)

            odds_t = txt(IDX_ODDS) if IDX_ODDS >= 0 and IDX_ODDS < len(cols) else ""
            try:
                odds = float(odds_t)
            except (ValueError, TypeError):
                odds = None

            pop_t = txt(IDX_POP) if IDX_POP >= 0 and IDX_POP < len(cols) else ""
            popularity = int(pop_t) if pop_t.isdigit() else None

            corner_positions = txt(IDX_CORNER) if IDX_CORNER >= 0 and IDX_CORNER < len(cols) else ""
            last_3f_str = txt(IDX_LAST3F) if IDX_LAST3F >= 0 and IDX_LAST3F < len(cols) else ""
            weight_text = txt(IDX_WEIGHT) if IDX_WEIGHT >= 0 and IDX_WEIGHT < len(cols) else ""

            weight_kg = None
            weight_change = None
            wm = re.match(r"(\d+)\(([+-]?\d+)\)", weight_text)
            if wm:
                weight_kg = int(wm.group(1))
                weight_change = int(wm.group(2))

            trainer_name = (
                link_text(IDX_TRAINER) if IDX_TRAINER >= 0 and IDX_TRAINER < len(cols) else ""
            )
            trainer_url = (
                link_href(IDX_TRAINER) if IDX_TRAINER >= 0 and IDX_TRAINER < len(cols) else ""
            )
            trainer_id_m = re.search(r"/trainer/(?:result/recent/)?([A-Za-z0-9]+)", trainer_url)
            trainer_id = trainer_id_m.group(1) if trainer_id_m else ""

            prize_t = txt(IDX_PRIZE) if IDX_PRIZE >= 0 and IDX_PRIZE < len(cols) else ""
            try:
                prize_money = float(prize_t.replace(",", "")) * 10000 if prize_t else None
            except ValueError:
                prize_money = None

            cp_list = []
            if corner_positions:
                cp_list = [int(x) for x in corner_positions.split("-") if x.strip().isdigit()]
            n_cp = len(cp_list)

            horses.append(
                {
                    "race_id": race_id,
                    "finish_position": finish_position,
                    "bracket_number": bracket_number,
                    "horse_number": horse_number,
                    "horse_name": horse_name,
                    "horse_url": horse_url,
                    "horse_id": horse_id,
                    "sex_age": sex_age,
                    "sex": sex,
                    "age": age,
                    "jockey_weight": jockey_weight,
                    "jockey_name": jockey_name,
                    "jockey_url": jockey_url,
                    "jockey_id": jockey_id,
                    "finish_time": finish_time,
                    "margin": margin,
                    "odds": odds,
                    "popularity": popularity,
                    "corner_positions": corner_positions,
                    "corner_positions_list": cp_list,
                    "corner_1": cp_list[0] if n_cp >= 1 else None,
                    "corner_2": cp_list[1] if n_cp >= 2 else None,
                    "corner_3": cp_list[2] if n_cp >= 3 else None,
                    "corner_4": cp_list[3] if n_cp >= 4 else None,
                    "last_3f": last_3f_str,
                    "weight": weight_text,
                    "weight_kg": weight_kg,
                    "weight_change": weight_change,
                    "trainer_name": trainer_name,
                    "trainer_url": trainer_url,
                    "trainer_id": trainer_id,
                    "prize_money": prize_money,
                }
            )
        except Exception as ex:
            logger.debug(f"row parse error {race_id}: {ex}")
            continue

    # last_3f_rank 計算
    last_3f_vals = []
    for h in horses:
        try:
            last_3f_vals.append(float(h["last_3f"]))
        except (ValueError, TypeError):
            last_3f_vals.append(float("inf"))
    sorted_idx = sorted(range(len(last_3f_vals)), key=lambda i: last_3f_vals[i])
    ranks = [0] * len(horses)
    for rank, idx in enumerate(sorted_idx):
        if last_3f_vals[idx] != float("inf"):
            ranks[idx] = rank + 1
    for i, h in enumerate(horses):
        h["last_3f_rank"] = ranks[i] if ranks[i] > 0 else None

    # ---- pedigreeバッチ取得を先行起動 ----
    seen_horse_ids: set = set()
    unique_horses = []
    for h in horses:
        hid = h.get("horse_id", "")
        if hid and hid not in seen_horse_ids:
            seen_horse_ids.add(hid)
            unique_horses.append(h)
    all_horse_ids = [h.get("horse_id", "") for h in unique_horses if h.get("horse_id")]
    _ped_task = None
    if SUPABASE_ENABLED and all_horse_ids:
        _ped_task = asyncio.ensure_future(asyncio.to_thread(get_pedigree_cache_batch, all_horse_ids))

    # ---- ラップタイム解析 ----
    lap_cumulative = {}
    lap_sectional = {}
    for tbl in soup.find_all("table"):
        rows = tbl.find_all("tr")
        if len(rows) < 2:
            continue
        header_cells2 = rows[0].find_all(["th", "td"])
        headers_text = [
            c.get_text(strip=True).replace("\u3000", "").replace(" ", "") for c in header_cells2
        ]
        dists = []
        for h_txt in headers_text:
            dm = re.match(r"^(\d+)m?$", h_txt)
            if dm:
                d = int(dm.group(1))
                if 100 <= d <= 4000 and d % 200 == 0:
                    dists.append(d)
        if len(dists) >= 3:
            time_cells = rows[1].find_all("td")
            for i, dist in enumerate(dists):
                if i < len(time_cells):
                    try:
                        t = float(time_cells[i].get_text(strip=True))
                        if 5.0 <= t <= 200.0:
                            lap_cumulative[dist] = t
                    except ValueError:
                        pass
            if lap_cumulative:
                sorted_dists = sorted(lap_cumulative.keys())
                prev = 0.0
                for d in sorted_dists:
                    lap_sectional[d] = round(lap_cumulative[d] - prev, 1)
                    prev = lap_cumulative[d]
                break

    del soup, html

    # ---- pedigreeタスク待機 ----
    pedigree_batch: dict = {}
    if _ped_task is not None:
        try:
            pedigree_batch = await _ped_task
        except Exception:
            pass

    # ---- 馬詳細スクレイピング（4頭ずつ並列） ----
    async def _fetch_detail(h):
        hid = h.get("horse_id", "")
        hurl = h.get("horse_url", "")
        if not hid:
            return
        detail = await scrape_horse_detail(
            session, hid, hurl, pedigree_cache=pedigree_batch, quick_mode=_quick_mode
        )
        h.update(detail)
        del detail

    for _ci in range(0, len(unique_horses), 4):
        _chunk = unique_horses[_ci : _ci + 4]
        await asyncio.gather(*[_fetch_detail(h) for h in _chunk])
    gc.collect()

    return {
        "race_info": {
            "race_id": race_id,
            "race_name": race_name,
            "venue": venue,
            "date": date_str,
            "post_time": post_time,
            "race_class": race_class,
            "kai": kai,
            "day": day,
            "course_direction": course_direction,
            "distance": distance,
            "track_type": track_type,
            "weather": weather,
            "field_condition": field_condition,
            "num_horses": num_horses,
            "surface": None,
            "lap_cumulative": lap_cumulative,
            "lap_sectional": lap_sectional,
        },
        "horses": horses,
    }
