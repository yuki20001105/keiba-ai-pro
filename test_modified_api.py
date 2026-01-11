"""
修正後のAPIをテスト（testOnlyモード付き）
"""
import requests

def test_modified_api():
    base_url = "http://localhost:3000"
    
    # 有効なUUID（ダミー）
    test_user_id = "00000000-0000-0000-0000-000000000000"
    
    # テスト1: testOnlyモードで1Rの存在確認
    print("=" * 60)
    print("Test 1: testOnly mode (race exists)")
    print("=" * 60)
    
    race_id_exists = "2024010606010101"  # 2024/1/6 中山1回1日1R
    response = requests.post(
        f"{base_url}/api/netkeiba/race",
        json={"raceId": race_id_exists, "userId": test_user_id, "testOnly": True},
        timeout=10
    )
    
    print(f"Status: {response.status_code}")
    print(f"Response: {response.json()}")
    
    # テスト2: testOnlyモードで存在しないレース
    print("\n" + "=" * 60)
    print("Test 2: testOnly mode (race not exists)")
    print("=" * 60)
    
    race_id_not_exists = "2024010699999999"
    response = requests.post(
        f"{base_url}/api/netkeiba/race",
        json={"raceId": race_id_not_exists, "userId": test_user_id, "testOnly": True},
        timeout=10
    )
    
    print(f"Status: {response.status_code}")
    print(f"Response: {response.json()}")
    
    # テスト3: 通常モードでスクレイピング
    print("\n" + "=" * 60)
    print("Test 3: normal scraping mode")
    print("=" * 60)
    
    response = requests.post(
        f"{base_url}/api/netkeiba/race",
        json={"raceId": race_id_exists, "userId": test_user_id, "testOnly": False},
        timeout=30
    )
    
    print(f"Status: {response.status_code}")
    data = response.json()
    if data.get("success"):
        print(f"Success: {data.get('raceName')}, {data.get('resultsCount')} horses")
    else:
        print(f"Error: {data.get('error')}")

if __name__ == "__main__":
    test_modified_api()
