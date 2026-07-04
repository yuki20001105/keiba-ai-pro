import sqlite3, json
con = sqlite3.connect('keiba/data/keiba_ultimate.db')

# return_tables_ultimate の構造と内容確認
cols = [r[1] for r in con.execute('PRAGMA table_info(return_tables_ultimate)').fetchall()]
print('return_tables_ultimate cols:', cols)

rows = con.execute("SELECT * FROM return_tables_ultimate LIMIT 3").fetchall()
for r in rows:
    print(r)

# 着順ありのレースを探す
print()
row = con.execute(
    "SELECT race_id, data FROM race_results_ultimate WHERE json_extract(data, '$.finish_position') IS NOT NULL LIMIT 1"
).fetchone()
if row:
    d = json.loads(row[1])
    print('valid result race_id:', row[0])
    print('finish_position:', d.get('finish_position'))
    print('odds:', d.get('odds'))
    # 同レースの全馬
    rows2 = con.execute(
        "SELECT data FROM race_results_ultimate WHERE race_id=? ORDER BY id", (row[0],)
    ).fetchall()
    print(f'horses in {row[0]}:')
    for r2 in rows2:
        d2 = json.loads(r2[0])
        print(f"  馬番{d2.get('horse_number')} 着順{d2.get('finish_position')} オッズ{d2.get('odds')}")
    # return_tables確認
    rt = con.execute(
        "SELECT * FROM return_tables_ultimate WHERE race_id=? LIMIT 1", (row[0],)
    ).fetchone()
    if rt:
        print('return_table:', rt)

con.close()
