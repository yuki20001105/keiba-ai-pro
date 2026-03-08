"""
馬詳細スクレイピング: 血統・プロフィール・過去成績を取得する。
"""

import asyncio
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup

from scraping.constants import HTML_STRAINER, COAT_COLORS, COAT_RE

if TYPE_CHECKING:
    pass


def extract_coat_color(soup: "BeautifulSoup", html: str = "") -> str:
    """HTMLから毛色文字列を抽出する（複数手法フォールバック）。"""
    # 1) db_prof_table: <th>毛色</th>
    prof = soup.find("table", class_=lambda c: c and "db_prof_table" in c)
    if prof:
        for tr in prof.find_all("tr"):
            th, td = tr.find("th"), tr.find("td")
            if th and td and "毛色" in th.get_text(strip=True):
                v = td.get_text(strip=True)
                if v:
                    return v
    # 2) 全テーブル: <th>毛色</th> または 性齢/性別フィールドから抽出
    for tbl in soup.find_all("table"):
        for tr in tbl.find_all("tr"):
            th, td = tr.find("th"), tr.find("td")
            if not th or not td:
                continue
            label = th.get_text(strip=True)
            if "毛色" in label:
                v = td.get_text(strip=True)
                if v:
                    return v
            if "性齢" in label or "性別" in label:
                v = td.get_text(strip=True)
                m = COAT_RE.search(v)
                if m:
                    return m.group(0)
    # 3) ページ先頭3000文字から「牡/牝/セン + 毛色」パターンを探す
    if html:
        target = html[:3000]
        sex_coat = re.search(
            r"(?:牡|牝|セン?)\s*(" + "|".join(re.escape(c) for c in COAT_COLORS) + r")",
            target,
        )
        if sex_coat:
            return sex_coat.group(1)
    return ""

# ---------------------------------------------------------------------------
# SQLite ローカル血統キャッシュ（SUPABASE_ENABLED=False 時のフォールバック）
# ---------------------------------------------------------------------------

_PEDIGREE_DB_PATH: Path = (
    Path(__file__).parent.parent.parent / "keiba" / "data" / "pedigree_cache.db"
)


def _init_pedigree_table() -> None:
    """血統キャッシュテーブルを初期化する（なければ作成）"""
    try:
        _PEDIGREE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_PEDIGREE_DB_PATH))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pedigree_cache (
                horse_id TEXT PRIMARY KEY,
                sire TEXT DEFAULT '',
                dam TEXT DEFAULT '',
                damsire TEXT DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()
    except Exception:
        pass  # DB作成失敗は無視（ログスパム防止）


def _get_pedigree_sqlite(horse_id: str) -> dict | None:
    """SQLite 血統キャッシュを検索する。"""
    try:
        conn = sqlite3.connect(str(_PEDIGREE_DB_PATH))
        row = conn.execute(
            "SELECT sire, dam, damsire FROM pedigree_cache WHERE horse_id = ?", (horse_id,)
        ).fetchone()
        conn.close()
        if row and row[0]:
            return {"sire": row[0], "dam": row[1], "damsire": row[2]}
    except Exception:
        pass
    return None


def _save_pedigree_sqlite(horse_id: str, sire: str, dam: str, damsire: str) -> None:
    """SQLite 血統キャッシュに保存する。"""
    if not sire:
        return
    try:
        conn = sqlite3.connect(str(_PEDIGREE_DB_PATH))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """INSERT OR REPLACE INTO pedigree_cache (horse_id, sire, dam, damsire)
               VALUES (?, ?, ?, ?)""",
            (horse_id, sire, dam, damsire),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# テーブル起動時初期化
_init_pedigree_table()

# ---------------------------------------------------------------------------
# Supabase ヘルパー（オプション依存）
# ---------------------------------------------------------------------------
try:
    from supabase_client import (  # type: ignore
        get_pedigree_cache,
        save_pedigree_cache,
    )
    _SUPABASE_HORSE_OK = True
except ImportError:
    _SUPABASE_HORSE_OK = False

    def get_pedigree_cache(horse_id):  # type: ignore
        return None

    def save_pedigree_cache(horse_id, sire, dam, damsire):  # type: ignore
        pass


try:
    from app_config import SUPABASE_ENABLED, logger  # type: ignore
except ImportError:
    import logging
    SUPABASE_ENABLED = False
    logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 血統テーブルパース
# ---------------------------------------------------------------------------

def _parse_blood_table(blood_table, result: dict) -> None:
    """blood_table (BeautifulSoup Tag) から sire/dam/damsire を抽出する共通ロジック。
    複数の HTML パターン（class 名・行構造の違い）に対応する。"""
    trs = blood_table.find_all("tr")
    if not trs:
        return

    half = len(trs) // 2  # 5世代=32行 → half=16

    # ---- 父 (sire) ----
    sire_tds = trs[0].find_all("td")
    if sire_tds:
        a = sire_tds[0].find("a")
        if a:
            result["sire"] = a.get_text(strip=True)

    if not result.get("sire"):
        for td in blood_table.find_all("td", class_=lambda c: c and "b_ml" in c):
            a = td.find("a")
            if a:
                result["sire"] = a.get_text(strip=True)
                break

    # ---- 母 (dam) / 母の父 (damsire) ----
    if half > 0 and len(trs) > half:
        dam_tds = trs[half].find_all("td")
        if dam_tds:
            a = dam_tds[0].find("a")
            if a:
                result["dam"] = a.get_text(strip=True)
        if len(dam_tds) >= 2:
            a = dam_tds[1].find("a")
            if a:
                result["damsire"] = a.get_text(strip=True)

    if not result.get("dam"):
        for td in blood_table.find_all("td", class_=lambda c: c and "b_fml" in c):
            a = td.find("a")
            if a:
                result["dam"] = a.get_text(strip=True)
                break


# ---------------------------------------------------------------------------
# 馬詳細スクレイピング（メイン関数）
# ---------------------------------------------------------------------------

async def scrape_horse_detail(
    session,
    horse_id: str,
    horse_url: str = "",
    pedigree_cache: dict = None,
    quick_mode: bool = False,
) -> dict:
    """
    馬の詳細ページをスクレイピング。
    血統(sire/dam/damsire)、プロフィール、通算成績、直近2走を取得。
    pedigree_cache: {horse_id: {sire,dam,damsire}} のバッチ取得済みキャッシュ（あれば Supabase 個別クエリ省略）
    quick_mode: True=毛色SPフォールバックをスキップして高速化
    """
    if not horse_id and not horse_url:
        return {}

    # ── 地方馬（B プレフィックス）
    if horse_id and re.match(r"^B", str(horse_id)):
        _nar_result: dict = {}

        # 1) Supabase 血統キャッシュ確認
        cached_b = (pedigree_cache or {}).get(horse_id) if pedigree_cache is not None else None
        if cached_b is None and SUPABASE_ENABLED:
            try:
                cached_b = await asyncio.to_thread(get_pedigree_cache, horse_id)
            except Exception as _e:
                logger.debug(f"NAR馬 血統キャッシュ確認失敗: {_e}")
        elif cached_b is None and not SUPABASE_ENABLED:
            # Supabase 無効時は SQLite ローカルキャッシュを確認
            cached_b = _get_pedigree_sqlite(horse_id)
        if cached_b and cached_b.get("sire") and cached_b["sire"] not in ("", "unknown_local"):
            logger.debug(f"NAR馬 血統キャッシュヒット: {horse_id} sire={cached_b['sire']}")
            _nar_result = {k: cached_b.get(k, "") for k in ("sire", "dam", "damsire")}
        else:
            # 2) /horse/ped/<horse_id>/ を直接取得
            ped_url_b = f"https://db.netkeiba.com/horse/ped/{horse_id}/"
            pedigree_result_b: dict = {}
            for attempt in range(3):
                try:
                    if attempt > 0:
                        await asyncio.sleep(attempt * 1.0)
                    async with session.get(ped_url_b) as ped_resp_b:
                        if ped_resp_b.status == 200:
                            ped_content_b = await ped_resp_b.read()
                            ped_html_b = ped_content_b.decode("euc-jp", errors="replace")
                            ped_soup_b = BeautifulSoup(ped_html_b, "lxml", parse_only=HTML_STRAINER)
                            blood_table_b = ped_soup_b.find("table", class_="blood_table")
                            if blood_table_b:
                                _parse_blood_table(blood_table_b, pedigree_result_b)
                            if pedigree_result_b.get("sire"):
                                logger.info(f"NAR馬 /ped/ 血統取得成功: {horse_id} sire={pedigree_result_b['sire']}")
                                if SUPABASE_ENABLED:
                                    await asyncio.to_thread(
                                        save_pedigree_cache,
                                        horse_id,
                                        pedigree_result_b.get("sire", ""),
                                        pedigree_result_b.get("dam", ""),
                                        pedigree_result_b.get("damsire", ""),
                                    )
                                else:
                                    _save_pedigree_sqlite(
                                        horse_id,
                                        pedigree_result_b.get("sire", ""),
                                        pedigree_result_b.get("dam", ""),
                                        pedigree_result_b.get("damsire", ""),
                                    )
                                _nar_result = pedigree_result_b
                                break
                            logger.debug(f"NAR馬 /ped/ 200 だが blood_table 未検出: {horse_id}")
                            break
                        elif ped_resp_b.status == 429:
                            await asyncio.sleep(5.0 + attempt * 3.0)
                            continue
                        else:
                            logger.debug(f"NAR馬 /ped/ HTTP {ped_resp_b.status}: {horse_id}")
                            break
                except Exception as e_b:
                    logger.debug(f"NAR馬 /ped/ 取得失敗 試行{attempt + 1} {horse_id}: {e_b}")
                    if attempt < 2:
                        await asyncio.sleep(2.0 ** attempt)
            if not _nar_result.get("sire"):
                logger.debug(f"NAR馬 血統取得不可: {horse_id} → unknown_local")
                _nar_result = {"sire": "unknown_local", "dam": "unknown_local", "damsire": "unknown_local"}

        # 3) sp.netkeiba で生年月日・通算成績を補完
        _sp_html_parsed = None
        if not quick_mode and _sp_html_parsed is None:
            try:
                await asyncio.sleep(0.1)
                async with session.get(f"https://db.sp.netkeiba.com/horse/{horse_id}/") as _sp_resp2:
                    if _sp_resp2.status == 200:
                        _sp_html_parsed = BeautifulSoup(
                            (await _sp_resp2.read()).decode("euc-jp", errors="replace"),
                            "lxml",
                            parse_only=HTML_STRAINER,
                        )
            except Exception:
                pass

        if _sp_html_parsed:
            sp_text = _sp_html_parsed.get_text()
            if not _nar_result.get("horse_birth_date"):
                for tbl in _sp_html_parsed.find_all("table"):
                    for tr in tbl.find_all("tr"):
                        th2, td2 = tr.find("th"), tr.find("td")
                        if th2 and td2 and "生年月日" in th2.get_text(strip=True):
                            _nar_result["horse_birth_date"] = td2.get_text(strip=True)
                            break
                    if _nar_result.get("horse_birth_date"):
                        break
            if not _nar_result.get("horse_total_runs"):
                runs_m2 = re.search(r"(\d+)戦\s*(\d+)勝", sp_text)
                if runs_m2:
                    _nar_result["horse_total_runs"] = int(runs_m2.group(1))
                    _nar_result["horse_total_wins"] = int(runs_m2.group(2))

        return _nar_result

    # ── JRA馬
    url = horse_url if horse_url.startswith("http") else f"https://db.netkeiba.com/horse/{horse_id}/"
    if url and not url.endswith("/"):
        url = url + "/"
    result = {}

    async def _safe_get_horse(u: str):
        try:
            async with session.get(u) as r:
                if r.status != 200:
                    return None
                return (await r.read()).decode("euc-jp", errors="replace")
        except Exception:
            return None

    _cached_ped = (pedigree_cache or {}).get(horse_id)
    _has_ped_cache = bool(_cached_ped and _cached_ped.get("sire"))

    async def _noop_fetch():
        return None

    html, _pre_result_html, _pre_ped_html = await asyncio.gather(
        _safe_get_horse(url),
        _safe_get_horse(f"https://db.netkeiba.com/horse/result/{horse_id}/"),
        _noop_fetch() if _has_ped_cache else _safe_get_horse(f"https://db.netkeiba.com/horse/ped/{horse_id}/"),
    )
    if html is None:
        logger.debug(f"馬詳細取得失敗 {horse_id}")
        return result

    soup = BeautifulSoup(html, "lxml", parse_only=HTML_STRAINER)
    del html

    # ===== プロフィール（db_prof_table から取得） =====
    prof_table = soup.find("table", attrs={"class": re.compile(r"db_prof_table")})
    if prof_table:
        for row in prof_table.find_all("tr"):
            th = row.find("th")
            td = row.find("td")
            if not th or not td:
                continue
            key = th.get_text(strip=True)
            val = td.get_text(strip=True)
            if "生年月日" in key:
                result["horse_birth_date"] = val
            elif "馬主" in key and "horse_owner" not in result:
                result["horse_owner"] = val
            elif "生産者" in key and "horse_breeder" not in result:
                result["horse_breeder"] = val
            elif "産地" in key and "horse_breeding_farm" not in result:
                result["horse_breeding_farm"] = val
            elif "通算成績" in key:
                runs_m = re.search(r"(\d+)戦\s*(\d+)勝", val)
                if runs_m:
                    result["horse_total_runs"] = int(runs_m.group(1))
                    result["horse_total_wins"] = int(runs_m.group(2))
            elif "獲得賞金" in key and "中央" in key:
                prize_m = re.search(r"([\d,]+)", val)
                if prize_m:
                    try:
                        result["horse_total_prize_money"] = float(prize_m.group(1).replace(",", "")) * 10000
                    except ValueError:
                        pass
    else:
        full_text = soup.get_text()
        runs_m = re.search(r"(\d+)戦\s*(\d+)勝", full_text)
        if runs_m:
            result["horse_total_runs"] = int(runs_m.group(1))
            result["horse_total_wins"] = int(runs_m.group(2))
        prize_m = re.search(r"獲得賞金[^\d]*([\d,]+(?:\.\d+)?)\s*万円", full_text)
        if prize_m:
            try:
                result["horse_total_prize_money"] = float(prize_m.group(1).replace(",", "")) * 10000
            except ValueError:
                pass
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                th_tag = row.find("th")
                td_tag = row.find("td")
                if not th_tag or not td_tag:
                    continue
                key = th_tag.get_text(strip=True)
                val = td_tag.get_text(strip=True)
                if "生年月日" in key:
                    result["horse_birth_date"] = val
                elif "馬主" in key and "horse_owner" not in result:
                    result["horse_owner"] = val
                elif "生産者" in key and "horse_breeder" not in result:
                    result["horse_breeder"] = val
                elif "産地" in key and "horse_breeding_farm" not in result:
                    result["horse_breeding_farm"] = val

    # ===== 血統 (sire / dam / damsire) =====
    pedigree_cached = False

    if horse_id:
        cached = (pedigree_cache or {}).get(horse_id) if pedigree_cache is not None else None
        if cached is None and SUPABASE_ENABLED:
            try:
                cached = await asyncio.to_thread(get_pedigree_cache, horse_id)
            except Exception as _e:
                logger.debug(f"血統キャッシュ確認失敗: {_e}")
        elif cached is None and not SUPABASE_ENABLED:
            # Supabase 無効時は SQLite ローカルキャッシュを確認
            cached = _get_pedigree_sqlite(horse_id)
        if cached:
            result["sire"] = cached.get("sire") or ""
            result["dam"] = cached.get("dam") or ""
            result["damsire"] = cached.get("damsire") or ""
            pedigree_cached = True
            logger.debug(f"血統キャッシュヒット: {horse_id} sire={result['sire']}")

    if not pedigree_cached:
        blood_table_main = soup.find("table", class_="blood_table")
        if blood_table_main:
            _parse_blood_table(blood_table_main, result)
            logger.debug(f"メインページ血統: {horse_id} sire={result.get('sire')}")

        if not result.get("sire"):
            if _pre_ped_html:
                ped_soup = BeautifulSoup(_pre_ped_html, "lxml", parse_only=HTML_STRAINER)
                del _pre_ped_html
                blood_table = ped_soup.find("table", class_="blood_table")
                if blood_table:
                    _parse_blood_table(blood_table, result)
                    if result.get("sire"):
                        logger.debug(f"ped 血統取得成功(並列): {horse_id} sire={result['sire']}")
            if not result.get("sire"):
                ped_url = f"https://db.netkeiba.com/horse/ped/{horse_id}/"
                try:
                    await asyncio.sleep(0.2)
                    async with session.get(ped_url) as ped_resp:
                        if ped_resp.status == 200:
                            ped_content = await ped_resp.read()
                            ped_html_retry = ped_content.decode("euc-jp", errors="replace")
                            ped_soup = BeautifulSoup(ped_html_retry, "lxml", parse_only=HTML_STRAINER)
                            blood_table = ped_soup.find("table", class_="blood_table")
                            if blood_table:
                                _parse_blood_table(blood_table, result)
                                logger.debug(f"ped リトライ成功: {horse_id} sire={result.get('sire')}")
                        elif ped_resp.status == 429:
                            await asyncio.sleep(8.0)
                except Exception as _e:
                    logger.debug(f"ped リトライ失敗 {horse_id}: {_e}")
        else:
            if _pre_ped_html is not None:
                del _pre_ped_html

        if SUPABASE_ENABLED and horse_id:
            await asyncio.to_thread(
                save_pedigree_cache,
                horse_id,
                result.get("sire", ""),
                result.get("dam", ""),
                result.get("damsire", ""),
            )
        elif horse_id and not SUPABASE_ENABLED:
            # Supabase 無効時は SQLite ローカルキャッシュに保存
            _save_pedigree_sqlite(
                horse_id,
                result.get("sire", ""),
                result.get("dam", ""),
                result.get("damsire", ""),
            )
        try:
            del soup
        except NameError:
            pass
    else:
        try:
            del soup
        except NameError:
            pass

    # ===== 過去レース結果（最新2走） =====
    try:
        if _pre_result_html is not None:
            res_soup = BeautifulSoup(_pre_result_html, "lxml", parse_only=HTML_STRAINER)
            del _pre_result_html
            race_hist_table = None
            for tbl in res_soup.find_all("table"):
                headers = [th.get_text(strip=True) for th in tbl.find_all("th")]
                if "日付" in headers and ("着順" in headers or "着" in headers):
                    race_hist_table = tbl
                    break

            if race_hist_table:
                header_rows = [r for r in race_hist_table.find_all("tr") if r.find("th")]
                if header_rows:
                    header_ths = header_rows[0].find_all("th")
                    headers = [th.get_text(strip=True) for th in header_ths]
                else:
                    headers = []
                cidx = {h: i for i, h in enumerate(headers)}
                date_i = cidx.get("日付", 0)
                venue_i = cidx.get("開催", 1)
                finish_i = cidx.get("着順", cidx.get("着", -1))
                time_i = cidx.get("タイム", -1)
                weight_i = cidx.get("馬体重", -1)
                course_i = -1
                for cname in ["距離", "コース", "芝・距離"]:
                    if cname in cidx:
                        course_i = cidx[cname]
                        break
                if course_i == -1:
                    course_i = next((cidx[h] for h in headers if "コース" in h or "距離" in h), -1)

                data_rows = [r for r in race_hist_table.find_all("tr") if r.find("td")]
                for i, row in enumerate(data_rows[:2]):
                    cols = row.find_all("td")
                    pfx = "prev" if i == 0 else "prev2"
                    try:
                        if date_i < len(cols):
                            result[f"{pfx}_race_date"] = cols[date_i].get_text(strip=True)
                        if venue_i < len(cols):
                            result[f"{pfx}_race_venue"] = cols[venue_i].get_text(strip=True)
                        if finish_i != -1 and finish_i < len(cols):
                            fin_t = cols[finish_i].get_text(strip=True)
                            if re.match(r"^\d+$", fin_t):
                                result[f"{pfx}_race_finish"] = int(fin_t)
                        if time_i != -1 and time_i < len(cols):
                            t_t = cols[time_i].get_text(strip=True)
                            tm = re.match(r"(\d+):(\d+\.\d+)", t_t)
                            if tm:
                                result[f"{pfx}_race_time"] = float(tm.group(1)) * 60 + float(tm.group(2))
                            else:
                                try:
                                    result[f"{pfx}_race_time"] = float(t_t)
                                except ValueError:
                                    pass
                        if weight_i != -1 and weight_i < len(cols):
                            w_t = cols[weight_i].get_text(strip=True)
                            w_m = re.match(r"(\d+)", w_t)
                            if w_m:
                                result[f"{pfx}_race_weight"] = int(w_m.group(1))
                        if course_i != -1 and course_i < len(cols):
                            c_t = cols[course_i].get_text(strip=True)
                            d_m = re.search(r"(\d{3,4})", c_t)
                            if d_m:
                                result[f"{pfx}_race_distance"] = int(d_m.group(1))
                            if "芝" in c_t:
                                result[f"{pfx}_race_surface"] = "芝"
                            elif "ダ" in c_t or "ダート" in c_t:
                                result[f"{pfx}_race_surface"] = "ダート"
                    except Exception:
                        pass
    except Exception as e:
        logger.debug(f"過去成績ページ取得失敗 {horse_id}: {e}")

    return result
