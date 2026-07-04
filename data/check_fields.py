import sqlite3, json
conn = sqlite3.connect('keiba/data/keiba_ultimate.db')
cur = conn.cursor()
cur.execute("SELECT race_id, data FROM races_ultimate WHERE race_id LIKE '2026%' LIMIT 5")
for rid, d in cur.fetchall():
    j = json.loads(d)
    fields = {k:v for k,v in j.items() if k in ['post_time','race_time','start_time','weight_condition','race_class','round_no','day_no','weight_type']}
    print(rid, fields)
print("---all keys---")
cur.execute("SELECT data FROM races_ultimate WHERE race_id LIKE '2026%' LIMIT 1")
row = cur.fetchone()
if row:
    print(list(json.loads(row[0]).keys()))
conn.close()
