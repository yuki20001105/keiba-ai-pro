import sqlite3, json

conn = sqlite3.connect('keiba/data/keiba_ultimate.db')
cur = conn.cursor()

# 2014/2016 の no_race vs 開催あり
print("=== scraped_dates no_race内訳 ===")
for yr in ['2013','2014','2015','2016','2017']:
    cur.execute("SELECT no_race, COUNT(*) FROM scraped_dates WHERE date LIKE ? GROUP BY no_race", (yr+'%',))
    rows = {r[0]: r[1] for r in cur.fetchall()}
    print(f"  {yr}: has_race={rows.get(0,0)}, no_race={rows.get(1,0)}")

# races_ultimate の date(JSON) 年別
cur.execute("SELECT json_extract(data,'$.date') d, race_id FROM races_ultimate LIMIT 5")
print("\nsample dates:", [r for r in cur.fetchall()])

cur.execute("SELECT COUNT(*) FROM races_ultimate")
print("Total:", cur.fetchone()[0])

conn.close()
