import sqlite3, json
con = sqlite3.connect('keiba/data/keiba_ultimate.db')

# 特定レースの全馬データを確認
race_id = '202604010108'
rows = con.execute(
    "SELECT data FROM race_results_ultimate WHERE race_id=? ORDER BY id",
    (race_id,)
).fetchall()
print(f'=== {race_id}: {len(rows)} horses ===')
for row in rows:
    d = json.loads(row[0])
    print(f"  馬番{d.get('horse_number')} 着順{d.get('finish_position')} オッズ{d.get('odds')} 馬名{d.get('horse_name')}")

# payoutsがあるか確認
print()
row2 = con.execute(
    "SELECT * FROM sqlite_master WHERE type='table'"
).fetchall()
print('tables:', [r[1] for r in row2])

# race_infoテーブルも確認
try:
    ri = con.execute("SELECT data FROM race_info WHERE race_id=? LIMIT 1", (race_id,)).fetchone()
    if ri:
        d = json.loads(ri[0])
        print('race_info keys:', list(d.keys()))
        if 'payouts' in d:
            print('payouts:', d['payouts'])
except Exception as e:
    print('race_info error:', e)

con.close()
