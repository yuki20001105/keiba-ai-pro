"""
keiba_ultimate.db 補修スクリプト

問題:
  1. venue='' または venue が数字コード ('45', '08'等) → VENUE_MAP で補完
  2. date=null → 馬エントリーの prev_race_date の最大値 + 推定休養期間で近似
                + "date_approximate": True フラグを付与

使い方:
  python tools/repair_db.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

# プロジェクトルートを PYTHONPATH に追加
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python-api"))

VENUE_MAP: dict[str, str] = {
    # JRA
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟",
    "05": "東京", "06": "中山", "07": "中京", "08": "京都",
    "09": "阪神", "10": "小倉",
    # NAR
    "30": "門別", "31": "帯広（ば）",
    "35": "盛岡", "36": "水沢",
    "42": "浦和", "43": "船橋", "44": "大井", "45": "川崎",
    "46": "金沢", "47": "笠松", "48": "名古屋",
    "50": "園田", "51": "姫路",
    "54": "福山", "55": "高知",
    "60": "佐賀",
    "65": "帯広(ばんえい)", "66": "中津",
}

DB_PATH = ROOT / "keiba" / "data" / "keiba_ultimate.db"


def _parse_prev_date(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y/%m/%d", "%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(s), fmt)
        except ValueError:
            pass
    return None


def repair_db(dry_run: bool = False) -> None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # ── venue 修正 ──────────────────────────────────────────────────────
    cur.execute("SELECT race_id, data FROM races_ultimate")
    rows = cur.fetchall()

    venue_fixed = 0
    date_fixed = 0
    date_approx = 0

    updates: list[tuple[str, str]] = []  # (new_data_json, race_id)

    for row in rows:
        race_id: str = row["race_id"]
        data: dict = json.loads(row["data"])
        modified = False

        # ── 1. venue 修正 ──
        venue_raw = data.get("venue", "")
        venue_code = race_id[4:6]
        venue_correct = VENUE_MAP.get(venue_code, venue_code)

        # venue が空 or 数字コードのまま → 修正
        needs_venue_fix = (
            not venue_raw
            or (re.match(r"^\d+$", str(venue_raw)) and str(venue_raw) != venue_correct)
        )
        if needs_venue_fix:
            data["venue"] = venue_correct
            modified = True
            venue_fixed += 1

        # ── 2. date 修正 ──
        date_raw = data.get("date")
        if not date_raw or not re.match(r"^\d{8}$", str(date_raw)):
            # 馬エントリーの prev_race_date から推定
            cur.execute(
                "SELECT data FROM race_results_ultimate WHERE race_id = ?", (race_id,)
            )
            horse_rows = cur.fetchall()

            prev_dates: list[datetime] = []
            for hr in horse_rows:
                h = json.loads(hr["data"])
                d = _parse_prev_date(h.get("prev_race_date"))
                if d:
                    prev_dates.append(d)
                d2 = _parse_prev_date(h.get("prev2_race_date"))
                if d2:
                    prev_dates.append(d2)

            if prev_dates:
                # 最も新しい前走日付 + 45日 (平均的な中間休養) を近似値とする
                latest_prev = max(prev_dates)
                approx_dt = latest_prev + timedelta(days=45)
                # 年を超えないようにキャップ
                year_from_id = int(race_id[:4])
                if approx_dt.year > year_from_id + 1:
                    approx_dt = datetime(year_from_id, 12, 1)
                elif approx_dt.year < year_from_id:
                    approx_dt = datetime(year_from_id, 6, 1)
                approx_date_str = approx_dt.strftime("%Y%m%d")
                data["date"] = approx_date_str
                data["date_approximate"] = True
                date_fixed += 1
                date_approx += 1
                modified = True
            else:
                # 馬エントリーなし → year + 0601 でフォールバック
                year_from_id = int(race_id[:4])
                data["date"] = f"{year_from_id}0601"
                data["date_approximate"] = True
                date_fixed += 1
                date_approx += 1
                modified = True

        if modified:
            updates.append((json.dumps(data, ensure_ascii=False), race_id))

    print(f"\n=== 補修サマリー ===")
    print(f"  対象レース数: {len(rows)}")
    print(f"  venue 修正:   {venue_fixed} 件")
    print(f"  date  補完:   {date_fixed} 件 (近似値)")
    print(f"  更新合計:     {len(updates)} 件")

    if dry_run:
        print("\n[DRY RUN] 変更は保存されませんでした。")
        # サンプル表示
        for new_data_json, race_id in updates[:5]:
            d = json.loads(new_data_json)
            print(f"  {race_id}: venue={d.get('venue')!r}  date={d.get('date')!r}  approx={d.get('date_approximate', False)}")
    else:
        # バルク UPDATE
        for data_json, race_id in updates:
            cur.execute(
                "UPDATE races_ultimate SET data = ? WHERE race_id = ?",
                (data_json, race_id),
            )
        conn.commit()
        print(f"\n[OK] {len(updates)} 件を更新しました: {DB_PATH}")

    conn.close()

    # ── 修正後の確認 ──
    if not dry_run:
        conn2 = sqlite3.connect(str(DB_PATH))
        cur2 = conn2.cursor()
        cur2.execute(
            "SELECT COUNT(*) FROM races_ultimate WHERE json_extract(data,'$.date') IS NULL"
        )
        null_date = cur2.fetchone()[0]
        cur2.execute(
            "SELECT COUNT(*) FROM races_ultimate WHERE json_extract(data,'$.venue') = '' OR json_extract(data,'$.venue') IS NULL"
        )
        null_venue = cur2.fetchone()[0]
        cur2.execute(
            "SELECT json_extract(data,'$.venue'), COUNT(*) FROM races_ultimate WHERE CAST(json_extract(data,'$.venue') AS INTEGER) > 0 GROUP BY 1"
        )
        numeric_venue = cur2.fetchall()
        conn2.close()

        print(f"\n=== 修正後の状態 ===")
        print(f"  date=null  残り: {null_date} 件")
        print(f"  venue='' 残り:   {null_venue} 件")
        print(f"  venue=数字 残り: {numeric_venue}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="変更をDBに保存しない")
    args = parser.parse_args()
    repair_db(dry_run=args.dry_run)
