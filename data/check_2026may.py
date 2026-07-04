import sqlite3, json
conn = sqlite3.connect('keiba/data/keiba_ultimate.db')
cur = conn.cursor()
# Check races_ultimate for recent 2026 dates
cur.execute("SELECT race_id, json_extract(data,'$.date') as d FROM races_ultimate WHERE race_id LIKE '202605%' ORDER BY race_id LIMIT 20")
rows = cur.fetchall()
print("races_ultimate (May 2026):")
for r in rows:
    print(r)

# Check race_results_ultimate 
cur.execute("SELECT COUNT(*), MIN(race_id), MAX(race_id) FROM race_results_ultimate WHERE race_id LIKE '202605%'")
print("\nrace_results_ultimate May 2026:", cur.fetchone())
conn.close()
