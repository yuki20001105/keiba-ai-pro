import sqlite3
conn = sqlite3.connect('keiba/data/keiba_ultimate.db')
for d in ['20260611','20260612','20260613','20260614','20260615']:
    r1 = conn.execute("SELECT COUNT(*) FROM races_ultimate WHERE json_extract(data,'$.date')=?", (d,)).fetchone()[0]
    r2 = conn.execute("SELECT COUNT(*) FROM race_results_ultimate WHERE race_id IN (SELECT race_id FROM races_ultimate WHERE json_extract(data,'$.date')=?)", (d,)).fetchone()[0]
    print(f'{d}: races={r1}, results={r2}')
conn.close()
