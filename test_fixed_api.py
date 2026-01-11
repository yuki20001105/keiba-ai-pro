"""
修正後のAPIをテスト（12桁race_idとh1.RaceName）
"""
import requests

base_url = "http://localhost:3000"
test_user_id = "00000000-0000-0000-0000-000000000000"

# ユーザー提供のrace_id（2026/06/01 新潟 1R）
race_id = "202606010401"

print("=" * 80)
print("修正後のAPIテスト（12桁race_id + h1.RaceName）")
print("=" * 80)

# テスト1: testOnlyモードで存在確認
print(f"\nTest 1: testOnly mode for race_id={race_id}")
print("-" * 80)
response = requests.post(
    f"{base_url}/api/netkeiba/race",
    json={"raceId": race_id, "userId": test_user_id, "testOnly": True},
    timeout=10
)

print(f"Status: {response.status_code}")
data = response.json()
print(f"Response: {data}")

if data.get("success"):
    print("SUCCESS: レースが存在します！")
    
    # テスト2: 実際にスクレイピング
    print(f"\nTest 2: 実際のスクレイピング for race_id={race_id}")
    print("-" * 80)
    response = requests.post(
        f"{base_url}/api/netkeiba/race",
        json={"raceId": race_id, "userId": test_user_id, "testOnly": False},
        timeout=30
    )
    
    print(f"Status: {response.status_code}")
    data = response.json()
    
    if data.get("success"):
        print(f"SUCCESS!")
        print(f"  Race Name: {data.get('raceName')}")
        print(f"  Results Count: {data.get('resultsCount')}")
    else:
        print(f"FAILED: {data.get('error')}")
else:
    print(f"FAILED: {data.get('error')}")
    print("\n別のrace_idで試してみましょう...")
    
    # 過去のレースで試す
    test_cases = [
        "202312230601",  # 2023/12/23 中山 1R
        "202301070601",  # 2023/01/07 中山 1R  
    ]
    
    for test_id in test_cases:
        print(f"\nTrying race_id={test_id}")
        response = requests.post(
            f"{base_url}/api/netkeiba/race",
            json={"raceId": test_id, "userId": test_user_id, "testOnly": True},
            timeout=10
        )
        data = response.json()
        print(f"  Result: {data}")
        if data.get("success"):
            print(f"  >>> FOUND!")
            break

print("\n" + "=" * 80)
