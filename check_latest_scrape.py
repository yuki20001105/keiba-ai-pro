import sqlite3, json
conn = sqlite3.connect('keiba/data/scrape_jobs.db')
row = conn.execute('SELECT status, progress FROM scrape_jobs WHERE job_id=?', ('1db32a44',)).fetchone()
p = json.loads(row[1])
print('status:', row[0])
print('done:', p.get('done'), '/', p.get('total'))
print('saved_races:', p.get('saved_races'))
print('last_date:', p.get('last_date'))
conn.close()

conn2 = sqlite3.connect('keiba/data/keiba_ultimate.db')
for d in ['20260612','20260613','20260614']:
    r = conn2.execute("SELECT COUNT(*) FROM races_ultimate WHERE json_extract(data,'$.date')=?", (d,)).fetchone()[0]
    print(f'{d}: {r}レース in races_ultimate')
conn2.close()
