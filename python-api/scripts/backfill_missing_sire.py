"""
pedigree_cache に sire が登録されていない馬の血統を再スクレイプして補完するスクリプト。

使い方:
  python-api\.venv\Scripts\python.exe python-api\scripts\backfill_missing_sire.py
  python-api\.venv\Scripts\python.exe python-api\scripts\backfill_missing_sire.py --limit 500 --sleep 2.0
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3
import sys
import time
from pathlib import Path

# パス設定
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "python-api"))

from bs4 import BeautifulSoup
from scraping.constants import login_netkeiba, get_random_headers
from scraping.horse import _parse_blood_table, _save_profile_sqlite, _get_profile_sqlite

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

MAIN_DB = REPO_ROOT / "keiba" / "data" / "keiba_ultimate.db"
PED_DB  = REPO_ROOT / "keiba" / "data" / "pedigree_cache.db"


def get_missing_horse_ids(limit: int) -> list[str]:
    """race_results_ultimate に存在するが pedigree_cache に sire がない horse_id を返す。"""
    conn = sqlite3.connect(str(MAIN_DB))
    # race_results_ultimate から全 horse_id を収集
    rows = conn.execute(
        "SELECT DISTINCT json_extract(data,'$.horse_id') FROM race_results_ultimate "
        "WHERE json_extract(data,'$.horse_id') IS NOT NULL"
    ).fetchall()
    conn.close()

    all_ids = {r[0] for r in rows if r[0]}

    # pedigree_cache に sire が登録済みの horse_id を除外
    conn_ped = sqlite3.connect(str(PED_DB))
    cached = {
        r[0]
        for r in conn_ped.execute(
            "SELECT horse_id FROM pedigree_cache WHERE sire IS NOT NULL AND sire != ''"
        ).fetchall()
    }
    conn_ped.close()

    missing = sorted(all_ids - cached)
    logger.info(f"sire 欠損馬: {len(missing)} 頭 / 全 {len(all_ids)} 頭")
    return missing[:limit]


async def fetch_ped_html(session, horse_id: str) -> str | None:
    """血統ページ (/horse/ped/<id>/) を取得して HTML 文字列を返す。失敗時は None。"""
    url = f"https://db.netkeiba.com/horse/ped/{horse_id}/"
    try:
        async with session.get(url) as r:
            if r.status == 200:
                return (await r.read()).decode("euc-jp", errors="replace")
            elif r.status == 429:
                logger.warning(f"429 Too Many Requests: {horse_id} → 10s 待機")
                await asyncio.sleep(10.0)
            else:
                logger.debug(f"HTTP {r.status}: {horse_id}")
    except Exception as e:
        logger.debug(f"取得例外 {horse_id}: {e}")
    return None


async def fetch_prof_html(session, horse_id: str) -> str | None:
    """/horse/<id>/ プロフィールページを取得。"""
    url = f"https://db.netkeiba.com/horse/{horse_id}/"
    try:
        async with session.get(url) as r:
            if r.status == 200:
                return (await r.read()).decode("euc-jp", errors="replace")
    except Exception:
        pass
    return None


async def backfill(horse_ids: list[str], sleep_sec: float) -> dict:
    """全 horse_id の血統を補完する。"""
    import aiohttp

    headers = get_random_headers()
    conn = aiohttp.TCPConnector(limit=2, ssl=False)
    async with aiohttp.ClientSession(headers=headers, connector=conn) as session:
        # ログイン
        from scraping.constants import HTML_STRAINER
        try:
            await login_netkeiba(session)
            logger.info("ログイン成功")
        except Exception as e:
            logger.warning(f"ログイン失敗（未ログインで続行）: {e}")

        stats = {"ok": 0, "fail": 0, "skip": 0}

        for i, horse_id in enumerate(horse_ids, 1):
            # 既にキャッシュ済みなら skip
            cached = _get_profile_sqlite(horse_id)
            if cached and cached.get("sire"):
                stats["skip"] += 1
                continue

            # ped ページを優先取得
            ped_html = await fetch_ped_html(session, horse_id)
            await asyncio.sleep(sleep_sec)

            result: dict = {}

            if ped_html:
                from scraping.constants import HTML_STRAINER
                soup = BeautifulSoup(ped_html, "lxml", parse_only=HTML_STRAINER)
                blood_table = soup.find("table", class_="blood_table")
                if blood_table:
                    _parse_blood_table(blood_table, result)

            if not result.get("sire"):
                # ped から取れなかった場合はプロフィールページも試みる
                prof_html = await fetch_prof_html(session, horse_id)
                if prof_html:
                    from scraping.constants import HTML_STRAINER
                    import re
                    prof_soup = BeautifulSoup(prof_html, "lxml", parse_only=HTML_STRAINER)
                    blood_table = prof_soup.find("table", class_="blood_table")
                    if blood_table:
                        _parse_blood_table(blood_table, result)
                    prof_table = prof_soup.find("table", attrs={"class": re.compile(r"db_prof_table")})
                    if prof_table:
                        for row in prof_table.find_all("tr"):
                            th = row.find("th"); td = row.find("td")
                            if not th or not td: continue
                            key = th.get_text(strip=True); val = td.get_text(strip=True)
                            if "生年月日" in key: result["horse_birth_date"] = val
                            elif "馬主" in key and "horse_owner" not in result: result["horse_owner"] = val
                            elif "生産者" in key and "horse_breeder" not in result: result["horse_breeder"] = val
                            elif "産地" in key and "horse_breeding_farm" not in result: result["horse_breeding_farm"] = val
                            elif "毛色" in key and "coat_color" not in result: result["coat_color"] = val
                await asyncio.sleep(sleep_sec)

            if result.get("sire"):
                _save_profile_sqlite(
                    horse_id,
                    result.get("sire", ""), result.get("dam", ""), result.get("damsire", ""),
                    result.get("horse_birth_date", ""), result.get("horse_owner", ""),
                    result.get("horse_breeder", ""), result.get("horse_breeding_farm", ""),
                    result.get("coat_color", ""),
                )
                logger.info(f"[{i}/{len(horse_ids)}] ✅ {horse_id} sire={result['sire']}")
                stats["ok"] += 1
            else:
                logger.warning(f"[{i}/{len(horse_ids)}] ❌ {horse_id} sire 取得失敗")
                stats["fail"] += 1

            # 進捗表示
            if i % 50 == 0:
                logger.info(f"進捗: {i}/{len(horse_ids)} | ok={stats['ok']} fail={stats['fail']}")

    return stats


def main():
    parser = argparse.ArgumentParser(description="sire 欠損馬の血統を一括補完する")
    parser.add_argument("--limit", type=int, default=10000, help="最大処理頭数 (default: 10000)")
    parser.add_argument("--sleep", type=float, default=1.5, help="リクエスト間スリープ秒 (default: 1.5)")
    args = parser.parse_args()

    missing = get_missing_horse_ids(args.limit)
    if not missing:
        logger.info("欠損馬なし。終了。")
        return

    logger.info(f"処理対象: {len(missing)} 頭 (sleep={args.sleep}s)")
    logger.info(f"推定所要時間: {len(missing) * args.sleep / 60:.1f} 分")

    t0 = time.time()
    stats = asyncio.run(backfill(missing, args.sleep))
    elapsed = time.time() - t0

    logger.info(
        f"完了: ok={stats['ok']} fail={stats['fail']} skip={stats['skip']} "
        f"経過={elapsed:.0f}s"
    )


if __name__ == "__main__":
    main()
