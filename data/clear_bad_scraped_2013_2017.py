import sqlite3

con = sqlite3.connect("keiba/data/keiba_ultimate.db")

rows = con.execute(
    "SELECT substr(date,1,4) as yr, COUNT(*) FROM scraped_dates "
    "WHERE no_race=1 AND date BETWEEN '20130101' AND '20171231' GROUP BY yr ORDER BY yr"
).fetchall()
print("削除対象（2013-2017 no_race=1）:")
for r in rows:
    print(f"  {r[0]}: {r[1]}件")

cur = con.execute(
    "DELETE FROM scraped_dates WHERE no_race=1 AND date BETWEEN '20130101' AND '20171231'"
)
print(f"削除件数: {cur.rowcount}件")
con.commit()

print("\n削除後 2013-2020 scraped_dates 残件数:")
rows2 = con.execute(
    "SELECT substr(date,1,4), COUNT(*), SUM(CASE WHEN race_count>0 THEN 1 ELSE 0 END) "
    "FROM scraped_dates WHERE date BETWEEN '20130101' AND '20201231' "
    "GROUP BY substr(date,1,4) ORDER BY 1"
).fetchall()
for r in rows2:
    print(f"  {r[0]}: {r[1]}件 (race_count>0: {r[2]}件)")
con.close()
