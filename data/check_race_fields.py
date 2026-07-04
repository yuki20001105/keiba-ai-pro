import sqlite3, json
conn = sqlite3.connect('keiba/data/keiba_ultimate.db')
row = conn.execute("SELECT data FROM race_results_ultimate WHERE race_id='202605020401' LIMIT 1").fetchone()
if row:
    d = json.loads(row[0])
    print("Keys:", list(d.keys()))
    print("odds:", d.get('odds'))
    print("win_odds:", d.get('win_odds'))
else:
    print("No data found")
conn.close()
