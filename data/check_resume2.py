import sqlite3

con = sqlite3.connect("keiba/data/keiba_ultimate.db")

# scraped_dates で確認
print("=== scraped_dates 最新20件 ===")
rows = con.execute("SELECT date, race_count, no_race, created_at FROM scraped_dates ORDER BY date DESC LIMIT 20").fetchall()
for r in rows:
    print(r)

print()
print("=== scraped_dates の範囲 ===")
row = con.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM scraped_dates").fetchone()
print(f"最古: {row[0]}, 最新: {row[1]}, 合計: {row[2]}件")

print()
print("=== race_count > 0 の最新20件 ===")
rows2 = con.execute("SELECT date, race_count, created_at FROM scraped_dates WHERE race_count > 0 ORDER BY date DESC LIMIT 20").fetchall()
for r in rows2:
    print(r)

print()
print("=== race_results_ultimate の件数 ===")
row2 = con.execute("SELECT COUNT(*) FROM race_results_ultimate").fetchone()
print(f"総レコード数: {row2[0]}")

# race_id からレース日付を推定（race_idは通常12桁: yyyymmddVVRR）
print()
print("=== race_results_ultimate 最新race_id ===")
rows3 = con.execute("SELECT race_id, created_at FROM race_results_ultimate ORDER BY race_id DESC LIMIT 10").fetchall()
for r in rows3:
    print(r)

con.close()
