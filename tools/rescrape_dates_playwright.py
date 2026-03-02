"""
Playwright (実ブラウザ) を使って netkeiba から date_approximate レースの
正確な日付を取得して DB を更新する。

使い方:
  python tools/rescrape_dates_playwright.py [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "keiba" / "data" / "keiba_ultimate.db"


def get_target_groups() -> dict[tuple, list[str]]:
    """(venue_code, kai, day) → [race_id の一覧] を返す"""
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute(
        "SELECT race_id, "
        "  SUBSTR(race_id,5,2) as vc, "
        "  CAST(json_extract(data,'$.kai') AS INTEGER) as kai, "
        "  CAST(json_extract(data,'$.day') AS INTEGER) as day "
        "FROM races_ultimate "
        "WHERE json_extract(data,'$.date_approximate') = 1 "
        "ORDER BY race_id"
    )
    rows = cur.fetchall()
    conn.close()

    groups: dict[tuple, list[str]] = defaultdict(list)
    for race_id, vc, kai, day in rows:
        groups[(vc, kai, day)].append(race_id)
    return dict(sorted(groups.items()))


def update_dates_in_db(race_date_map: dict[str, str], dry_run: bool) -> int:
    """DB を一括更新。成功件数を返す。"""
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    updated = 0
    for race_id, new_date in race_date_map.items():
        cur.execute("SELECT data FROM races_ultimate WHERE race_id = ?", (race_id,))
        row = cur.fetchone()
        if not row:
            continue
        data = json.loads(row[0])
        data["date"] = new_date
        data.pop("date_approximate", None)
        if not dry_run:
            cur.execute(
                "UPDATE races_ultimate SET data = ? WHERE race_id = ?",
                (json.dumps(data, ensure_ascii=False), race_id),
            )
        updated += 1
    if not dry_run:
        conn.commit()
    conn.close()
    return updated


async def scrape_dates_with_playwright(
    groups: dict[tuple, list[str]],
    dry_run: bool = False,
) -> None:
    from playwright.async_api import async_playwright  # type: ignore

    # 代表 race_id を per-group で1件だけアクセスして日付を取得
    # 同一 (venue, kai, day) グループは全員同じ日付になる
    group_dates: dict[tuple, str] = {}
    race_date_map: dict[str, str] = {}

    stats = {"ok": 0, "fail": 0}
    failed_groups: list[tuple] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="ja-JP",
            extra_http_headers={
                "Accept-Language": "ja,en-US;q=0.9",
                "Referer": "https://db.netkeiba.com/",
            },
        )
        page = await context.new_page()

        # まずトップページを叩いてCookieを取得
        print("netkeiba トップページにアクセス中...")
        try:
            await page.goto("https://db.netkeiba.com/", timeout=20000)
            await asyncio.sleep(1)
            print("  トップページ OK")
        except Exception as e:
            print(f"  トップページ: {e}")

        for (vc, kai, day), race_ids in groups.items():
            repr_race_id = race_ids[0]
            url = f"https://db.netkeiba.com/race/{repr_race_id}/"

            try:
                await page.goto(url, timeout=20000, wait_until="domcontentloaded")
                await asyncio.sleep(0.5)

                content = await page.content()
                dm = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", content)
                if dm:
                    date_str = (
                        f"{dm.group(1)}"
                        f"{int(dm.group(2)):02d}"
                        f"{int(dm.group(3)):02d}"
                    )
                    group_dates[(vc, kai, day)] = date_str
                    # 同グループの全 race_id に同じ日付を割り当て
                    for rid in race_ids:
                        race_date_map[rid] = date_str
                    stats["ok"] += 1
                    print(f"  [{stats['ok']:2d}] venue={vc} kai={kai} day={day}: {date_str}  ({repr_race_id})")
                else:
                    title = await page.title()
                    print(f"  [FAIL] venue={vc} kai={kai} day={day}: 日付なし  title={title!r}  ({repr_race_id})")
                    stats["fail"] += 1
                    failed_groups.append((vc, kai, day))

            except Exception as e:
                print(f"  [ERR] venue={vc} kai={kai} day={day}: {e}  ({repr_race_id})")
                stats["fail"] += 1
                failed_groups.append((vc, kai, day))

            await asyncio.sleep(0.5)  # レート制限対策

        await browser.close()

    print(f"\n=== スクレイピング結果 ===")
    print(f"  成功: {stats['ok']}/{len(groups)} グループ")
    print(f"  対象レース: {len(race_date_map)} 件 / {sum(len(v) for v in groups.values())} 件")

    if race_date_map:
        updated = update_dates_in_db(race_date_map, dry_run)
        if dry_run:
            print(f"\n[DRY RUN] {updated} 件更新予定 (変更は保存されていません)")
            # サンプル表示
            for rid, dt in list(race_date_map.items())[:5]:
                print(f"  {rid}: {dt}")
        else:
            print(f"\n[OK] {updated} 件を DB に保存しました")

    if failed_groups:
        print(f"\n失敗グループ ({len(failed_groups)}件):")
        for g in failed_groups:
            print(f"  venue={g[0]} kai={g[1]} day={g[2]}: {groups[g]}")

    # 最終確認
    if not dry_run:
        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM races_ultimate "
            "WHERE json_extract(data,'$.date_approximate') = 1"
        )
        remaining = cur.fetchone()[0]
        conn.close()
        print(f"\ndate_approximate 残り: {remaining} 件")


async def main(dry_run: bool = False) -> None:
    groups = get_target_groups()
    print(f"対象: {len(groups)} グループ ({sum(len(v) for v in groups.values())} レース)")
    await scrape_dates_with_playwright(groups, dry_run=dry_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
