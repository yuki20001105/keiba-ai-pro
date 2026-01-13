import sqlite3
import json

conn = sqlite3.connect('keiba/data/keiba_ultimate.db')
cursor = conn.cursor()

print("race_results_ultimateテーブル構造:")
cursor.execute('PRAGMA table_info(race_results_ultimate)')
cols = cursor.fetchall()
for col in cols:
    print(f'  {col[1]} ({col[2]})')

print("\n最初の5行のサンプル:")
cursor.execute('SELECT * FROM race_results_ultimate LIMIT 5')
rows = cursor.fetchall()
for i, row in enumerate(rows, 1):
    print(f'\n行{i}:')
    print(f'  カラム数: {len(row)}')
    if len(row) >= 2:
        print(f'  race_id: {row[1]}')
        if row[2]:
            data_str = str(row[2])[:200]
            print(f'  data (最初200文字): {data_str}')
            # JSON解析を試す
            try:
                data = json.loads(row[2])
                print(f'  JSON keys: {list(data.keys())[:10]}')
            except:
                print(f'  JSONパースエラー')

conn.close()
