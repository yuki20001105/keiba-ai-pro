"""
2020-2023年の過去データでテスト
"""
import requests
from bs4 import BeautifulSoup

test_cases = [
    ("202312230601", "2023/12/23 Nakayama 1R"),
    ("202312230501", "2023/12/23 Tokyo 1R"),
    ("202212240601", "2022/12/24 Nakayama 1R"),
    ("202112250601", "2021/12/25 Nakayama 1R"),
    ("202012260601", "2020/12/26 Nakayama 1R"),
]

print("過去データ（2020-2023年）でテスト")
print("=" * 80)

for race_id, desc in test_cases:
    url = f"https://race.netkeiba.com/race/result.html?race_id={race_id}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        r = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')
        race_name = soup.find('div', class_='RaceName')
        result_table = soup.find('table', class_='Race_Result_Table')
        
        print(f"\n{desc} - race_id: {race_id}")
        print(f"  Status: {r.status_code}")
        
        if race_name and result_table:
            name_text = race_name.get_text(strip=True)
            rows = result_table.find_all('tr')
            print(f"  SUCCESS! Race: {name_text}, Horses: {len(rows)-1}")
        else:
            print(f"  NOT FOUND")
    except Exception as e:
        print(f"\n{desc} - race_id: {race_id}")
        print(f"  Error: {e}")

print("\n" + "=" * 80)
