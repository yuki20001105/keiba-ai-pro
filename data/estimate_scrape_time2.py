"""10年分スクレイプ時間の精密試算"""
import sqlite3, json
from pathlib import Path

DB    = Path("keiba/data/keiba_ultimate.db")
CACHE = Path("keiba/data/pedigree_cache.db")
conn  = sqlite3.connect(str(DB))

# ---- 現状DBまとめ ----
total_races  = conn.execute("SELECT COUNT(*) FROM races_ultimate").fetchone()[0]
total_horses = conn.execute("SELECT COUNT(*) FROM race_results_ultimate").fetchone()[0]
cnt25 = conn.execute("SELECT COUNT(*) FROM races_ultimate WHERE substr(race_id,1,4)='2025'").fetchone()[0]
cnt26 = conn.execute("SELECT COUNT(*) FROM races_ultimate WHERE substr(race_id,1,4)='2026'").fetchone()[0]
avg_horse = total_horses / total_races
conn.close()

conn_c = sqlite3.connect(str(CACHE))
cache_bd = conn_c.execute(
    "SELECT COUNT(*) FROM pedigree_cache WHERE birth_date IS NOT NULL AND length(birth_date)>0"
).fetchone()[0]
cache_tot = conn_c.execute("SELECT COUNT(*) FROM pedigree_cache").fetchone()[0]
conn_c.close()
cache_hit = cache_bd / cache_tot

# ---- 年間レース数の推定 ----
# 2025: 完全年として最も信頼性が高い
# 2026: 1-5月(5ヶ月) → 年換算
races_2026_extrap = cnt26 / 5 * 12
# 参考: JRA のみ ~3,600/年、JRA+一部NAR ~3,500-4,000/年
RACES_PER_YEAR_LOW  = cnt25        # 2025実績（保守）
RACES_PER_YEAR_MID  = int((cnt25 + races_2026_extrap) / 2)  # 平均
RACES_PER_YEAR_HIGH = int(races_2026_extrap)  # 2026外挿（楽観）
TARGET_YEARS        = 10

# ---- 1レースあたりの所要時間（秒） ----
n = avg_horse  # 平均出走頭数

# 固定オーバーヘッド（INV-07スリープ合計）
FIXED = 7.0   # pre(2) + inter(1.5) + post(3.5) 平均

# HTTP単価
T_RACE_PAGE  = 4.0   # レース結果ページ
T_HORSE_HTTP = 4.0   # 馬詳細（プロフィール）
T_HOLDING    = 2.0   # 持ちタイムAPI
T_INTERVAL   = 0.6   # チャンク間スリープ/頭換算

# INV-07 minimum（理論下限）: スリープのみ、HTTP=0
T_MIN_INTERVAL = 2.5  # チャンク(4頭)×ceil(7/4)=2チャンク → 約2.5s/レース分
T_FLOOR = FIXED + T_RACE_PAGE + T_MIN_INTERVAL  # 約13.5s/レース

def calc(cache_rate, holding_cache=True):
    t_horse = T_HORSE_HTTP * (1 - cache_rate) + T_INTERVAL
    t_hold  = 0 if holding_cache else T_HOLDING
    return FIXED + T_RACE_PAGE + n * t_horse + t_hold

t_old  = calc(0.02,  holding_cache=False)  # 最適化前
t_new  = calc(cache_hit, holding_cache=False)  # 今回（新Cache, 持ちタイムは初回のみ）
t_new2 = calc(cache_hit, holding_cache=True)   # 今回（持ちタイムもキャッシュ済み）
t_full = calc(1.0,   holding_cache=True)  # 完全キャッシュ（2周目以降）

# 実測値で補正
ACTUAL_OLD = 87.0  # 最適化前の実測
speedup = ACTUAL_OLD / t_old
t_new_actual  = t_new  * speedup
t_new2_actual = t_new2 * speedup
t_full_actual = t_full * speedup

print("=" * 65)
print("  10年分 スクレイプ時間試算")
print("=" * 65)
print(f"\n現在DB: {total_races:,}レース / {total_horses:,}頭 (平均{avg_horse:.1f}頭/レース)")
print(f"pedigree cache: {cache_bd:,}/{cache_tot:,}馬 birth_date設定済み ({cache_hit*100:.0f}%)")

print(f"\n--- 年間レース数シナリオ ---")
print(f"  保守 (2025実績):     {RACES_PER_YEAR_LOW:,}/年")
print(f"  中間:                {RACES_PER_YEAR_MID:,}/年")
print(f"  楽観 (2026外挿):     {RACES_PER_YEAR_HIGH:,}/年")

print(f"\n--- 1レース所要時間 ---")
print(f"  最適化前 (旧):       {t_old:.1f}s  (実測 {ACTUAL_OLD:.0f}s)")
print(f"  今回実装後 (初回):   {t_new_actual:.1f}s  (旧比 {ACTUAL_OLD/t_new_actual:.1f}x速)")
print(f"  今回実装後 (2回目+): {t_new2_actual:.1f}s  (旧比 {ACTUAL_OLD/t_new2_actual:.1f}x速)")
print(f"  理論最小(INV-07限界):{T_FLOOR:.1f}s  (旧比 {ACTUAL_OLD/T_FLOOR:.1f}x速)")

print(f"\n{'':=<65}")
print(f"{'シナリオ':<28} {'保守(2,121/年)':>14} {'中間(3,201/年)':>14} {'楽観(4,282/年)':>14}")
print(f"{'':=<65}")

for label, t_per_race in [
    ("最適化前 (旧実測 87s/レース)", ACTUAL_OLD),
    ("今回実装後・初回 (~41s)",       t_new_actual),
    ("今回実装後・2回目+ (~30s)",     t_new2_actual),
    ("理論下限 INV-07 (~14s)",        t_full_actual),
]:
    cols = []
    for races_yr in [RACES_PER_YEAR_LOW, RACES_PER_YEAR_MID, RACES_PER_YEAR_HIGH]:
        total_sec = t_per_race * races_yr * TARGET_YEARS
        days = total_sec / 86400
        cols.append(f"{days:.1f}日")
    print(f"  {label:<26} {cols[0]:>14} {cols[1]:>14} {cols[2]:>14}")

print(f"{'':=<65}")
print(f"\n注意事項:")
print(f"  ・INV-07 (1s/req必須) がボトルネック: 完全キャッシュでも {T_FLOOR:.0f}s/レース以下は不可")
print(f"  ・IPブロック発生時: リトライ+待機で+数時間のロスあり")
print(f"  ・今回のpedigree_cacheバックフィル ({cache_bd:,}頭) は主に過去データ取得時に有効")
print(f"  ・新規年のデータ取得では初年度のキャッシュ率は低い（馬が初出走）")
print(f"  ・持ちタイムキャッシュは過去レース再スクレイプ時のみ有効")
