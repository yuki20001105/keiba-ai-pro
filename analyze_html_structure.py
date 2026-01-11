"""
より詳細にHTMLを解析して、すべての特徴量を出力
"""
import undetected_chromedriver as uc
from bs4 import BeautifulSoup
import time

def detailed_analysis(race_id):
    url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
    
    print(f"URL: {url}\n")
    
    options = uc.ChromeOptions()
    options.headless = False
    driver = uc.Chrome(options=options, use_subprocess=False, version_main=None)
    
    try:
        driver.get(url)
        time.sleep(5)
        
        html = driver.page_source
        soup = BeautifulSoup(html, 'html.parser')
        
        print("=" * 100)
        print("HTML内のすべてのテーブルを検索")
        print("=" * 100)
        
        tables = soup.find_all('table')
        print(f"\n見つかったテーブル数: {len(tables)}\n")
        
        for i, table in enumerate(tables):
            print(f"\n--- テーブル {i+1} ---")
            
            # クラス名
            class_names = table.get('class', [])
            print(f"クラス: {class_names}")
            
            # ID
            table_id = table.get('id', '')
            if table_id:
                print(f"ID: {table_id}")
            
            # ヘッダー
            headers = table.find('tr')
            if headers:
                header_cols = headers.find_all(['th', 'td'])
                if header_cols:
                    header_text = [col.text.strip() for col in header_cols]
                    print(f"ヘッダー: {header_text}")
            
            # 最初の数行
            rows = table.find_all('tr')[1:3]
            for j, row in enumerate(rows):
                cols = row.find_all('td')
                if cols:
                    col_texts = [col.text.strip()[:20] for col in cols]
                    print(f"  行{j+1}: {col_texts}")
        
        print("\n" + "=" * 100)
        print("レース結果用のテーブルを検索")
        print("=" * 100)
        
        # 様々なクラス名で検索
        possible_classes = [
            'Race_Result_Table',
            'Result_Table',
            'Shutuba_Table',
            'RaceResult',
            'result_table',
        ]
        
        result_table = None
        for class_name in possible_classes:
            result_table = soup.find('table', class_=class_name)
            if result_table:
                print(f"\n✓ {class_name} で見つかりました")
                break
        
        if not result_table:
            # クラス名なしで、内容から判定
            for table in tables:
                text = table.text
                if '着順' in text and '馬名' in text and 'タイム' in text:
                    result_table = table
                    print("\n✓ テキスト内容から結果テーブルを特定しました")
                    break
        
        if result_table:
            print("\n結果テーブルの詳細:")
            print("-" * 100)
            
            headers = result_table.find('tr')
            if headers:
                header_cols = headers.find_all(['th', 'td'])
                header_texts = [col.text.strip() for col in header_cols]
                print(f"\nヘッダー ({len(header_texts)}列):")
                for i, h in enumerate(header_texts):
                    print(f"  列{i}: {h}")
            
            rows = result_table.find_all('tr')[1:]
            print(f"\nデータ行数: {len(rows)}")
            
            if rows:
                print("\n最初の馬のデータ:")
                first_row = rows[0]
                cols = first_row.find_all('td')
                for i, col in enumerate(cols):
                    text = col.text.strip()
                    # リンク情報も取得
                    links = col.find_all('a')
                    link_info = [f"→{a.get('href', '')}" for a in links] if links else []
                    print(f"  列{i}: {text[:50]} {' '.join(link_info[:1])}")
        
        else:
            print("\n✗ 結果テーブルが見つかりませんでした")
            print("\nページに含まれるテキスト（最初の1000文字）:")
            print(soup.text[:1000])
        
    finally:
        driver.quit()

if __name__ == "__main__":
    # 過去の完了したレースで分析（2020年1月5日 中山1R）
    race_id = "202006010101"
    detailed_analysis(race_id)
