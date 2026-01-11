"""
race_list.htmlの正しい使い方を確認
2020年1月6日のレースを取得
"""
import undetected_chromedriver as uc
from bs4 import BeautifulSoup
import time
import re

def get_race_ids_detailed(kaisai_date):
    """
    指定した日付のrace_idを詳細に取得
    """
    url = f"https://race.netkeiba.com/top/race_list.html?kaisai_date={kaisai_date}"
    
    print(f"開催日: {kaisai_date[:4]}年{kaisai_date[4:6]}月{kaisai_date[6:8]}日")
    print(f"URL: {url}")
    print("=" * 80)
    
    options = uc.ChromeOptions()
    options.headless = False
    driver = uc.Chrome(options=options, use_subprocess=False, version_main=None)
    
    try:
        driver.get(url)
        time.sleep(4)
        
        html = driver.page_source
        soup = BeautifulSoup(html, 'html.parser')
        
        # HTMLの構造を確認
        print("\nページタイトル:", driver.title)
        
        # race_idを含むリンクをすべて抽出
        race_links = []
        for link in soup.find_all('a', href=True):
            href = link['href']
            if 'race_id=' in href:
                match = re.search(r'race_id=(\d+)', href)
                if match:
                    race_id = match.group(1)
                    link_text = link.get_text(strip=True)
                    race_links.append((race_id, link_text, href))
        
        print(f"\nrace_idを含むリンク: {len(race_links)}件")
        
        # 重複を除いてrace_idを抽出
        unique_race_ids = {}
        for race_id, text, href in race_links:
            if race_id not in unique_race_ids:
                unique_race_ids[race_id] = (text, href)
        
        print(f"ユニークなrace_id: {len(unique_race_ids)}件\n")
        
        # 最初の10件を表示
        for i, (race_id, (text, href)) in enumerate(list(unique_race_ids.items())[:10]):
            print(f"{i+1}. race_id={race_id}")
            print(f"   テキスト: {text[:50]}")
            print(f"   リンク: {href[:80]}")
            
            # race_idの構造を解析
            print(f"   解析: {race_id[:4]}年 ", end="")
            if len(race_id) == 12:
                print(f"{race_id[4:6]}月{race_id[6:8]}日 場{race_id[8:10]} {race_id[10:12]}R")
            elif len(race_id) == 14:
                print(f"開催{race_id[4:6]} 日{race_id[6:8]} 場{race_id[8:10]} {race_id[10:12]}R (14桁)")
            else:
                print(f"(桁数: {len(race_id)})")
            print()
        
        return list(unique_race_ids.keys())
        
    finally:
        driver.quit()

if __name__ == "__main__":
    # 2020年1月6日
    print("=" * 80)
    print("2020年1月6日のレースを取得")
    print("=" * 80 + "\n")
    
    race_ids = get_race_ids_detailed("20200106")
    
    print("\n" + "=" * 80)
    print(f"合計: {len(race_ids)}レース")
    print("=" * 80)
