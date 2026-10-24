import sqlite3
import pandas as pd
from pathlib import Path

DB = Path(r"C:\Users\yuki2\Documents\ws\keiba-ai-pro\keiba\data\keiba_ultimate.db")
OUT = Path(r"C:\Users\yuki2\Documents\ws\keiba-ai-pro\keiba\data\db_quality_audit")
OUT.mkdir(exist_ok=True)

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.execute("PRAGMA query_only = ON")

# 日付単位の不足
pd.read_sql("""
SELECT *
FROM scrape_date_completeness
WHERE status <> 'complete'
ORDER BY race_date
""", con).to_csv(
    OUT / "incomplete_dates.csv",
    index=False,
    encoding="utf-8-sig"
)

# レース単位の未完了
pd.read_sql("""
SELECT
    race_date,
    race_id,
    lifecycle,
    quality_status,
    last_error,
    exclusion_reason,
    first_seen_at,
    last_attempt_at,
    completed_at
FROM scrape_race_acquisition_state
WHERE quality_status <> 'complete'
   OR completed_at IS NULL
ORDER BY race_date, race_id
""", con).to_csv(
    OUT / "incomplete_races.csv",
    index=False,
    encoding="utf-8-sig"
)

# 未修復項目
pd.read_sql("""
SELECT *
FROM scrape_repair_queue
WHERE status <> 'completed'
ORDER BY entity_type, entity_id, repair_kind
""", con).to_csv(
    OUT / "pending_repairs.csv",
    index=False,
    encoding="utf-8-sig"
)

# 古い scraped_dates と新しい完全性台帳の矛盾
pd.read_sql("""
SELECT
    s.date,
    s.race_count AS legacy_race_count,
    s.no_race AS legacy_no_race,
    c.expected_race_count,
    c.status AS current_status,
    c.missing_race_ids_json
FROM scraped_dates s
JOIN scrape_date_completeness c
  ON c.race_date = s.date
WHERE
      (s.no_race = 1 AND c.expected_race_count > 0)
   OR (s.race_count <> c.expected_race_count)
ORDER BY s.date
""", con).to_csv(
    OUT / "legacy_state_conflicts.csv",
    index=False,
    encoding="utf-8-sig"
)

con.close()

print("完了:", OUT)
