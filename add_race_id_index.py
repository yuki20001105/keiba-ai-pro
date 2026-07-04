import sqlite3, time

print('Adding index idx_rru_race_id on race_results_ultimate(race_id)...')
conn = sqlite3.connect('keiba/data/keiba_ultimate.db', timeout=60)
conn.execute('PRAGMA journal_mode=WAL')
t0 = time.time()
conn.execute('CREATE INDEX IF NOT EXISTS idx_rru_race_id ON race_results_ultimate (race_id)')
conn.commit()
elapsed = time.time() - t0
print(f'Index created in {elapsed:.1f}s')
conn.close()

# 速度テスト after
print('\n=== AFTER (with index) ===')
conn2 = sqlite3.connect('keiba/data/keiba_ultimate.db')
t0 = time.time()
r = conn2.execute("""
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
conn2.close()
