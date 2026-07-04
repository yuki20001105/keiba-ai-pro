import sqlite3, json, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python-api'))

DB = r"C:\Users\yuki2\Documents\ws\keiba-ai-pro\keiba\data\keiba_ultimate.db"
conn = sqlite3.connect(DB)
cur = conn.cursor()

# 5月2日のレースを1件詳細表示
cur.execute("SELECT race_id, data FROM race_results_ultimate WHERE race_id LIKE '20260502%' LIMIT 1")
row = cur.fetchone()
if row:
    rid, d = row
    rec = json.loads(d)
    print(f"=== {rid} ===")
    for k, v in sorted(rec.items()):
        print(f"  {k} = {repr(v)[:80]}")
else:
    print("No 20260502 races found")

# race_results_ultimate に odds が入っている行の数
cur.execute("SELECT COUNT(*) FROM race_results_ultimate")
total = cur.fetchone()[0]
# サンプル100件でoddsフィールドを確認
cur.execute("SELECT data FROM race_results_ultimate ORDER BY rowid DESC LIMIT 200")
rows = cur.fetchall()
has_odds = 0
for (d,) in rows:
    r = json.loads(d)
    if r.get('odds') is not None:
        has_odds += 1
print(f"\nLatest 200 rows: has_odds={has_odds}/200")
print(f"Total rows: {total}")

conn.close()
