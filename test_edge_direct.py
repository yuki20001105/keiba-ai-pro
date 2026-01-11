"""
Edge WebDriverを直接テストする
"""
from selenium import webdriver
from selenium.webdriver.edge.service import Service
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.microsoft import EdgeChromiumDriverManager
import time

race_id = "202606010411"
url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'

print(f"Testing Edge WebDriver with race_id: {race_id}")
print("=" * 70)

try:
    # Edgeオプション（ヘッドレス無効でテスト）
    edge_options = Options()
    # edge_options.add_argument('--headless')  # 一旦コメントアウト
    edge_options.add_argument('--no-sandbox')
    edge_options.add_argument('--disable-dev-shm-usage')
    edge_options.add_argument('--disable-blink-features=AutomationControlled')
    edge_options.add_experimental_option('excludeSwitches', ['enable-automation'])
    edge_options.add_experimental_option('useAutomationExtension', False)
    
    print("Initializing Edge WebDriver...")
    service = Service(EdgeChromiumDriverManager().install())
    driver = webdriver.Edge(service=service, options=edge_options)
    
    # webdriver検出を回避
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    print(f"Opening URL: {url}")
    driver.get(url)
    
    print("Waiting for h1.RaceName element...")
    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, 'h1.RaceName'))
    )
    
    print("Additional wait for JavaScript...")
    time.sleep(3)
    
    # レース名を取得
    race_name = driver.find_element(By.CSS_SELECTOR, 'h1.RaceName').text
    print(f"\n✓ Success!")
    print(f"  Race Name: {race_name}")
    
    # ページタイトルも確認
    print(f"  Page Title: {driver.title}")
    
    driver.quit()
    print("\n✓ Edge WebDriver test passed!")
    
except Exception as e:
    print(f"\n✗ Error: {e}")
    import traceback
    traceback.print_exc()
    try:
        driver.quit()
    except:
        pass
