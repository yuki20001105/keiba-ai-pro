import sqlite3
conn = sqlite3.connect('keiba/data/keiba_ultimate.db')
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print('テーブル一覧:')
for t in tables:
    cnt = conn.execute('SELECT COUNT(*) FROM ' + t[0]).fetchone()[0]
    print('  ' + t[0] + ': ' + str(cnt) + '行')
conn.close()
