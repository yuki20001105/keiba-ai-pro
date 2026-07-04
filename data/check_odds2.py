import sqlite3

db = 'keiba/data/keiba_ultimate.db'
with sqlite3.connect(db) as conn:
    cur = conn.cursor()
    # race_results_ultimate の全列確認
    cur.execute('PRAGMA table_info(race_results_ultimate)')
    cols = [r[1] for r in cur.fetchall()]
    print('race_results_ultimate 列数:', len(cols))
    print('全列:', cols)
    
    # 数値系でオッズっぽい列
    num_like = [c for c in cols if any(x in c.lower() for x in ['prob','rate','payout','tansho','odds','implied'])]
    print('確率/オッズ系:', num_like)
    
    # entries テーブルの内容確認
    cur.execute('PRAGMA table_info(entries)')
    ecols = [r[1] for r in cur.fetchall()]
    print('\nentries 列:', ecols)
    cur.execute('SELECT COUNT(*) FROM entries')
    print('entries 行数:', cur.fetchone()[0])
    cur.execute('SELECT COUNT(*) FROM entries WHERE odds IS NOT NULL')
    print('entries odds 有効行数:', cur.fetchone()[0])
