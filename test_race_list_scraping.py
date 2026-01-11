"""
race_list.htmlから実際のrace_idを取得するテスト
指定した日付のレース一覧を取得
"""
import undetected_chromedriver as uc
from bs4 import BeautifulSoup
import time
import re

def get_race_ids_from_date(kaisai_date):
    """
    指定した日付のrace_idを取得
    kaisai_date: YYYYMMDD形式の文字列
    """
    url = f"https://race.netkeiba.com/top/race_list.html?kaisai_date={kaisai_date}"
    
    print(f"取得URL: {url}")
    print("=" * 80)
    
    # undetected-chromedriverで取得
    options = uc.ChromeOptions()
    options.headless = False
    driver = uc.Chrome(options=options, use_subprocess=False, version_main=None)
    
    try:
        driver.get(url)
        time.sleep(3)  # ページ読み込み待機
        
        html = driver.page_source
        soup = BeautifulSoup(html, 'html.parser')
        
        # race_idを抽出
        race_ids = []
        for link in soup.find_all('a', href=True):
            href = link['href']
            match = re.search(r'race_id=(\d{12})', href)
            if match:
                race_id = match.group(1)
                if race_id not in race_ids:
                    race_ids.append(race_id)
        
        print(f"\n取得結果: {len(race_ids)}レース")
        print("=" * 80)
        
        # race_idを開催場所ごとにグループ化
        grouped = {}
        for race_id in race_ids:
            venue_code = race_id[8:10]
            race_num = race_id[10:12]
            
            if venue_code not in grouped:
                grouped[venue_code] = []
            grouped[venue_code].append((race_id, race_num))
        
        # 場所名マッピング
        venue_names = {
            '01': '札幌', '02': '函館', '03': '福島', '04': '新潟',
            '05': '東京', '06': '中山', '07': '中京', '08': '京都',
            '09': '阪神', '10': '小倉'
        }
        
        for venue_code in sorted(grouped.keys()):
            venue_name = venue_names.get(venue_code, f'場コード{venue_code}')
            races = sorted(grouped[venue_code], key=lambda x: x[1])
            print(f"\n{venue_name} ({venue_code}): {len(races)}レース")
            for race_id, race_num in races[:3]:
                print(f"  {race_num}R: {race_id}")
            if len(races) > 3:
                print(f"  ... 他 {len(races)-3}レース")
        
        return race_ids
        
    finally:
        driver.quit()

if __name__ == "__main__":
    # テストケース1: 2020年1月6日（ユーザーが指摘した日付）
    print("\n🔍 テストケース1: 2020年1月6日")
    race_ids_1 = get_race_ids_from_date("20200106")
    
    print("\n" + "=" * 80)
    print("テストケース1 結果")
    print("=" * 80)
    if race_ids_1:
        print(f"✅ {len(race_ids_1)}レース取得成功")
        print(f"最初のrace_id: {race_ids_1[0]}")
    else:
        print("❌ レースが取得できませんでした")
    
    # テストケース2: 2024年1月8日（別の日付でも確認）
    print("\n\n🔍 テストケース2: 2024年1月8日")
    race_ids_2 = get_race_ids_from_date("20240108")
    
    print("\n" + "=" * 80)
    print("テストケース2 結果")
    print("=" * 80)
    if race_ids_2:
        print(f"✅ {len(race_ids_2)}レース取得成功")
        print(f"最初のrace_id: {race_ids_2[0]}")
    else:
        print("❌ レースが取得できませんでした")
