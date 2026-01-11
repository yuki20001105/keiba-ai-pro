"""
netkeiba.comから取得可能なすべての特徴量を分析
機械学習用の特徴量リストを作成
"""
import undetected_chromedriver as uc
from bs4 import BeautifulSoup
import time
import re

def analyze_race_page(race_id):
    """レース結果ページから取得可能なすべてのデータを分析"""
    
    url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
    
    print("=" * 100)
    print(f"レース結果ページ分析: {race_id}")
    print("=" * 100)
    print(f"URL: {url}\n")
    
    options = uc.ChromeOptions()
    options.headless = False
    driver = uc.Chrome(options=options, use_subprocess=False, version_main=None)
    
    try:
        driver.get(url)
        time.sleep(4)
        
        html = driver.page_source
        soup = BeautifulSoup(html, 'html.parser')
        
        features = {
            'レース基本情報': [],
            '馬の特徴量': [],
            '騎手の特徴量': [],
            '調教師の特徴量': [],
            'タイム・着差': [],
            'オッズ・人気': [],
            '馬体重': [],
            '血統情報': [],
            'その他': []
        }
        
        # ====================
        # 1. レース基本情報
        # ====================
        print("\n" + "=" * 100)
        print("1. レース基本情報")
        print("=" * 100)
        
        race_name = soup.find('h1', class_='RaceName')
        if race_name:
            print(f"レース名: {race_name.text.strip()}")
            features['レース基本情報'].append('レース名')
        
        race_data1 = soup.find('div', class_='RaceData01')
        race_data2 = soup.find('div', class_='RaceData02')
        
        if race_data1:
            text = race_data1.text.strip()
            print(f"\nRaceData01: {text}")
            
            # 距離
            dist_match = re.search(r'(\d+)m', text)
            if dist_match:
                features['レース基本情報'].append('距離 (m)')
            
            # トラック種別
            if '芝' in text or 'ダート' in text or 'ダ' in text:
                features['レース基本情報'].append('トラック種別 (芝/ダート)')
            
            # 天候
            if '天候' in text:
                features['レース基本情報'].append('天候')
            
            # 馬場状態
            if '馬場' in text:
                features['レース基本情報'].append('馬場状態')
            
            # コース種別（左回り/右回り）
            if '右' in text or '左' in text:
                features['レース基本情報'].append('コース方向 (左/右)')
            
            # 発走時刻
            time_match = re.search(r'(\d{2}:\d{2})', text)
            if time_match:
                features['レース基本情報'].append('発走時刻')
        
        if race_data2:
            text = race_data2.text.strip()
            print(f"RaceData02: {text}")
            
            # 賞金情報
            if '本賞金' in text or '万円' in text:
                features['レース基本情報'].append('賞金')
            
            # レースグレード
            if 'G1' in text or 'G2' in text or 'G3' in text:
                features['レース基本情報'].append('レースグレード')
        
        # ====================
        # 2. 結果テーブル
        # ====================
        print("\n" + "=" * 100)
        print("2. レース結果テーブル（馬ごとのデータ）")
        print("=" * 100)
        
        result_table = soup.find('table', class_='Race_Result_Table')
        if not result_table:
            result_table = soup.find('table', class_='Result_Table')
        
        if result_table:
            headers = result_table.find('tr')
            if headers:
                header_texts = [th.text.strip() for th in headers.find_all('th')]
                print(f"\nテーブルヘッダー: {header_texts}")
            
            rows = result_table.find_all('tr')[1:]
            
            if rows:
                print(f"\n最初の馬のデータを分析:")
                first_row = rows[0]
                cols = first_row.find_all('td')
                
                for i, col in enumerate(cols):
                    text = col.text.strip()
                    print(f"  列{i}: {text[:50]}")
                
                # 標準的な列構成
                if len(cols) >= 10:
                    print("\n検出された特徴量:")
                    feature_map = {
                        0: ('着順', '馬の特徴量'),
                        1: ('枠番', '馬の特徴量'),
                        2: ('馬番', '馬の特徴量'),
                        3: ('馬名', '馬の特徴量'),
                        4: ('性齢', '馬の特徴量'),
                        5: ('斤量', '騎手の特徴量'),
                        6: ('騎手名', '騎手の特徴量'),
                        7: ('タイム', 'タイム・着差'),
                        8: ('着差', 'タイム・着差'),
                        9: ('単勝オッズ', 'オッズ・人気'),
                        10: ('人気', 'オッズ・人気') if len(cols) > 10 else None,
                        11: ('馬体重', '馬体重') if len(cols) > 11 else None,
                        12: ('調教師', '調教師の特徴量') if len(cols) > 12 else None,
                    }
                    
                    for idx, (feature_name, category) in feature_map.items():
                        if feature_name and idx < len(cols):
                            features[category].append(feature_name)
                            print(f"    {feature_name} ({category})")
        
        # ====================
        # 3. 詳細データ（馬名リンクをクリックして取得可能）
        # ====================
        print("\n" + "=" * 100)
        print("3. 馬詳細ページから取得可能な追加特徴量")
        print("=" * 100)
        print("（馬名リンクをクリックすることで取得可能）")
        
        horse_links = soup.find_all('a', href=lambda x: x and '/horse/' in x)
        if horse_links:
            features['馬の特徴量'].extend([
                '生年月日',
                '生産地',
                '馬主',
                '父馬',
                '母馬',
                '母父馬',
                '過去の成績（通算）',
                '過去の着順履歴',
                '獲得賞金',
            ])
        
        # ====================
        # 4. 騎手詳細
        # ====================
        print("\n" + "=" * 100)
        print("4. 騎手詳細ページから取得可能な追加特徴量")
        print("=" * 100)
        print("（騎手名リンクをクリックすることで取得可能）")
        
        jockey_links = soup.find_all('a', href=lambda x: x and '/jockey/' in x)
        if jockey_links:
            features['騎手の特徴量'].extend([
                '騎手の過去成績',
                '勝率',
                '連対率',
                '複勝率',
                '重賞勝利数',
            ])
        
        # ====================
        # 5. 払い戻し情報
        # ====================
        print("\n" + "=" * 100)
        print("5. 払い戻し情報")
        print("=" * 100)
        
        payout_table = soup.find('table', class_='Payout_Detail_Table')
        if payout_table:
            rows = payout_table.find_all('tr')
            print(f"払い戻し件数: {len(rows)}")
            
            for row in rows[:3]:
                cols = row.find_all('td')
                if len(cols) >= 3:
                    print(f"  {cols[0].text.strip()}: {cols[1].text.strip()} → {cols[2].text.strip()}")
            
            features['オッズ・人気'].extend([
                '単勝払戻',
                '複勝払戻',
                '馬連払戻',
                '馬単払戻',
                'ワイド払戻',
                '三連複払戻',
                '三連単払戻',
            ])
        
        # ====================
        # 6. コーナー通過順位
        # ====================
        print("\n" + "=" * 100)
        print("6. コーナー通過順位")
        print("=" * 100)
        
        corner_data = soup.find('div', class_='Race_Corner_Info')
        if not corner_data:
            corner_data = soup.find('table', string=re.compile('コーナー通過順'))
        
        if corner_data:
            print("コーナー通過順位データあり")
            features['その他'].extend([
                '1コーナー通過順位',
                '2コーナー通過順位',
                '3コーナー通過順位',
                '4コーナー通過順位',
            ])
        
        # ====================
        # 7. ラップタイム
        # ====================
        print("\n" + "=" * 100)
        print("7. ラップタイム")
        print("=" * 100)
        
        lap_time = soup.find('table', class_='Lap_Time_Table')
        if lap_time:
            print("ラップタイムデータあり")
            features['タイム・着差'].append('ラップタイム（各区間）')
        
        # ====================
        # まとめ
        # ====================
        print("\n" + "=" * 100)
        print("機械学習用特徴量まとめ")
        print("=" * 100)
        
        total_features = 0
        for category, feature_list in features.items():
            if feature_list:
                print(f"\n【{category}】 ({len(set(feature_list))}個)")
                for feature in set(feature_list):
                    print(f"  ✓ {feature}")
                total_features += len(set(feature_list))
        
        print("\n" + "=" * 100)
        print(f"合計: {total_features}個の特徴量")
        print("=" * 100)
        
        # エクスポート用にリスト化
        print("\n" + "=" * 100)
        print("すべての特徴量（カテゴリ別）")
        print("=" * 100)
        
        for category, feature_list in features.items():
            if feature_list:
                print(f"\n## {category}")
                for i, feature in enumerate(set(feature_list), 1):
                    print(f"{i}. {feature}")
        
        return features
        
    finally:
        driver.quit()

if __name__ == "__main__":
    # フェアリーS（2026年1月11日）を分析
    race_id = "202606010411"
    
    print("\n現在スクレイピングしているデータから取得可能な特徴量を分析します")
    print("=" * 100 + "\n")
    
    features = analyze_race_page(race_id)
