"""
馬詳細スクレイピング: 血統・プロフィール・過去成績を取得する。
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup, SoupStrainer
from lxml import etree

from scraping.constants import HTML_STRAINER, COAT_COLORS, COAT_RE
from scraping.fetch_pipeline import FetchAccessBlocked, record_fetch_metric
from scraping.parsed_cache import (
    cached_sqlite_connection, collect_sources, fetch_source_text as fetch_text, get_parsed, parser_version,
    prefer_mobile_endpoint, put_parsed, remember_mobile_endpoint,
)
from scraping.saved_horse_reuse import (
    compatible_saved_pedigree, load_saved_horse_evidence, saved_horse_reuse_enabled,
)

_PARSER_VERSION = parser_version(__file__)
_HTML_PARSERS = ThreadPoolExecutor(max_workers=2, thread_name_prefix="horse-html")
_HORSE_TABLES = SoupStrainer("table")
_HORSE_TEXT = etree.XPath(
    "//text()[not(ancestor::script) and not(ancestor::style) and not(ancestor::template)]",
    smart_strings=False,
)
_HORSE_CELL_TEXT = etree.XPath(
    ".//text()[not(ancestor::script) and not(ancestor::style) and not(ancestor::template)]",
    smart_strings=False,
)

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
# SQLite ローカル血統キャッシュ
# ---------------------------------------------------------------------------

_PEDIGREE_DB_PATH: Path = (
    Path(__file__).parent.parent.parent / "keiba" / "data" / "pedigree_cache.db"
)
_PEDIGREE_SCHEMA_CONNECTION: sqlite3.Connection | None = None


def _ensure_pedigree_schema(conn: sqlite3.Connection) -> None:
    global _PEDIGREE_SCHEMA_CONNECTION
    if _PEDIGREE_SCHEMA_CONNECTION is not conn:
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
        _PEDIGREE_SCHEMA_CONNECTION = conn


def _init_pedigree_table() -> None:
    """血統キャッシュテーブルを初期化する（なければ作成）"""
    try:
        with cached_sqlite_connection(_PEDIGREE_DB_PATH, writable=True) as conn:
            _ensure_pedigree_schema(conn)
    except Exception:
        pass  # DB作成失敗は無視（ログスパム防止）


def _get_pedigree_sqlite(horse_id: str) -> dict | None:
    """SQLite 血統キャッシュを検索する。"""
    try:
        with cached_sqlite_connection(_PEDIGREE_DB_PATH) as conn:
            if conn is None:
                return None
            row = conn.execute(
                "SELECT sire, dam, damsire FROM pedigree_cache WHERE horse_id = ?", (horse_id,)
            ).fetchone()
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
        with cached_sqlite_connection(_PEDIGREE_DB_PATH, writable=True) as conn:
            _ensure_pedigree_schema(conn)
            conn.execute(
                """INSERT OR REPLACE INTO pedigree_cache (horse_id, sire, dam, damsire)
                   VALUES (?, ?, ?, ?)""",
                (horse_id, sire, dam, damsire),
            )
            conn.commit()
    except Exception:
        pass


# Reading/importing this module does not create cache storage. The first
# successful pedigree save (or an explicit initializer) creates the table.

# ---------------------------------------------------------------------------
# Supabase ヘルパー（オプション依存）
# ---------------------------------------------------------------------------
try:
    from app_config import logger  # type: ignore
except ImportError:
    import logging
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


def _find_pedigree_table(soup: "BeautifulSoup"):
    table = soup.find("table", class_="blood_table")
    if table is not None:
        return table
    # Mobile pedigree pages omit the desktop class but retain the canonical
    # 32-row five-generation layout (5 cells in rows 0 and 16).
    for candidate in soup.find_all("table"):
        rows = candidate.find_all("tr")
        if len(rows) >= 32:
            first_cells = rows[0].find_all("td")
            middle_cells = rows[len(rows) // 2].find_all("td")
            if len(first_cells) >= 5 and len(middle_cells) >= 5:
                return candidate
    return None


# ---------------------------------------------------------------------------
# 馬詳細スクレイピング（メイン関数）
# ---------------------------------------------------------------------------

def _horse_source_soup(html: str) -> tuple[BeautifulSoup, str]:
    """Build only the table tree consumed by the horse parser.

    Keep the original page's non-script text for the outside-table runs/prize
    fallbacks. All profile/blood tables and header-bearing rows survive; only
    unused third-and-later history data rows are omitted. The public coat-color
    helper and its original raw-HTML input are unchanged.
    """
    try:
        document = etree.HTML(html, parser=etree.HTMLParser(no_network=True))
        if document is None:
            raise ValueError("empty HTML document")
        full_text = "".join(_HORSE_TEXT(document))
        for table in document.iter("table"):
            headers = ["".join(value.strip() for value in _HORSE_CELL_TEXT(th))
                       for th in table.iter("th")]
            if "日付" not in headers or not ("着順" in headers or "着" in headers):
                continue
            # Nested tables and a combined blood/history table keep their
            # original shape because table-relative positions are significant.
            if "blood_table" in (table.get("class") or "").split() or table.findall(".//table"):
                break
            rows = list(table.iter("tr"))
            data_rows = [row for row in rows if row.find(".//td") is not None]
            keep = set(data_rows[:2])
            keep.update(row for row in rows if row.find(".//th") is not None)
            for row in rows:
                if row not in keep:
                    row.getparent().remove(row)
            break
        table_html = "".join(
            etree.tostring(table, method="html", encoding="unicode", with_tail=False)
            for table in document.xpath("//table[not(ancestor::table)]")
        )
        return BeautifulSoup(table_html, "lxml", parse_only=_HORSE_TABLES), full_text
    except (etree.LxmlError, ValueError, TypeError):
        # An unsupported/malformed document takes the original parser path.
        soup = BeautifulSoup(html, "lxml", parse_only=HTML_STRAINER)
        return soup, soup.get_text()


def _parse_horse_source_html(html: str, horse_id: str) -> dict:
    """Pure HTML extraction, independent of pedigree requests and completion.

    An empty extraction is a parse result, never a successful horse acquisition.
    Missing pedigree is still fetched by the caller on every attempt.
    """
    soup, page_text = _horse_source_soup(html)
    result: dict = {}
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
        full_text = page_text
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

    blood: dict = {}
    blood_table = soup.find("table", class_="blood_table")
    if blood_table:
        _parse_blood_table(blood_table, blood)
    # ===== 過去レース結果（最新2走） =====
    try:
        if soup is not None:
            res_soup = soup
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

    return {"fields": result, "blood": blood}


def _complete_detail(result: dict) -> bool:
    return all(result.get(field) not in (None, "", "unknown_local") for field in (
        "sire", "dam", "damsire", "prev_race_date", "prev_race_finish",
        "prev_race_time", "prev_race_distance",
    ))


async def scrape_horse_detail(
    session,
    horse_id: str,
    horse_url: str = "",
    pedigree_cache: dict = None,
    quick_mode: bool = False,
) -> dict:
    """Reuse a complete parse only while every source snapshot is still valid."""
    if not horse_id and not horse_url:
        return {}
    # Scope is supplied only by the historical acquisition worker. This
    # read-only evidence never changes the live prediction path and does not
    # replace the provider's current profile or its original history fields.
    saved = None
    if saved_horse_reuse_enabled():
        started = time.perf_counter()
        saved = await asyncio.to_thread(load_saved_horse_evidence, str(horse_id))
        record_fetch_metric("local_horse_evidence_elapsed_ms", round((time.perf_counter() - started) * 1000))
        record_fetch_metric("local_horse_evidence_hits" if saved else "local_horse_evidence_misses")
    canonical_url = (
        horse_url.rstrip("/") + "/"
        if horse_url.startswith("http") and "/horse/result/" in horse_url
        else f"https://db.netkeiba.com/horse/result/{horse_id}/"
    )
    current_pedigree = (pedigree_cache or {}).get(horse_id)
    if current_pedigree is None:
        current_pedigree = _get_pedigree_sqlite(horse_id)
    saved_pedigree = compatible_saved_pedigree(saved, current_pedigree)
    if saved_pedigree is not None:
        pedigree_cache = {**(pedigree_cache or {}), horse_id: saved_pedigree}
        current_pedigree = saved_pedigree
    base_key = [str(horse_id), canonical_url, bool(quick_mode), not horse_url.startswith("http")]
    # Provider-only fields are date independent; their HTTP snapshots and the
    # current dedicated pedigree cache already validate this original key.
    # A local-pedigree injected payload must remain isolated, including after
    # source edits make that pedigree ineligible on a subsequent attempt.
    key = json.dumps(base_key + ([saved.cache_token] if saved_pedigree is not None else []))
    cached = get_parsed("horse-detail", key, _PARSER_VERSION)
    if cached is not None and all(
        cached.get(field, "") == (current_pedigree or {}).get(field, "")
        for field in ("sire", "dam", "damsire")
        if current_pedigree is not None
    ):
        return _attach_saved_evidence(cached, saved, saved_pedigree)
    with collect_sources() as sources:
        result = await _scrape_horse_detail_uncached(
            session, horse_id, horse_url, pedigree_cache, quick_mode,
        )
    # Partial/profile-only responses, debutants without a history table and
    # failed enrichment remain retryable. Preserve every returned field.
    if _complete_detail(result):
        put_parsed("horse-detail", key, _PARSER_VERSION, result, sources,
                   max_age_sec=6 * 60 * 60)
    return _attach_saved_evidence(result, saved, saved_pedigree)


def _attach_saved_evidence(result: dict, saved, saved_pedigree: dict | None) -> dict:
    if saved is not None:
        # Reserved audit metadata is not a feature column. Original provider
        # fields are preserved even when the strict-past evidence differs.
        metadata = json.loads(json.dumps(saved.metadata))
        reused = saved_pedigree is not None and all(
            result.get(field) == saved_pedigree[field] for field in ("sire", "dam", "damsire")
        )
        metadata["pedigree"]["reused"] = reused
        result["_acquisition_reuse"] = metadata
        record_fetch_metric("local_history_evidence_generated")
        if metadata["history"]["coverage_complete"]:
            record_fetch_metric("local_history_date_coverage_complete")
        if reused:
            # This counts candidates consumed, NOT proven HTTP requests saved.
            record_fetch_metric("saved_pedigree_candidates_used")
        elif saved.pedigree is None:
            record_fetch_metric("saved_pedigree_candidates_rejected")
    return result


async def _scrape_horse_detail_uncached(
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

        # 1) SQLite 血統キャッシュ確認
        cached_b = (pedigree_cache or {}).get(horse_id) if pedigree_cache is not None else None
        if cached_b is None:
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
                    _ped_fetch, ped_html_b = await fetch_text(
                        session,
                        ped_url_b,
                        cache_ttl_sec=30 * 24 * 60 * 60,
                        resume_key=f"horse:{horse_id}:ped-nar",
                        min_interval_sec=1.0,
                        max_retries=3,
                        retry_statuses={429, 500, 503},
                        retry_base_sec=2.0,
                        retry_jitter_sec=0.6,
                        circuit_threshold=3,
                        circuit_cooldown_sec=120.0,
                    )
                    if _ped_fetch.status == 200:
                        ped_soup_b = BeautifulSoup(ped_html_b, "lxml", parse_only=HTML_STRAINER)
                        blood_table_b = ped_soup_b.find("table", class_="blood_table")
                        if blood_table_b:
                            _parse_blood_table(blood_table_b, pedigree_result_b)
                        if pedigree_result_b.get("sire"):
                            logger.info(f"NAR馬 /ped/ 血統取得成功: {horse_id} sire={pedigree_result_b['sire']}")
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
                    elif _ped_fetch.status == 429:
                        await asyncio.sleep(5.0 + attempt * 3.0)
                        continue
                    else:
                        logger.debug(f"NAR馬 /ped/ HTTP {_ped_fetch.status}: {horse_id}")
                        break
                except FetchAccessBlocked:
                    raise
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
                _sp_fetch, _sp_text = await fetch_text(
                    session,
                    f"https://db.sp.netkeiba.com/horse/{horse_id}/",
                    cache_ttl_sec=7 * 24 * 60 * 60,
                    resume_key=f"horse:{horse_id}:sp",
                    min_interval_sec=1.0,
                    max_retries=2,
                    retry_statuses={429, 500, 503},
                    retry_base_sec=2.0,
                    retry_jitter_sec=0.6,
                    circuit_threshold=3,
                    circuit_cooldown_sec=120.0,
                )
                if _sp_fetch.status == 200:
                    _sp_html_parsed = BeautifulSoup(_sp_text, "lxml", parse_only=HTML_STRAINER)
            except FetchAccessBlocked:
                raise
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
    # 2025/8以降: netkeiba の馬詳細は /horse/{id}/ から /horse/result/{id}/ へ移行
    # horse_url が渡された場合はそのURLを使用し、/horse/result/{id}/ 方式にも対応
    if horse_url.startswith("http") and "/horse/result/" in horse_url:
        url = horse_url if horse_url.endswith("/") else horse_url + "/"
    else:
        # Shutuba links point at /horse/{id}/, whose current page does not
        # consistently expose the historical-results table. Use the canonical
        # result endpoint even when an absolute main-profile URL was supplied.
        url = f"https://db.netkeiba.com/horse/result/{horse_id}/"
    result = {}
    source_unavailable = False
    source_url = url
    source_snapshot = None
    endpoint_candidates: set[str] = set()

    async def _safe_get_horse(u: str):
        nonlocal source_unavailable, source_url, source_snapshot
        endpoint_kind = "horse-result" if "/horse/result/" in u else "horse-profile"
        preferred_mobile = "db.netkeiba.com" in u and prefer_mobile_endpoint(endpoint_kind)
        request_url = u.replace("db.netkeiba.com", "db.sp.netkeiba.com", 1) if preferred_mobile else u
        try:
            _fetch, text = await fetch_text(
                session,
                request_url,
                cache_ttl_sec=7 * 24 * 60 * 60,
                resume_key=f"horse:{horse_id}:{u}",
                min_interval_sec=1.0,
                max_retries=3,
                retry_statuses={429, 500, 503},
                retry_base_sec=2.0,
                retry_jitter_sec=0.6,
                circuit_threshold=3,
                circuit_cooldown_sec=120.0,
            )
            if preferred_mobile and _fetch.status in (400, 404):
                remember_mobile_endpoint(endpoint_kind, valid=False)
                # The preferred endpoint ceased working; try the original
                # path once. Restriction errors are raised by fetch_text.
                _fetch, text = await fetch_text(
                    session, u, cache_ttl_sec=7 * 24 * 60 * 60,
                    resume_key=f"horse:{horse_id}:{u}", min_interval_sec=1.0,
                    max_retries=3, retry_statuses={429, 500, 503},
                    retry_base_sec=2.0, retry_jitter_sec=0.6,
                    circuit_threshold=3, circuit_cooldown_sec=120.0,
                )
            if _fetch.status == 400 and "db.netkeiba.com" in u:
                mobile_url = u.replace("db.netkeiba.com", "db.sp.netkeiba.com", 1)
                _fetch, text = await fetch_text(
                    session,
                    mobile_url,
                    cache_ttl_sec=7 * 24 * 60 * 60,
                    resume_key=f"horse:{horse_id}:{mobile_url}",
                    min_interval_sec=1.0,
                    max_retries=3,
                    retry_statuses={429, 500, 503},
                    retry_base_sec=2.0,
                    retry_jitter_sec=0.6,
                    circuit_threshold=3,
                    circuit_cooldown_sec=120.0,
                )
                # Record only a candidate here. It is promoted after the
                # complete horse result has successfully parsed below.
                if _fetch.status == 200:
                    endpoint_candidates.add(endpoint_kind)
            if _fetch.status != 200:
                source_unavailable = _fetch.status in (401, 403, 429)
                return None
            source_url = _fetch.normalized_url
            snapshot = getattr(_fetch, "cache_snapshot", None)
            source_snapshot = dict(snapshot) if snapshot is not None else None
            return text
        except FetchAccessBlocked:
            raise
        except Exception:
            return None

    _cached_ped = (pedigree_cache or {}).get(horse_id)
    if _cached_ped is None:
        _cached_ped = _get_pedigree_sqlite(horse_id)
    _has_ped_cache = bool(_cached_ped and _cached_ped.get("sire"))

    # Fetch the result page first. Concurrent requests for the result and
    # pedigree pages share one host-level rate limiter and intermittently let
    # only the pedigree request succeed, silently dropping every prior-race
    # field. Pedigree is loaded from cache or by the existing fallback below.
    html = await _safe_get_horse(url)
    _pre_ped_html = None

    # フォールバック: /horse/result/{id}/ が失敗した場合は旧 /horse/{id}/ を試みる
    if html is None and not source_unavailable and not horse_url.startswith("http"):
        _old_url = f"https://db.netkeiba.com/horse/{horse_id}/"
        logger.debug(f"新URL失敗 → 旧URLでフォールバック: {horse_id}")
        html = await _safe_get_horse(_old_url)

    if html is None:
        logger.debug(f"馬詳細取得失敗 {horse_id}")
        return result

    expected_sources = {source_snapshot["url"]: source_snapshot} if source_snapshot else {}
    parsed_component = get_parsed(
        "horse-html", source_url, _PARSER_VERSION, expected_sources=expected_sources,
    ) if source_snapshot else None
    if parsed_component is None:
        # Keep at most two HTML parsers active while the shared HTTP worker can
        # continue. Parsing is pure and does not share mutable race dictionaries.
        parsed_component = await asyncio.get_running_loop().run_in_executor(
            _HTML_PARSERS, _parse_horse_source_html, html, horse_id,
        )
        if source_snapshot is not None:
            put_parsed("horse-html", source_url, _PARSER_VERSION, parsed_component,
                       expected_sources, max_age_sec=7 * 24 * 60 * 60)
    result.update(parsed_component["fields"])
    del html

    # ===== 血統 (sire / dam / damsire) =====
    pedigree_cached = False

    if horse_id:
        cached = _cached_ped
        if cached:
            result["sire"] = cached.get("sire") or ""
            result["dam"] = cached.get("dam") or ""
            result["damsire"] = cached.get("damsire") or ""
            pedigree_cached = True
            logger.debug(f"血統キャッシュヒット: {horse_id} sire={result['sire']}")

    if not pedigree_cached:
        if parsed_component["blood"]:
            result.update(parsed_component["blood"])
            logger.debug(f"メインページ血統: {horse_id} sire={result.get('sire')}")

        if not result.get("sire"):
            if _pre_ped_html:
                ped_soup = BeautifulSoup(_pre_ped_html, "lxml", parse_only=HTML_STRAINER)
                del _pre_ped_html
                blood_table = _find_pedigree_table(ped_soup)
                if blood_table:
                    _parse_blood_table(blood_table, result)
                    if result.get("sire"):
                        logger.debug(f"ped 血統取得成功(並列): {horse_id} sire={result['sire']}")
            if not result.get("sire"):
                ped_url = f"https://db.netkeiba.com/horse/ped/{horse_id}/"
                try:
                    _ped_fetch2, ped_html_retry = await fetch_text(
                        session,
                        ped_url,
                        cache_ttl_sec=30 * 24 * 60 * 60,
                        resume_key=f"horse:{horse_id}:ped-jra",
                        min_interval_sec=1.0,
                        max_retries=3,
                        retry_statuses={429, 500, 503},
                        retry_base_sec=2.0,
                        retry_jitter_sec=0.6,
                        circuit_threshold=3,
                        circuit_cooldown_sec=120.0,
                    )
                    if _ped_fetch2.status == 200:
                        ped_soup = BeautifulSoup(ped_html_retry, "lxml", parse_only=HTML_STRAINER)
                        blood_table = _find_pedigree_table(ped_soup)
                        if blood_table:
                            _parse_blood_table(blood_table, result)
                            logger.debug(f"ped リトライ成功: {horse_id} sire={result.get('sire')}")
                    elif _ped_fetch2.status == 429:
                        await asyncio.sleep(8.0)
                except FetchAccessBlocked:
                    raise
                except Exception as _e:
                    logger.debug(f"ped リトライ失敗 {horse_id}: {_e}")
        else:
            if _pre_ped_html is not None:
                del _pre_ped_html

        if horse_id:
            _save_pedigree_sqlite(
                horse_id,
                result.get("sire", ""),
                result.get("dam", ""),
                result.get("damsire", ""),
            )

    if _complete_detail(result):
        for endpoint_kind in endpoint_candidates:
            remember_mobile_endpoint(endpoint_kind)
    return result
