"""
スクレイピング品質監査スクリプト
各カラムについて: NULL/0/空文字の割合、スクレイピング修正 vs 補完どちらが適切か判断
"""
import sqlite3, sys, os
sys.path.insert(0, 'python-api'); sys.path.insert(0, 'keiba')
os.chdir('python-api')

con = sqlite3.connect('../keiba/data/keiba_ultimate.db')

# ===== 1. テーブル構造確認 =====
tables = [r[0] for r in con.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
).fetchall()]
print(f"Tables: {tables}\n")

total = con.execute('SELECT COUNT(*) FROM race_entries').fetchone()[0]
total_races = con.execute('SELECT COUNT(DISTINCT race_id) FROM race_entries').fetchone()[0]
print(f"race_entries: {total} 行 / {total_races} レース\n")

# ===== 2. 全カラム NULL / 0 / 空文字 集計 =====
cols = [r[1] for r in con.execute("PRAGMA table_info(race_entries)").fetchall()]
print(f"{'カラム':<30} {'NULL':>7} {'0/空':>7} {'合計問題':>9} {'問題率':>8}")
print("-"*65)

problem_cols = []
for col in cols:
    try:
        n_null = con.execute(
            f"SELECT COUNT(*) FROM race_entries WHERE [{col}] IS NULL"
        ).fetchone()[0]
        n_zero = con.execute(
            f"SELECT COUNT(*) FROM race_entries WHERE CAST([{col}] AS TEXT) IN ('0','','None','nan','NULL')"
        ).fetchone()[0]
        total_prob = n_null + n_zero
        rate = total_prob / total * 100
        if rate > 1:  # 1%以上問題があるもののみ表示
            print(f"{col:<30} {n_null:>7} {n_zero:>7} {total_prob:>9} {rate:>7.1f}%")
            problem_cols.append((col, n_null, n_zero, rate))
    except Exception as e:
        print(f"{col:<30}  ERROR: {e}")

# ===== 3. distance=0 の詳細 - レースIDパターン確認 =====
print("\n\n===== distance=0/NULL の詳細 =====")
dist_zero = con.execute("""
    SELECT race_id, horse_name, distance, surface, venue
    FROM race_entries
    WHERE CAST(distance AS TEXT) = '0' OR distance IS NULL
    LIMIT 20
""").fetchall()
print(f"distance=0/NULL サンプル (先頭20件):")
for r in dist_zero:
    print(f"  race_id={r[0]}  馬={r[1]}  dist={r[2]}  surface={r[3]}  venue={r[4]}")

# レース単位での集計
dist_by_race = con.execute("""
    SELECT race_id, COUNT(*) as cnt, MIN(distance), MAX(distance)
    FROM race_entries
    WHERE CAST(distance AS TEXT) = '0' OR distance IS NULL
    GROUP BY race_id
    ORDER BY race_id
""").fetchall()
print(f"\ndistance=0/NULL が存在するレース数: {len(dist_by_race)}")
print("先頭10レース:")
for r in dist_by_race[:10]:
    # 同レースの正常なdistanceを取得
    normal = con.execute(
        "SELECT DISTINCT distance FROM race_entries WHERE race_id=? AND CAST(distance AS TEXT)!='0' AND distance IS NOT NULL",
        (r[0],)
    ).fetchall()
    normal_vals = [x[0] for x in normal]
    print(f"  {r[0]}: {r[1]}頭分が0/NULL, 正常distance={normal_vals}")

# ===== 4. race_results_ultimate の distance =====
print("\n\n===== race_results_ultimate の distance =====")
try:
    rr_total = con.execute("SELECT COUNT(*) FROM race_results_ultimate").fetchone()[0]
    rr_dist_null = con.execute(
        "SELECT COUNT(*) FROM race_results_ultimate WHERE distance IS NULL OR CAST(distance AS TEXT) IN ('0','')"
    ).fetchone()[0]
    print(f"race_results_ultimate: {rr_total} 行")
    print(f"distance=0/NULL: {rr_dist_null} 件 ({rr_dist_null/rr_total*100:.1f}%)")
    
    # race_results_ultimate と race_entries の distance を突き合わせ
    sample_mismatch = con.execute("""
        SELECT e.race_id, e.horse_name, e.distance AS entry_dist, r.distance AS result_dist
        FROM race_entries e
        JOIN race_results_ultimate r ON e.race_id = r.race_id AND e.horse_name = r.horse_name
        WHERE (CAST(e.distance AS TEXT) = '0' OR e.distance IS NULL)
        LIMIT 10
    """).fetchall()
    print(f"\nrace_entries distance=0 と race_results_ultimate 突き合わせ (先頭10件):")
    for r in sample_mismatch:
        print(f"  {r[0]} {r[1]}: entry_dist={r[2]}, result_dist={r[3]}")
except Exception as e:
    print(f"ERROR: {e}")

# ===== 5. races_ultimate の distance =====
print("\n\n===== races_ultimate の distance (レース単位) =====")
try:
    import json
    races_sample = con.execute(
        "SELECT race_id, data FROM races_ultimate ORDER BY race_id LIMIT 3"
    ).fetchall()
    if races_sample:
        d = json.loads(races_sample[0][1]) if isinstance(races_sample[0][1], str) else races_sample[0][1]
        if isinstance(d, dict):
            print(f"races_ultimate.data のキー (先頭): {list(d.keys())[:15]}")
            print(f"  distance={d.get('distance')}, course_info={d.get('course_info')}")
except Exception as e:
    print(f"ERROR: {e}")

# ===== 6. weight / weight_diff =====
print("\n\n===== weight / weight_diff の詳細 =====")
for col in ['weight', 'weight_diff']:
    if col in cols:
        null_cnt = con.execute(f"SELECT COUNT(*) FROM race_entries WHERE [{col}] IS NULL").fetchone()[0]
        zero_cnt = con.execute(f"SELECT COUNT(*) FROM race_entries WHERE CAST([{col}] AS TEXT)='0'").fetchone()[0]
        print(f"{col}: NULL={null_cnt} ({null_cnt/total*100:.1f}%), 0={zero_cnt} ({zero_cnt/total*100:.1f}%)")
        # horse_weight=0 は「計量不能」（輸送後などJRA公式に存在する）か確認
        sample = con.execute(
            f"SELECT race_id, horse_name, [{col}] FROM race_entries WHERE CAST([{col}] AS TEXT)='0' LIMIT 5"
        ).fetchall()
        for r in sample:
            print(f"  例: {r[0]} {r[1]} {col}={r[2]}")

# ===== 7. odds / popularity =====
print("\n\n===== odds / popularity =====")
for col in ['odds', 'popularity']:
    if col in cols:
        null_cnt = con.execute(f"SELECT COUNT(*) FROM race_entries WHERE [{col}] IS NULL").fetchone()[0]
        zero_cnt = con.execute(f"SELECT COUNT(*) FROM race_entries WHERE CAST([{col}] AS TEXT)='0'").fetchone()[0]
        print(f"{col}: NULL={null_cnt} ({null_cnt/total*100:.1f}%), 0={zero_cnt} ({zero_cnt/total*100:.1f}%)")

con.close()
print("\n===== 監査完了 =====")
