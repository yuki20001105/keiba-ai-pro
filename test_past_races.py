import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

print("=" * 100)
print(f"Current date: {datetime.now().date()}")
print("=" * 100)

# 確実に存在する過去のレース
# 2024年12月: 有馬記念（中山G1）
test_races = [
    ("202412220612", "2024/12/22 中山12R（有馬記念予定日）"),
    ("202412210611", "2024/12/21 中山11R"),
    ("202412010601", "2024/12/01 中山1R"),
    ("202401010601", "2024/01/01 中山1R（元日）"),
]

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

print("\n過去のレースをテスト:\n")

for race_id, description in test_races:
    url = f"https://race.netkeiba.com/race/result.html?race_id={race_id}"
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        
        status = response.status_code
        if status == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            race_name = soup.select_one('h1.RaceName')
            race_name_text = race_name.get_text(strip=True) if race_name else "NOT FOUND"
            print(f"✓ {description}")
            print(f"  race_id: {race_id}")
            print(f"  Status: {status}, RaceName: {race_name_text}")
            print()
        else:
            print(f"✗ {description}")
            print(f"  race_id: {race_id}")
            print(f"  Status: {status}")
            print()
            
    except Exception as e:
        print(f"✗ {description} - ERROR: {e}\n")
