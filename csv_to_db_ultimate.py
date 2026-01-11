"""
Ultimate版CSV→DB変換スクリプト
90列のCSVをUltimate版データベースに登録
"""
import sys
import re
from pathlib import Path
import pandas as pd
from datetime import datetime

# パスを追加
sys.path.insert(0, str(Path(__file__).parent / "keiba"))

from keiba_ai import db_ultimate


def parse_weight_string(weight_str: str) -> tuple:
    """馬体重文字列をパース: '460(+2)' → (460, 2)"""
    if pd.isna(weight_str) or weight_str == '':
        return None, None
    
    match = re.search(r'(\d+)\(([+-]?\d+)\)', str(weight_str))
    if match:
        weight_kg = int(match.group(1))
        weight_change = int(match.group(2))
        return weight_kg, weight_change
    
    # 数値のみの場合
    if str(weight_str).isdigit():
        return int(weight_str), None
    
    return None, None


def convert_csv_to_db(csv_path: str, db_path: str = None):
    """CSVをデータベースに変換"""
    
    print("=" * 60)
    print("Ultimate版CSV→DB変換開始")
    print("=" * 60)
    
    # CSV読み込み
    print(f"\n📂 CSV読み込み: {csv_path}")
    df = pd.read_csv(csv_path, encoding='utf-8-sig')
    print(f"   行数: {len(df)}行")
    print(f"   列数: {len(df.columns)}列")
    
    # データベース接続
    print(f"\n💾 データベース接続...")
    con = db_ultimate.connect(db_path)
    
    # スキーマ初期化
    print("   スキーマ初期化中...")
    db_ultimate.init_db(con)
    print("   ✅ スキーマ初期化完了")
    
    # レースIDを取得（全行で同じと仮定）
    race_id = df['race_id'].iloc[0] if 'race_id' in df.columns else None
    
    if not race_id:
        print("❌ エラー: race_idが見つかりません")
        return
    
    print(f"\n🏇 レースID: {race_id}")
    
    # ============================================================
    # 1. レース基本情報を登録
    # ============================================================
    print("\n[1/7] レース基本情報を登録中...")
    
    race_data = {
        'race_id': race_id,
        'race_name': df['race_name'].iloc[0] if 'race_name' in df.columns else None,
        'post_time': df['post_time'].iloc[0] if 'post_time' in df.columns else None,
        'track_type': df['track_type'].iloc[0] if 'track_type' in df.columns else None,
        'distance': int(df['distance'].iloc[0]) if 'distance' in df.columns and pd.notna(df['distance'].iloc[0]) else None,
        'course_direction': df['course_direction'].iloc[0] if 'course_direction' in df.columns else None,
        'weather': df['weather'].iloc[0] if 'weather' in df.columns else None,
        'field_condition': df['field_condition'].iloc[0] if 'field_condition' in df.columns else None,
        'kai': int(df['kai'].iloc[0]) if 'kai' in df.columns and pd.notna(df['kai'].iloc[0]) else None,
        'venue': df['venue'].iloc[0] if 'venue' in df.columns else None,
        'day': int(df['day'].iloc[0]) if 'day' in df.columns and pd.notna(df['day'].iloc[0]) else None,
        'race_class': df['race_class'].iloc[0] if 'race_class' in df.columns else None,
        'horse_count': int(df['horse_count'].iloc[0]) if 'horse_count' in df.columns and pd.notna(df['horse_count'].iloc[0]) else None,
        'prize_money': df['prize_money'].iloc[0] if 'prize_money' in df.columns else None,
        'market_entropy': float(df['market_entropy'].iloc[0]) if 'market_entropy' in df.columns and pd.notna(df['market_entropy'].iloc[0]) else None,
        'top3_probability': float(df['top3_probability'].iloc[0]) if 'top3_probability' in df.columns and pd.notna(df['top3_probability'].iloc[0]) else None,
        'kaisai_date': None,
        'source': 'csv_import'
    }
    
    db_ultimate.upsert_race(con, race_data)
    print("   ✅ レース情報登録完了")
    
    # ============================================================
    # 2. 馬詳細情報を登録（ユニークな馬ごと）
    # ============================================================
    print("\n[2/7] 馬詳細情報を登録中...")
    
    horse_count = 0
    for _, row in df.iterrows():
        if pd.isna(row.get('horse_id')):
            continue
        
        horse_data = {
            'horse_id': str(row['horse_id']),
            'horse_name': row.get('horse_name'),
            'birth_date': row.get('horse_birth_date'),
            'coat_color': row.get('horse_coat_color'),
            'owner_name': row.get('horse_owner'),
            'breeder_name': row.get('horse_breeder'),
            'breeding_farm': row.get('horse_breeding_farm'),
            'sale_price': row.get('horse_sale_price'),
            'total_prize_money': float(row['horse_total_prize_money']) if 'horse_total_prize_money' in row and pd.notna(row['horse_total_prize_money']) else None,
            'total_runs': int(row['horse_total_runs']) if 'horse_total_runs' in row and pd.notna(row['horse_total_runs']) else None,
            'total_wins': int(row['horse_total_wins']) if 'horse_total_wins' in row and pd.notna(row['horse_total_wins']) else None,
            'total_seconds': None,
            'total_thirds': None,
            'sire': row.get('sire'),
            'dam': row.get('dam'),
            'damsire': row.get('damsire')
        }
        
        db_ultimate.upsert_horse_details(con, horse_data)
        horse_count += 1
    
    print(f"   ✅ 馬詳細情報登録完了: {horse_count}頭")
    
    # ============================================================
    # 3. 騎手情報を登録
    # ============================================================
    print("\n[3/7] 騎手情報を登録中...")
    
    jockey_count = 0
    unique_jockeys = df['jockey_id'].dropna().unique() if 'jockey_id' in df.columns else []
    
    for jockey_id in unique_jockeys:
        jockey_row = df[df['jockey_id'] == jockey_id].iloc[0]
        
        jockey_data = {
            'jockey_id': str(jockey_id),
            'jockey_name': jockey_row.get('jockey_name'),
            'win_rate': float(jockey_row['jockey_win_rate']) if 'jockey_win_rate' in jockey_row and pd.notna(jockey_row['jockey_win_rate']) else None,
            'place_rate_top2': float(jockey_row['jockey_place_rate_top2']) if 'jockey_place_rate_top2' in jockey_row and pd.notna(jockey_row['jockey_place_rate_top2']) else None,
            'show_rate': float(jockey_row['jockey_show_rate']) if 'jockey_show_rate' in jockey_row and pd.notna(jockey_row['jockey_show_rate']) else None,
            'graded_wins': int(jockey_row['jockey_graded_wins']) if 'jockey_graded_wins' in jockey_row and pd.notna(jockey_row['jockey_graded_wins']) else None,
            'total_races': None
        }
        
        db_ultimate.upsert_jockey_details(con, jockey_data)
        jockey_count += 1
    
    print(f"   ✅ 騎手情報登録完了: {jockey_count}人")
    
    # ============================================================
    # 4. 調教師情報を登録
    # ============================================================
    print("\n[4/7] 調教師情報を登録中...")
    
    trainer_count = 0
    unique_trainers = df['trainer_id'].dropna().unique() if 'trainer_id' in df.columns else []
    
    for trainer_id in unique_trainers:
        trainer_row = df[df['trainer_id'] == trainer_id].iloc[0]
        
        trainer_data = {
            'trainer_id': str(trainer_id),
            'trainer_name': trainer_row.get('trainer_name'),
            'win_rate': float(trainer_row['trainer_win_rate']) if 'trainer_win_rate' in trainer_row and pd.notna(trainer_row['trainer_win_rate']) else None,
            'place_rate_top2': float(trainer_row['trainer_place_rate_top2']) if 'trainer_place_rate_top2' in trainer_row and pd.notna(trainer_row['trainer_place_rate_top2']) else None,
            'show_rate': float(trainer_row['trainer_show_rate']) if 'trainer_show_rate' in trainer_row and pd.notna(trainer_row['trainer_show_rate']) else None,
            'total_races': None
        }
        
        db_ultimate.upsert_trainer_details(con, trainer_data)
        trainer_count += 1
    
    print(f"   ✅ 調教師情報登録完了: {trainer_count}人")
    
    # ============================================================
    # 5. エントリー情報を登録
    # ============================================================
    print("\n[5/7] エントリー情報を登録中...")
    
    entries_list = []
    for _, row in df.iterrows():
        if pd.isna(row.get('horse_id')):
            continue
        
        weight_kg, weight_change = parse_weight_string(row.get('weight'))
        
        entry = {
            'horse_id': str(row['horse_id']),
            'horse_name': row.get('horse_name'),
            'horse_no': int(row['horse_number']) if 'horse_number' in row and pd.notna(row['horse_number']) else None,
            'bracket': int(row['bracket_number']) if 'bracket_number' in row and pd.notna(row['bracket_number']) else None,
            'sex': None,
            'age': None,
            'sex_age': row.get('sex_age'),
            'handicap': float(row['jockey_weight']) if 'jockey_weight' in row and pd.notna(row['jockey_weight']) else None,
            'jockey_id': str(row['jockey_id']) if 'jockey_id' in row and pd.notna(row['jockey_id']) else None,
            'jockey_name': row.get('jockey_name'),
            'trainer_id': str(row['trainer_id']) if 'trainer_id' in row and pd.notna(row['trainer_id']) else None,
            'trainer_name': row.get('trainer_name'),
            'weight': int(row['weight_kg']) if 'weight_kg' in row and pd.notna(row['weight_kg']) else weight_kg,
            'weight_diff': int(row['weight_change']) if 'weight_change' in row and pd.notna(row['weight_change']) else weight_change,
            'weight_kg': int(row['weight_kg']) if 'weight_kg' in row and pd.notna(row['weight_kg']) else weight_kg,
            'weight_change': int(row['weight_change']) if 'weight_change' in row and pd.notna(row['weight_change']) else weight_change,
            'odds': float(row['odds']) if 'odds' in row and pd.notna(row['odds']) else None,
            'popularity': int(row['popularity']) if 'popularity' in row and pd.notna(row['popularity']) else None
        }
        entries_list.append(entry)
    
    db_ultimate.upsert_entries(con, race_id, entries_list)
    print(f"   ✅ エントリー情報登録完了: {len(entries_list)}頭")
    
    # ============================================================
    # 6. 結果情報を登録
    # ============================================================
    print("\n[6/7] 結果情報を登録中...")
    
    results_list = []
    for _, row in df.iterrows():
        if pd.isna(row.get('horse_id')):
            continue
        
        result = {
            'horse_id': str(row['horse_id']),
            'finish': int(row['finish_position']) if 'finish_position' in row and pd.notna(row['finish_position']) else None,
            'bracket_number': int(row['bracket_number']) if 'bracket_number' in row and pd.notna(row['bracket_number']) else None,
            'horse_number': int(row['horse_number']) if 'horse_number' in row and pd.notna(row['horse_number']) else None,
            'time': row.get('finish_time'),
            'margin': row.get('margin'),
            'last3f': float(row['last_3f']) if 'last_3f' in row and pd.notna(row['last_3f']) else None,
            'last_3f_rank': int(row['last_3f_rank']) if 'last_3f_rank' in row and pd.notna(row['last_3f_rank']) else None,
            'pass_order': row.get('corner_positions_horse'),
            'corner_1': row.get('corner_1'),
            'corner_2': row.get('corner_2'),
            'corner_3': row.get('corner_3'),
            'corner_4': row.get('corner_4'),
            'odds': float(row['odds']) if 'odds' in row and pd.notna(row['odds']) else None,
            'popularity': int(row['popularity']) if 'popularity' in row and pd.notna(row['popularity']) else None
        }
        results_list.append(result)
    
    db_ultimate.upsert_results(con, race_id, results_list)
    print(f"   ✅ 結果情報登録完了: {len(results_list)}頭")
    
    # ============================================================
    # 7. ラップタイム情報を登録
    # ============================================================
    print("\n[7/7] ラップタイム情報を登録中...")
    
    first_row = df.iloc[0]
    lap_data = {}
    
    # 累計ラップ
    for dist in [200, 400, 600, 800, 1000, 1200, 1400, 1600, 1800, 2000, 2200, 2400]:
        col_name = f'lap_{dist}m'
        if col_name in first_row:
            lap_data[col_name] = float(first_row[col_name]) if pd.notna(first_row[col_name]) else None
    
    # 区間ラップ
    for dist in [200, 400, 600, 800, 1000, 1200, 1400, 1600, 1800, 2000, 2200, 2400]:
        col_name = f'lap_sect_{dist}m'
        if col_name in first_row:
            lap_data[col_name] = float(first_row[col_name]) if pd.notna(first_row[col_name]) else None
    
    lap_data['pace_diff'] = None  # 計算が必要な場合は追加
    
    db_ultimate.upsert_lap_times(con, race_id, lap_data)
    print("   ✅ ラップタイム情報登録完了")
    
    # ============================================================
    # 統計情報表示
    # ============================================================
    print("\n" + "=" * 60)
    print("📊 データベース統計")
    print("=" * 60)
    
    stats = db_ultimate.get_database_stats(con)
    for table, count in stats.items():
        print(f"   {table:25s}: {count:5d} レコード")
    
    con.close()
    
    print("\n✅ CSV→DB変換完了")
    print("=" * 60)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Ultimate版CSV→DB変換')
    parser.add_argument('csv_path', help='変換するCSVファイルのパス')
    parser.add_argument('--db', dest='db_path', help='出力先データベースパス（省略時: keiba/data/keiba_ultimate.db）')
    
    args = parser.parse_args()
    
    try:
        convert_csv_to_db(args.csv_path, args.db_path)
    except Exception as e:
        print(f"\n❌ エラー発生: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
