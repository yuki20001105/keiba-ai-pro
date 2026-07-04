"""
scraped_dates の誤 no_race エントリをクリアする
（IPブロック由来の2018-2020 no_race=1 エントリを削除）
"""
import sqlite3

con = sqlite3.connect("keiba/data/keiba_ultimate.db")

# 削除対象確認
rows = con.execute("""
    SELECT substr(date,1,4) as yr, COUNT(*) 
    FROM scraped_dates 
    WHERE no_race=1 AND date BETWEEN '20180101' AND '20201231'
    GROUP BY yr ORDER BY yr
""").fetchall()
print("削除対象（2018-2020 no_race=1）:")
for r in rows:
    print(f"  {r[0]}: {r[1]}件")

# 削除実行
cur = con.execute("""
    DELETE FROM scraped_dates 
    WHERE no_race=1 AND date BETWEEN '20180101' AND '20201231'
""")
print(f"\n削除件数: {cur.rowcount}件")
con.commit()
con.close()
print("完了")
