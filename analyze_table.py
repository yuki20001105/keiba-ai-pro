#!/usr/bin/env python3
"""
テーブル構造を詳細に分析
"""
import undetected_chromedriver as uc
from bs4 import BeautifulSoup
import time

def main():
    print("ChromeDriver起動中...")
    
    options = uc.ChromeOptions()
    options.add_argument('--disable-blink-features=AutomationControlled')
    prefs = {'profile.managed_default_content_settings.images': 2}
    options.add_experimental_option('prefs', prefs)
    
    driver = uc.Chrome(options=options, headless=False)
    
    try:
        race_id = "202406010101"
        url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
        
        print(f"URL: {url}")
        driver.get(url)
        time.sleep(4)
        
        # page_sourceはすでにUnicodeなのでそのまま使用
        html = driver.page_source
        soup = BeautifulSoup(html, 'html.parser')
        
        # レース名
        race_name = soup.find('h1', class_='RaceName')
        print(f"\nレース名: {race_name.text.strip() if race_name else 'なし'}")
        
        # テーブル取得
        table = soup.find('table', class_='RaceTable01')
        if not table:
            print("❌ テーブルが見つかりません")
            return
        
        print(f"✓ テーブル発見")
        
        # 全行を取得
        rows = table.find_all('tr')
        print(f"総行数: {len(rows)}")
        
        # ヘッダー行
        if len(rows) > 0:
            header = rows[0]
            ths = header.find_all('th')
            print(f"\nヘッダー ({len(ths)}列):")
            for i, th in enumerate(ths[:15]):
                print(f"  {i}: {th.text.strip()}")
        
        # データ行をカウント
        data_rows = []
        for i, row in enumerate(rows[1:], 1):
            cols = row.find_all('td')
            if len(cols) > 10:  # 十分な列がある行のみ
                data_rows.append((i, len(cols), row))
        
        print(f"\nデータ行: {len(data_rows)}行")
        
        # 最初の3頭分のデータを表示
        for i, (row_idx, col_count, row) in enumerate(data_rows[:3], 1):
            print(f"\n馬 {i} (行{row_idx}, {col_count}列):")
            cols = row.find_all('td')
            
            # 着順
            print(f"  着順: {cols[0].text.strip()}")
            # 枠番
            print(f"  枠番: {cols[1].text.strip()}")
            # 馬番
            print(f"  馬番: {cols[2].text.strip()}")
            # 馬名
            horse_link = cols[3].find('a')
            if horse_link:
                horse_name = horse_link.text.strip()
                horse_url = horse_link.get('href', '')
                print(f"  馬名: {horse_name}")
                print(f"  URL: {horse_url}")
            # 性齢
            print(f"  性齢: {cols[4].text.strip()}")
            # 斤量
            print(f"  斤量: {cols[5].text.strip()}")
            # 騎手
            jockey_link = cols[6].find('a')
            if jockey_link:
                print(f"  騎手: {jockey_link.text.strip()}")
        
    finally:
        driver.quit()

if __name__ == '__main__':
    main()
