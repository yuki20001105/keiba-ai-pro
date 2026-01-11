"""
シンプルにURLからrace_idを抽出
"""
import requests
import re

def simple_extract():
    date = "20240106"
    url = f"https://race.netkeiba.com/top/race_list.html?kaisai_date={date}"
    
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    print(f"Date: {date}")
    print("="* 60)
    
    response = requests.get(url, headers=headers, timeout=10)
    html = response.text
    
    # 方法1: href="/top/race.html?race_id=数字"
    pattern1 = re.findall(r'href="[^"]*race\.html\?race_id=(\d+)', html)
    print(f"\nPattern 1 (race.html?race_id=): {len(pattern1)} found")
    for i, rid in enumerate(set(pattern1)):
        print(f"  {i+1}. {rid}")
    
    # 方法2: すべてのhref
    all_hrefs = re.findall(r'href="([^"]+)"', html)
    race_hrefs = [h for h in all_hrefs if 'race' in h.lower() and ('2024' in h or 'race_id' in h)]
    print(f"\nPattern 2 (all race hrefs): {len(race_hrefs)} found")
    for i, href in enumerate(race_hrefs[:20]):
        print(f"  {i+1}. {href}")

if __name__ == "__main__":
    simple_extract()
