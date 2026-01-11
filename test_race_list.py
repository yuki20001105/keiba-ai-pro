"""
race-list APIの動作検証スクリプト
実際の開催日でレースIDが取得できるかテスト
"""
import requests
import json
from datetime import datetime, timedelta

def test_race_list_api():
    """race-list APIをテスト"""
    base_url = "http://localhost:3000"
    
    # テスト対象の日付（最近の開催日をいくつかテスト）
    test_dates = [
        "2024-01-06",  # 土曜日
        "2024-01-07",  # 日曜日
        "2024-01-08",  # 月曜日（開催なし想定）
        "2023-12-23",  # 土曜日
        "2023-12-24",  # 日曜日
    ]
    
    print("=" * 60)
    print("race-list API 動作検証")
    print("=" * 60)
    
    total_races = 0
    
    for date in test_dates:
        print(f"\n📅 {date} のテスト:")
        print("-" * 60)
        
        try:
            # race-list APIを呼び出し
            response = requests.post(
                f"{base_url}/api/netkeiba/race-list",
                headers={"Content-Type": "application/json"},
                json={"date": date},
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                race_ids = data.get("raceIds", [])
                count = data.get("count", 0)
                
                if count > 0:
                    print(f"✅ 成功: {count}レースのIDを取得")
                    total_races += count
                    
                    # 最初の3レースIDを表示
                    print(f"   レースID例:")
                    for i, race_id in enumerate(race_ids[:3]):
                        print(f"     {i+1}. {race_id}")
                    
                    if len(race_ids) > 3:
                        print(f"     ... 他 {len(race_ids) - 3}レース")
                else:
                    print(f"⚠️  開催なし: レースIDが0件")
            else:
                print(f"❌ エラー: HTTP {response.status_code}")
                print(f"   レスポンス: {response.text[:200]}")
                
        except requests.exceptions.ConnectionError:
            print(f"❌ 接続エラー: Next.jsサーバーが起動していません")
            print(f"   'npm run dev' を実行してください")
            return False
        except Exception as e:
            print(f"❌ エラー: {e}")
    
    print("\n" + "=" * 60)
    print(f"合計: {total_races}レースのIDを取得")
    print("=" * 60)
    
    return total_races > 0

def test_single_race_scrape():
    """単一レースのスクレイピングテスト"""
    base_url = "http://localhost:3000"
    
    print("\n" + "=" * 60)
    print("単一レース スクレイピングテスト")
    print("=" * 60)
    
    # 実際に存在するレースID（2024年1月6日 中山1R）
    test_race_id = "2024010606010101"
    
    print(f"\n🏇 レースID: {test_race_id}")
    print("-" * 60)
    
    try:
        response = requests.post(
            f"{base_url}/api/netkeiba/race",
            headers={"Content-Type": "application/json"},
            json={"raceId": test_race_id, "userId": "test"},
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get("success"):
                print(f"✅ スクレイピング成功")
                print(f"   レース名: {data.get('raceName', 'N/A')}")
                print(f"   出走馬数: {data.get('resultsCount', 0)}頭")
                return True
            else:
                print(f"❌ スクレイピング失敗: {data.get('error', 'Unknown')}")
        else:
            print(f"❌ HTTP エラー: {response.status_code}")
            print(f"   レスポンス: {response.text[:200]}")
            
    except Exception as e:
        print(f"❌ エラー: {e}")
    
    return False

if __name__ == "__main__":
    print("\n🚀 テスト開始\n")
    
    # 1. race-list APIのテスト
    race_list_ok = test_race_list_api()
    
    # 2. 単一レーススクレイピングのテスト
    race_scrape_ok = test_single_race_scrape()
    
    print("\n" + "=" * 60)
    print("テスト結果サマリー")
    print("=" * 60)
    print(f"race-list API: {'✅ OK' if race_list_ok else '❌ NG'}")
    print(f"レーススクレイピング: {'✅ OK' if race_scrape_ok else '❌ NG'}")
    print("=" * 60)
    
    if race_list_ok and race_scrape_ok:
        print("\n✅ すべてのテストが成功しました")
        print("   UIから一括取得を実行できます")
    else:
        print("\n❌ 一部のテストが失敗しました")
        print("   エラー内容を確認してください")
