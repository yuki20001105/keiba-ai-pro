import sqlite3
conn = sqlite3.connect('keiba/data/keiba_ultimate.db')

# インデックス定義確認
rows = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL").fetchall()
for r in rows:
    print(r[0], ':')
    print(' ', r[1])
    print()

# クエリプランで確認
print('=== EXPLAIN QUERY PLAN for by_date ===')
plan = conn.execute("""
EXPLAIN QUERY PLAN
SELECT r.race_id, r.data
FROM races_ultimate r
WHERE json_extract(r.data, '$.date') = '20180317'
  AND EXISTS (
      SELECT 1 FROM race_results_ultimate rr
      WHERE rr.race_id = r.race_id
  )
""").fetchall()
for p in plan:
    print(p)

conn.close()
