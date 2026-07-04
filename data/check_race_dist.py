"""実際のレース数と日付を確認するスクリプト"""
import sqlite3, json
from pathlib import Path

DB = Path("keiba/data/keiba_ultimate.db")
conn = sqlite3.connect(str(DB))

# 2025年（最も完全な過去年）
cnt25 = conn.execute(
    "SELECT COUNT(*) FROM races_ultimate WHERE substr(race_id,1,4)='2025'"
).fetchone()[0]
cnt26 = conn.execute(
    "SELECT COUNT(*) FROM races_ultimate WHERE substr(race_id,1,4)='2026'"
).fetchone()[0]
print(f"2025年: {cnt25:,}レース（完全年）")
print(f"2026年: {cnt26:,}レース（1月〜5月末 ≈ 5ヶ月）")
print(f"2026年から年間換算: {cnt26 / 5 * 12:.0f}レース/年")

# JSONの日付フィールドを確認
sample = conn.execute(
    "SELECT data FROM races_ultimate WHERE substr(race_id,1,4)='2026' LIMIT 1"
).fetchone()
if sample:
    d = json.loads(sample[0])
    date_val = d.get("date") or d.get("race_date") or d.get("kaisai_date") or d.get("event_date")
    print(f"\nJSON date field: {date_val}")
    print(f"JSON keys (first 15): {list(d.keys())[:15]}")

# 2026年の月別（JSONのdate フィールドから）
print("\n2026年 実際の月別（2026xx 全件より開催分布確認）:")
all_dates = conn.execute(
    "SELECT json_extract(data,'$.date') FROM races_ultimate WHERE substr(race_id,1,4)='2026'"
).fetchall()
from collections import Counter
months = Counter()
for (d,) in all_dates:
    if d and len(str(d)) >= 6:
        months[str(d)[:6]] += 1
for m, c in sorted(months.items()):
    print(f"  {m}: {c}レース")

conn.close()
