import sys, sqlite3, json
import pandas as pd
import numpy as np
sys.path.insert(0, 'keiba')
sys.path.insert(0, 'python-api')

conn = sqlite3.connect('keiba/data/keiba_ultimate.db')

# 2018年のデータで欠損率確認（学習に使ったデータ）
print("=== 2013-2018データの主要フィールド欠損率・取得状況 ===\n")
query = """
SELECT data FROM race_results_ultimate
WHERE CAST(substr(race_id,1,4) AS INTEGER) BETWEEN 2013 AND 2018
LIMIT 5000
"""
rows = conn.execute(query).fetchall()
records = [json.loads(r[0]) for r in rows]
df = pd.DataFrame(records)

# 重要フィールドの取得率確認
fields_to_check = {
    # レース環境
    'weather': '天気',
    'field_condition': '馬場状態',
    'distance': '距離',
    'surface': '馬場種別(surface)',
    'track_type': '馬場種別(track_type)',
    'kai': '開催回次',
    'day': '開催日次',
    'course_direction': 'コース方向',
    # 馬個別
    'odds': 'オッズ',
    'popularity': '人気',
    'horse_number': '馬番',
    'bracket_number': '枠番',
    'age': '年齢',
    'sex': '性別',
    'weight_kg': '馬体重(kg)',
    'weight_change': '体重増減',
    'jockey_weight': '斤量',
    'last_3f': '上がり3F',
    'last_3f_rank': '上がり3F順位',
    'corner_1': '1角通過',
    'corner_4': '4角通過',
    # 前走情報
    'prev_race_distance': '前走距離',
    'prev_race_surface': '前走馬場',
    'prev_race_date': '前走日付',
    'prev2_race_finish': '前々走着順',
    'prev2_race_distance': '前々走距離',
    # 血統
    'sire': '父',
    'damsire': '母父',
    # 調教師
    'trainer_id': '調教師ID',
    'jockey_id': '騎手ID',
    # 通算成績
    'horse_total_wins': '通算勝利数',
    'horse_total_runs': '通算出走数',
}

# races_ultimateからも一部フィールド確認
r_rows = conn.execute("""
    SELECT data FROM races_ultimate
    WHERE CAST(substr(race_id,1,4) AS INTEGER) BETWEEN 2013 AND 2018
    LIMIT 2000
""").fetchall()
race_records = [json.loads(r[0]) for r in r_rows]
race_df = pd.DataFrame(race_records)

print(f"サンプル数: {len(df):,}行 (race_results_ultimate)\n")
print(f"{'フィールド':<25} {'日本語名':<18} {'取得率':>7}  状況")
print("-"*70)
for col, name in fields_to_check.items():
    if col in df.columns:
        val = df[col]
        non_null = val.notna().sum()
        non_zero = (val.notna() & (val != '') & (val != 0)).sum() if col not in ('corner_1', 'corner_4') else val.notna().sum()
        rate = non_null / len(df) * 100
        status = "✅" if rate >= 80 else ("⚠️" if rate >= 40 else "❌")
        print(f"{col:<25} {name:<18} {rate:>6.1f}%  {status}")
    elif col in race_df.columns:
        val = race_df[col]
        non_null = val.notna().sum()
        rate = non_null / len(race_df) * 100
        status = "✅" if rate >= 80 else ("⚠️" if rate >= 40 else "❌")
        print(f"{col:<25} {name:<18} {rate:>6.1f}%  {status} [races_ultimate]")
    else:
        print(f"{col:<25} {name:<18}    N/A   ❌ カラム未存在")

conn.close()
