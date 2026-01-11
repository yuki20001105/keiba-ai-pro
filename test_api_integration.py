import requests
import json
import time

# APIルートのテスト (スクレイピングサービス経由)
test_race_id = "202606010411"  # フェアリーS
test_user_id = "00000000-0000-0000-0000-000000000000"

print(f"Testing API route integration with race_id: {test_race_id}")
print("=" * 60)

# まず、スクレイピングサービスが起動しているか確認
try:
    scrape_response = requests.post(
        "http://localhost:8001/scrape/race",
        json={"race_id": test_race_id},
        timeout=30
    )
    print(f"\n✓ Scraping service is running")
    print(f"  Status: {scrape_response.status_code}")
    scrape_data = scrape_response.json()
    print(f"  Race name: {scrape_data.get('race_name', 'N/A')}")
except Exception as e:
    print(f"\n✗ Scraping service error: {e}")
    print("Please make sure scraping_service.py is running on port 8001")
    exit(1)

print("\n" + "=" * 60)
print("Testing Next.js API route...")
print("=" * 60)

# Next.js API route (http://localhost:3000/api/netkeiba/race) のテスト
try:
    # testOnlyモードでテスト
    api_url = "http://localhost:3000/api/netkeiba/race"
    
    print("\n1. Testing testOnly mode...")
    test_response = requests.post(
        api_url,
        json={
            "raceId": test_race_id,
            "userId": test_user_id,
            "testOnly": True
        },
        timeout=30
    )
    
    print(f"  Status: {test_response.status_code}")
    test_result = test_response.json()
    print(f"  Response: {json.dumps(test_result, ensure_ascii=False, indent=2)}")
    
    if test_result.get("success"):
        print("  ✓ Race exists!")
    else:
        print("  ✗ Race not found")
        exit(1)
    
    # フルモードでテスト
    print("\n2. Testing full data collection...")
    full_response = requests.post(
        api_url,
        json={
            "raceId": test_race_id,
            "userId": test_user_id,
            "testOnly": False
        },
        timeout=60
    )
    
    print(f"  Status: {full_response.status_code}")
    full_result = full_response.json()
    print(f"  Response: {json.dumps(full_result, ensure_ascii=False, indent=2)}")
    
    if full_result.get("success"):
        print("\n✓ Full integration test passed!")
        print(f"  Results collected: {full_result.get('resultsCount', 0)}")
        print(f"  Payouts collected: {full_result.get('payoutsCount', 0)}")
    else:
        print("\n✗ Full integration test failed")
        
except requests.exceptions.ConnectionError:
    print("\n✗ Could not connect to Next.js server")
    print("  Please make sure Next.js dev server is running on port 3000")
    print("  Run: npm run dev")
except Exception as e:
    print(f"\n✗ API error: {e}")
    import traceback
    traceback.print_exc()
