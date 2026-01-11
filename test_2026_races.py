"""
最新のレース（2025-2026年）をテスト
"""
import requests
from bs4 import BeautifulSoup

# 2026年1月と2025年12月を試す
test_race_ids = [
    ("2026010406010101", "2026/1/4 中山"),
    ("2026010506010101", "2026/1/5 中山"),
    ("2026011106010101", "2026/1/11 中山 (today)"),
    ("2026011206010101", "2026/1/12 中山"),
    ("2025122806010101", "2025/12/28 中山"),
    ("2025122906010101", "2025/12/29 中山"),
]

headers = {'User-Agent': 'Mozilla/5.0'}

for race_id, description in test_race_ids:
    url = f"https://race.netkeiba.com/race/result.html?race_id={race_id}"
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        html = response.text
        
        exists = '404' not in html and 'not found' not in html.lower() and 'error' not in html.lower()[:500]
        has_table = '<table' in html and 'Result_Table' in html
        
        print(f"{description} ({race_id})")
        print(f"  Status: {response.status_code}, Exists: {exists}, HasTable: {has_table}")
        
        if has_table:
            soup = BeautifulSoup(html, 'html.parser')
            h1 = soup.find('h1')
            title = soup.find('title')
            if h1:
                print(f"  H1: {h1.get_text(strip=True)[:60]}")
            if title:
                print(f"  Title: {title.get_text(strip=True)[:60]}")
            print(f"  >>> FOUND!")
        print()
        
    except Exception as e:
        print(f"{description} ({race_id})")
        print(f"  Error: {e}")
        print()
