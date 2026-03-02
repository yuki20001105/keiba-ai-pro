"""
date_approximate=True のレースを netkeiba から再スクレイピングして
正確な日付でDBを上書きする。

使い方:
  python tools/rescrape_dates.py [--dry-run] [--concurrency 3]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python-api"))

from scraping.constants import SCRAPE_HEADERS  # type: ignore
from scraping.race import scrape_race_full     # type: ignore
from scraping.storage import _save_race_sqlite_only  # type: ignore

DB_PATH = ROOT / "keiba" / "data" / "keiba_ultimate.db"


def get_target_race_ids() -> list[str]:
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute(
        "SELECT race_id FROM races_ultimate "
        "WHERE json_extract(data,'$.date_approximate') = 1 "
        "ORDER BY race_id"
    )
    ids = [r[0] for r in cur.fetchall()]
    conn.close()
    return ids


def update_race_date(race_id: str, new_date: str) -> None:
    """races_ultimate の date を上書きし date_approximate フラグを削除"""
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT data FROM races_ultimate WHERE race_id = ?", (race_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return
    data = json.loads(row[0])
    data["date"] = new_date
    data.pop("date_approximate", None)  # フラグ除去
    cur.execute(
        "UPDATE races_ultimate SET data = ? WHERE race_id = ?",
        (json.dumps(data, ensure_ascii=False), race_id),
    )
    conn.commit()
    conn.close()


async def rescrape_all(dry_run: bool = False, concurrency: int = 3) -> None:
    race_ids = get_target_race_ids()
    print(f"対象: {len(race_ids)} 件 (dry_run={dry_run}, concurrency={concurrency})")

    timeout = aiohttp.ClientTimeout(total=30, connect=10)
    connector = aiohttp.TCPConnector(limit=concurrency * 2, limit_per_host=concurrency)
    semaphore = asyncio.Semaphore(concurrency)

    stats = {"ok": 0, "no_date": 0, "error": 0, "skip": 0}
    failed: list[str] = []

    async with aiohttp.ClientSession(
        headers=SCRAPE_HEADERS, timeout=timeout, connector=connector
    ) as session:

        async def process(race_id: str) -> None:
            async with semaphore:
                try:
                    race_data = await scrape_race_full(
                        session, race_id, date_hint="", quick_mode=True
                    )
                    if not race_data:
                        print(f"  [ERROR] {race_id}: scrape_race_full が None を返却")
                        stats["error"] += 1
                        failed.append(race_id)
                        return

                    race_info = race_data.get("race_info", {})
                    new_date = race_info.get("date", "")

                    if not new_date:
                        print(f"  [NO_DATE] {race_id}: HTMLから日付を取得できず")
                        stats["no_date"] += 1
                        failed.append(race_id)
                        return

                    print(f"  [OK] {race_id}: date={new_date}  venue={race_info.get('venue')}")

                    if not dry_run:
                        # races_ultimate と race_results_ultimate を丸ごと上書き
                        _save_race_sqlite_only(race_data, DB_PATH, overwrite=True)
                    stats["ok"] += 1

                except Exception as e:
                    print(f"  [EXCEPTION] {race_id}: {e}")
                    stats["error"] += 1
                    failed.append(race_id)

        # チャンク処理（全件並列は避ける）
        tasks = [process(rid) for rid in race_ids]
        await asyncio.gather(*tasks)

    print(f"\n=== 結果 ===")
    print(f"  成功:       {stats['ok']} 件")
    print(f"  日付なし:   {stats['no_date']} 件")
    print(f"  エラー:     {stats['error']} 件")
    print(f"  合計:       {len(race_ids)} 件")
    if failed:
        print(f"\n  失敗 race_id ({len(failed)}件):")
        for rid in failed[:20]:
            print(f"    {rid}")

    if not dry_run:
        # 残存 date_approximate 件数を確認
        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM races_ultimate "
            "WHERE json_extract(data,'$.date_approximate') = 1"
        )
        remaining = cur.fetchone()[0]
        conn.close()
        print(f"\n  date_approximate 残り: {remaining} 件")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--concurrency", type=int, default=3)
    args = parser.parse_args()
    asyncio.run(rescrape_all(dry_run=args.dry_run, concurrency=args.concurrency))
