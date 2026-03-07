"""
tools/patch_horse_names.py
==========================
race_results_ultimate テーブルで horse_name が空のレコードを
同一 horse_id の他レコードから逆引きして補完する。

使い方:
    python tools/patch_horse_names.py [--db PATH] [--dry-run]

オプション:
    --db PATH    DB ファイルパス (既定: keiba/data/keiba_ultimate.db)
    --dry-run    実際には更新せず件数だけ表示
"""
import argparse
import json
import sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).parent.parent / "keiba" / "data" / "keiba_ultimate.db"


def build_horse_name_map(cur: sqlite3.Cursor) -> dict[str, str]:
    """horse_id → horse_name のマップを、名前が入っているレコードから構築する。"""
    name_map: dict[str, str] = {}
    cur.execute("SELECT data FROM race_results_ultimate")
    for (data_json,) in cur.fetchall():
        try:
            d = json.loads(data_json)
        except (json.JSONDecodeError, TypeError):
            continue
        hid = str(d.get("horse_id") or "").strip()
        hname = str(d.get("horse_name") or "").strip()
        if hid and hname and hname not in ("", "None", "nan") and not hname.startswith("["):
            name_map.setdefault(hid, hname)  # 最初に見つかったものを採用
    return name_map


def patch(db_path: Path, dry_run: bool = False) -> None:
    print(f"DB: {db_path}")
    if not db_path.exists():
        print("ERROR: DB ファイルが見つかりません")
        return

    con = sqlite3.connect(db_path)
    cur = con.cursor()

    print("horse_name マップを構築中...")
    name_map = build_horse_name_map(cur)
    print(f"  → {len(name_map)} 件の horse_id→horse_name マッピング確認")

    # 空の horse_name を持つレコードを取得
    cur.execute("SELECT rowid, data FROM race_results_ultimate")
    rows = cur.fetchall()

    to_update: list[tuple[str, int]] = []  # (updated_json, rowid)
    still_empty = 0

    for rowid, data_json in rows:
        try:
            d = json.loads(data_json)
        except (json.JSONDecodeError, TypeError):
            continue

        hname = str(d.get("horse_name") or "").strip()
        if hname and hname not in ("", "None", "nan") and not hname.startswith("["):
            continue  # 既に名前あり

        hid = str(d.get("horse_id") or "").strip()
        recovered = name_map.get(hid, "")
        if recovered:
            d["horse_name"] = recovered
            to_update.append((json.dumps(d, ensure_ascii=False), rowid))
        else:
            still_empty += 1

    print(f"  → 修正対象: {len(to_update)} 件 / 逆引き不可 (再スクレイプ要): {still_empty} 件")

    if dry_run:
        print("[dry-run] 実際の更新は行いません")
        if to_update:
            print("  修正サンプル (最大5件):")
            for updated_json, rowid in to_update[:5]:
                d2 = json.loads(updated_json)
                print(f"    rowid={rowid} horse_id={d2.get('horse_id')} → horse_name={d2.get('horse_name')}")
        con.close()
        return

    if to_update:
        cur.executemany(
            "UPDATE race_results_ultimate SET data = ? WHERE rowid = ?",
            to_update,
        )
        con.commit()
        print(f"  ✓ {len(to_update)} 件の horse_name を更新しました")
    else:
        print("  修正対象なし")

    if still_empty:
        print(
            f"  ⚠ {still_empty} 件は全レコード中に同じ horse_id の名前が見つかりませんでした。"
            f" 再スクレイプが必要です。"
        )

    con.close()


def main():
    parser = argparse.ArgumentParser(description="patch_horse_names: 空 horse_name を他レコードから補完")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="DB ファイルパス")
    parser.add_argument("--dry-run", action="store_true", help="実際には更新せず確認のみ")
    args = parser.parse_args()
    patch(args.db, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
