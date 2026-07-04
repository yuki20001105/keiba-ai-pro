import sqlite3

conn = sqlite3.connect('keiba/data/keiba_ultimate.db')
cur = conn.cursor()

cur.execute("SELECT substr(json_extract(data,'$.race_date'),1,4) yr, COUNT(*) cnt FROM races_ultimate GROUP BY yr ORDER BY yr")
print("=== races_ultimate 年別 ===")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]}")

cur.execute("SELECT COUNT(*) FROM scraped_dates")
print(f"\nscraped_dates 合計: {cur.fetchone()[0]} 行")

cur.execute("SELECT substr(date,1,4) yr, COUNT(*) cnt FROM scraped_dates GROUP BY yr ORDER BY yr")
print("=== scraped_dates 年別 ===")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]}")

cur.execute("SELECT COUNT(*) FROM race_results_ultimate")
print(f"\nrace_results_ultimate: {cur.fetchone()[0]} 行")

conn.close()
