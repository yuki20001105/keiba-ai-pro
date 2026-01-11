import sqlite3
conn = sqlite3.connect('data/keiba.db')
cur = conn.cursor()
cur.execute('SELECT race_id FROM races LIMIT 5')
races = cur.fetchall()
print('登録されているレースID:')
for r in races:
    print(f'  {r[0]}')
conn.close()
