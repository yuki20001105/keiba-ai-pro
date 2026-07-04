import sqlite3

con = sqlite3.connect("keiba/data/keiba_ultimate.db")

# 2014-2019 の scraped_dates（いつ作られたか確認）
print("=== 2014-2019 scraped_dates サンプル（各年先頭10件） ===")
for yr in ["2014", "2015", "2017", "2018", "2019"]:
    rows = con.execute("""
        SELECT date, race_count, no_race, created_at FROM scraped_dates
        WHERE date LIKE ? ORDER BY date ASC LIMIT 5
    """, (f"{yr}%",)).fetchall()
    print(f"\n{yr}年:")
    for r in rows:
        print(f"  {r}")

# 実際の races テーブルで 2013-2020 のデータ確認
print("\n=== races テーブル: kaisai_date 2013-2020 件数 ===")
row = con.execute("""
    SELECT MIN(kaisai_date), MAX(kaisai_date), COUNT(DISTINCT kaisai_date), COUNT(*)
    FROM races WHERE kaisai_date BETWEEN '20130101' AND '20201231'
""").fetchone()
print(f"min={row[0]}, max={row[1]}, 日数={row[2]}, レース数={row[3]}")

# race_results_ultimate に2013-2020データはあるか
print("\n=== race_results_ultimate: 2013-2020 のrace_id ===")
rows2 = con.execute("""
    SELECT race_id, created_at FROM race_results_ultimate
    WHERE substr(race_id,1,4) BETWEEN '2013' AND '2020'
    ORDER BY race_id ASC LIMIT 5
""").fetchall()
for r in rows2:
    print(r)
row3 = con.execute("""
    SELECT COUNT(*) FROM race_results_ultimate
    WHERE substr(race_id,1,4) BETWEEN '2013' AND '2020'
""").fetchone()
print(f"合計: {row3[0]}件")

# scraped_dates: force_rescrape=True のジョブが通過した可能性のある日付
# → no_race かつ created_at が 2026-05-24 (35ccd2e0 run)
print("\n=== no_race=1 かつ 2026-05-24 に作成された件数（年別）===")
rows4 = con.execute("""
    SELECT substr(date,1,4), COUNT(*) FROM scraped_dates
    WHERE no_race=1 AND date(created_at) = '2026-05-24'
    GROUP BY substr(date,1,4) ORDER BY 1
""").fetchall()
for r in rows4:
    print(r)

con.close()
