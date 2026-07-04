import json, sqlite3
conn = sqlite3.connect('keiba/data/keiba_ultimate.db')

# 5/31の全horse rowsのodds状況
rids = [r[0] for r in conn.execute("SELECT race_id FROM races_ultimate WHERE json_extract(data, '$.date') = '20260531'").fetchall()]
print(f"5/31 race count: {len(rids)}")

total_horses = 0
horses_with_odds = 0
for rid in rids:
    horses = conn.execute("SELECT data FROM race_results_ultimate WHERE race_id=?", (rid,)).fetchall()
    total_horses += len(horses)
    for h in horses:
        d = json.loads(h[0])
        if d.get('odds') and float(d.get('odds') or 0) > 0:
            horses_with_odds += 1

print(f"total horses: {total_horses}, with odds: {horses_with_odds}")

# odds>0のサンプル
if horses_with_odds > 0:
    for rid in rids:
        horses = conn.execute("SELECT data FROM race_results_ultimate WHERE race_id=?", (rid,)).fetchall()
        for h in horses:
            d = json.loads(h[0])
            if d.get('odds') and float(d.get('odds') or 0) > 0:
                print(f"  {rid}: horse={d.get('horse_number')}, odds={d.get('odds')}")
                break
        else:
            continue
        break

conn.close()
