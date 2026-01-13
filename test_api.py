#!/usr/bin/env python3
"""
既存のスクレイピングAPIをテスト
"""
import requests

# ローカルのスクレイピングサービスをテスト
api_url = "http://localhost:8001/scrape/race_list"

test_date = "20240106"

print(f"テスト日付: {test_date}")
print(f"API URL: {api_url}\n")

try:
    response = requests.post(
        api_url,
        json={"kaisai_date": test_date},
        timeout=30
    )
    
    print(f"ステータス: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        
        if data.get('success'):
            race_ids = data.get('race_ids', [])
            print(f"✅ 成功: {len(race_ids)} レース発見")
            for rid in race_ids[:10]:
                print(f"  - {rid}")
        else:
            print(f"❌ 失敗: {data.get('error')}")
    else:
        print(f"❌ HTTP エラー: {response.status_code}")
        print(response.text[:500])

except requests.exceptions.ConnectionError:
    print("❌ エラー: スクレイピングサービスに接続できません")
    print("💡 解決策: 'npm run dev:all' を実行してサービスを起動してください")
except Exception as e:
    print(f"❌ エラー: {e}")
