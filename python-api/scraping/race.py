"""
レーススクレイピング: netkeiba.com から単一レースの完全データを取得する。
"""
from __future__ import annotations

import asyncio
import gc
import re
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup

from scraping.constants import HTML_STRAINER, VENUE_MAP, SCRAPE_PROXY_URL, is_cloudflare_block
from scraping.horse import scrape_horse_detail

try:
    from app_config import logger  # type: ignore
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


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
    html: str | None = None
    # ── リトライ付きフェッチ（最大3回、指数バックオフ） ──
    for _attempt in range(3):
        try:
            if _attempt > 0:
                await asyncio.sleep(2.0 ** _attempt)
            _get_kwargs: dict = {}
            if SCRAPE_PROXY_URL:
                _get_kwargs["proxy"] = SCRAPE_PROXY_URL
            async with session.get(url, **_get_kwargs) as resp:
                if resp.status == 429:
                    logger.warning(f"429 Too Many Requests: {url} (試行{_attempt+1}/3)")
                    await asyncio.sleep(10.0 + _attempt * 5.0)
                    continue
                if resp.status != 200:
                    logger.warning(f"HTTP {resp.status}: {url}")
                    return None
                content = await resp.read()
                if is_cloudflare_block(content):
                    logger.error(
                        f"Cloudflare ブロック検知 ({len(content)}B): {url} — "
                        f"SCRAPE_PROXY_URL 環境変数でプロキシを設定してください"
                    )
                    return None
                raw = content.decode("euc-jp", errors="replace")
                if "\ufffd" in raw[:500]:
                    logger.debug(f"EUC-JP 変換警告 (先頭500文字に置換文字あり): {race_id}")
                html = raw
                break
        except asyncio.TimeoutError:
            logger.warning(f"タイムアウト {race_id} 試行{_attempt+1}/3")
        except Exception as e:
            logger.error(f"取得エラー {race_id} 試行{_attempt+1}/3: {e}")
    if html is None:
        logger.error(f"最大リトライ到達、取得失敗: {race_id}")
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
    # L1-2: より多くのパターンに対応（全角m、ドット区切り、障害、方向なし、「右 外」のようにスペース入りなど）
    # 例: 芝右1200m / 芝右 外1200m / ダート左1600m / 障害3200m
    dist_m = re.search(r"(芝|ダ(?:ート)?|障(?:害)?)[\s・右左直外内障]{0,8}(\d{3,4})\s*[mｍ]", info_text)
    if dist_m:
        _tt_raw = dist_m.group(1)
        track_type = "芝" if _tt_raw == "芝" else ("障害" if _tt_raw.startswith("障") else "ダート")
        distance = int(dist_m.group(2))
    else:
        # Fallback 1: レース名から距離・種別を抽出（mainrace_div が取れなかった場合に有効）
        _name_m = re.search(r"(芝|ダ(?:ート)?|障(?:害)?).*?(\d{3,4})\s*[mｍ]", race_name)
        if _name_m:
            _tt_raw = _name_m.group(1)
            track_type = "芝" if _tt_raw == "芝" else ("障害" if _tt_raw.startswith("障") else "ダート")
            distance = int(_name_m.group(2))
        else:
            # Fallback 2: 距離の数字のみ（1200〜3600m の範囲）+ 種別を別途推定
            _num_m = re.search(r"\b(\d{3,4})\s*[mｍ]", info_text)
            _tt_only = re.search(r"(芝|ダート|ダ|障害)", info_text[:500])
            _vc_tmp = race_id[4:6]
            if _vc_tmp == "65":
                track_type = "ばんえい"
                banei_m = re.search(r"ばんえい\s*(\d+)", info_text) or re.search(r"(\d{3})\s*m", info_text)
                distance = int(banei_m.group(1)) if banei_m else 200
            elif _num_m and 100 <= int(_num_m.group(1)) <= 3600:
                distance = int(_num_m.group(1))
                if _tt_only:
                    _raw = _tt_only.group(1)
                    track_type = "芝" if _raw == "芝" else ("障害" if _raw == "障害" else "ダート")
                else:
                    track_type = ""
            else:
                track_type = ""
                distance = 0
                # S-3: distance=0 → パース失敗。race_info に _invalid_distance フラグを付与して
                # DB に保存し、ローダー側でスキップさせる。中央値補完はしない。
                import logging as _log
                _log.error(
                    f"[S-3][INVALID] distance=0: race_id={race_id} HTMLから距離/種別を取得できませんでした。"
                    f" このレースは _invalid_distance=True で保存され学習・推論から除外されます。"
                    f" info_text: {info_text[:120]!r}"
                )

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
    # NOTE: race_id[:8] は YEAR+VENUE_CODE+KAI であり日付ではないため使用禁止。
    # date_hint が渡されない場合（手動呼び出し等）は空のままとし、
    # DB repair スクリプトで後処理する。
    if not date_str:
        # タイトルタグや他の要素からも試みる
        title_tag = soup.find("title")
        if title_tag:
            tdm = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", title_tag.get_text())
            if tdm:
                date_str = f"{tdm.group(1)}{int(tdm.group(2)):02d}{int(tdm.group(3)):02d}"
    if not date_str:
        logger.warning(f"race_id={race_id}: 日付を取得できませんでした (date_hint={date_hint!r})")
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
            # NAR: C1二/C1三/B3等（括弧内優先）
            r"[（(]([ABC][1-3][二三一四]?)[）)]",
            r"[（(]([ABC][1-3])[）)]",
            # NAR: 単文字クラス (A)/(B)
            r"[（(]([AB]混合)[）)]",
            r"[（(]([AB])[）)]",
            # NAR: 年齢条件 (3歳) または "3歳○○"
            r"[（(](\d歳)[）)]",
            r"^(\d歳)",
            # NAR: 括弧なしでクラス名が直接表記されるケース
            r"(?<![A-Za-z])([ABC][1-3])(?![A-Za-z])",
            # NAR: "AB混合" など括弧なし
            r"([AB]B?混合)",
        ]:
            cm = re.search(pat, src)
            if cm:
                race_class = cm.group(1)
                break
    if race_class == "OP":
        race_class = "オープン"
    # 解析不可な場合のフォールバック標注
    if not race_class:
        if "特別" in race_name:
            race_class = "オープン"  # "○○特別"は一般的にオープン認定
        elif "新馬" in race_name:
            race_class = "新馬"
        elif "未勝利" in race_name:
            race_class = "未勝利"
        elif "重賞" in race_name:
            race_class = "重賞"
        else:
            race_class = "不明"  # 完全に判定不能な場合は明示的に残す

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
        logger.warning(f"race_table_01 not found: {race_id} → 出馬表ページへフォールバック")
        return await _scrape_shutuba_fallback(session, race_id, date_hint)

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
    IDX_CORNER = col_idx(["通過", "コーナー"], -1)
    IDX_LAST3F = col_idx(["上り"], -1)
    IDX_ODDS = col_idx(["単勝"], -1)
    IDX_POP = col_idx(["人気"], -1)
    IDX_WEIGHT = col_idx(["馬体重"], -1)
    IDX_TRAINER = col_idx(["調教師"], -1)
    IDX_PRIZE = col_idx(["賞金"], -1)

    # タイム指数列がある場合の追加確認（デフォルト位置補正）
    has_time_index = any("ﾀｲﾑ指数" in h or "タイム指数" in h for h in header_texts)
    if has_time_index:
        # col_idx で名前解決できなかった場合のみ位置フォールバックを適用
        if IDX_ODDS == -1:
            IDX_ODDS = 13
        if IDX_POP == -1:
            IDX_POP = 14
        if IDX_WEIGHT == -1:
            IDX_WEIGHT = 15
    else:
        if IDX_ODDS == -1:
            IDX_ODDS = 9
        if IDX_POP == -1:
            IDX_POP = 10
        if IDX_WEIGHT == -1:
            IDX_WEIGHT = 13

    # 必須列が取得できなかった場合は警告を出す（サイレント欠損防止）
    for _col_name, _col_idx in [("馬名", IDX_HORSE), ("騎手", IDX_JOCKEY), ("タイム", IDX_TIME)]:
        if _col_idx < 0 or _col_idx >= len(header_texts):
            logger.warning(f"必須列 [{_col_name}] が検出できませんでした: {race_id} headers={header_texts}")

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
                """セル内の<a>テキストを返す。<a>が空の場合はセル全体テキストにフォールバック。"""
                if i >= len(_cols):
                    return ""
                a = _cols[i].find("a")
                if a:
                    txt_a = a.get_text(strip=True)
                    if txt_a:  # <a>にテキストあり
                        return txt_a
                    # <a>が空 (例: <a href="..."><img/></a>) → セル全体テキストを試みる
                    cell_txt = _cols[i].get_text(strip=True)
                    if cell_txt:
                        return cell_txt
                    # <a>のsiblingからも探す
                    cell_txt2 = " ".join(
                        t.get_text(strip=True)
                        for t in _cols[i].find_all(["span", "b", "strong"])
                    ).strip()
                    return cell_txt2 if cell_txt2 else ""
                return _cols[i].get_text(strip=True)

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
            # 2025/8以降 netkeiba は /horse/result/{id}/ 形式も使用するため両対応
            horse_id_m = re.search(r"/horse/(?:result/)?([A-Za-z0-9]+)(?:/|$)", horse_url)
            horse_id = horse_id_m.group(1) if horse_id_m else ""
            # horse_name が空の場合は警告（horse_id は href から取得済みなので結合キーは維持される）
            if not horse_name:
                logger.warning(
                    f"[horse_name空] race_id={race_id} horse_id={horse_id}"
                    f" IDX_HORSE={IDX_HORSE} cell_html={str(cols[IDX_HORSE])[:120]!r}"
                )

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
    _ped_task = None  # Supabase バッチ取得は削除済み。SQLite キャッシュは馬単位で確認する。

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

    # ---- 払い戻し表パース（soupを解放する前に実行） ----
    return_tables = _parse_return_tables(soup, race_id)

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
        if _ci + 4 < len(unique_horses):
            await asyncio.sleep(1.0)  # 4頭ごとにインターバル（IP ブロック抑制）
        gc.collect()

    # distance=0 のレースは _invalid_distance フラグを付与（ローダーが除外する）
    _invalid_dist_flag = (distance == 0 or distance is None)

    return _build_race_result(race_id, race_name, venue, date_str, post_time, race_class,
                              kai, day, course_direction, distance, track_type, weather,
                              field_condition, num_horses, lap_cumulative, lap_sectional,
                              horses, _invalid_dist_flag, return_tables=return_tables)


def _parse_return_tables(soup, race_id: str) -> list[dict]:
    """払い戻し表（単勝・複勝・馬連・三連単など）をパースして返す。

    返り値: [{"bet_type": str, "combinations": str, "payout": int, "popularity": int|None}, ...]
    複数組合せ（複勝３頭など）はそれぞれ別エントリとして返す。
    解析失敗時は空リストを返す（サイレント）。
    """
    results: list[dict] = []

    # ── 払い戻しブロックを広めに探す ──
    # netkeiba は class名が "pay_block_w*" または "payout_block" などを用いる
    pay_tables: list = []
    for tbl in soup.find_all("table"):
        cls_str = " ".join(tbl.get("class", []))
        if "pay_block" in cls_str or "pay_table" in cls_str:
            pay_tables.append(tbl)

    # クラス名で見つからない場合: 単勝/複勝/馬連キーワードを含む行を持つテーブルを探す
    if not pay_tables:
        _pay_keywords = {"単勝", "複勝", "馬連", "馬単", "三連複", "三連単", "枠連", "ワイド"}
        for tbl in soup.find_all("table"):
            tbl_text = tbl.get_text()
            if any(kw in tbl_text for kw in _pay_keywords):
                pay_tables.append(tbl)

    for tbl in pay_tables:
        rows = tbl.find_all("tr")
        for row in rows:
            # th (賭式名) + td cells (馬番組合せ, 払戻金額, 人気)
            th = row.find("th")
            if not th:
                continue
            bet_type = th.get_text(strip=True)
            if not bet_type:
                continue

            tds = row.find_all("td")
            if len(tds) < 2:
                continue

            # 複勝・ワイドなどは同一行に複数の払い戻し組み合わせ (<br> 区切り)
            combo_cell = tds[0]
            payout_cell = tds[1]
            pop_cell = tds[2] if len(tds) >= 3 else None

            # <br> で複数エントリが区切られている場合のテキスト分割
            def _split_cell(td) -> list[str]:
                if td is None:
                    return []
                # <br> を改行に置換してから分割
                for br in td.find_all("br"):
                    br.replace_with("\n")
                return [s.strip() for s in td.get_text().split("\n") if s.strip()]

            combos = _split_cell(combo_cell)
            payouts = _split_cell(payout_cell)
            pops = _split_cell(pop_cell) if pop_cell else []

            # 組合せ数に合わせてエントリを生成
            n = max(len(combos), 1)
            for idx in range(n):
                combo_str = combos[idx] if idx < len(combos) else ""
                payout_str = payouts[idx] if idx < len(payouts) else ""
                pop_str = pops[idx] if idx < len(pops) else ""

                # 払戻金額を整数に変換 (例: "1,320円" → 1320)
                payout_int = 0
                payout_clean = re.sub(r"[^\d]", "", payout_str)
                if payout_clean:
                    try:
                        payout_int = int(payout_clean)
                    except ValueError:
                        pass

                if not combo_str and payout_int == 0:
                    continue

                # 人気を整数に変換 (例: "5番人気" → 5)
                pop_int: int | None = None
                pop_m = re.search(r"(\d+)", pop_str)
                if pop_m:
                    try:
                        pop_int = int(pop_m.group(1))
                    except ValueError:
                        pass

                results.append({
                    "bet_type": bet_type,
                    "combinations": combo_str,
                    "payout": payout_int,
                    "popularity": pop_int,
                })

    if results:
        logger.debug(f"払い戻し表パース完了: {race_id} {len(results)}件")
    return results


def _build_race_result(race_id, race_name, venue, date_str, post_time, race_class,
                       kai, day, course_direction, distance, track_type, weather,
                       field_condition, num_horses, lap_cumulative, lap_sectional,
                       horses, _invalid_dist_flag, return_tables=None):
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
            # distance が取れなかった場合はこのレースをスキップ対象としてマーク
            **({"_invalid_distance": True,
                "_skip_reason": "distance=0: HTMLからの距離パース失敗"}
               if _invalid_dist_flag else {}),
        },
        "horses": horses,
        "return_tables": return_tables or [],
    }


async def _scrape_shutuba_fallback(
    session, race_id: str, date_hint: str = ""
) -> Optional[dict]:
    """
    db.netkeiba.com に結果がない（当日・未来レース）場合に
    race.netkeiba.com/race/shutuba.html から出走馬情報を取得する。
    """
    import httpx

    shutuba_url = f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as hx:
            resp = await hx.get(shutuba_url)
        if resp.status_code != 200:
            logger.warning(f"shutuba HTTP {resp.status_code}: {race_id}")
            return None
        html = resp.content.decode("euc-jp", errors="replace")
    except Exception as e:
        logger.error(f"shutuba 取得エラー {race_id}: {e}")
        return None

    soup = BeautifulSoup(html, "lxml")

    # ---- レース名 ----
    race_name = ""
    for _cls in ["RaceName", "race_name"]:
        _h = soup.find(class_=_cls)
        if _h:
            race_name = _h.get_text(strip=True)
            break
    if not race_name:
        h1 = soup.find("h1")
        if h1:
            race_name = h1.get_text(strip=True)

    # ---- 基本情報 (RaceData01 / RaceData02) ----
    rd1 = soup.find(class_="RaceData01")
    rd2 = soup.find(class_="RaceData02")
    info1 = rd1.get_text(" ") if rd1 else ""
    info2 = rd2.get_text(" ") if rd2 else ""

    # 距離・種別
    dist_m = re.search(r"(芝|ダ(?:ート)?|障(?:害)?).*?(\d{3,4})\s*m", info1)
    if dist_m:
        _tt = dist_m.group(1)
        track_type = "芝" if _tt == "芝" else ("障害" if _tt.startswith("障") else "ダート")
        distance = int(dist_m.group(2))
    else:
        track_type = ""
        distance = 0

    # 天候・馬場
    weather_m = re.search(r"天候\s*[:/：]?\s*([^\s/]+)", info1)
    weather = weather_m.group(1).strip() if weather_m else ""
    cond_m = re.search(r"馬場\s*[:/：]?\s*([^\s/]+)", info1)
    field_condition = cond_m.group(1).strip() if cond_m else ""

    # 発走時刻
    pt_m = re.search(r"(\d{1,2}:\d{2})発走", info1)
    post_time = pt_m.group(1) if pt_m else ""

    # 開催情報
    venue_code = race_id[4:6]
    venue = VENUE_MAP.get(venue_code, venue_code)
    kai_m = re.search(r"(\d+)回", info2)
    kai = int(kai_m.group(1)) if kai_m else None
    day_m = re.search(r"(\d+)日目", info2)
    day = int(day_m.group(1)) if day_m else None

    # レースクラス
    race_class = ""
    for pat in [r"(G[1-3])", r"(新馬)", r"(未勝利)", r"([1-3]勝クラス)", r"(オープン|OP)", r"(重賞)"]:
        cm = re.search(pat, info2 + " " + race_name)
        if cm:
            race_class = cm.group(1)
            break
    if not race_class:
        race_class = "不明"

    # 日付
    date_str = date_hint
    if not date_str:
        dm = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", html)
        if dm:
            date_str = f"{dm.group(1)}{int(dm.group(2)):02d}{int(dm.group(3)):02d}"

    # コース方向
    dir_m = re.search(r"[（(](右|左|直線)[）)]", info1) or re.search(r"(右|左)", info1)
    course_direction = dir_m.group(1) if dir_m else ""

    # ---- 出走馬テーブル ----
    table = soup.find("table", class_="Shutuba_Table")
    if not table:
        logger.warning(f"Shutuba_Table not found: {race_id}")
        return None

    rows = table.find_all("tr")
    horses: list[dict] = []
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 7:
            continue

        def _txt(idx):
            return cells[idx].get_text(strip=True) if idx < len(cells) else ""

        def _link_id(idx, pattern):
            a = cells[idx].find("a", href=True) if idx < len(cells) else None
            if not a:
                return ""
            m = re.search(pattern, a["href"])
            return m.group(1) if m else ""

        bracket = _txt(0)
        horse_num = _txt(1)
        horse_name_cell = cells[3] if len(cells) > 3 else None
        if not horse_name_cell:
            continue
        horse_name = horse_name_cell.get_text(strip=True)
        horse_a = horse_name_cell.find("a", href=re.compile(r"/horse/"))
        horse_id = ""
        horse_url = ""
        if horse_a:
            horse_url = horse_a["href"]
            hm = re.search(r"/horse/([\w]+)", horse_url)
            horse_id = hm.group(1) if hm else ""

        sex_age = _txt(4)
        jw = _txt(5)  # 斤量

        # 騎手
        jockey_cell = cells[6] if len(cells) > 6 else None
        jockey_name = jockey_cell.get_text(strip=True) if jockey_cell else ""
        jockey_a = jockey_cell.find("a", href=re.compile(r"/jockey/")) if jockey_cell else None
        jockey_id = ""
        jockey_url = ""
        if jockey_a:
            jockey_url = jockey_a["href"]
            jm = re.search(r"/jockey/result/recent/([\w]+)", jockey_url)
            jockey_id = jm.group(1) if jm else ""

        # 厩舎
        trainer_cell = cells[7] if len(cells) > 7 else None
        trainer_name = trainer_cell.get_text(strip=True) if trainer_cell else ""
        trainer_a = trainer_cell.find("a", href=re.compile(r"/trainer/")) if trainer_cell else None
        trainer_id = ""
        trainer_url = ""
        if trainer_a:
            trainer_url = trainer_a["href"]
            tm = re.search(r"/trainer/result/recent/([\w]+)", trainer_url)
            trainer_id = tm.group(1) if tm else ""

        # 馬体重
        weight_str = _txt(8) if len(cells) > 8 else ""
        wm = re.match(r"(\d+)\(([+-]?\d+)\)", weight_str)
        horse_weight = int(wm.group(1)) if wm else None
        weight_diff = int(wm.group(2)) if wm else None

        # 単勝オッズ（発走当日は公開済み、前日以前は "---" や空欄の場合あり）
        odds_str = _txt(9) if len(cells) > 9 else ""
        try:
            odds = float(odds_str) if odds_str and odds_str not in ("---", "**", "-") else None
        except (ValueError, TypeError):
            odds = None

        # 人気（レース前は ** の場合あり）
        pop_str = _txt(10) if len(cells) > 10 else ""
        pop_m = re.search(r"\d+", pop_str)
        popularity = int(pop_m.group()) if pop_m else None

        if not horse_num or not horse_name:
            continue

        horses.append({
            "race_id": race_id,
            "horse_number": int(horse_num) if horse_num.isdigit() else horse_num,
            "bracket_number": int(bracket) if bracket.isdigit() else bracket,
            "horse_name": horse_name,
            "horse_id": horse_id,
            "horse_url": horse_url,
            "sex_age": sex_age,
            "sex": sex_age[0] if sex_age else "",
            "age": int(sex_age[1:]) if len(sex_age) > 1 and sex_age[1:].isdigit() else None,
            "jockey_weight": float(jw) if jw else None,
            "jockey_name": jockey_name,
            "jockey_id": jockey_id,
            "jockey_url": jockey_url,
            "trainer_name": trainer_name,
            "trainer_id": trainer_id,
            "trainer_url": trainer_url,
            "weight_kg": horse_weight,
            "weight_diff": weight_diff,
            "popularity": popularity,
            "odds": odds,          # 出馬表公開済みなら取得、未公開なら None
            "finish_position": None,
            "finish_time": None,
            "date": date_str,
            "distance": distance,
            "track_type": track_type,
            "venue": venue,
            "_shutuba": True,      # 出馬表から取得したフラグ
        })

    if not horses:
        logger.warning(f"shutuba: 出走馬なし {race_id}")
        return None

    logger.info(f"[shutuba] {race_id}: {len(horses)}頭取得 ({race_name} @ {venue} {distance}m)")
    return _build_race_result(
        race_id, race_name, venue, date_str, post_time, race_class,
        kai, day, course_direction, distance, track_type, weather,
        field_condition, len(horses), [], [], horses,
        (distance == 0)
    )
