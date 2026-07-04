"""データ品質チェックスクリプト"""
import sqlite3
import json
from pathlib import Path

DB = Path("keiba/data/keiba_ultimate.db")
PED = Path("keiba/data/pedigree_cache.db")

# ============ pedigree_cache ============
conn2 = sqlite3.connect(str(PED))
tables = conn2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print(f"\n=== pedigree_cache テーブル: {[t[0] for t in tables]} ===")

if tables:
    ped_tbl = tables[0][0]
    cols = [c[1] for c in conn2.execute(f"PRAGMA table_info({ped_tbl})").fetchall()]
    print(f"カラム: {cols}")
    ped_total = conn2.execute(f"SELECT COUNT(*) FROM {ped_tbl}").fetchone()[0]
    print(f"総エントリ: {ped_total:,}")
    if "sire" in cols:
        ped_with_sire = conn2.execute(f'SELECT COUNT(*) FROM {ped_tbl} WHERE sire IS NOT NULL AND sire != ""').fetchone()[0]
        print(f"sire設定済み: {ped_with_sire:,} ({ped_with_sire/max(ped_total,1)*100:.1f}%)")
    if "birth_date" in cols:
        ped_with_prof = conn2.execute(f'SELECT COUNT(*) FROM {ped_tbl} WHERE birth_date IS NOT NULL AND birth_date != ""').fetchone()[0]
        print(f"birth_date設定済み: {ped_with_prof:,} ({ped_with_prof/max(ped_total,1)*100:.1f}%)")
    # Show a sample
    row = conn2.execute(f"SELECT * FROM {ped_tbl} LIMIT 1").fetchone()
    if row:
        print(f"サンプル: {dict(zip(cols, row))}")
conn2.close()

# ============ keiba_ultimate.db ============
conn = sqlite3.connect(str(DB))

# Date range
row = conn.execute("SELECT MIN(race_date), MAX(race_date) FROM races_ultimate").fetchone()
print(f"\n=== races_ultimate 日付範囲: {row[0]} 〜 {row[1]} ===")

# race_results_ultimate columns
cols_rr = [c[1] for c in conn.execute("PRAGMA table_info(race_results_ultimate)").fetchall()]
print(f"race_results_ultimate カラム: {cols_rr}")

# Check if data is JSON blob or columns
sample_row = conn.execute("SELECT * FROM race_results_ultimate LIMIT 1").fetchone()
print(f"サンプル行 (先頭3フィールド): {sample_row[:3] if sample_row else 'N/A'}")

# If data is a column
if "data" in cols_rr:
    sample = conn.execute("SELECT data FROM race_results_ultimate ORDER BY rowid DESC LIMIT 300").fetchall()
    fields = [
        "sire", "dam", "damsire", "horse_birth_date", "coat_color",
        "horse_owner", "horse_breeder", "horse_total_runs", "horse_total_wins",
        "horse_total_prize_money", "finish_time", "odds", "popularity"
    ]
    print(f"\n=== 馬データフィールド充足率(直近300頭) ===")
    for f in fields:
        filled = sum(1 for (d,) in sample if json.loads(d).get(f) not in (None, "", 0))
        pct = filled / len(sample) * 100
        flag = "⚠" if pct < 50 else "✓"
        print(f"  {flag} {f:<30}: {filled:>3}/{len(sample)} ({pct:.0f}%)")

    # Check race types in sample
    sample_races = conn.execute("SELECT data FROM race_results_ultimate ORDER BY rowid DESC LIMIT 300").fetchall()
    venues = {}
    for (d,) in sample_races:
        obj = json.loads(d)
        venue = obj.get("venue", obj.get("course", "不明"))
        venues[venue] = venues.get(venue, 0) + 1
    top_venues = sorted(venues.items(), key=lambda x: -x[1])[:10]
    print(f"\n=== 直近300頭の競馬場分布 ===")
    for venue, cnt in top_venues:
        print(f"  {venue}: {cnt}")
else:
    # Direct columns
    print(f"\n=== 直接カラムで充足率確認 ===")
    for f in ["sire", "dam", "damsire"]:
        if f in cols_rr:
            filled = conn.execute(f"SELECT COUNT(*) FROM (SELECT {f} FROM race_results_ultimate ORDER BY rowid DESC LIMIT 300) WHERE {f} IS NOT NULL AND {f} != ''").fetchone()[0]
            print(f"  {f}: {filled}/300 ({filled/3:.0f}%)")

# return_tables
ret_count = conn.execute("SELECT COUNT(DISTINCT race_id) FROM return_tables_ultimate").fetchone()[0]
total_races = conn.execute("SELECT COUNT(*) FROM races_ultimate").fetchone()[0]
print(f"\n=== 払い戻しデータ充足率 ===")
print(f"払い戻しあり: {ret_count:,} / {total_races:,} ({ret_count/total_races*100:.1f}%)")

# Check future races (no results yet)
today = "20260531"
future = conn.execute(f"SELECT COUNT(*) FROM races_ultimate WHERE race_date > '{today}'").fetchone()[0]
past_today = conn.execute(f"SELECT COUNT(*) FROM races_ultimate WHERE race_date <= '{today}'").fetchone()[0]
past_no_ret = total_races - future - ret_count
print(f"当日以前レース(結果あるはず): {past_today:,}")
print(f"未来レース: {future:,}")
print(f"払い戻し欠損(過去レース - 払い戻しあり): {past_no_ret:,}")

conn.close()
print("\n=== チェック完了 ===")
