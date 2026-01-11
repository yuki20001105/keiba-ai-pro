from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import time

# 以前test_scraping_service.pyで成功したrace_id
race_id = "202606010411"  
url = f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"

print(f"Testing direct Selenium access to: {url}")
print("=" * 80)

# Chromeオプション設定
chrome_options = Options()
# chrome_options.add_argument('--headless')  # ヘッドレス無効化で確認
chrome_options.add_argument('--no-sandbox')
chrome_options.add_argument('--disable-dev-shm-usage')
chrome_options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')

# WebDriver初期化
service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service, options=chrome_options)

try:
    print(f"\n1. Loading page...")
    driver.get(url)
    
    # 要素が読み込まれるのを待つ
    print(f"2. Waiting for h1.RaceName element...")
    wait = WebDriverWait(driver, 10)
    race_name_element = wait.until(
        EC.presence_of_element_located((By.CSS_SELECTOR, 'h1.RaceName'))
    )
    
    print(f"3. Element found! Waiting 2 seconds for JS to complete...")
    time.sleep(2)
    
    # ページソースを取得
    html = driver.page_source
    
    # h1.RaceNameを取得
    race_name = driver.find_element(By.CSS_SELECTOR, 'h1.RaceName').text.strip()
    print(f"\n✓ Race Name: {race_name}")
    
    # RaceData01を取得
    try:
        race_data_elements = driver.find_elements(By.CSS_SELECTOR, '.RaceData01')
        if race_data_elements:
            race_data = race_data_elements[0].text.strip()
            print(f"✓ Race Data: {race_data}")
        else:
            print("✗ RaceData01 not found")
    except Exception as e:
        print(f"✗ Error getting RaceData01: {e}")
    
    # Result_Tableを確認
    try:
        result_tables = driver.find_elements(By.CSS_SELECTOR, 'table.Result_Table')
        print(f"✓ Result_Table count: {len(result_tables)}")
        if result_tables:
            rows = result_tables[0].find_elements(By.TAG_NAME, 'tr')
            print(f"  Rows in first table: {len(rows)}")
    except Exception as e:
        print(f"✗ Error checking Result_Table: {e}")
    
    print(f"\n✓ Success! Page loaded and elements found.")
    
finally:
    driver.quit()
    print("\n✓ Browser closed")
