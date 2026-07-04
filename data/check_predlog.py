import sqlite3
conn = sqlite3.connect('keiba/data/keiba_ultimate.db')
rows = conn.execute(
    "SELECT race_id, race_name, model_id, predicted_at FROM prediction_log "
    "WHERE race_date='20260503' GROUP BY race_id ORDER BY predicted_at DESC LIMIT 5"
).fetchall()
for r in rows:
    print(r)
conn.close()
