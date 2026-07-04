import sqlite3

con = sqlite3.connect("keiba/data/keiba_ultimate.db")

# race_results_ultimate の race_id 先頭4桁（年）で集計
print("=== race_results_ultimate: 年別件数 ===")
rows = con.execute("""
    SELECT substr(race_id,1,4) as yr, COUNT(DISTINCT race_id) as races, COUNT(*) as records
    FROM race_results_ultimate
    GROUP BY yr ORDER BY yr
""").fetchall()
for r in rows:
    print(f"  {r[0]}: {r[1]}レース, {r[2]}レコード")

# scraped_dates 年別（全年）
print()
print("=== scraped_dates: 年別カバレッジ（全年）===")
rows2 = con.execute("""
    SELECT substr(date,1,4) as yr,
           COUNT(*) as total_dates,
           SUM(CASE WHEN race_count > 0 THEN 1 ELSE 0 END) as dates_with_races,
           SUM(CASE WHEN no_race = 1 THEN 1 ELSE 0 END) as no_race_days
    FROM scraped_dates
    GROUP BY yr ORDER BY yr
""").fetchall()
for r in rows2:
    print(f"  {r[0]}: scraped={r[1]}, with_races={r[2]}, no_race={r[3]}")

con.close()
