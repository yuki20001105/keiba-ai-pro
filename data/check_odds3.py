import sqlite3, json

db = 'keiba/data/keiba_ultimate.db'
with sqlite3.connect(db) as conn:
    cur = conn.cursor()
    
    # results テーブル
    cur.execute('PRAGMA table_info(results)')
    rcols = [r[1] for r in cur.fetchall()]
    print('results 列:', rcols)
    cur.execute('SELECT COUNT(*) FROM results')
    print('results 行数:', cur.fetchone()[0])
    cur.execute('SELECT COUNT(*) FROM results WHERE odds IS NOT NULL')
    print('results odds 有効行数:', cur.fetchone()[0])
    
    # race_results_ultimate は JSON BLOB
    cur.execute('SELECT race_id, data FROM race_results_ultimate LIMIT 1')
    row = cur.fetchone()
    if row:
        print('\nrace_results_ultimate sample race_id:', row[0])
        parsed = json.loads(row[1]) if isinstance(row[1], str) else row[1]
        if isinstance(parsed, list) and len(parsed) > 0:
            print('data[0] keys:', list(parsed[0].keys()) if isinstance(parsed[0], dict) else type(parsed[0]))
            if isinstance(parsed[0], dict):
                odds_keys = [k for k in parsed[0].keys() if 'odds' in k.lower() or 'prob' in k.lower()]
                print('オッズ系キー:', odds_keys)
                if odds_keys:
                    print('サンプル値:', [(k, parsed[0][k]) for k in odds_keys[:3]])
