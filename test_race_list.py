import undetected_chromedriver as uc
from bs4 import BeautifulSoup
import time
import re

# ドライバー初期化
options = uc.ChromeOptions()
options.add_argument('--headless=new')
driver = uc.Chrome(options=options)

# レース一覧ページを取得
kaisai_date = "20240106"
url = f"https://race.netkeiba.com/top/race_list.html?kaisai_date={kaisai_date}"
print(f"URL: {url}")

driver.get(url)
time.sleep(3)

# HTMLを解析
soup = BeautifulSoup(driver.page_source, 'html.parser', from_encoding='euc-jp')

# すべてのリンクを確認
all_links = soup.find_all('a', href=True)
print(f"\n総リンク数: {len(all_links)}")

# race_id=を含むリンクを抽出
races = []
for link in all_links:
    href = link.get('href', '')
    if 'race_id=' in href:
        match = re.search(r'race_id=(\d{12})', href)
        if match:
            race_id = match.group(1)
            if race_id not in races:
                races.append(race_id)

print(f"\n抽出されたrace_id数: {len(races)}")
print("\n最初の10件:")
for i, race_id in enumerate(races[:10], 1):
    print(f"{i}. {race_id}")

driver.quit()
