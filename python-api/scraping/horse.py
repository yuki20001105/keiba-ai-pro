"""
馬詳細スクレイピング: 血統・プロフィール・過去成績を取得する。
"""
from __future__ import annotations

import asyncio
import json
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
# SQLite ローカル血統キャッシュ
# ---------------------------------------------------------------------------

_PEDIGREE_DB_PATH: Path = (
    Path(__file__).parent.parent.parent / "keiba" / "data" / "pedigree_cache.db"
)
_ULTIMATE_DB_PATH: Path = (
    Path(__file__).parent.parent.parent / "keiba" / "data" / "keiba_ultimate.db"
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
        # B: プロフィールカラムを追加（マイグレーション: 既存テーブルへの後付け）
        for _col in [
            "birth_date TEXT DEFAULT ''",
            "owner TEXT DEFAULT ''",
            "breeder TEXT DEFAULT ''",
            "breeding_farm TEXT DEFAULT ''",
            "coat_color TEXT DEFAULT ''",
        ]:
            try:
                conn.execute(f"ALTER TABLE pedigree_cache ADD COLUMN {_col}")
            except Exception:
                pass  # カラム既存の場合は無視
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
    """SQLite 血統キャッシュに保存する（プロフィールなし）。_save_profile_sqlite に委譲。"""
    if not sire:
        return
    _save_profile_sqlite(horse_id, sire, dam, damsire, "", "", "", "", "")


def _get_profile_sqlite(horse_id: str) -> dict | None:
    """B: SQLite プロフィールキャッシュを検索する。birth_date が設定済みの場合のみ有効。"""
    try:
        conn = sqlite3.connect(str(_PEDIGREE_DB_PATH))
        row = conn.execute(
            "SELECT sire, dam, damsire, birth_date, owner, breeder, breeding_farm, coat_color "
            "FROM pedigree_cache WHERE horse_id = ?",
            (horse_id,),
        ).fetchone()
        conn.close()
        if row and row[3]:  # birth_date (index 3) が有効な場合のみキャッシュ利用
            return {
                "sire": row[0] or "",
                "dam": row[1] or "",
                "damsire": row[2] or "",
                "horse_birth_date": row[3],
                "horse_owner": row[4] or "",
                "horse_breeder": row[5] or "",
                "horse_breeding_farm": row[6] or "",
                "coat_color": row[7] or "",
            }
        # birth_dateなし → pedigreeのみ確認
        if row and row[0]:
            return {"sire": row[0] or "", "dam": row[1] or "", "damsire": row[2] or ""}
    except Exception:
        pass
    return None


def _save_profile_sqlite(
    horse_id: str,
    sire: str, dam: str, damsire: str,
    birth_date: str, owner: str, breeder: str, breeding_farm: str, coat_color: str,
) -> None:
    """B: SQLite プロフィールキャッシュに保存する（血統 + 静的プロフィール）。"""
    if not horse_id:
        return
    try:
        conn = sqlite3.connect(str(_PEDIGREE_DB_PATH))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """INSERT OR REPLACE INTO pedigree_cache
               (horse_id, sire, dam, damsire, birth_date, owner, breeder, breeding_farm, coat_color)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (horse_id, sire, dam, damsire, birth_date, owner, breeder, breeding_farm, coat_color),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# C: ローカルDB前走データ取得
# ---------------------------------------------------------------------------

_RACE_DB_INDEXES_ENSURED = False


def _ensure_race_db_indexes() -> None:
    """C: keiba_ultimate.db の horse_id / date 検索インデックスを作成する（初回のみ）。"""
    global _RACE_DB_INDEXES_ENSURED
    if _RACE_DB_INDEXES_ENSURED:
        return
    try:
        if not _ULTIMATE_DB_PATH.exists():
            return
        conn = sqlite3.connect(str(_ULTIMATE_DB_PATH))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_rru_horse_id
            ON race_results_ultimate (json_extract(data, '$.horse_id'))
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_races_date
            ON races_ultimate (json_extract(data, '$.date'))
        """)
        conn.commit()
        conn.close()
        _RACE_DB_INDEXES_ENSURED = True
    except Exception:
        pass  # DB未存在 or インデックス作成失敗は無視


def _get_horse_prev_races_from_db(horse_id: str, before_date: str, limit: int = 5) -> list:
    """C: race_results_ultimate から horse_id の前走データ（before_date より前）を取得する。"""
    _ensure_race_db_indexes()
    try:
        if not _ULTIMATE_DB_PATH.exists():
            return []
        conn = sqlite3.connect(str(_ULTIMATE_DB_PATH))
        rows = conn.execute(
            """
            SELECT
                json_extract(ri.data, '$.date')             AS race_date,
                json_extract(ri.data, '$.venue')            AS venue,
                json_extract(ri.data, '$.distance')         AS distance,
                json_extract(ri.data, '$.track_type')       AS track_type,
                json_extract(r.data,  '$.finish_position')  AS finish_position,
                json_extract(r.data,  '$.finish_time')      AS finish_time,
                json_extract(r.data,  '$.weight_kg')        AS weight_kg,
                json_extract(ri.data, '$.race_class')       AS race_class
            FROM race_results_ultimate r
            JOIN races_ultimate ri ON r.race_id = ri.race_id
            WHERE json_extract(r.data, '$.horse_id') = ?
              AND json_extract(ri.data, '$.date') < ?
            ORDER BY json_extract(ri.data, '$.date') DESC, r.race_id DESC
            LIMIT ?
            """,
            (horse_id, before_date, limit),
        ).fetchall()
        conn.close()
        return [
            {
                "race_date":       row[0] or "",
                "venue":           row[1] or "",
                "distance":        row[2],
                "track_type":      row[3] or "",
                "finish_position": row[4],
                "finish_time":     row[5] or "",
                "weight_kg":       row[6],
                "race_class":      row[7] or "",
            }
            for row in rows
        ]
    except Exception:
        return []


def _apply_db_prev_races(result: dict, db_prev_races: list) -> None:
    """C: DBから取得した前走データを result dict の prev_race_* フィールドに適用する。"""
    _prefixes = ["prev", "prev2", "prev3", "prev4", "prev5"]
    for i, row in enumerate(db_prev_races[:5]):
        pfx = _prefixes[i]
        result[f"{pfx}_race_date"]  = row.get("race_date") or ""
        result[f"{pfx}_race_venue"] = row.get("venue") or ""
        rc = row.get("race_class") or ""
        if rc:
            result[f"{pfx}_race_class"] = rc
        try:
            fin = row.get("finish_position")
            if fin is not None:
                result[f"{pfx}_race_finish"] = int(fin)
        except (ValueError, TypeError):
            pass
        ft = row.get("finish_time") or ""
        if ft:
            _tm = re.match(r"(\d+):(\d+\.\d+)", str(ft))
            if _tm:
                result[f"{pfx}_race_time"] = float(_tm.group(1)) * 60 + float(_tm.group(2))
            else:
                try:
                    result[f"{pfx}_race_time"] = float(ft)
                except (ValueError, TypeError):
                    pass
        try:
            wk = row.get("weight_kg")
            if wk is not None:
                result[f"{pfx}_race_weight"] = int(wk)
        except (ValueError, TypeError):
            pass
        try:
            dist = row.get("distance")
            if dist is not None:
                result[f"{pfx}_race_distance"] = int(dist)
        except (ValueError, TypeError):
            pass
        tt = row.get("track_type") or ""
        if tt:
            result[f"{pfx}_race_surface"] = tt


# テーブル起動時初期化
_init_pedigree_table()

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


def _parse_prof_table(tbl, result: dict) -> None:
    """db_prof_table から各フィールドを抽出する共通ロジック。未設定フィールドのみ更新。"""
    for row in tbl.find_all("tr"):
        th = row.find("th")
        td = row.find("td")
        if not th or not td:
            continue
        key = th.get_text(strip=True)
        val = td.get_text(strip=True)
        if "生年月日" in key and "horse_birth_date" not in result:
            result["horse_birth_date"] = val
        elif "毛色" in key and "coat_color" not in result:
            result["coat_color"] = val
        elif "馬主" in key and "horse_owner" not in result:
            result["horse_owner"] = val
        elif "生産者" in key and "horse_breeder" not in result:
            result["horse_breeder"] = val
        elif "産地" in key and "horse_breeding_farm" not in result:
            result["horse_breeding_farm"] = val
        elif "通算成績" in key and "horse_total_runs" not in result:
            runs_m = re.search(r"(\d+)戦\s*(\d+)勝", val)
            if runs_m:
                result["horse_total_runs"] = int(runs_m.group(1))
                result["horse_total_wins"] = int(runs_m.group(2))
        elif "獲得賞金" in key and "中央" in key and "horse_total_prize_money" not in result:
            prize_m = re.search(r"([\d,]+)", val)
            if prize_m:
                try:
                    result["horse_total_prize_money"] = float(prize_m.group(1).replace(",", "")) * 10000
                except ValueError:
                    pass


# ---------------------------------------------------------------------------
# 馬詳細スクレイピング（メイン関数）
# ---------------------------------------------------------------------------


def _parse_hist_date(s: str) -> str:
    """馬歴テーブルの日付セルを YYYYMMDD 形式に変換。変換失敗は空文字列を返す。

    対応フォーマット: "2025年5月26日" / "2025/05/26" / "2025-05-26"
    """
    m = re.search(r"(\d{4})[/年\-](\d{1,2})[/月\-](\d{1,2})", s)
    return f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}" if m else ""


async def scrape_horse_detail(
    session,
    horse_id: str,
    horse_url: str = "",
    pedigree_cache: dict = None,
    quick_mode: bool = False,
    before_date: str | None = None,
) -> dict:
    """
    馬の詳細ページをスクレイピング。
    血統(sire/dam/damsire)、プロフィール、通算成績、直近5走を取得。
    pedigree_cache: {horse_id: {sire,dam,damsire}} のバッチ取得済みキャッシュ（あれば Supabase 個別クエリ省略）
    quick_mode: True=毛色SPフォールバックをスキップして高速化
    before_date: YYYYMMDD または YYYY-MM-DD。指定時はその日付より前の走のみ prev1-5 に使用。
                 歴史レース再スクレイプ時の未来データ混入を防ぐ（INV-01）。
    """
    if not horse_id and not horse_url:
        return {}

    # ── 地方馬（B プレフィックス）
    if horse_id and re.match(r"^B", str(horse_id)):
        _nar_result: dict = {}

        # 1) SQLite 血統キャッシュ確認
        cached_b = (pedigree_cache or {}).get(horse_id) if pedigree_cache is not None else None
        if cached_b is None:
            cached_b = _get_profile_sqlite(horse_id)
        if cached_b and cached_b.get("sire") and cached_b["sire"] not in ("", "unknown_local"):
            logger.debug(f"NAR馬 血統キャッシュヒット: {horse_id} sire={cached_b['sire']}")
            _nar_result = {k: cached_b.get(k, "") for k in ("sire", "dam", "damsire", "horse_birth_date")}
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

        # 3) sp.netkeiba で生年月日を補完（birth_date がキャッシュにない場合のみ）
        _sp_html_parsed = None
        if not quick_mode and not _nar_result.get("horse_birth_date"):
            try:
                await asyncio.sleep(1.0)
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
    # /horse/result/{id}/ → 過去レース履歴 (C: ローカルDBで代替可能)
    # /horse/ped/{id}/    → 血統 (A/B: SQLiteキャッシュで省略可能)
    # /horse/{id}/        → プロフィール (B: SQLiteキャッシュで省略可能)
    _result_url = f"https://db.netkeiba.com/horse/result/{horse_id}/"
    result = {}

    async def _safe_get_horse(u: str):
        try:
            async with session.get(u) as r:
                if r.status != 200:
                    return None
                return (await r.read()).decode("euc-jp", errors="replace")
        except Exception:
            return None

    async def _noop_fetch():
        return None

    # ─── A+B: キャッシュ確認 ──────────────────────────────────────────────
    # B: フルプロファイルキャッシュ（birth_date が有効な場合）
    _profile_cache = _get_profile_sqlite(horse_id) if horse_id else None
    _has_ped_cache  = bool(_profile_cache and _profile_cache.get("sire"))
    _has_prof_cache = bool(_profile_cache and _profile_cache.get("horse_birth_date"))

    # A: プロファイルキャッシュにsireがない場合、血統のみ別途確認
    _legacy_ped = None
    if not _has_ped_cache:
        _legacy_ped = (pedigree_cache or {}).get(horse_id)
        if not _legacy_ped or not _legacy_ped.get("sire"):
            _legacy_ped = _get_profile_sqlite(horse_id)  # A: SQLiteキャッシュ確認
        _has_ped_cache = bool(_legacy_ped and _legacy_ped.get("sire"))

    # C: before_dateがある場合、ローカルDBから前走データを先行取得
    _db_prev_races: list = []
    if before_date and horse_id:
        _bd_norm = before_date.replace("-", "")[:8]
        _db_prev_races = await asyncio.to_thread(
            _get_horse_prev_races_from_db, horse_id, _bd_norm
        )

    # プロファイルキャッシュ済み + DB前走データあり → result ページ不要
    _skip_result_fetch = bool(_has_prof_cache and _db_prev_races)

    # HTTP リクエストが発生するかどうかを示すフラグ（呼び出し元がスリープ省略に利用）
    # 全キャッシュヒット = _skip_result_fetch=True かつ _has_ped_cache=True の場合のみ False
    result["_http_made"] = not (_skip_result_fetch and _has_ped_cache)

    # ─── 並列フェッチ（キャッシュ済みなら省略）──────────────────────────
    html, _pre_ped_html, _pre_prof_html = await asyncio.gather(
        _noop_fetch() if _skip_result_fetch else _safe_get_horse(_result_url),
        _noop_fetch() if _has_ped_cache else _safe_get_horse(f"https://db.netkeiba.com/horse/ped/{horse_id}/"),
        _noop_fetch() if _has_prof_cache else _safe_get_horse(f"https://db.netkeiba.com/horse/{horse_id}/"),
    )

    # ─── 全キャッシュヒット（B+C）: HTML解析不要 ─────────────────────────
    if _skip_result_fetch:
        for _k in ("coat_color", "horse_birth_date", "horse_owner", "horse_breeder",
                   "horse_breeding_farm", "sire", "dam", "damsire"):
            if _profile_cache.get(_k):
                result[_k] = _profile_cache[_k]
        _apply_db_prev_races(result, _db_prev_races)
        logger.debug(f"馬詳細: 全キャッシュヒット（HTTPスキップ）: {horse_id}")
        return result

    if html is None:
        logger.debug(f"馬詳細取得失敗 {horse_id}")
        # result ページ失敗でも、並列取得済みの ped/profile ページから血統・プロフィールを回収する
        _fallback_saved = False
        if _pre_ped_html:
            _ped_soup_fb = BeautifulSoup(_pre_ped_html, "lxml", parse_only=HTML_STRAINER)
            _blood_table_fb = _ped_soup_fb.find("table", class_="blood_table")
            if _blood_table_fb:
                _parse_blood_table(_blood_table_fb, result)
                if result.get("sire"):
                    logger.debug(f"ped 血統回収(result失敗フォールバック): {horse_id} sire={result['sire']}")
                    _fallback_saved = True
        if _pre_prof_html:
            _prof_soup_fb = BeautifulSoup(_pre_prof_html, "lxml", parse_only=HTML_STRAINER)
            _prof_table_fb = _prof_soup_fb.find("table", attrs={"class": re.compile(r"db_prof_table")})
            if _prof_table_fb:
                _parse_prof_table(_prof_table_fb, result)
                _fallback_saved = True
        # 取得できたデータを pedigree_cache に保存（次回は HTTP スキップ可能）
        if _fallback_saved:
            _save_profile_sqlite(
                horse_id,
                result.get("sire", ""), result.get("dam", ""), result.get("damsire", ""),
                result.get("horse_birth_date", ""), result.get("horse_owner", ""),
                result.get("horse_breeder", ""), result.get("horse_breeding_farm", ""),
                result.get("coat_color", ""),
            )
        return result

    # B: プロファイルキャッシュをプリロード（/horse/{id}/ のフェッチは不要）
    if _has_prof_cache and _profile_cache:
        for _k in ("coat_color", "horse_birth_date", "horse_owner", "horse_breeder",
                   "horse_breeding_farm"):
            if _profile_cache.get(_k):
                result[_k] = _profile_cache[_k]

    # /horse/result/{id}/ から過去レース履歴を取得
    _pre_result_html = html

    soup = BeautifulSoup(html, "lxml", parse_only=HTML_STRAINER)
    del html

    # ===== 毛色 =====
    if not result.get("coat_color"):
        _coat = extract_coat_color(soup, _pre_result_html)
        if _coat:
            result["coat_color"] = _coat

    # ===== プロフィール（db_prof_table から取得）=====
    prof_table = None if _has_prof_cache else soup.find("table", attrs={"class": re.compile(r"db_prof_table")})
    if not _has_prof_cache:
        if prof_table:
            _parse_prof_table(prof_table, result)
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

    # ===== プロフィールページ（/horse/{id}/）から補完 =====
    # /horse/result/{id}/ には db_prof_table がないため、旧プロフィールページを使用
    if _pre_prof_html and not result.get("horse_birth_date"):
        _prof_soup = BeautifulSoup(_pre_prof_html, "lxml", parse_only=HTML_STRAINER)
        _prof_table = _prof_soup.find("table", attrs={"class": re.compile(r"db_prof_table")})
        if _prof_table:
            _parse_prof_table(_prof_table, result)
        # coat_color が still 未取得なら extract_coat_color でも試みる
        if not result.get("coat_color"):
            _coat_from_prof = extract_coat_color(_prof_soup, _pre_prof_html)
            if _coat_from_prof:
                result["coat_color"] = _coat_from_prof
        del _prof_soup
    del _pre_prof_html  # type: ignore[name-defined]

    # ===== 血統 (sire / dam / damsire) =====
    pedigree_cached = False

    if _has_ped_cache:
        # A/B: キャッシュから適用
        _src = _profile_cache if (_profile_cache and _profile_cache.get("sire")) else _legacy_ped
        if _src:
            result["sire"] = _src.get("sire") or ""
            result["dam"] = _src.get("dam") or ""
            result["damsire"] = _src.get("damsire") or ""
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
                    await asyncio.sleep(1.0)
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

        try:
            del soup
        except NameError:
            pass
    else:
        try:
            del soup
        except NameError:
            pass

    # ===== 過去レース結果（最新5走） =====
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
                # before_date 指定時：その日付より前のレースのみ使用（未来データ混入防止 INV-01）
                if before_date:
                    _bd_norm = before_date.replace("-", "")[:8]  # "2025-05-26" → "20250526"
                    _filtered: list = []
                    for _row in data_rows:
                        _cols = _row.find_all("td")
                        _d = _parse_hist_date(
                            _cols[date_i].get_text(strip=True) if date_i < len(_cols) else ""
                        )
                        if not _d or _d < _bd_norm:  # 日付未取得行は安全側でスキップ
                            if _d:  # 日付が取れて < before_date の場合のみ採用
                                _filtered.append(_row)
                        # _d が空の行は含めない（不完全データ）
                    data_rows = _filtered
                # prev/prev2/prev3/prev4/prev5 の5走分を取得
                _prefixes = ["prev", "prev2", "prev3", "prev4", "prev5"]
                for i, row in enumerate(data_rows[:5]):
                    cols = row.find_all("td")
                    pfx = _prefixes[i]
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
        elif _db_prev_races:
            # C: ローカルDBの前走データを適用（HTTPフェッチ不要）
            _apply_db_prev_races(result, _db_prev_races)
    except Exception as e:
        logger.debug(f"過去成績取得失敗 {horse_id}: {e}")

    # B: プロファイルキャッシュ保存（新規取得時のみ）
    if not _has_prof_cache and horse_id and result.get("horse_birth_date"):
        _save_profile_sqlite(
            horse_id,
            result.get("sire", ""), result.get("dam", ""), result.get("damsire", ""),
            result.get("horse_birth_date", ""), result.get("horse_owner", ""),
            result.get("horse_breeder", ""), result.get("horse_breeding_farm", ""),
            result.get("coat_color", ""),
        )
    elif not _has_ped_cache and horse_id and result.get("sire"):
        # sireは取得できたがprofileは不完全な場合、血統のみキャッシュ
        _save_pedigree_sqlite(
            horse_id,
            result.get("sire", ""),
            result.get("dam", ""),
            result.get("damsire", ""),
        )

    return result
