"""
ユーザー提供の実際のrace_idでテスト
race_id: 202606010401 (2026/06/01 新潟 1R)
"""
import requests
from bs4 import BeautifulSoup

race_id = "202606010401"

# 両方のURLパターンをテスト
urls = [
    (f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}", "出馬表(shutuba)"),
    (f"https://race.netkeiba.com/race/result.html?race_id={race_id}", "レース結果(result)"),
]

headers = {'User-Agent': 'Mozilla/5.0'}

print("=" * 80)
print(f"race_id: {race_id} のテスト")
print("=" * 80)

for url, description in urls:
    print(f"\n{description}")
    print(f"URL: {url}")
    print("-" * 80)
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        html = response.text
        soup = BeautifulSoup(html, 'html.parser')
        
        print(f"Status: {response.status_code}")
        print(f"HTML Length: {len(html)}")
        
        # レース名を探す
        race_name = soup.find('div', class_='RaceName')
        if race_name:
            print(f"RaceName: {race_name.get_text(strip=True)}")
        else:
            print(f"RaceName: NOT FOUND")
        
        # レースデータ（距離、天候など）
        race_data = soup.find('div', class_='RaceData01')
        if race_data:
            print(f"RaceData: {race_data.get_text(strip=True)[:80]}")
        
        # shutuba.htmlの場合: 出馬表テーブル
        shutuba_table = soup.find('table', class_='Shutuba_Table')
        if shutuba_table:
            rows = shutuba_table.find_all('tr')
            print(f"出馬表テーブル: {len(rows)-1}頭")
        
        # result.htmlの場合: 結果テーブル  
        result_table = soup.find('table', class_='Race_Result_Table')
        if result_table:
            rows = result_table.find_all('tr')
            print(f"結果テーブル: {len(rows)-1}頭")
        
        # 404チェック
        if '404' in html or 'not found' in html.lower():
            print(">>> ページが存在しません (404)")
        else:
            print(">>> ページは存在します")
            
    except Exception as e:
        print(f"Error: {e}")

print("\n" + "=" * 80)
print("結論")
print("=" * 80)
print("このrace_idが動作すれば、12桁フォーマットが正しいことが確認できます")
print("形式: YYYYMMDD + 場コード2桁 + レース番号2桁")
