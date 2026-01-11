"""
最終版: 全特徴量をCSVに出力
追加された特徴量も含む完全版
"""
import requests
import csv
import time
import re
from datetime import datetime

def fetch_ultimate_data(race_id):
    """最終版スクレイピングでデータを取得"""
    url = "http://localhost:8001/scrape/ultimate"
    payload = {
        "race_id": race_id,
        "include_details": True
    }
    
    print(f"→ race_id={race_id} のデータ取得中...")
    response = requests.post(url, json=payload, timeout=300)
    
    if response.status_code == 200:
        data = response.json()
        if data.get('success'):
            print(f"✓ データ取得成功")
            return data
        else:
            print(f"✗ エラー: {data.get('error')}")
            return None
    else:
        print(f"✗ HTTP エラー: {response.status_code}")
        return None


def export_ultimate_to_csv(data, output_file):
    """最終版データをCSVに出力"""
    
    race_info = data.get('race_info', {})
    results = data.get('results', [])
    lap_times = data.get('lap_times', {})
    lap_times_sectional = data.get('lap_times_sectional', {})
    corner_positions = data.get('corner_positions', {})
    derived_features = data.get('derived_features', {})
    
    if not results:
        print("✗ 結果データがありません")
        return
    
    # CSVヘッダー（最終版）
    headers = [
        # ===== レース基本情報 (16列) =====
        'race_id',
        'race_name',
        'post_time',
        'track_type',
        'distance',
        'course_direction',
        'weather',
        'field_condition',
        'kai',
        'venue',
        'day',
        'race_class',
        'horse_count',
        'prize_money',
        'market_entropy',  # 派生: 市場エントロピー
        'top3_probability',  # 派生: 上位3頭確率和
        
        # ===== 結果テーブル (20列) =====
        'finish_position',  # ⭐目的変数
        'bracket_number',
        'horse_number',
        'horse_id',  # ID
        'horse_name',
        'sex_age',
        'jockey_weight',
        'jockey_id',  # ID
        'jockey_name',
        'finish_time',  # ⭐目的変数
        'margin',
        'popularity',
        'odds',
        'last_3f',
        'last_3f_rank',  # 派生: 上がり順位
        'corner_positions_horse',
        'trainer_id',  # ID
        'trainer_name',
        'weight_kg',  # 分解: 馬体重(kg)
        'weight_change',  # 分解: 馬体重変化
        
        # ===== 馬詳細 (14列) =====
        'horse_birth_date',
        'horse_coat_color',  # 毛色
        'horse_owner',
        'horse_breeder',
        'horse_breeding_farm',
        'horse_sale_price',  # セール価格
        'horse_total_prize_money',  # 獲得賞金
        'horse_total_runs',  # 通算出走数
        'horse_total_wins',  # 通算勝利数
        'sire',  # 父馬
        'dam',  # 母馬
        'damsire',  # 母父馬
        'past_performance_1',
        'past_performance_2',
        
        # ===== 過去成績の派生特徴 (6列) =====
        'prev_race_date',  # 前走日付
        'prev_race_venue',  # 前走場所
        'prev_race_distance',  # 前走距離
        'prev_race_finish',  # 前走着順
        'prev_race_weight',  # 前走馬体重
        'distance_change',  # 距離変化（今回-前走）
        
        # ===== 騎手詳細 (4列) =====
        'jockey_win_rate',
        'jockey_place_rate_top2',
        'jockey_show_rate',
        'jockey_graded_wins',
        
        # ===== 調教師詳細 (3列) =====
        'trainer_win_rate',
        'trainer_place_rate_top2',
        'trainer_show_rate',
        
        # ===== ラップタイム: 累計 (12列) =====
        'lap_200m',
        'lap_400m',
        'lap_600m',
        'lap_800m',
        'lap_1000m',
        'lap_1200m',
        'lap_1400m',
        'lap_1600m',
        'lap_1800m',
        'lap_2000m',
        'lap_2200m',
        'lap_2400m',
        
        # ===== ラップタイム: 区間 (12列) =====
        'lap_sect_200m',
        'lap_sect_400m',
        'lap_sect_600m',
        'lap_sect_800m',
        'lap_sect_1000m',
        'lap_sect_1200m',
        'lap_sect_1400m',
        'lap_sect_1600m',
        'lap_sect_1800m',
        'lap_sect_2000m',
        'lap_sect_2200m',
        'lap_sect_2400m',
        
        # ===== コーナー通過順位 (4列) =====
        'corner_1',
        'corner_2',
        'corner_3',
        'corner_4',
    ]
    
    total_horses = 0
    
    with open(output_file, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        
        for result in results:
            row = {
                # レース基本情報
                'race_id': race_info.get('race_id', ''),
                'race_name': race_info.get('race_name', ''),
                'post_time': race_info.get('post_time', ''),
                'track_type': race_info.get('track_type', ''),
                'distance': race_info.get('distance', ''),
                'course_direction': race_info.get('course_direction', ''),
                'weather': race_info.get('weather', ''),
                'field_condition': race_info.get('field_condition', ''),
                'kai': race_info.get('kai', ''),
                'venue': race_info.get('venue', ''),
                'day': race_info.get('day', ''),
                'race_class': race_info.get('race_class', ''),
                'horse_count': race_info.get('horse_count', ''),
                'prize_money': race_info.get('prize_money', ''),
                'market_entropy': derived_features.get('market_entropy', ''),
                'top3_probability': derived_features.get('top3_probability', ''),
                
                # 結果テーブル
                'finish_position': result.get('finish_position', ''),
                'bracket_number': result.get('bracket_number', ''),
                'horse_number': result.get('horse_number', ''),
                'horse_id': result.get('horse_id', ''),
                'horse_name': result.get('horse_name', ''),
                'sex_age': result.get('sex_age', ''),
                'jockey_weight': result.get('jockey_weight', ''),
                'jockey_id': result.get('jockey_id', ''),
                'jockey_name': result.get('jockey_name', ''),
                'finish_time': result.get('finish_time', ''),
                'margin': result.get('margin', ''),
                'popularity': result.get('popularity', ''),
                'odds': result.get('odds', ''),
                'last_3f': result.get('last_3f', ''),
                'last_3f_rank': result.get('last_3f_rank', ''),
                'corner_positions_horse': result.get('corner_positions', ''),
                'trainer_id': result.get('trainer_id', ''),
                'trainer_name': result.get('trainer_name', ''),
                'weight_kg': result.get('weight_kg', ''),
                'weight_change': result.get('weight_change', ''),
            }
            
            # 馬詳細
            horse_details = result.get('horse_details', {})
            row['horse_birth_date'] = horse_details.get('birth_date', '')
            row['horse_coat_color'] = horse_details.get('coat_color', '')
            row['horse_owner'] = horse_details.get('owner', '')
            row['horse_breeder'] = horse_details.get('breeder', '')
            row['horse_breeding_farm'] = horse_details.get('breeding_farm', '')
            row['horse_sale_price'] = horse_details.get('sale_price', '')
            row['horse_total_prize_money'] = horse_details.get('total_prize_money', '')
            row['horse_total_runs'] = horse_details.get('total_runs', '')
            row['horse_total_wins'] = horse_details.get('total_wins', '')
            row['sire'] = horse_details.get('sire', '')
            row['dam'] = horse_details.get('dam', '')
            row['damsire'] = horse_details.get('damsire', '')
            
            # 過去成績
            past_perfs = horse_details.get('past_performances', [])
            if past_perfs:
                perf1 = past_perfs[0]
                row['past_performance_1'] = f"{perf1.get('date', '')} {perf1.get('venue', '')} {perf1.get('race_name', '')} {perf1.get('finish', '')}着"
                row['prev_race_date'] = perf1.get('date', '')
                row['prev_race_venue'] = perf1.get('venue', '')
                row['prev_race_distance'] = perf1.get('distance', '')
                row['prev_race_finish'] = perf1.get('finish', '')
                row['prev_race_weight'] = perf1.get('weight', '')
                
                # 距離変化
                try:
                    prev_dist = int(re.search(r'\d+', perf1.get('distance', '0')).group())
                    curr_dist = race_info.get('distance', 0)
                    row['distance_change'] = curr_dist - prev_dist
                except:
                    row['distance_change'] = ''
            
            if len(past_perfs) > 1:
                perf2 = past_perfs[1]
                row['past_performance_2'] = f"{perf2.get('date', '')} {perf2.get('venue', '')} {perf2.get('race_name', '')} {perf2.get('finish', '')}着"
            
            # 騎手詳細
            jockey_details = result.get('jockey_details', {})
            row['jockey_win_rate'] = jockey_details.get('win_rate', '')
            row['jockey_place_rate_top2'] = jockey_details.get('place_rate_top2', '')
            row['jockey_show_rate'] = jockey_details.get('show_rate', '')
            row['jockey_graded_wins'] = jockey_details.get('graded_wins', '')
            
            # 調教師詳細
            trainer_details = result.get('trainer_details', {})
            row['trainer_win_rate'] = trainer_details.get('win_rate', '')
            row['trainer_place_rate_top2'] = trainer_details.get('place_rate_top2', '')
            row['trainer_show_rate'] = trainer_details.get('show_rate', '')
            
            # ラップタイム（累計）
            for dist in ['200m', '400m', '600m', '800m', '1000m', '1200m', '1400m', '1600m', '1800m', '2000m', '2200m', '2400m']:
                row[f'lap_{dist}'] = lap_times.get(dist, '')
            
            # ラップタイム（区間）
            for dist in ['200m', '400m', '600m', '800m', '1000m', '1200m', '1400m', '1600m', '1800m', '2000m', '2200m', '2400m']:
                row[f'lap_sect_{dist}'] = lap_times_sectional.get(dist, '')
            
            # コーナー通過順位
            for i in range(1, 5):
                corner_key = f'{i}コーナー'
                row[f'corner_{i}'] = corner_positions.get(corner_key, '')
            
            writer.writerow(row)
            total_horses += 1
    
    print(f"\n✓ CSVファイルに保存しました: {output_file}")
    print(f"  - 出走馬数: {total_horses}頭")
    print(f"  - 列数: {len(headers)}列")


def main():
    print("=" * 100)
    print("最終版: 全特徴量CSVエクスポート")
    print("=" * 100)
    
    race_id = "202006010101"
    
    data = fetch_ultimate_data(race_id)
    
    if data:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = f"race_data_ultimate_{race_id}_{timestamp}.csv"
        
        export_ultimate_to_csv(data, output_file)
        
        print(f"\n✓ 完了！")


if __name__ == "__main__":
    print("\n※ 事前に最終版スクレイピングサービスを起動してください:")
    print("  C:\\Users\\yuki2\\Documents\\ws\\keiba\\Scripts\\python.exe scraping_service_ultimate.py\n")
    
    input("準備ができたらEnterキーを押してください...")
    
    main()
