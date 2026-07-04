import sqlite3, json
conn = sqlite3.connect("keiba/data/scrape_jobs.db")
rows = conn.execute("SELECT job_id, progress, result FROM scrape_jobs ORDER BY rowid DESC LIMIT 5").fetchall()
for job_id, prog, result in rows:
    print(f"\n=== {job_id} ===")
    if prog:
        p = json.loads(prog) if isinstance(prog, str) else prog
        print(f"  progress: {json.dumps(p, ensure_ascii=False, indent=2)[:600]}")
    if result:
        r = json.loads(result) if isinstance(result, str) else result
        print(f"  result: {json.dumps(r, ensure_ascii=False, indent=2)[:600]}")
conn.close()
