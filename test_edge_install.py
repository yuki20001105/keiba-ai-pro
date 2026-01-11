"""
EdgeChromiumDriverManagerのテスト
"""
from webdriver_manager.microsoft import EdgeChromiumDriverManager
from selenium import webdriver
from selenium.webdriver.edge.service import Service
from selenium.webdriver.edge.options import Options

print("Testing EdgeChromiumDriverManager...")
print("=" * 70)

try:
    print("1. Installing Edge WebDriver...")
    driver_path = EdgeChromiumDriverManager().install()
    print(f"   ✓ Driver installed at: {driver_path}")
    
    print("\n2. Initializing Edge browser...")
    edge_options = Options()
    # edge_options.add_argument('--headless')
    service = Service(driver_path)
    driver = webdriver.Edge(service=service, options=edge_options)
    print(f"   ✓ Browser started")
    
    print("\n3. Opening test page...")
    driver.get("https://www.google.com")
    print(f"   ✓ Page title: {driver.title}")
    
    driver.quit()
    print("\n✓ All tests passed! Edge WebDriver is working correctly.")
    
except Exception as e:
    print(f"\n✗ Error: {e}")
    import traceback
    traceback.print_exc()
