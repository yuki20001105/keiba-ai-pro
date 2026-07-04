import sqlite3, time

conn = sqlite3.connect('keiba/data/keiba_ultimate.db')

# EXPLAIN QUERY PLAN でインデックス使用を確認
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
print('=== QUERY PLAN ===')
for p in plan:
    print(p)

# 実際のクエリ速度
print('\n=== QUERY SPEED ===')
for d in ['20180317', '20180310']:
    t = time.time()
    rows = conn.execute("""
        SELECT r.race_id, r.data
        FROM races_ultimate r
        WHERE json_extract(r.data, '$.date') = ?
          AND EXISTS (
              SELECT 1 FROM race_results_ultimate rr
              WHERE rr.race_id = r.race_id
          )
    """, (d,)).fetchall()
    ms = (time.time()-t)*1000
    print(f'{d}: count={len(rows)} ({ms:.1f}ms)')

conn.close()
