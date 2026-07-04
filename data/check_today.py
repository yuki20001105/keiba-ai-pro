from datetime import datetime
import sqlite3, os
print('Today:', datetime.now().strftime('%Y%m%d'))
db_path = 'keiba/data/keiba_ultimate.db'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.execute('SELECT DISTINCT race_date FROM race_results_ultimate ORDER BY race_date DESC LIMIT 5')
    print('Recent race dates in DB:', [r[0] for r in cur.fetchall()])
    conn.close()
