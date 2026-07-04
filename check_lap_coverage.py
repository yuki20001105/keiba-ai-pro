import sqlite3, json
conn = sqlite3.connect('keiba/data/keiba_ultimate.db')

# races_ultimate のサンプルでlap_cumulativeを確認
rows = conn.execute("""
    SELECT race_id, data FROM races_ultimate
    LIMIT 500
""").fetchall()

has_lap = 0
no_lap = 0
sample_with = None
sample_without = None
for race_id, data in rows:
    d = json.loads(data)
    lc = d.get('lap_cumulative')
    if lc and len(lc) > 0:
        has_lap += 1
        if sample_with is None:
            sample_with = (race_id, lc)
    else:
        no_lap += 1
        if sample_without is None:
            sample_without = (race_id, lc)

print(f"lap_cumulative あり: {has_lap} / {has_lap+no_lap}")
print(f"lap_cumulative なし/空: {no_lap} / {has_lap+no_lap}")
if sample_with:
    print(f"サンプル(あり): race_id={sample_with[0]}, lap={sample_with[1]}")
if sample_without:
    print(f"サンプル(なし): race_id={sample_without[0]}, lap={sample_without[1]}")

# 年別カバレッジ
print("\n年別 lap_cumulative カバレッジ:")
rows2 = conn.execute("SELECT race_id, data FROM races_ultimate").fetchall()
year_total = {}
year_has = {}
for race_id, data in rows2:
    y = race_id[:4]
    year_total[y] = year_total.get(y, 0) + 1
    d = json.loads(data)
    lc = d.get('lap_cumulative')
    if lc and len(lc) > 0:
        year_has[y] = year_has.get(y, 0) + 1

for y in sorted(year_total.keys()):
    t = year_total[y]
    h = year_has.get(y, 0)
    print(f"  {y}: {h}/{t} ({100*h/t:.0f}%)")

conn.close()
