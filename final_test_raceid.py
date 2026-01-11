"""
最新のレース（2025-2026年）を12桁フォーマットでテスト
"""
import requests
from bs4 import BeautifulSoup

def test_race_id(race_id, description):
    """race_idをテストして結果を表示"""
    url = f"https://race.netkeiba.com/race/result.html?race_id={race_id}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        html = response.text
        soup = BeautifulSoup(html, 'html.parser')
        
        race_name = soup.find('div', class_='RaceName')
        result_table = soup.find('table', class_='Race_Result_Table')
        
        print(f"\n{description}")
        print(f"  race_id: {race_id}")
        print(f"  URL: {url}")
        print(f"  Status: {response.status_code}")
        
        if race_name and result_table:
            name_text = race_name.get_text(strip=True)
            rows = result_table.find_all('tr')
            print(f"  Result: SUCCESS!")
            print(f"  Race Name: {name_text}")
            print(f"  Horses: {len(rows)-1}")
            return True
        else:
            print(f"  Result: NOT FOUND (no RaceName or Result_Table)")
            return False
    except Exception as e:
        print(f"\n{description}")
        print(f"  race_id: {race_id}")
        print(f"  Error: {e}")
        return False

# テストするrace_id一覧
test_cases = [
    # 2026年1月（今日は1/11）
    ("202601110501", "2026/1/11 Tokyo 1R"),
    ("202601110601", "2026/1/11 Nakayama 1R"),
    ("202601040601", "2026/1/4 Nakayama 1R"),
    ("202601050601", "2026/1/5 Nakayama 1R"),
    
    # 2025年12月
    ("202512280601", "2025/12/28 Nakayama 1R"),
    ("202512290601", "2025/12/29 Nakayama 1R"),
    
    # 別のフォーマット: 14桁（従来の方法）
    ("20260111060101", "2026/1/11 Nakayama 1回1日1R (14桁)"),
]

print("=" * 80)
print("race_idフォーマットの検証テスト")
print("=" * 80)

success_count = 0
for race_id, desc in test_cases:
    if test_race_id(race_id, desc):
        success_count += 1

print("\n" + "=" * 80)
print(f"結果: {success_count}/{len(test_cases)} 成功")
print("=" * 80)

if success_count == 0:
    print("\n重要: 全てのrace_idが見つかりませんでした")
    print("可能性:")
    print("1. 2026年1月のデータがまだnetkeiba.comに存在しない")
    print("2. race_idのフォーマットが変更された")
    print("3. ブラウザでログインが必要になった")
    print("\n次のアクション: ブラウザで実際にnetkeiba.comを開いて、")
    print("動作するrace_idの例を1つ見つけてください")
