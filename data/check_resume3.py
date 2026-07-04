import sqlite3

con = sqlite3.connect("keiba/data/keiba_ultimate.db")

# 年別スクレイプ状況
print("=== scraped_dates 年別カバレッジ ===")
rows = con.execute("""
    SELECT substr(date,1,4) as yr, 
           COUNT(*) as total_dates,
           SUM(CASE WHEN race_count > 0 THEN 1 ELSE 0 END) as dates_with_races,
           SUM(CASE WHEN no_race = 1 THEN 1 ELSE 0 END) as no_race_days,
           SUM(race_count) as total_races
    FROM scraped_dates
    GROUP BY yr ORDER BY yr
""").fetchall()
for r in rows:
    print(f"  {r[0]}: {r[2]}/{r[1]}日にレース ({r[4]}レース), no_race={r[3]}")

# scraped_dates に2013-2020の間で race_count=0 かつ no_race=0 の「未取得」日
print()
print("=== 2013-2020: 未取得（race_count=0 AND no_race=0）の件数 ===")
row = con.execute("""
    SELECT COUNT(*) FROM scraped_dates 
    WHERE date BETWEEN '20130101' AND '20200630' 
      AND race_count = 0 AND no_race = 0
""").fetchone()
print(f"未取得日数: {row[0]}")

# 2013-2020の間で正常に保存された最後の日
print()
print("=== 2013-2020で race_count > 0 の最新5件 ===")
rows2 = con.execute("""
    SELECT date, race_count, created_at FROM scraped_dates
    WHERE date BETWEEN '20130101' AND '20200630' AND race_count > 0
    ORDER BY date DESC LIMIT 5
""").fetchall()
for r in rows2:
    print(r)

print()
print("=== 2013-2020で race_count > 0 の最古5件 ===")
rows3 = con.execute("""
    SELECT date, race_count, created_at FROM scraped_dates
    WHERE date BETWEEN '20130101' AND '20200630' AND race_count > 0
    ORDER BY date ASC LIMIT 5
""").fetchall()
for r in rows3:
    print(r)

# force_rescrape ジョブ 35ccd2e0 で「実際に保存された」最後の日を推定
# → scrape_jobs.db の http_400 以外のイベントを確認
con.close()

# scrape_jobs.db の確認
con2 = sqlite3.connect("keiba/data/scrape_jobs.db")
print()
print("=== 35ccd2e0: forced_stop以外のイベント（保存成功した日） ===")
rows4 = con2.execute("""
    SELECT event, date_processing, races_scraped, days_scraped, consecutive_400_count, note
    FROM scrape_param_log
    WHERE job_id='35ccd2e0' AND event NOT IN ('forced_stop','http_400')
    ORDER BY timestamp DESC LIMIT 20
""").fetchall()
for r in rows4:
    print(r)

print()
print("=== 35ccd2e0: ジョブステータス ===")
rows5 = con2.execute("SELECT * FROM scrape_jobs WHERE job_id='35ccd2e0'").fetchall()
for r in rows5:
    print(r[:4])  # job_id, status, created_at, params_jsonなど最初の4列

con2.close()
