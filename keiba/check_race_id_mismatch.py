"""race_idとCSVファイルの不一致を調査"""
from pathlib import Path
import pandas as pd

# CSVファイルのリストを取得
csv_dir = Path("data/netkeiba/results_by_race")
csv_files = list(csv_dir.glob("*.csv"))
csv_race_ids = set(f.stem for f in csv_files)

# race_ids.csvを読み込み
race_ids_csv = Path("data/netkeiba/race_ids.csv")
if race_ids_csv.exists():
    df_race_ids = pd.read_csv(race_ids_csv, dtype=str)
    registered_race_ids = set(df_race_ids['race_id'].dropna().astype(str))
else:
    registered_race_ids = set()

print("=" * 60)
print("race_idとCSVファイルの突き合わせ")
print("=" * 60)
print(f"CSVファイル数: {len(csv_race_ids)}")
print(f"race_ids.csv登録数: {len(registered_race_ids)}")
print()

# CSVにあるがrace_ids.csvにないもの
csv_only = csv_race_ids - registered_race_ids
if csv_only:
    print(f"⚠️  CSVにあるがrace_ids.csvにない: {len(csv_only)}件")
    for rid in sorted(csv_only)[:10]:
        print(f"  - {rid}")
    if len(csv_only) > 10:
        print(f"  ... 他{len(csv_only) - 10}件")
else:
    print("✅ CSVにあるがrace_ids.csvにないもの: なし")

print()

# race_ids.csvにあるがCSVにないもの
registered_only = registered_race_ids - csv_race_ids
if registered_only:
    print(f"⚠️  race_ids.csvにあるがCSVにない: {len(registered_only)}件")
    for rid in sorted(registered_only)[:10]:
        print(f"  - {rid}")
    if len(registered_only) > 10:
        print(f"  ... 他{len(registered_only) - 10}件")
else:
    print("✅ race_ids.csvにあるがCSVにないもの: なし")

print()
print("=" * 60)
print("【まとめ】")
print(f"  一致: {len(csv_race_ids & registered_race_ids)}件")
print(f"  CSV独自: {len(csv_only)}件")
print(f"  race_ids.csv独自: {len(registered_only)}件")
