"""
複数のrace_idをテスト
"""
import requests
from bs4 import BeautifulSoup

# 2024年1月のいくつかの土日
test_race_ids = [
    ("2024010606010101", "2024/1/6 中山1回1日1R"),
    ("2024010705010101", "2024/1/7 東京1回1日1R"),
    ("2024010809010101", "2024/1/8 京都1回1日1R"),
    ("2024012006010101", "2024/1/20 中山2回1日1R"),
    ("2024012705010101", "2024/1/27 東京2回1日1R"),
]

headers = {'User-Agent': 'Mozilla/5.0'}

for race_id, description in test_race_ids:
    url = f"https://race.netkeiba.com/race/result.html?race_id={race_id}"
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        html = response.text
        
        exists = '404' not in html and 'not found' not in html.lower()
        has_table = '<table' in html
        
        print(f"{description} ({race_id})")
        print(f"  Status: {response.status_code}, Exists: {exists}, HasTable: {has_table}")
        
        if exists and has_table:
            soup = BeautifulSoup(html, 'html.parser')
            h1 = soup.find('h1')
            if h1:
                print(f"  >>> FOUND: {h1.get_text(strip=True)[:50]}")
        print()
        
    except Exception as e:
        print(f"{description} ({race_id})")
        print(f"  Error: {e}")
        print()

print("\\n提案: 上記のFOUNDとなったrace_idを使ってテストしてください")
