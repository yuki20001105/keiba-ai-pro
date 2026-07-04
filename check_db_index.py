import sqlite3
conn = sqlite3.connect('keiba/data/keiba_ultimate.db')
rows = conn.execute("SELECT name, tbl_name FROM sqlite_master WHERE type='index' ORDER BY tbl_name").fetchall()
for r in rows:
    print(r[1], '->', r[0])

# WALモード確認
mode = conn.execute('PRAGMA journal_mode').fetchone()
print('\njournal_mode:', mode[0])

# races_ultimate のサイズ
cnt = conn.execute('SELECT COUNT(*) FROM races_ultimate').fetchone()[0]
print('races_ultimate rows:', cnt)

# race_results_ultimate のサイズ
cnt2 = conn.execute('SELECT COUNT(*) FROM race_results_ultimate').fetchone()[0]
print('race_results_ultimate rows:', cnt2)
conn.close()
