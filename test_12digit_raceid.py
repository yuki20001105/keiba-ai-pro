"""
カレンダーから開催日を取得し、race_idを推測して検証する方法

参考サイトによると:
- race_idは12桁: YYYYMMDD + 場コード2桁 + レース番号2桁
- 例: 202401060606 = 2024/01/06 中山(06) 6R

場コード:
01=札幌, 02=函館, 03=福島, 04=新潟, 05=東京
06=中山, 07=中京, 08=京都, 09=阪神, 10=小倉
"""
import requests
from bs4 import BeautifulSoup
import re

def get_kaisai_dates(year, month):
    """カレンダーから開催日を取得"""
    url = f"https://race.netkeiba.com/top/calendar.html?year={year}&month={month}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    response = requests.get(url, headers=headers, timeout=10)
    html = response.text
    
    dates = re.findall(r'kaisai_date=(\d{8})', html)
    return sorted(set(dates))

def test_race_id_exists(race_id):
    """race_idが存在するかテスト"""
    url = f"https://race.netkeiba.com/race/result.html?race_id={race_id}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        html = response.text
        soup = BeautifulSoup(html, 'html.parser')
        
        # レース名を確認
        race_name = soup.find('div', class_='RaceName')
        result_table = soup.find('table', class_='Race_Result_Table')
        
        if race_name and result_table:
            return True, race_name.get_text(strip=True)
        return False, None
    except:
        return False, None

def find_races_for_date(date):
    """指定日のレースを場コードとレース番号で探す"""
    print(f"\nDate: {date}")
    print("-" * 60)
    
    venues = {
        '01': '札幌', '02': '函館', '03': '福島', '04': '新潟', '05': '東京',
        '06': '中山', '07': '中京', '08': '京都', '09': '阪神', '10': '小倉'
    }
    
    found_races = []
    
    # 主要3場を先にチェック
    priority_venues = ['05', '06', '09']
    
    for venue_code in priority_venues:
        venue_name = venues[venue_code]
        
        # 1Rをテスト（存在チェック）
        test_race_id = f"{date}{venue_code}01"
        exists, race_name = test_race_id_exists(test_race_id)
        
        if exists:
            print(f"  {venue_name}: レースあり")
            
            # 1Rから12Rまでチェック
            for race_num in range(1, 13):
                race_id = f"{date}{venue_code}{race_num:02d}"
                exists, race_name = test_race_id_exists(race_id)
                
                if exists:
                    found_races.append((race_id, venue_name, race_num, race_name))
                    print(f"    {race_num}R: {race_id} - {race_name[:30]}")
                else:
                    # このレース番号で見つからなければ終了
                    break
        else:
            print(f"  {venue_name}: 開催なし")
    
    return found_races

if __name__ == "__main__":
    print("=" * 80)
    print("正しいrace_idフォーマット（12桁）でテスト")
    print("=" * 80)
    
    # 2024年1月のカレンダーから開催日を取得
    dates = get_kaisai_dates(2024, 1)
    print(f"\nFound {len(dates)} kaisai dates in 2024/1")
    
    if dates:
        # 最初の開催日でテスト
        first_date = dates[0]
        races = find_races_for_date(first_date)
        
        print("\n" + "=" * 80)
        print(f"Summary: Found {len(races)} races on {first_date}")
        print("=" * 80)
        
        if races:
            print("\nSuccess! Race IDs:")
            for race_id, venue, num, name in races:
                print(f"  {race_id} - {venue}{num}R: {name[:40]}")
            
            print(f"\n*** 重要: race_idは12桁が正解! ***")
            print(f"例: {races[0][0]} = {first_date[:4]}/{first_date[4:6]}/{first_date[6:8]} {races[0][1]} {races[0][2]}R")
