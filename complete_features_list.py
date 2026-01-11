"""
スクレイピングで取得可能なすべての特徴量を整理
"""

import undetected_chromedriver as uc
from bs4 import BeautifulSoup
import re
from time import sleep

def extract_all_features(race_id):
    url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
    
    options = uc.ChromeOptions()
    options.headless = False
    driver = uc.Chrome(options=options, use_subprocess=False, version_main=None)
    
    try:
        driver.get(url)
        sleep(5)
        
        html = driver.page_source
        soup = BeautifulSoup(html, 'html.parser')
        
        features = {}
        
        # ===== 1. レース基本情報 =====
        print("=" * 100)
        print("1. レース基本情報")
        print("=" * 100)
        
        race_name_elem = soup.find('h1', class_='RaceName')
        if race_name_elem:
            features['レース名'] = race_name_elem.text.strip()
            print(f"レース名: {features['レース名']}")
        
        data01 = soup.find('div', class_='RaceData01')
        if data01:
            text = data01.text.strip()
            print(f"\nRaceData01: {text}")
            
            # 発走時刻
            if '発走' in text:
                features['発走時刻'] = re.search(r'(\d+:\d+)発走', text).group(1) if re.search(r'(\d+:\d+)発走', text) else None
            
            # トラック種別
            if '芝' in text:
                features['トラック種別'] = '芝'
            elif 'ダート' in text:
                features['トラック種別'] = 'ダート'
            
            # 距離
            distance_match = re.search(r'(\d+)m', text)
            if distance_match:
                features['距離'] = int(distance_match.group(1))
            
            # コース方向
            if '右' in text:
                features['コース方向'] = '右'
            elif '左' in text:
                features['コース方向'] = '左'
            
            # 天候
            if '天候' in text:
                weather_match = re.search(r'天候:([^\s/]+)', text)
                if weather_match:
                    features['天候'] = weather_match.group(1)
            
            # 馬場状態
            if '馬場' in text:
                baba_match = re.search(r'馬場:([^\s]+)', text)
                if baba_match:
                    features['馬場状態'] = baba_match.group(1)
        
        data02 = soup.find('div', class_='RaceData02')
        if data02:
            text = data02.text.strip()
            print(f"RaceData02: {text}")
            
            # 開催情報
            kaisai_match = re.search(r'(\d+)回\s+([^\s]+)\s+(\d+)日目', text)
            if kaisai_match:
                features['開催回'] = int(kaisai_match.group(1))
                features['競馬場'] = kaisai_match.group(2)
                features['開催日目'] = int(kaisai_match.group(3))
            
            # レースクラス
            if 'オープン' in text:
                features['レースクラス'] = 'オープン'
            elif '新馬' in text:
                features['レースクラス'] = '新馬'
            elif '未勝利' in text:
                features['レースクラス'] = '未勝利'
            elif '１勝クラス' in text or '1勝クラス' in text:
                features['レースクラス'] = '1勝クラス'
            elif '２勝クラス' in text or '2勝クラス' in text:
                features['レースクラス'] = '2勝クラス'
            elif '３勝クラス' in text or '3勝クラス' in text:
                features['レースクラス'] = '3勝クラス'
            
            # 頭数
            head_match = re.search(r'(\d+)頭', text)
            if head_match:
                features['出走頭数'] = int(head_match.group(1))
        
        # 賞金
        prize_text = soup.find(string=re.compile('本賞金'))
        if prize_text:
            print(f"賞金: {prize_text.strip()}")
            features['賞金'] = prize_text.strip()
        
        print(f"\n取得したレース基本情報: {len([k for k in features.keys() if k not in ['馬データ']])}個")
        for key, value in features.items():
            if key != '馬データ':
                print(f"  ✓ {key}: {value}")
        
        # ===== 2. レース結果テーブル（各馬の情報） =====
        print("\n" + "=" * 100)
        print("2. レース結果テーブル（各馬の情報）")
        print("=" * 100)
        
        result_table = soup.find('table', id='All_Result_Table')
        if not result_table:
            # idがない場合、内容から検索
            tables = soup.find_all('table')
            for table in tables:
                text = table.text
                if '着順' in text and '馬名' in text and 'タイム' in text:
                    result_table = table
                    break
        
        horse_features = []
        if result_table:
            headers = result_table.find('tr')
            header_cols = headers.find_all(['th', 'td'])
            header_texts = [col.text.strip() for col in header_cols]
            
            print(f"列ヘッダー ({len(header_texts)}列):")
            for i, h in enumerate(header_texts):
                print(f"  {i+1}. {h}")
            
            rows = result_table.find_all('tr')[1:]
            print(f"\n出走馬数: {len(rows)}頭")
            
            # 各馬のデータを抽出
            for row in rows[:3]:  # サンプルとして最初の3頭だけ表示
                cols = row.find_all('td')
                horse_data = {}
                
                if len(cols) >= 15:
                    horse_data['着順'] = cols[0].text.strip()
                    horse_data['枠番'] = cols[1].text.strip()
                    horse_data['馬番'] = cols[2].text.strip()
                    
                    # 馬名（リンク付き）
                    horse_link = cols[3].find('a')
                    if horse_link:
                        horse_data['馬名'] = horse_link.text.strip()
                        horse_data['馬ID'] = horse_link.get('href', '')
                    
                    horse_data['性齢'] = cols[4].text.strip()
                    horse_data['斤量'] = cols[5].text.strip()
                    
                    # 騎手（リンク付き）
                    jockey_link = cols[6].find('a')
                    if jockey_link:
                        horse_data['騎手'] = jockey_link.text.strip()
                        horse_data['騎手ID'] = jockey_link.get('href', '')
                    
                    horse_data['タイム'] = cols[7].text.strip()
                    horse_data['着差'] = cols[8].text.strip()
                    horse_data['人気'] = cols[9].text.strip()
                    horse_data['単勝オッズ'] = cols[10].text.strip()
                    horse_data['後3F'] = cols[11].text.strip()
                    horse_data['コーナー通過順'] = cols[12].text.strip()
                    
                    # 厩舎（リンク付き）
                    trainer_cell = cols[13]
                    trainer_link = trainer_cell.find('a')
                    if trainer_link:
                        horse_data['調教師'] = trainer_link.text.strip()
                        horse_data['調教師ID'] = trainer_link.get('href', '')
                    
                    horse_data['馬体重'] = cols[14].text.strip()
                    
                    horse_features.append(horse_data)
            
            # サンプル表示
            if horse_features:
                print(f"\n【サンプル】1着馬のデータ:")
                for key, value in horse_features[0].items():
                    if 'ID' not in key:
                        print(f"  {key}: {value}")
        
        # ===== 3. ラップタイム =====
        print("\n" + "=" * 100)
        print("3. ラップタイム")
        print("=" * 100)
        
        lap_table = soup.find('table', class_='Race_HaronTime')
        if lap_table:
            headers = lap_table.find('tr')
            if headers:
                distances = [th.text.strip() for th in headers.find_all(['th', 'td'])]
                times_row = lap_table.find_all('tr')[1] if len(lap_table.find_all('tr')) > 1 else None
                if times_row:
                    times = [td.text.strip() for td in times_row.find_all('td')]
                    print("距離別ラップタイム:")
                    for dist, time in zip(distances, times):
                        print(f"  {dist}: {time}")
        
        # ===== 4. コーナー通過順位 =====
        print("\n" + "=" * 100)
        print("4. コーナー通過順位")
        print("=" * 100)
        
        corner_table = soup.find('table', class_='Corner_Num')
        if corner_table:
            rows = corner_table.find_all('tr')
            for row in rows:
                cols = row.find_all(['th', 'td'])
                if len(cols) >= 2:
                    corner = cols[0].text.strip()
                    order = cols[1].text.strip()
                    if corner and order:
                        print(f"  {corner}: {order}")
        
        # ===== 5. 払戻（オッズ情報） =====
        print("\n" + "=" * 100)
        print("5. 払戻（オッズ情報）")
        print("=" * 100)
        
        payout_tables = soup.find_all('table', class_='Payout_Detail_Table')
        if payout_tables:
            for table in payout_tables[:3]:  # 単勝、複勝、枠連程度表示
                rows = table.find_all('tr')
                for row in rows[:2]:
                    cols = row.find_all(['th', 'td'])
                    text = ' '.join([col.text.strip() for col in cols])
                    print(f"  {text}")
        
        # ===== 特徴量サマリー =====
        print("\n" + "=" * 100)
        print("【特徴量サマリー】")
        print("=" * 100)
        
        print(f"""
A. レース単位の特徴量: {len([k for k in features.keys() if k != '馬データ'])}個
   1. レース名
   2. 発走時刻
   3. トラック種別（芝/ダート）
   4. 距離
   5. コース方向（左/右）
   6. 天候
   7. 馬場状態
   8. 開催回
   9. 競馬場
   10. 開催日目
   11. レースクラス
   12. 出走頭数
   13. 賞金
   14. ラップタイム（複数地点）
   15. コーナー通過順位（4コーナー分）

B. 各馬の特徴量（結果テーブルから直接取得）: 15個
   1. 着順 ⭐（目的変数）
   2. 枠番
   3. 馬番
   4. 馬名
   5. 性齢
   6. 斤量
   7. 騎手
   8. タイム ⭐（目的変数の一つ）
   9. 着差
   10. 人気
   11. 単勝オッズ
   12. 後3F（上がり3ハロン）
   13. コーナー通過順
   14. 調教師
   15. 馬体重（増減）

C. リンク先から取得可能な追加特徴量:
   
   【馬詳細ページから】
   - 生年月日（年齢計算）
   - 性別
   - 毛色
   - 生産地
   - 馬主
   - 父馬
   - 母馬
   - 母父馬
   - 過去の全成績（着順、賞金、レース条件）
   - 血統情報
   - 獲得賞金累計
   
   【騎手詳細ページから】
   - 勝率
   - 連対率
   - 複勝率
   - 重賞勝利数
   - 年間勝利数
   - 得意なコース・距離
   
   【調教師詳細ページから】
   - 勝率
   - 連対率
   - 複勝率
   - 管理馬の成績
   - 得意なレース傾向

合計: 
  - 現在取得中: 約30個の基本特徴量
  - 詳細ページ含む: 70個以上の特徴量が取得可能

⭐ 目的変数（予測対象）:
  - 着順（1位、2位、3位...）
  - タイム（秒数）
  - 着差
  - 3着以内に入るか（複勝予測）
        """)
        
    finally:
        driver.quit()

if __name__ == "__main__":
    # 2020年1月5日 中山1R（完了済みレース）
    race_id = "202006010101"
    extract_all_features(race_id)
