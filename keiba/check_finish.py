"""データベースとCSVのfinish列を確認"""
import sqlite3
import pandas as pd

# データベースのfinish列を確認
conn = sqlite3.connect('data/keiba.db')
cur = conn.cursor()
cur.execute('SELECT finish, COUNT(*) FROM results GROUP BY finish ORDER BY finish')
print('【データベース】finish列の分布:')
for row in cur.fetchall():
    print(f'  finish={row[0]}: {row[1]}頭')

print()

# CSVのfinish列を確認
csv_file = 'data/netkeiba/results_by_race/202401010101.csv'
df = pd.read_csv(csv_file, encoding='utf-8-sig')
print(f'【CSV】{csv_file}')
print(f'カラム: {df.columns.tolist()[:10]}')
if 'finish' in df.columns:
    print(f'\nfinish列:')
    print(df[['horse_name', 'finish']].head(10).to_string(index=False))
else:
    # 着順に関連するカラムを探す
    finish_cols = [c for c in df.columns if '着' in str(c) or 'finish' in str(c).lower()]
    print(f'\n着順関連カラム: {finish_cols}')
    if finish_cols:
        print(df[finish_cols[:3]].head(5))

conn.close()
