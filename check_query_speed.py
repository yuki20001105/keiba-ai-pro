import sqlite3, time
conn = sqlite3.connect('keiba/data/keiba_ultimate.db')

# race_results_ultimate のスキーマ確認
rows = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='race_results_ultimate'").fetchone()
print('Schema:', rows[0][:200] if rows else 'not found')

# クエリ速度テスト before
print('\n=== BEFORE (no index on race_id) ===')
t0 = time.time()
r = conn.execute("""
SELECT r.race_id, r.data
FROM races_ultimate r
WHERE json_extract(r.data, '$.date') = '20180317'
  AND EXISTS (
      SELECT 1 FROM race_results_ultimate rr
      WHERE rr.race_id = r.race_id
  )
""").fetchall()
elapsed = time.time() - t0
print(f'count={len(r)}, elapsed={elapsed:.3f}s')
conn.close()
