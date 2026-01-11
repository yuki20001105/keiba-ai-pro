"""
複数レースを一括でCSVに出力
日付を指定して、その日の全レースをCSVに保存
"""
import requests
import csv
import time
from datetime import datetime

def get_race_list(kaisai_date):
    """指定日のrace_id一覧を取得"""
    url = "http://localhost:8001/race_list"
    payload = {"kaisai_date": kaisai_date}
    
    print(f"→ {kaisai_date[:4]}年{kaisai_date[4:6]}月{kaisai_date[6:8]}日のレース一覧取得中...")
    response = requests.post(url, json=payload, timeout=60)
    
    if response.status_code == 200:
        data = response.json()
        if data.get('success'):
            race_ids = data.get('race_ids', [])
            print(f"✓ {len(race_ids)}件のレース取得")
            return race_ids
        else:
            print(f"✗ エラー: {data.get('error')}")
            return []
    else:
        print(f"✗ HTTP エラー: {response.status_code}")
        return []


def fetch_enhanced_data(race_id):
    """拡張版スクレイピングでデータを取得"""
    url = "http://localhost:8001/scrape/enhanced"
    payload = {
        "race_id": race_id,
        "include_details": True
    }
    
    print(f"\n→ race_id={race_id} のデータ取得中...")
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


def export_all_to_csv(all_data, output_file):
    """複数レースのデータを1つのCSVファイルに出力"""
    
    if not all_data:
        print("✗ データがありません")
        return
    
    # CSVヘッダーを定義
    headers = [
        # レース基本情報
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
        
        # 結果テーブル（各馬）
        'finish_position',
        'bracket_number',
        'horse_number',
        'horse_name',
        'sex_age',
        'jockey_weight',
        'jockey_name',
        'finish_time',
        'margin',
        'popularity',
        'odds',
        'last_3f',
        'corner_positions_horse',
        'trainer_name',
        'weight',
        
        # 馬詳細
        'horse_birth_date',
        'horse_owner',
        'horse_breeder',
        'horse_breeding_farm',
        'sire',
        'dam',
        'damsire',
        'past_performance_1',
        'past_performance_2',
        'past_performance_3',
        
        # 騎手詳細
        'jockey_win_rate',
        'jockey_place_rate_top2',
        'jockey_show_rate',
        'jockey_graded_wins',
        
        # 調教師詳細
        'trainer_win_rate',
        'trainer_place_rate_top2',
        'trainer_show_rate',
        
        # ラップタイム
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
        'lap_2500m',
        'lap_2600m',
        'lap_3000m',
        'lap_3200m',
        'lap_3400m',
        'lap_3600m',
        
        # コーナー通過順位
        'corner_1',
        'corner_2',
        'corner_3',
        'corner_4',
    ]
    
    total_horses = 0
    
    # CSVファイルに書き込み
    with open(output_file, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        
        # 各レースのデータを処理
        for data in all_data:
            race_info = data.get('race_info', {})
            results = data.get('results', [])
            lap_times = data.get('lap_times', {})
            corner_positions = data.get('corner_positions', {})
            
            # race_idを追加
            race_id = data.get('race_id', '')
            race_info['race_id'] = race_id
            
            # 各馬のデータを行として出力
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
                    
                    # 結果テーブル
                    'finish_position': result.get('finish_position', ''),
                    'bracket_number': result.get('bracket_number', ''),
                    'horse_number': result.get('horse_number', ''),
                    'horse_name': result.get('horse_name', ''),
                    'sex_age': result.get('sex_age', ''),
                    'jockey_weight': result.get('jockey_weight', ''),
                    'jockey_name': result.get('jockey_name', ''),
                    'finish_time': result.get('finish_time', ''),
                    'margin': result.get('margin', ''),
                    'popularity': result.get('popularity', ''),
                    'odds': result.get('odds', ''),
                    'last_3f': result.get('last_3f', ''),
                    'corner_positions_horse': result.get('corner_positions', ''),
                    'trainer_name': result.get('trainer_name', ''),
                    'weight': result.get('weight', ''),
                }
                
                # 馬詳細
                horse_details = result.get('horse_details', {})
                row['horse_birth_date'] = horse_details.get('birth_date', '')
                row['horse_owner'] = horse_details.get('owner', '')
                row['horse_breeder'] = horse_details.get('breeder', '')
                row['horse_breeding_farm'] = horse_details.get('breeding_farm', '')
                row['sire'] = horse_details.get('sire', '')
                row['dam'] = horse_details.get('dam', '')
                row['damsire'] = horse_details.get('damsire', '')
                
                # 過去成績
                past_perfs = horse_details.get('past_performances', [])
                for i in range(3):
                    if i < len(past_perfs):
                        perf = past_perfs[i]
                        row[f'past_performance_{i+1}'] = f"{perf.get('date', '')} {perf.get('venue', '')} {perf.get('race_name', '')} {perf.get('finish', '')}着"
                    else:
                        row[f'past_performance_{i+1}'] = ''
                
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
                
                # ラップタイム
                for dist in ['200m', '400m', '600m', '800m', '1000m', '1200m', '1400m', '1600m', '1800m', '2000m', '2200m', '2400m', '2500m', '2600m', '3000m', '3200m', '3400m', '3600m']:
                    row[f'lap_{dist}'] = lap_times.get(dist, '')
                
                # コーナー通過順位
                for i in range(1, 5):
                    corner_key = f'{i}コーナー'
                    row[f'corner_{i}'] = corner_positions.get(corner_key, '')
                
                writer.writerow(row)
                total_horses += 1
    
    print(f"\n✓ CSVファイルに保存しました: {output_file}")
    print(f"  - レース数: {len(all_data)}レース")
    print(f"  - 総出走馬数: {total_horses}頭")
    print(f"  - 列数: {len(headers)}列")


def main():
    """メイン処理"""
    print("=" * 100)
    print("複数レース一括CSVエクスポート")
    print("=" * 100)
    
    # 日付を指定（例: 2020年1月5日）
    kaisai_date = "20200105"
    
    print(f"\n対象日: {kaisai_date[:4]}年{kaisai_date[4:6]}月{kaisai_date[6:8]}日\n")
    
    # race_id一覧を取得
    race_ids = get_race_list(kaisai_date)
    
    if not race_ids:
        print("\n✗ レースが見つかりませんでした")
        return
    
    print(f"\n合計 {len(race_ids)} レースのデータを取得します")
    print("=" * 100)
    
    # 各レースのデータを取得
    all_data = []
    for i, race_id in enumerate(race_ids, 1):
        print(f"\n[{i}/{len(race_ids)}] race_id={race_id}")
        data = fetch_enhanced_data(race_id)
        
        if data:
            data['race_id'] = race_id  # race_idを追加
            all_data.append(data)
            print(f"✓ レース{i}のデータ取得完了")
        else:
            print(f"✗ レース{i}のデータ取得失敗")
        
        # レート制限のため少し待機
        if i < len(race_ids):
            print("  (3秒待機...)")
            time.sleep(3)
    
    # CSVに出力
    if all_data:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = f"race_data_bulk_{kaisai_date}_{timestamp}.csv"
        
        export_all_to_csv(all_data, output_file)
        
        print(f"\n✓ 完了！")
        print(f"\nExcelで開く場合:")
        print(f"  1. {output_file} をExcelで開く")
        print(f"  2. UTF-8エンコーディングで正しく表示されます")
        print(f"\nPandasで読み込む場合:")
        print(f"  import pandas as pd")
        print(f"  df = pd.read_csv('{output_file}')")
    else:
        print("\n✗ データ取得に失敗しました")


if __name__ == "__main__":
    print("\n※ 事前に拡張版スクレイピングサービスを起動してください:")
    print("  C:\\Users\\yuki2\\Documents\\ws\\keiba\\Scripts\\python.exe scraping_service_enhanced.py\n")
    
    input("準備ができたらEnterキーを押してください...")
    
    main()
