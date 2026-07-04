"""
出馬表プリフェッチモジュール

当日（または指定日）の全出馬表＋馬詳細をDBへ事前保存する。
analyze_race は races_ultimate を先に確認するため、
このモジュールで事前保存しておけばオンデマンドスクレイプが不要になる。

主な流れ:
  1. race_list_sub.html から当日レースIDを取得
  2. races_ultimate に未登録のレースのみ対象にする
  3. ログイン済みセッションで _scrape_shutuba_fallback を直接呼び出す
     （scrape_race_full より高速: 結果ページの無駄な fetch をスキップ）
  4. 結果を races_ultimate / race_results_ultimate に保存
"""
from __future__ import annotations

import asyncio
import re
import sqlite3
import logging
from typing import TypedDict

logger = logging.getLogger(__name__)


class PrefetchResult(TypedDict):
    date: str
    races_total: int
    races_already_cached: int
    races_fetched: int
    races_failed: int
    race_ids_fetched: list[str]
    race_ids_failed: list[str]


async def get_race_ids_for_date(date_str: str) -> list[str]:
    """
    指定日のレースID一覧を取得する。

    優先順: race_list_sub (当日・未来対応) → db.netkeiba race/list (結果確定分)
    """
    import httpx
    from bs4 import BeautifulSoup

    race_ids: list[str] = []

    # ① race.netkeiba.com/top/race_list_sub.html (当日・未来レース対応)
    sub_url = f"https://race.netkeiba.com/top/race_list_sub.html?kaisai_date={date_str}"
    try:
        async with httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        ) as hx:
            resp = await hx.get(sub_url)
        if resp.status_code == 200:
            html = resp.content.decode("euc-jp", errors="replace")
            soup = BeautifulSoup(html, "lxml")
            seen: set[str] = set()
            for a in soup.find_all("a", href=True):
                m = re.search(r"race_id=(\d{12})", a["href"])
                if m and m.group(1) not in seen:
                    seen.add(m.group(1))
                    race_ids.append(m.group(1))
            if race_ids:
                logger.info(f"[prefetch] {date_str}: race_list_sub から {len(race_ids)} レースID取得")
                return race_ids
        else:
            logger.warning(f"[prefetch] race_list_sub HTTP {resp.status_code}: {date_str}")
    except Exception as e:
        logger.warning(f"[prefetch] race_list_sub 取得失敗 {date_str}: {e}")

    # ② フォールバック: db.netkeiba.com/race/list/{date}/ (確定済みレースのみ)
    db_url = f"https://db.netkeiba.com/race/list/{date_str}/"
    try:
        async with httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        ) as hx:
            resp2 = await hx.get(db_url)
        if resp2.status_code == 200:
            html2 = resp2.content.decode("euc-jp", errors="replace")
            race_ids = list(dict.fromkeys(re.findall(r"/race/(\d{12})/", html2)))
            if race_ids:
                logger.info(f"[prefetch] {date_str}: db.netkeiba から {len(race_ids)} レースID取得")
    except Exception as e:
        logger.warning(f"[prefetch] db.netkeiba 取得失敗 {date_str}: {e}")

    return race_ids


def _get_cached_race_ids(race_ids: list[str], db_path: str) -> set[str]:
    """races_ultimate に既にキャッシュされているrace_idを返す。"""
    if not race_ids:
        return set()
    try:
        conn = sqlite3.connect(db_path)
        placeholders = ",".join("?" * len(race_ids))
        cur = conn.execute(
            f"SELECT race_id FROM races_ultimate WHERE race_id IN ({placeholders})",
            race_ids,
        )
        result = {row[0] for row in cur.fetchall()}
        conn.close()
        return result
    except Exception as e:
        logger.warning(f"[prefetch] DB確認失敗: {e}")
        return set()


def _has_horse_details(race_id: str, db_path: str) -> bool:
    """races_ultimate のエントリに馬詳細（sire）が含まれているか確認する。
    血統なし（シュツバ取得済みだが馬詳細未取得）の場合は再フェッチ対象にする。
    """
    try:
        import json as _json
        conn = sqlite3.connect(db_path)
        cur = conn.execute("SELECT data FROM races_ultimate WHERE race_id = ?", (race_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return False
        data = _json.loads(row[0])
        # race_info には horses がない。horse詳細は race_results_ultimate に入る
        # race_results_ultimate の 1行でも sire があれば詳細取得済みとみなす
        conn2 = sqlite3.connect(db_path)
        cur2 = conn2.execute(
            "SELECT data FROM race_results_ultimate WHERE race_id = ? LIMIT 5",
            (race_id,),
        )
        rows2 = cur2.fetchall()
        conn2.close()
        for r in rows2:
            h = _json.loads(r[0])
            if h.get("sire"):
                return True
        return False
    except Exception:
        return False


async def prefetch_shutuba_for_date(
    date_str: str,
    db_path: str,
    *,
    force: bool = False,
    skip_login: bool = False,
) -> PrefetchResult:
    """
    指定日の全出馬表＋馬詳細をプリフェッチしてDBに保存する。

    Args:
        date_str: YYYYMMDD 形式の日付
        db_path: keiba_ultimate.db のパス (str)
        force: True にすると既キャッシュのレースも再取得する
        skip_login: True にするとログインをスキップ（テスト用）
    """
    from scraping.race import _scrape_shutuba_fallback  # type: ignore
    from scraping.storage import _save_race_to_ultimate_db  # type: ignore
    from scraping.constants import login_netkeiba, warm_up_netkeiba, jitter_sleep  # type: ignore
    from scraping.jobs import _new_session  # type: ignore
    from app_config import ULTIMATE_DB  # type: ignore
    from pathlib import Path

    _db_path_obj = Path(db_path)

    result: PrefetchResult = {
        "date": date_str,
        "races_total": 0,
        "races_already_cached": 0,
        "races_fetched": 0,
        "races_failed": 0,
        "race_ids_fetched": [],
        "race_ids_failed": [],
    }

    # 1. レースID取得
    race_ids = await get_race_ids_for_date(date_str)
    if not race_ids:
        logger.info(f"[prefetch] {date_str}: レースなし → スキップ")
        return result

    result["races_total"] = len(race_ids)

    # 2. キャッシュチェック（馬詳細あり = 取得完了済みとみなす）
    if not force:
        to_fetch: list[str] = []
        for rid in race_ids:
            if _has_horse_details(rid, db_path):
                result["races_already_cached"] += 1
            else:
                to_fetch.append(rid)
    else:
        to_fetch = list(race_ids)

    if not to_fetch:
        logger.info(f"[prefetch] {date_str}: 全{len(race_ids)}レース取得済み → スキップ")
        return result

    logger.info(
        f"[prefetch] {date_str}: {result['races_already_cached']}件キャッシュ済み,"
        f" {len(to_fetch)}件プリフェッチ開始"
    )

    # 3. ログイン済みセッションを準備
    sess = _new_session()
    try:
        if not skip_login:
            await warm_up_netkeiba(sess)
            await login_netkeiba(sess)
            await jitter_sleep(1.0, 2.0)

        for race_id in to_fetch:
            try:
                await jitter_sleep(1.0, 1.5)  # INV-07: >=1.0s
                race_data = await _scrape_shutuba_fallback(sess, race_id, date_hint=date_str)
                if race_data and race_data.get("horses"):
                    _save_race_to_ultimate_db(race_data, _db_path_obj)
                    result["races_fetched"] += 1
                    result["race_ids_fetched"].append(race_id)
                    logger.info(
                        f"[prefetch] {race_id}: {len(race_data['horses'])}頭 保存完了"
                        f" (sire率: {sum(1 for h in race_data['horses'] if h.get('sire'))}/{len(race_data['horses'])})"
                    )
                else:
                    result["races_failed"] += 1
                    result["race_ids_failed"].append(race_id)
                    logger.warning(f"[prefetch] {race_id}: データなし（Cloudflareブロックまたは404）")
            except Exception as e:
                result["races_failed"] += 1
                result["race_ids_failed"].append(race_id)
                logger.warning(f"[prefetch] {race_id}: 取得失敗 → {e}")

    finally:
        await sess.close()

    logger.info(
        f"[prefetch] {date_str} 完了: 取得={result['races_fetched']},"
        f" 失敗={result['races_failed']}, スキップ={result['races_already_cached']}"
    )
    return result
