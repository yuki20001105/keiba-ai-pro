"""10年分スクレイプ時間試算スクリプト"""
import sqlite3
from pathlib import Path

DB = Path("keiba/data/keiba_ultimate.db")
CACHE = Path("keiba/data/pedigree_cache.db")
conn = sqlite3.connect(str(DB))

# 年別レース数
rows = conn.execute(
    "SELECT substr(race_id,1,4) AS year, COUNT(*) AS cnt "
    "FROM races_ultimate GROUP BY year ORDER BY year"
).fetchall()
print("=== 年別レース数 ===")
for r in rows:
    print(f"  {r[0]}: {r[1]:,}")

total_races  = conn.execute("SELECT COUNT(*) FROM races_ultimate").fetchone()[0]
total_horses = conn.execute("SELECT COUNT(*) FROM race_results_ultimate").fetchone()[0]
oldest = conn.execute("SELECT MIN(substr(race_id,1,8)) FROM races_ultimate").fetchone()[0]
latest = conn.execute("SELECT MAX(substr(race_id,1,8)) FROM races_ultimate").fetchone()[0]
years  = set(r[0] for r in rows)
avg_per_year = total_races / len(years)
conn.close()

conn_c = sqlite3.connect(str(CACHE))
cache_total  = conn_c.execute("SELECT COUNT(*) FROM pedigree_cache").fetchone()[0]
cache_with_bd = conn_c.execute(
    "SELECT COUNT(*) FROM pedigree_cache WHERE birth_date IS NOT NULL AND length(birth_date) > 0"
).fetchone()[0]
conn_c.close()

print(f"\n=== DB現状 ===")
print(f"合計レース      : {total_races:,}レース")
print(f"合計出走        : {total_horses:,}頭")
print(f"期間            : {oldest} - {latest}  ({len(years)}年分)")
print(f"年平均レース数  : {avg_per_year:.0f}")
print(f"平均出走頭数    : {total_horses/total_races:.1f}頭/レース")
print(f"\npedigree_cache  : {cache_total:,}頭登録 / birth_date={cache_with_bd:,}頭 ({100*cache_with_bd/cache_total:.0f}%)")

# ---------- 時間試算 ----------
print("\n=== 10年分スクレイプ時間試算 ===")

TARGET_YEARS = 10
races_10y = int(avg_per_year * TARGET_YEARS)
horses_per_race = total_horses / total_races

# キャッシュヒット率（backfill後）
cache_hit_rate = cache_with_bd / cache_total  # ~79%

# 1レースあたりの時間内訳
# -----------------------------------------------------------
# HTTP処理:
#   - レース結果ページ:       3-5s (1回/レース、固定)
#   - 馬プロファイル(初回):  ~4s  (1頭あたり、キャッシュミス時)
#   - 馬プロファイル(キャッシュ): 0s
#   - 持ちタイムAPI:          2s   (キャッシュミス時)、0s (キャッシュヒット時)
#   - INV-07 インターバル:    1.5-3.5s/チャンク(4頭) → 1頭あたり平均~0.6s
# 固定オーバーヘッド:
#   - jitter_sleep(pre):     2-3s
#   - jitter_sleep(inter):   1.5s
#   - jitter_sleep(post):    4-7s
# -----------------------------------------------------------

# --- 最適化前（現状推定：旧キャッシュ < 2%） ---
SEC_RACE_PAGE       = 4.0   # レースページHTTP
SEC_HORSE_HTTP      = 4.0   # 馬詳細HTTP（プロフィール）
SEC_HOLDING_HTTP    = 2.0   # 持ちタイムAPI
SEC_INTERVAL_HORSE  = 0.6   # チャンク間インターバル平均/頭
SEC_FIXED_OVERHEAD  = 7.0   # pre+inter+post スリープ合計（平均）

old_cache_hit = 0.02  # 旧キャッシュヒット率
n = horses_per_race
sec_old = (
    SEC_RACE_PAGE
    + n * (SEC_HORSE_HTTP * (1 - old_cache_hit) + SEC_INTERVAL_HORSE)
    + SEC_HOLDING_HTTP          # 毎回
    + SEC_FIXED_OVERHEAD
)

# --- 最適化後（新キャッシュ ~79%）---
new_cache_hit = cache_hit_rate
sec_new = (
    SEC_RACE_PAGE
    + n * (SEC_HORSE_HTTP * (1 - new_cache_hit) + SEC_INTERVAL_HORSE)
    + 0                          # 持ちタイムはキャッシュ後は0
    + SEC_FIXED_OVERHEAD
)

# --- 完全キャッシュ（2周目以降：全馬キャッシュ済み）---
sec_full_cache = (
    SEC_RACE_PAGE
    + n * SEC_INTERVAL_HORSE     # インターバルのみ
    + 0                          # holding: キャッシュ
    + SEC_FIXED_OVERHEAD
)

def fmt(sec_per_race, races):
    total_sec = sec_per_race * races
    total_min = total_sec / 60
    total_hr  = total_min / 60
    total_day = total_hr  / 24
    return (f"{sec_per_race:.1f}s/レース → 合計 {total_sec:,.0f}s "
            f"= {total_hr:.1f}時間 = {total_day:.2f}日")

print(f"\n対象: 10年分 ≈ {races_10y:,}レース ({avg_per_year:.0f}レース/年 × 10年)")
print(f"\n[旧キャッシュ ~2%]    {fmt(sec_old, races_10y)}")
print(f"[新キャッシュ ~{new_cache_hit*100:.0f}%]  {fmt(sec_new, races_10y)}")
print(f"[完全キャッシュ100%]  {fmt(sec_full_cache, races_10y)}")

# --- 実測値ベース（バッチ実績: ~87s/レース） ---
actual_old = 87.0
actual_speedup = sec_old / sec_new if sec_new > 0 else 1
actual_new = actual_old / actual_speedup
print(f"\n--- 実測ベース補正 ---")
print(f"旧実測速度            : {actual_old:.0f}s/レース")
print(f"試算スピードアップ倍率: {actual_speedup:.2f}x")
print(f"新推定速度            : {actual_new:.1f}s/レース")

def fmt2(sec_per_race, races, label):
    t = sec_per_race * races
    print(f"  {label}: {sec_per_race:.0f}s/レース → {t/3600:.1f}時間 = {t/86400:.2f}日")

fmt2(actual_old,   races_10y, "旧（実測）              ")
fmt2(actual_new,   races_10y, "新（実測×スピードアップ）")
fmt2(sec_full_cache, races_10y, "完全キャッシュ（2周目+） ")

# カレンダーHTTP削減
total_months_10y = TARGET_YEARS * 12
print(f"\n--- カレンダー削減 ---")
print(f"カレンダーHTTP削減: {total_months_10y}ヶ月分 × 15s = {total_months_10y*15}s節約 (キャッシュ後)")
