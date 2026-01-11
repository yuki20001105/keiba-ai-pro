import undetected_chromedriver as uc
from bs4 import BeautifulSoup
import time

print("=== スクレイピングテスト ===")
print("1. ChromeDriver起動中...")
options = uc.ChromeOptions()
options.add_argument("--headless=new")
driver = uc.Chrome(options=options)

print("2. ページ取得中...")
driver.get("https://race.netkeiba.com/race/result.html?race_id=202305010101")
time.sleep(5)

soup = BeautifulSoup(driver.page_source, "html.parser")
print(f"3. ページサイズ: {len(driver.page_source)}文字")

table = soup.find("table", class_="race_table_01 nk_tb_common")
print(f"4. 結果テーブル: {'見つかった' if table else '見つからない'}")

if table:
    body = table.find("tbody")
    if body:
        rows = body.find_all("tr")
        print(f"5. 行数: {len(rows)}")
        if rows:
            for i, row in enumerate(rows[:3], 1):
                cols = row.find_all("td")
                if len(cols) >= 4:
                    horse_link = cols[3].find("a")
                    horse_name = horse_link.text.strip() if horse_link else cols[3].text.strip()
                    print(f"   {i}着: {horse_name}")
    else:
        print("5. tbody要素なし")
else:
    print("5. 代替確認: ページ内のtable要素")
    tables = soup.find_all("table")
    print(f"   table総数: {len(tables)}")
    for i, t in enumerate(tables[:3], 1):
        print(f"   {i}. class={t.get('class', [])}")

driver.quit()
print("完了")
