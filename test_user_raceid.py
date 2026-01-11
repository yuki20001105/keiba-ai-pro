"""
ユーザーが指摘したrace_id 202006010101を確認
"""
import requests
from bs4 import BeautifulSoup

# ユーザーが指摘したrace_id
race_id = "202006010101"

# db.netkeiba.comのURL
url = f"https://db.netkeiba.com/race/{race_id}/"

print(f"race_id: {race_id}")
print(f"URL: {url}")
print("=" * 80)

response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
print(f"Status: {response.status_code}")

if response.status_code == 200:
    soup = BeautifulSoup(response.text, 'html.parser')
    
    title = soup.find('title')
    if title:
        print(f"Title: {title.text.strip()}")
    
    # データベースページの構造を確認
    race_name = soup.find('h1', class_='raceTitle')
    if race_name:
        print(f"Race Name: {race_name.text.strip()}")
    
    # race_idの解析
    print("\n" + "=" * 80)
    print("race_id解析:")
    print("=" * 80)
    print(f"YYYY: {race_id[:4]} (2020年)")
    print(f"MM: {race_id[4:6]} (6月)")
    print(f"DD: {race_id[6:8]} (1日)")
    print(f"場コード: {race_id[8:10]} (01=札幌)")
    print(f"レース番号: {race_id[10:12]} (01R)")
    
    # つまり 2020年6月1日 札幌1R
    print(f"\n解釈: 2020年6月1日 札幌1R")
    
    # race.netkeiba.comでも確認
    print("\n" + "=" * 80)
    print("race.netkeiba.comでの確認:")
    print("=" * 80)
    
    result_url = f"https://race.netkeiba.com/race/result.html?race_id={race_id}"
    print(f"URL: {result_url}")
    
    result_response = requests.get(result_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
    print(f"Status: {result_response.status_code}")
    
    if result_response.status_code == 200:
        result_soup = BeautifulSoup(result_response.text, 'html.parser')
        result_title = result_soup.find('title')
        if result_title:
            print(f"Title: {result_title.text.strip()}")
