"""
tools/fix_distance_zero.py
──────────────────────────────────────────────────────────────
既存 DB の races_ultimate テーブルで distance=0 のレースを修正する。

修正戦略:
  1. race_name フィールドを正規表現で再パース → distance / track_type を復元
  2. 復元できた場合  → JSON を UPDATE（正しい値で上書き）
  3. 復元できない場合 → JSON に _invalid_distance=true フラグを付与
     → db_ultimate_loader がこのフラグを見てそのレースをスキップする

実行:
  python-api\\.venv\\Scripts\\python.exe tools/fix_distance_zero.py [--db PATH] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

# ────────────────────────────── 設定 ──────────────────────────────
DEFAULT_DB = Path(__file__).resolve().parents[1] / "keiba" / "data" / "keiba_ultimate.db"

# race.py と同じパターン
_RE_FULL    = re.compile(r"(芝|ダ(?:ート)?|障(?:害)?)[・右左直外内障]{0,4}\s*(\d{3,4})\s*[mｍ]")
_RE_NUMONLY = re.compile(r"\b(\d{3,4})\s*[mｍ]")
_RE_SURFACE = re.compile(r"(芝|ダート|ダ|障害)")

def _try_recover(race_id: str, data: dict) -> tuple[int | None, str | None]:
    """race_name / race_条件 / race_class 等から distance と track_type を復元。"""
    candidates = [
        data.get("race_name", ""),
        data.get("condition", ""),
        data.get("race_class", ""),
        data.get("course_info", ""),
    ]
    text = " ".join(str(c) for c in candidates if c)

    # パターン1: 芝/ダ + 距離
    m = _RE_FULL.search(text)
    if m:
        raw_tt = m.group(1)
        t = "芝" if raw_tt == "芝" else ("障害" if raw_tt.startswith("障") else "ダート")
        return int(m.group(2)), t

    # パターン2: 距離だけ + 種別別途
    m2 = _RE_NUMONLY.search(text)
    if m2:
        dist = int(m2.group(1))
        if 100 <= dist <= 3600:
            ms = _RE_SURFACE.search(text)
            t = ""
            if ms:
                raw = ms.group(1)
                t = "芝" if raw == "芝" else ("障害" if raw == "障害" else "ダート")
            return dist, t

    # ばんえい（venue_code=65）は距離不定だが 200m がデフォルト
    if race_id[4:6] == "65":
        return 200, "ばんえい"

    return None, None


def run(db_path: Path, dry_run: bool) -> None:
    print(f"DB: {db_path}")
    if not db_path.exists():
        print("ERROR: DB が見つかりません")
        return

    con = sqlite3.connect(str(db_path))
    rows = con.execute("SELECT race_id, data FROM races_ultimate").fetchall()
    print(f"races_ultimate 総件数: {len(rows)}")

    fixed       = []   # (race_id, old_dist, new_dist, new_tt)
    flagged     = []   # (race_id, race_name)  → _invalid_distance=True
    already_ok  = 0
    already_bad = 0    # すでにフラグが立っていた

    for race_id, raw in rows:
        try:
            d = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:
            continue

        # すでにフラグ済みはスキップ
        if d.get("_invalid_distance"):
            already_bad += 1
            continue

        dist_val = d.get("distance")
        try:
            dist_int = int(float(str(dist_val))) if dist_val is not None else 0
        except (ValueError, TypeError):
            dist_int = 0

        if dist_int > 0:
            already_ok += 1
            continue

        # ── distance=0 → 復元を試みる ──
        new_dist, new_tt = _try_recover(race_id, d)

        if new_dist:
            fixed.append((race_id, dist_val, new_dist, new_tt))
            if not dry_run:
                d["distance"]   = new_dist
                if new_tt and not d.get("track_type"):
                    d["track_type"] = new_tt
                    d["surface"]    = new_tt
                con.execute(
                    "UPDATE races_ultimate SET data=? WHERE race_id=?",
                    (json.dumps(d, ensure_ascii=False), race_id)
                )
        else:
            flagged.append((race_id, d.get("race_name", "")))
            if not dry_run:
                d["_invalid_distance"] = True
                d["_skip_reason"] = "distance=0 で race_name からも復元不可"
                con.execute(
                    "UPDATE races_ultimate SET data=? WHERE race_id=?",
                    (json.dumps(d, ensure_ascii=False), race_id)
                )

    if not dry_run:
        con.commit()
    con.close()

    # ── レポート ──
    print(f"\n{'='*60}")
    print(f"  既に正常 (distance>0)    : {already_ok}")
    print(f"  既にフラグ済み            : {already_bad}")
    print(f"  復元成功 → UPDATE         : {len(fixed)}")
    print(f"  復元不可 → _invalid_flag  : {len(flagged)}")
    if dry_run:
        print("  ※ DRY-RUN のため DB未更新")
    print(f"{'='*60}")

    if fixed:
        print(f"\n[復元成功] {len(fixed)} 件 (先頭20件):")
        for race_id, old, new, tt in fixed[:20]:
            print(f"  {race_id}: {old} → {new}m  track_type={tt}")

    if flagged:
        print(f"\n[_invalid_distance フラグ付与] {len(flagged)} 件:")
        for race_id, name in flagged:
            print(f"  {race_id}: race_name={name!r}")
        print()
        print("  → これらのレースは db_ultimate_loader でスキップされます")
        print("  → 必要に応じて再スクレイピングか、手動修正してください")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fix distance=0 records in races_ultimate")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--dry-run", action="store_true",
                        help="DB を変更せずレポートのみ出力")
    args = parser.parse_args()
    run(args.db, args.dry_run)
