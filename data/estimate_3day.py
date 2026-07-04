"""3日完了のための必要条件と並列設計の試算"""
from math import ceil

# ---- 基本パラメータ ----
RACES_10Y_LOW  = 21000   # 保守 (2,121/年 × 10)
RACES_10Y_MID  = 32000   # 中間 (3,201/年 × 10) ← 現実的
RACES_10Y_HIGH = 43000   # 楽観 (4,282/年 × 10)
TARGET_DAYS    = 3
TARGET_SEC     = TARGET_DAYS * 86400  # 259,200s

# ---- 1レースのINV-07下限 ----
AVG_HORSES         = 7.0
CHUNKS_PER_RACE    = ceil(AVG_HORSES / 4)           # ceil(7/4) = 2 チャンク
MIN_CHUNK_SLEEP    = 1.0 * (CHUNKS_PER_RACE - 1)    # 最後のチャンクはスキップ可 = 1.0s
PRE_SLEEP_MIN      = 1.0                             # INV-07: >=1.0s
POST_SLEEP_MIN     = 1.0                             # INV-07: >=1.0s
RACE_PAGE_HTTP     = 3.5                             # レースページ取得
HOLDING_API        = 0.0                             # キャッシュ後
HORSE_HTTP_CACHED  = 0.0                             # 完全キャッシュ後
HORSE_HTTP_MISS    = 4.0                             # キャッシュミス時
HORSE_INTERVAL     = 1.0                             # チャンク間スリープ/チャンク換算
HORSE_CACHE_HIT    = 0.79                            # 現在の79%

# ---- 現実的な1レース所要時間（シングルIP） ----
# シナリオA: 現状（79% cache hit）
t_horse_A = AVG_HORSES * (HORSE_HTTP_MISS * (1 - HORSE_CACHE_HIT) + 0.4)
t_A = RACE_PAGE_HTTP + t_horse_A + 2.0 + 7.0  # +holding(初回)+固定overhead
# 実測換算
ACTUAL_SPEEDUP = 87.0 / 44.5
t_A_actual = t_A * ACTUAL_SPEEDUP

# シナリオB: 完全キャッシュ（2周目以降）
t_horse_B = AVG_HORSES * 0.4  # intervalのみ
t_B = RACE_PAGE_HTTP + t_horse_B + 0.0 + 7.0
t_B_actual = t_B * ACTUAL_SPEEDUP

# シナリオC: INV-07理論最小（スリープ最適化）
#   pre=1.0s, post=1.0s, チャンク間=1.0s, レースページ=3.5s
#   horse cache=100%, holding=0s
t_C = RACE_PAGE_HTTP + MIN_CHUNK_SLEEP + PRE_SLEEP_MIN + POST_SLEEP_MIN + HORSE_HTTP_CACHED
# 理論値なので補正なし（理論下限）

print("=" * 70)
print("  3日完了 達成条件分析")
print("=" * 70)
print(f"\n対象: {RACES_10Y_MID:,}レース (中間シナリオ) / 目標: {TARGET_DAYS}日")
print(f"目標所要時間: {TARGET_SEC:,}s / 必要速度: {TARGET_SEC/RACES_10Y_MID:.1f}s/レース以下")

print(f"\n--- シングルIP（現在）の限界 ---")
print(f"  現状実測 (~41s/race):     {t_A_actual*RACES_10Y_MID/86400:.1f}日  ❌")
print(f"  完全キャッシュ (~30s):    {t_B_actual*RACES_10Y_MID/86400:.1f}日  ❌")
print(f"  INV-07理論下限 ({t_C:.1f}s): {t_C*RACES_10Y_MID/86400:.1f}日  ❌")
print(f"  → シングルIPでは3日は【物理的に不可能】")

print(f"\n--- 並列Worker × N (異なるIP) ---")
for n_workers in [2, 3, 4, 5]:
    for label, t in [
        ("現状実測 ~41s", t_A_actual),
        ("完全キャッシュ ~30s", t_B_actual),
        ("理論下限 {:.1f}s".format(t_C), t_C),
    ]:
        days = t * RACES_10Y_MID / (86400 * n_workers)
        ok   = "✅" if days <= TARGET_DAYS else "❌"
        print(f"  {n_workers}workers × {label:<22}: {days:.1f}日  {ok}")
    print()

print("--- 達成に必要な最小ワーカー数 ---")
for label, t in [
    ("現状実測 ~41s", t_A_actual),
    ("完全キャッシュ ~30s", t_B_actual),
    ("理論下限", t_C),
]:
    needed = ceil(t * RACES_10Y_MID / TARGET_SEC)
    print(f"  {label:<28}: {needed}ワーカー（異なるIP）が必要")

print(f"""
=== 並列スクレイプ設計案 ===

[Worker分割方法]
  Worker 1: 2016-2018年担当  (~9,600レース)
  Worker 2: 2019-2021年担当  (~9,600レース)
  Worker 3: 2022-2025年担当  (~9,600レース)
  Worker 4: 既存データ補完用（オプション）

[IP管理]
  各WorkerはVPN/プロキシで異なるIPを使用
  SCRAPE_PROXY_URL 環境変数で切り替え可能（race.py既対応）

[SQLite並列書き込み]
  keiba_ultimate.db: WAL mode 設定済み → 複数プロセスからの書き込みOK
  pedigree_cache.db: WAL mode 設定済み → 血統データ共有OK
  scrape_jobs.db: 別プロセスで直接実行するため不要

[必要な実装]
  1. スタンドアロン実行スクリプト（FastAPIなし、直接DB書き込み）
  2. 日付範囲引数 --start / --end
  3. 別プロセス起動 × 3（各自異なる SCRAPE_PROXY_URL）
  4. ログを別ファイルに分離

[リスク]
  ・netkeiba利用規約: 商用目的スクレイピング禁止（個人利用は黙認傾向）
  ・同一アカウントで複数IPログイン = 不審に映る可能性あり
  ・ログインセッション共有: 各Workerは独立したセッションが必要
  ・pedigree_cacheの書き込み競合: WAL mode でほぼ回避可能だが
    同一horse_idの同時書き込みは排他制御が必要（INSERT OR REPLACE で代替可）
""")

print("--- 結論 ---")
print(f"  シングルIPで3日: 【不可能】（最速でも{t_C*RACES_10Y_MID/86400:.1f}日）")
print(f"  3ワーカー×異IP:  【可能】（完全キャッシュ後 ~{t_B_actual*RACES_10Y_MID/86400/3:.1f}日）")
print(f"  2ワーカー×異IP:  【ギリギリ】（完全キャッシュ後 ~{t_B_actual*RACES_10Y_MID/86400/2:.1f}日）")
print(f"  推奨: 3ワーカー + VPN×3 でほぼ確実に3日以内")
