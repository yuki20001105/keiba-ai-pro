"""
古いスクレイプデータ削除スクリプト

prev3 が存在しない（旧スクレイパーで収集された）レコードを
race_results_ultimate から削除する。

--dry-run  : 削除対象件数を表示するだけで実際には削除しない
--date-from: 削除対象の開始日（YYYYMMDD）。未指定 = 全期間
--date-to  : 削除対象の終了日（YYYYMMDD）。未指定 = 全期間
--all      : prev3 の有無に関わらず全レコードを削除（完全リセット）

使用例:
  # 削除対象の確認
  python-api\.venv\Scripts\python.exe python-api/scripts/cleanup_old_data.py --dry-run

  # 2025-2026 評価範囲の prev3 欠損レコードを削除（再スクレイプ前に実行）
  python-api\.venv\Scripts\python.exe python-api/scripts/cleanup_old_data.py \
      --date-from 20250101 --date-to 20260430

  # 全データを完全リセット
  python-api\.venv\Scripts\python.exe python-api/scripts/cleanup_old_data.py --all
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = PROJECT_ROOT / "keiba" / "data" / "keiba_ultimate.db"


def _has_prev3(data_str: str) -> bool:
    """JSON data フィールドに prev3_race_time が存在するか判定。"""
    try:
        d = json.loads(data_str)
        return d.get("prev3_race_time") is not None
    except Exception:
        return False


def _has_prev1(data_str: str) -> bool:
    """JSON data フィールドに prev_race_time が存在するか（馬に過去走があるか）。"""
    try:
        d = json.loads(data_str)
        return d.get("prev_race_time") is not None
    except Exception:
        return False


def run(dry_run: bool, date_from: str | None, date_to: str | None, delete_all: bool) -> None:
    print(f"DB: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))

    total = conn.execute("SELECT COUNT(*) FROM race_results_ultimate").fetchone()[0]
    print(f"現在の総レコード数: {total:,}")

    if delete_all:
        # 全削除モード
        if dry_run:
            print(f"[DRY-RUN] 全 {total:,} 件を削除予定")
            conn.close()
            return
        print(f"全 {total:,} 件を削除します...")
        conn.execute("DELETE FROM race_results_ultimate")
        conn.execute("DELETE FROM scraped_dates")
        conn.commit()
        conn.execute("VACUUM")
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM race_results_ultimate").fetchone()[0]
        print(f"削除完了。残レコード: {after:,}")
        conn.close()
        return

    # prev3 欠損レコードを特定
    # ただし prev1 も存在しない（馬に過去走なし）のは正当なので対象外
    # → prev1 あり かつ prev3 なし = 旧スクレイパーで取得した不完全データ

    # date_from/date_to フィルタ付きクエリ
    where_clauses = []
    params: list = []
    if date_from:
        where_clauses.append("race_id >= ?")
        params.append(date_from)
    if date_to:
        where_clauses.append("race_id < ?")
        params.append(str(int(date_to[:4]) * 10000 + int(date_to[4:6]) * 100 + int(date_to[6:8]) + 1)[:8] + "00000000")
        # race_id は "YYYYVVDDRRXX" 形式なので年の次8桁超は含まれない
        # シンプルに date_to の翌日相当のプレフィックスを作る
        params[-1] = _next_date_prefix(date_to)

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    q = f"SELECT rowid, race_id, data FROM race_results_ultimate {where_sql}"
    rows = conn.execute(q, params).fetchall()

    to_delete: list[int] = []
    for rowid, race_id, data_str in rows:
        if data_str and _has_prev1(data_str) and not _has_prev3(data_str):
            to_delete.append(rowid)

    date_label = ""
    if date_from:
        date_label += f" from={date_from}"
    if date_to:
        date_label += f" to={date_to}"

    print(f"対象範囲{date_label}: {len(rows):,} 件中")
    print(f"  prev1 あり + prev3 なし（旧スクレイパー）: {len(to_delete):,} 件")
    print(f"  削除後に残るレコード: {total - len(to_delete):,} 件")

    if dry_run:
        print("[DRY-RUN] 実際の削除はスキップしました。")
        conn.close()
        return

    if not to_delete:
        print("削除対象がありません。")
        conn.close()
        return

    print(f"{len(to_delete):,} 件を削除中...")
    # バッチ削除（1000件ずつ）
    batch_size = 1000
    for i in range(0, len(to_delete), batch_size):
        batch = to_delete[i : i + batch_size]
        placeholders = ",".join("?" * len(batch))
        conn.execute(f"DELETE FROM race_results_ultimate WHERE rowid IN ({placeholders})", batch)
    conn.commit()

    # scraped_dates も削除（対象範囲の日付を再スクレイプ可能にする）
    if date_from or date_to:
        sc_where = []
        sc_params: list = []
        if date_from:
            sc_where.append("date >= ?")
            sc_params.append(int(date_from))
        if date_to:
            sc_where.append("date <= ?")
            sc_params.append(int(date_to))
        sc_sql = "DELETE FROM scraped_dates WHERE " + " AND ".join(sc_where)
        conn.execute(sc_sql, sc_params)
        conn.commit()
        print(f"scraped_dates の {date_label} 範囲もクリアしました（再スクレイプ可能）。")

    conn.execute("VACUUM")
    conn.commit()

    after = conn.execute("SELECT COUNT(*) FROM race_results_ultimate").fetchone()[0]
    print(f"削除完了。残レコード: {after:,}  (削除: {total - after:,} 件)")
    conn.close()


def _next_date_prefix(date_yyyymmdd: str) -> str:
    """YYYYMMDD の翌日を YYYYMMDD で返す（race_id の上限フィルタ用）。"""
    from datetime import datetime, timedelta
    d = datetime.strptime(date_yyyymmdd, "%Y%m%d") + timedelta(days=1)
    return d.strftime("%Y%m%d")


def main() -> None:
    parser = argparse.ArgumentParser(description="古いスクレイプデータを削除する")
    parser.add_argument("--dry-run", action="store_true", help="削除対象を表示するだけ（実行しない）")
    parser.add_argument("--date-from", default=None, help="削除対象の開始日 YYYYMMDD")
    parser.add_argument("--date-to", default=None, help="削除対象の終了日 YYYYMMDD")
    parser.add_argument("--all", dest="delete_all", action="store_true", help="全データを削除（完全リセット）")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"ERROR: DB が見つかりません: {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    run(
        dry_run=args.dry_run,
        date_from=args.date_from,
        date_to=args.date_to,
        delete_all=args.delete_all,
    )


if __name__ == "__main__":
    main()
