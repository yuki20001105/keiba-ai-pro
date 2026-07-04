import sqlite3, json
con = sqlite3.connect('keiba/data/keiba_ultimate.db')
row = con.execute("SELECT race_id, data FROM race_results_ultimate ORDER BY race_id DESC LIMIT 1").fetchone()
if row:
    d = json.loads(row[1])
    print('race_id:', row[0])
    print('keys:', list(d.keys()))
    if 'entries' in d and d['entries']:
        print('entry[0] keys:', list(d['entries'][0].keys()))
        print('entry[0]:', d['entries'][0])
    if 'payouts' in d:
        print('payouts:', d['payouts'])
    if 'results' in d:
        print('results:', d['results'][:3])
else:
    row2 = con.execute("SELECT race_id FROM race_results_ultimate LIMIT 5").fetchall()
    print('sample:', row2)
con.close()
