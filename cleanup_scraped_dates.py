"""
scraped_dates の汚染エントリを削除し、正しい状態に戻す。

今回の IP ブロック中に HTTP 400（空ボディ）を「非開催日」と誤認して
no_race=1 が書き込まれた 2014〜2024 年のエントリを削除する。

保持するもの:
  - 2013年: 有効な取得済み（has_race 含む）
  - 2015年: 有効な取得済み
  - 2025年: 有効（本物のレースデータあり）
  - 2026年: 有効
  - 2017年の既存 has_race エントリ（元々あった 9〜10日分）

削除するもの:
  - 2014年の no_race=1 エントリ（今回の IP ブロックで汚染）
  - 2016年の no_race=1 エントリ（今回の IP ブロックで汚染）
  - 2017〜2024年の全エントリ（全て今回の IP ブロックで誤追加）
"""
import sqlite3

DB_PATH = "keiba/data/keiba_ultimate.db"

conn = sqlite3.connect(DB_PATH)
conn.execute("PRAGMA journal_mode=WAL")

# 削除前の状態確認
print("=== 削除前 ===")
rows = conn.execute(
    "SELECT substr(date,1,4) yr, no_race, COUNT(*) cnt FROM scraped_dates GROUP BY yr, no_race ORDER BY yr, no_race"
).fetchall()
for r in rows:
    label = "no_race" if r[1] == 1 else "has_race"
    print(f"  {r[0]}: {label} = {r[2]}日")

total_before = conn.execute("SELECT COUNT(*) FROM scraped_dates").fetchone()[0]
print(f"\n合計: {total_before}行")

# ========================================
# 削除対象: IP ブロック中に誤書き込みされた no_race=1 エントリ
# 2014, 2016 の no_race=1 のみ削除（has_race は有効な可能性あり）
# 2017〜2024: 全て削除（scraped_dates に全く存在しなかった年 → 全部今回の汚染）
# ========================================

# 2014 の no_race=1 を削除
r1 = conn.execute("DELETE FROM scraped_dates WHERE date LIKE '2014%' AND no_race = 1")
print(f"\n削除: 2014 no_race=1 → {r1.rowcount}行")

# 2016 の no_race=1 を削除
r2 = conn.execute("DELETE FROM scraped_dates WHERE date LIKE '2016%' AND no_race = 1")
print(f"削除: 2016 no_race=1 → {r2.rowcount}行")

# 2017〜2024 の全エントリを削除（元々0日だった→全て汚染）
for yr in range(2017, 2025):
    rx = conn.execute(f"DELETE FROM scraped_dates WHERE date LIKE '{yr}%'")
    if rx.rowcount > 0:
        print(f"削除: {yr} 全エントリ → {rx.rowcount}行")

conn.commit()

# 削除後の確認
print("\n=== 削除後 ===")
rows = conn.execute(
    "SELECT substr(date,1,4) yr, no_race, COUNT(*) cnt FROM scraped_dates GROUP BY yr, no_race ORDER BY yr, no_race"
).fetchall()
for r in rows:
    label = "no_race" if r[1] == 1 else "has_race"
    print(f"  {r[0]}: {label} = {r[2]}日")

total_after = conn.execute("SELECT COUNT(*) FROM scraped_dates").fetchone()[0]
print(f"\n合計: {total_after}行（削除: {total_before - total_after}行）")

conn.close()
print("\nクリーンアップ完了")
