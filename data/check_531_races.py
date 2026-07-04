import sqlite3, json
conn = sqlite3.connect('keiba/data/keiba_ultimate.db')
rows = conn.execute("""
  SELECT race_id,
         json_extract(data,'$.date') as d,
         json_extract(data,'$.venue') as v,
         json_extract(data,'$.venue_code') as vc
  FROM races_ultimate
  WHERE json_extract(data,'$.date') = '20260531'
  ORDER BY race_id
""").fetchall()
print(f"Total: {len(rows)} races")
for r in rows:
    print(r)
conn.close()
