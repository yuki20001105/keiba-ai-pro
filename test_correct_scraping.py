"""
参考サイトに基づいた正しいスクレイピング方法をテスト

重要な発見:
1. race_idは12桁: 202401060101 (YYYYMMDD + 場コード2桁 + レース番号2桁)
   ※従来の14桁ではなく、開催回・開催日情報は含まない
   
2. 開催日一覧はカレンダーページから取得
3. race_id一覧は各開催日のrace_list.htmlから取得
"""
import requests
from bs4 import BeautifulSoup
import re

def test_calendar_scraping():
    """カレンダーページから開催日を取得"""
    print("=" * 80)
    print("1. カレンダーページから開催日を取得")
    print("=" * 80)
    
    url = "https://race.netkeiba.com/top/calendar.html?year=2024&month=1"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    response = requests.get(url, headers=headers, timeout=10)
    html = response.text
    
    # kaisai_date=YYYYMMDDのパターンを抽出
    dates = re.findall(r'kaisai_date=(\d{8})', html)
    unique_dates = sorted(set(dates))
    
    print(f"Status: {response.status_code}")
    print(f"Found {len(unique_dates)} unique dates:")
    for date in unique_dates[:10]:
        print(f"  {date}")
    
    return unique_dates

def test_race_list_scraping(kaisai_date):
    """race_list.htmlから12桁のrace_idを取得"""
    print(f"\n{'=' * 80}")
    print(f"2. race_list.htmlからrace_id一覧を取得 (kaisai_date={kaisai_date})")
    print("=" * 80)
    
    url = f"https://race.netkeiba.com/top/race_list.html?kaisai_date={kaisai_date}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    response = requests.get(url, headers=headers, timeout=10)
    html = response.text
    soup = BeautifulSoup(html, 'html.parser')
    
    # race_id=数字 のパターンを抽出
    race_ids = []
    
    # リンクからrace_idを抽出
    for link in soup.find_all('a', href=True):
        href = link['href']
        match = re.search(r'race_id=(\d{12})', href)
        if match:
            race_id = match.group(1)
            if race_id not in race_ids:
                race_ids.append(race_id)
    
    print(f"Status: {response.status_code}")
    print(f"Found {len(race_ids)} race IDs:")
    for i, race_id in enumerate(race_ids[:15]):
        print(f"  {i+1}. {race_id}")
    
    return race_ids

def test_race_scraping(race_id):
    """実際のレース結果ページからデータを取得"""
    print(f"\n{'=' * 80}")
    print(f"3. レース結果ページからデータを取得 (race_id={race_id})")
    print("=" * 80)
    
    url = f"https://race.netkeiba.com/race/result.html?race_id={race_id}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    response = requests.get(url, headers=headers, timeout=10)
    html = response.text
    soup = BeautifulSoup(html, 'html.parser')
    
    print(f"Status: {response.status_code}")
    
    # レース名を取得
    race_name = soup.find('div', class_='RaceName')
    if race_name:
        print(f"Race Name: {race_name.get_text(strip=True)}")
    
    # レース情報を取得
    race_data = soup.find('div', class_='RaceData01')
    if race_data:
        print(f"Race Data: {race_data.get_text(strip=True)[:100]}")
    
    # 結果テーブルを取得
    result_table = soup.find('table', class_='Race_Result_Table')
    if result_table:
        rows = result_table.find_all('tr')
        print(f"Result Table: {len(rows)-1} horses found")
        
        # 最初の3頭を表示
        for i, row in enumerate(rows[1:4]):
            cols = row.find_all('td')
            if len(cols) >= 3:
                finish = cols[0].get_text(strip=True)
                horse = cols[3].get_text(strip=True) if len(cols) > 3 else 'N/A'
                print(f"  {finish}着: {horse}")
        
        return True
    else:
        print("Result Table: NOT FOUND")
        return False

if __name__ == "__main__":
    print("\n🚀 正しいスクレイピング方法のテスト開始\n")
    
    # 1. カレンダーから開催日を取得
    dates = test_calendar_scraping()
    
    if dates:
        # 2. 最初の開催日のrace_id一覧を取得
        first_date = dates[0]
        race_ids = test_race_list_scraping(first_date)
        
        if race_ids:
            # 3. 最初のレースをスクレイピング
            first_race_id = race_ids[0]
            success = test_race_scraping(first_race_id)
            
            print("\n" + "=" * 80)
            print("テスト結果サマリー")
            print("=" * 80)
            print(f"✅ 開催日取得: {len(dates)}日")
            print(f"✅ race_id取得: {len(race_ids)}レース")
            print(f"{'✅' if success else '❌'} レース詳細取得: {'成功' if success else '失敗'}")
            
            if success:
                print("\n🎉 正しい方法でスクレイピングが成功しました！")
                print(f"\n💡 重要: race_idは12桁 (例: {first_race_id})")
                print("   形式: YYYYMMDD + 場コード2桁 + レース番号2桁")
        else:
            print("\n❌ race_id一覧の取得に失敗")
    else:
        print("\n❌ 開催日一覧の取得に失敗")
