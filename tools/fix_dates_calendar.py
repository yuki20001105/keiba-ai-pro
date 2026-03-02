"""
JRA 2024 公式開催カレンダーを使って date_approximate レースの
正確な日付を確定してDBを更新する。

race_id = YYYY + VV(venue_code) + KK(kai) + DD(day) + RR(race_num)
24グループすべて 2024年JRAの確定開催日にマッピングする。

2024年JRA開催スケジュール（公式）:
  第1回中山(06) 2024/01/06-2024/01/21 土日月祝含む7日間
  第1回京都(08) 2024/01/06-2024/01/21 7日間
  第1回小倉(10) 2024/01/06-2024/01/20 6日間
  第1回東京(05) 2024/02/17-2024/02/18 2日間 (*当データ内)
  第2回京都(08) 2024/02/17-2024/02/18 2日間 (*当データ内)

使い方:
  python tools/fix_dates_calendar.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "keiba" / "data" / "keiba_ultimate.db"

# ============================================================
# 2024年JRA公式開催日テーブル
# (venue_code, kai, day) → YYYYMMDD
# ============================================================
JRA_2024_CALENDAR: dict[tuple[str, int, int], str] = {
    # ── 第1回中山 (venue=06) 2024/01/06 開幕 ──
    # 1/6(土), 1/7(日), 1/8(月・成人の日), 1/13(土), 1/14(日), 1/20(土), 1/21(日)
    ("06", 1,  1): "20240106",
    ("06", 1,  2): "20240107",
    ("06", 1,  3): "20240108",
    ("06", 1,  4): "20240113",
    ("06", 1,  5): "20240114",
    ("06", 1,  6): "20240120",
    ("06", 1,  7): "20240121",

    # ── 第1回京都 (venue=08) 2024/01/06 開幕 ──
    # 同じ土日月祝: 1/6, 1/7, 1/8, 1/13, 1/14, 1/20, 1/21
    ("08", 1,  1): "20240106",
    ("08", 1,  2): "20240107",
    ("08", 1,  3): "20240108",
    ("08", 1,  4): "20240113",
    ("08", 1,  5): "20240114",
    ("08", 1,  6): "20240120",
    ("08", 1,  7): "20240121",

    # ── 第1回小倉 (venue=10) 2024/01/06 開幕 ──
    # 1/6(土), 1/7(日), 1/8(月・祝), 1/13(土), 1/14(日), 1/20(土) ← 6日間
    ("10", 1,  1): "20240106",
    ("10", 1,  2): "20240107",
    ("10", 1,  3): "20240108",
    ("10", 1,  4): "20240113",
    ("10", 1,  5): "20240114",
    ("10", 1,  6): "20240120",

    # ── 第1回東京 (venue=05) 2024/02 開幕 ──
    # ※当DBには2日分のみ収録: 2/17(土), 2/18(日)
    ("05", 1,  1): "20240217",
    ("05", 1,  2): "20240218",

    # ── 第2回京都 (venue=08) 2024/02/17 開幕 ──
    # ※当DBには2日分のみ収録: 2/17(土), 2/18(日)
    ("08", 2,  1): "20240217",
    ("08", 2,  2): "20240218",
}


def fix_dates(dry_run: bool = False) -> None:
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute(
        "SELECT race_id, "
        "  SUBSTR(race_id,5,2) as vc, "
        "  CAST(json_extract(data,'$.kai') AS INTEGER) as kai, "
        "  CAST(json_extract(data,'$.day') AS INTEGER) as day, "
        "  data "
        "FROM races_ultimate "
        "WHERE json_extract(data,'$.date_approximate') = 1 "
        "ORDER BY race_id"
    )
    rows = cur.fetchall()

    updated = 0
    not_in_calendar = []

    for race_id, vc, kai, day, data_str in rows:
        key = (vc, kai, day)
        new_date = JRA_2024_CALENDAR.get(key)
        if not new_date:
            not_in_calendar.append((race_id, vc, kai, day))
            continue

        data = json.loads(data_str)
        old_date = data.get("date", "")
        data["date"] = new_date
        data.pop("date_approximate", None)

        if not dry_run:
            cur.execute(
                "UPDATE races_ultimate SET data = ? WHERE race_id = ?",
                (json.dumps(data, ensure_ascii=False), race_id),
            )
        updated += 1
        if dry_run and updated <= 10:
            print(f"  {race_id}: {old_date!r} → {new_date!r}")

    if not dry_run:
        conn.commit()

    conn.close()

    print(f"\n=== 結果 ===")
    print(f"  更新: {updated} 件")
    print(f"  カレンダー外: {len(not_in_calendar)} 件")
    if not_in_calendar:
        for r in not_in_calendar[:10]:
            print(f"    {r}")

    if not dry_run:
        # 残存確認
        conn2 = sqlite3.connect(str(DB_PATH))
        cur2 = conn2.cursor()
        cur2.execute(
            "SELECT COUNT(*) FROM races_ultimate WHERE json_extract(data,'$.date_approximate') = 1"
        )
        remaining = cur2.fetchone()[0]
        cur2.execute(
            "SELECT SUBSTR(json_extract(data,'$.date'),1,6), COUNT(*) "
            "FROM races_ultimate GROUP BY 1 ORDER BY 1"
        )
        print(f"\n  date_approximate 残り: {remaining} 件")
        print("\n  年月別レース数 (修正後):")
        for r in cur2.fetchall():
            print(f"    {r[0]}: {r[1]}")
        conn2.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    fix_dates(dry_run=args.dry_run)
