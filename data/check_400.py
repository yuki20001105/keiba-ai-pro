import sqlite3, json
con = sqlite3.connect("keiba/data/scrape_jobs.db")
tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("tables:", tables)
if "scrape_param_log" in tables:
    rows = con.execute(
        "SELECT event, date_processing, consecutive_400_count, note, timestamp "
        "FROM scrape_param_log WHERE job_id='35ccd2e0' ORDER BY id DESC LIMIT 30"
    ).fetchall()
    for r in rows:
        print(r)
else:
    print("scrape_param_log not found")
con.close()
