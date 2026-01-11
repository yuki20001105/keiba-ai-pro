"""
Ultimate版データベース構造とサンプルデータの確認
"""
import sqlite3
from pathlib import Path

db_path = Path("keiba/data/keiba_ultimate.db")

print("=" * 80)
print("【Ultimate版データベースの確認】")
print("=" * 80)

if not db_path.exists():
    print(f"\n✗ データベースが存在しません: {db_path}")
    print("  → まずデータをスクレイピングしてください")
    exit(1)

print(f"\n✓ データベース: {db_path}")

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# テーブル一覧
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()

print(f"\n【テーブル一覧】 {len(tables)}個")
for table in tables:
    table_name = table[0]
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    count = cursor.fetchone()[0]
    print(f"  {table_name}: {count}行")

# race_info テーブルの構造
print(f"\n【race_info テーブル構造】")
cursor.execute("PRAGMA table_info(race_info)")
columns = cursor.fetchall()
for col in columns:
    print(f"  {col[1]}: {col[2]}")

# result_data テーブルの構造（Ultimate版で拡張された部分）
print(f"\n【result_data テーブル構造（⭐Ultimate版で拡張）】")
cursor.execute("PRAGMA table_info(result_data)")
columns = cursor.fetchall()
for col in columns:
    marker = " ⭐" if col[1] in ['horse_id', 'jockey_id', 'trainer_id', 'weight_kg', 'weight_change', 'last_3f_rank'] else ""
    print(f"  {col[1]}: {col[2]}{marker}")

# サンプルレースを取得
cursor.execute("SELECT race_id, race_name, venue, distance, num_horses FROM race_info LIMIT 1")
sample_race = cursor.fetchone()

if sample_race:
    race_id = sample_race[0]
    print(f"\n{'='*80}")
    print(f"【サンプルレース】")
    print('='*80)
    print(f"  race_id: {race_id}")
    print(f"  レース名: {sample_race[1]}")
    print(f"  場所: {sample_race[2]}")
    print(f"  距離: {sample_race[3]}")
    print(f"  出走: {sample_race[4]}頭")
    
    # 1着馬の詳細データ
    cursor.execute("""
        SELECT 
            horse_name, horse_id, jockey_name, jockey_id, 
            trainer_name, trainer_id, finish_time, odds,
            weight_kg, weight_change, last_3f_rank
        FROM result_data
        WHERE race_id = ? AND finish_position = 1
    """, (race_id,))
    
    winner = cursor.fetchone()
    if winner:
        print(f"\n【1着馬の詳細（⭐Ultimate版特徴量）】")
        print(f"  馬名: {winner[0]}")
        print(f"  horse_id: {winner[1]} ⭐")
        print(f"  騎手: {winner[2]}")
        print(f"  jockey_id: {winner[3]} ⭐")
        print(f"  調教師: {winner[4]}")
        print(f"  trainer_id: {winner[5]} ⭐")
        print(f"  タイム: {winner[6]}")
        print(f"  オッズ: {winner[7]}倍")
        print(f"  weight_kg: {winner[8]} kg ⭐")
        print(f"  weight_change: {winner[9]} kg ⭐")
        print(f"  last_3f_rank: {winner[10]} ⭐")
    
    # horse_details テーブル
    cursor.execute("""
        SELECT coat_color, sale_price, total_prize_money, total_runs, total_wins
        FROM horse_details
        WHERE horse_id = ?
    """, (winner[1],))
    
    horse_detail = cursor.fetchone()
    if horse_detail:
        print(f"\n【馬詳細（⭐Ultimate版）】")
        print(f"  毛色: {horse_detail[0]} ⭐")
        print(f"  セール価格: {horse_detail[1]} ⭐")
        print(f"  通算獲得賞金: {horse_detail[2]} ⭐")
        print(f"  通算出走: {horse_detail[3]} 回 ⭐")
        print(f"  通算勝利: {horse_detail[4]} 勝 ⭐")
    
    # past_performance テーブル（前走データ）
    cursor.execute("""
        SELECT race_date, venue, distance, finish_position, weight, distance_change
        FROM past_performance
        WHERE horse_id = ? AND race_id = ?
        LIMIT 1
    """, (winner[1], race_id))
    
    past_perf = cursor.fetchone()
    if past_perf:
        print(f"\n【前走データ（⭐Ultimate版）】")
        print(f"  前走日付: {past_perf[0]} ⭐")
        print(f"  前走場所: {past_perf[1]} ⭐")
        print(f"  前走距離: {past_perf[2]} m ⭐")
        print(f"  前走着順: {past_perf[3]} 着 ⭐")
        print(f"  前走馬体重: {past_perf[4]} kg ⭐")
        print(f"  距離変化: {past_perf[5]} m ⭐")
    
    # jockey_stats テーブル
    cursor.execute("""
        SELECT win_rate, place_rate_top2, show_rate
        FROM jockey_stats
        WHERE jockey_id = ?
    """, (winner[3],))
    
    jockey_stat = cursor.fetchone()
    if jockey_stat:
        print(f"\n【騎手統計（⭐Ultimate版）】")
        print(f"  勝率: {jockey_stat[0]}%")
        print(f"  連対率: {jockey_stat[1]}%")
        print(f"  複勝率: {jockey_stat[2]}%")
    
    # trainer_stats テーブル
    cursor.execute("""
        SELECT win_rate, place_rate_top2
        FROM trainer_stats
        WHERE trainer_id = ?
    """, (winner[5],))
    
    trainer_stat = cursor.fetchone()
    if trainer_stat:
        print(f"\n【調教師統計（⭐Ultimate版）】")
        print(f"  勝率: {trainer_stat[0]}%")
        print(f"  連対率: {trainer_stat[1]}%")
    
    # lap_times テーブル（累計）
    cursor.execute("""
        SELECT distance, cumulative_time
        FROM lap_times
        WHERE race_id = ?
        ORDER BY distance
        LIMIT 6
    """, (race_id,))
    
    laps = cursor.fetchall()
    if laps:
        print(f"\n【ラップタイム: 累計】")
        for lap in laps:
            print(f"  {lap[0]}m: {lap[1]}")
    
    # lap_sectional テーブル（区間）
    cursor.execute("""
        SELECT distance, sectional_time
        FROM lap_sectional
        WHERE race_id = ?
        ORDER BY distance
        LIMIT 6
    """, (race_id,))
    
    sects = cursor.fetchall()
    if sects:
        print(f"\n【ラップタイム: 区間（⭐Ultimate版のみ）】")
        for sect in sects:
            print(f"  {sect[0]}m: {sect[1]}")
    
    # corner_positions テーブル
    cursor.execute("""
        SELECT corner_number, position_order
        FROM corner_positions
        WHERE race_id = ?
        ORDER BY corner_number
    """, (race_id,))
    
    corners = cursor.fetchall()
    if corners:
        print(f"\n【コーナー通過順位】")
        for corner in corners:
            print(f"  コーナー{corner[0]}: {corner[1]}")

print(f"\n{'='*80}")
print("【Ultimate版特徴量の総数】")
print('='*80)

feature_categories = {
    'レース基本情報': 16,
    '結果テーブル（ID・分解重量含む）': 20,
    '馬詳細（毛色・セール価格等）': 14,
    '過去成績（前走データ）': 6,
    '騎手統計': 4,
    '調教師統計': 3,
    'ラップ累計': 12,
    'ラップ区間（⭐新規）': 12,
    'コーナー': 4,
    '血統': 3,
}

total = 0
for category, count in feature_categories.items():
    print(f"  {category}: {count}列")
    total += count

print(f"\n  【合計】 {total}列")
print(f"  標準版: 60列")
print(f"  Ultimate版: {total}列 （+{total-60}列）")

print(f"\n{'='*80}")
print("✓ Ultimate版データベースの確認が完了しました")
print('='*80)

conn.close()
