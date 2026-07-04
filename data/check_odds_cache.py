import sqlite3, json, sys
sys.path.insert(0, "python-api")
sys.path.insert(0, "keiba")
from app_config import ULTIMATE_DB

conn = sqlite3.connect(str(ULTIMATE_DB))
rows = conn.execute(
    "SELECT data FROM race_results_ultimate WHERE race_id=? LIMIT 8",
    ("202605021201",)
).fetchall()
print(f"== 202605021201 horse records: {len(rows)} ==")
for r in rows:
    d = json.loads(r[0])
    hn = d.get("horse_number")
    odds = d.get("odds")
    sire = str(d.get("sire", ""))[:12]
    shutuba = d.get("_shutuba")
    print(f"  horse#{hn:>2}  odds={odds!r:>8}  sire={sire!r:>14}  _shutuba={shutuba}")
conn.close()
