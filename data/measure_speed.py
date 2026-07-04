"""ライブスクレイプ速度計測"""
import sqlite3, time, json

def snapshot():
    conn = sqlite3.connect("keiba/data/keiba_ultimate.db")
    r = conn.execute("SELECT COUNT(*) FROM races_ultimate").fetchone()[0]
    h = conn.execute("SELECT COUNT(*) FROM race_results_ultimate").fetchone()[0]
    conn.close()
    conn2 = sqlite3.connect("keiba/data/scrape_jobs.db")
    job = conn2.execute("SELECT job_id, status, progress FROM scrape_jobs ORDER BY rowid DESC LIMIT 1").fetchone()
    conn2.close()
    return r, h, job

t0 = time.time()
r0, h0, j0 = snapshot()
print(f"t=0: races={r0:,} horses={h0:,}")
if j0:
    p = json.loads(j0[2]) if j0[2] else {}
    msg = p.get("message", "")
    print(f"  job={j0[0]} status={j0[1]} | {msg}")

time.sleep(30)
r1, h1, j1 = snapshot()
elapsed = time.time() - t0
delta_r = r1 - r0
delta_h = h1 - h0
print(f"\nt={elapsed:.0f}s: races={r1:,} horses={h1:,}")
print(f"  +{delta_r}レース / +{delta_h}頭 in {elapsed:.0f}秒")
if delta_r > 0:
    print(f"  速度: {elapsed/delta_r:.1f}秒/レース, {elapsed/delta_h:.2f}秒/頭")
if j1:
    p1 = json.loads(j1[2]) if j1[2] else {}
    msg1 = p1.get("message", "")
    print(f"  job={j1[0]} status={j1[1]} | {msg1}")
