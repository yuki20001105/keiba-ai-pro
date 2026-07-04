import sqlite3, json
conn = sqlite3.connect('keiba/data/keiba_ultimate.db')
cur = conn.cursor()
# Check for dates in late May 2026
cur.execute("SELECT DISTINCT json_extract(data,'$.date') FROM races_ultimate WHERE json_extract(data,'$.date') >= '20260520' ORDER BY 1")
rows = cur.fetchall()
print("Available dates in races_ultimate (late May 2026+):")
for r in rows:
    print(r[0])

# Also check the date range  
cur.execute("SELECT DISTINCT json_extract(data,'$.date') FROM races_ultimate ORDER BY 1 DESC LIMIT 15")
rows = cur.fetchall()
print("\nMost recent dates in races_ultimate:")
for r in rows:
    print(r[0])
conn.close()
