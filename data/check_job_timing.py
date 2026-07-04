"""スクレイプジョブ履歴・処理時間チェック"""
import sqlite3, json
from datetime import datetime

conn = sqlite3.connect("keiba/data/scrape_jobs.db")
tables = [t[0] for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("Tables:", tables)

if "scrape_jobs" in tables:
    rows = conn.execute("SELECT job_id, status, created_at, updated_at, progress, result FROM scrape_jobs ORDER BY rowid DESC LIMIT 10").fetchall()
    for r in rows:
        job_id, status, created, updated, progress, result = r
        params = None
        print(f"\n--- job_id: {job_id} ---")
        print(f"  status: {status}")
        print(f"  created: {created}  updated: {updated}")
        # Elapsed
        try:
            t0 = datetime.fromisoformat(created)
            t1 = datetime.fromisoformat(updated)
            elapsed = (t1 - t0).total_seconds()
            print(f"  elapsed: {elapsed:.0f}秒")
        except Exception:
            pass
        if params:
            p = json.loads(params) if isinstance(params, str) else params
            print(f"  params: {p}")
        if progress:
            pr = json.loads(progress) if isinstance(progress, str) else progress
            saved = pr.get("saved_races", 0)
            saved_h = pr.get("saved_horses", 0)
            if saved:
                print(f"  saved: {saved}レース / {saved_h}頭")

conn.close()
