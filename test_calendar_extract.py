"""
カレンダーページの各開催場からレースIDを生成
"""
import requests
from bs4 import BeautifulSoup
import re

def get_race_ids_from_calendar():
    """カレンダーから開催場情報を取得してレースIDを生成"""
    
    year = 2024
    month = 1
    url = f"https://race.netkeiba.com/top/calendar.html?year={year}&month={month}"
    
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    print(f"Year/Month: {year}/{month}")
    print("="* 60)
    
    response = requests.get(url, headers=headers, timeout=10)
    html = response.text
    soup = BeautifulSoup(html, 'html.parser')
    
    # 開催日を探す
    race_dates = []
    
    # td class="RaceCellBox" を探す
    cells = soup.find_all('td', class_=lambda x: x and 'Race' in x)
    print(f"\nFound {len(cells)} race cells")
    
    for cell in cells[:5]:
        print(f"\nCell: {cell.get('class')}")
        # 日付を取得
        day_elem = cell.find(['span', 'div', 'a'], class_=lambda x: x and ('day' in str(x).lower() or 'date' in str(x).lower()))
        if day_elem:
            print(f"  Day element: {day_elem}")
        
        # リンクを探す
        links = cell.find_all('a', href=True)
        for link in links:
            href = link['href']
            print(f"  Link: {href}")
            # race_list.html?kaisai_date=YYYYMMDD を探す
            match = re.search(r'kaisai_date=(\d{8})', href)
            if match:
                date = match.group(1)
                race_dates.append(date)
                print(f"    -> Found date: {date}")
    
    # ユニークな日付
    unique_dates = list(set(race_dates))
    unique_dates.sort()
    
    print(f"\n\nUnique race dates found: {len(unique_dates)}")
    for date in unique_dates:
        print(f"  {date}")
    
    return unique_dates

if __name__ == "__main__":
    dates = get_race_ids_from_calendar()
