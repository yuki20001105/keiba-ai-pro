"""
新しい/race_listエンドポイントのテスト
"""
import requests

def test_race_list_api():
    """race_listエンドポイントのテスト"""
    print("=" * 80)
    print("race_list APIエンドポイントのテスト")
    print("=" * 80)
    
    # テストケース1: 2020年1月6日
    print("\n[テスト1] 2020年1月6日")
    print("-" * 80)
    
    response1 = requests.post(
        'http://localhost:8001/race_list',
        json={'kaisai_date': '20200106'},
        timeout=30
    )
    
    print(f"Status: {response1.status_code}")
    
    if response1.status_code == 200:
        data1 = response1.json()
        
        if data1['success']:
            race_ids = data1['race_ids']
            print(f"✓ 成功: {len(race_ids)}レース取得")
            
            # 最初の5件を表示
            for i, race_id in enumerate(race_ids[:5]):
                print(f"  {i+1}. {race_id}")
            
            if len(race_ids) > 5:
                print(f"  ... 他 {len(race_ids)-5}レース")
        else:
            print(f"✗ 失敗: {data1.get('error', 'Unknown')}")
    else:
        print(f"✗ HTTPエラー: {response1.status_code}")
    
    # テストケース2: 2020年1月13日
    print("\n[テスト2] 2020年1月13日")
    print("-" * 80)
    
    response2 = requests.post(
        'http://localhost:8001/race_list',
        json={'kaisai_date': '20200113'},
        timeout=30
    )
    
    print(f"Status: {response2.status_code}")
    
    if response2.status_code == 200:
        data2 = response2.json()
        
        if data2['success']:
            race_ids = data2['race_ids']
            print(f"✓ 成功: {len(race_ids)}レース取得")
            
            for i, race_id in enumerate(race_ids[:5]):
                print(f"  {i+1}. {race_id}")
            
            if len(race_ids) > 5:
                print(f"  ... 他 {len(race_ids)-5}レース")
        else:
            print(f"✗ 失敗: {data2.get('error', 'Unknown')}")
    else:
        print(f"✗ HTTPエラー: {response2.status_code}")
    
    print("\n" + "=" * 80)
    print("テスト完了")
    print("=" * 80)

if __name__ == "__main__":
    # まずサービスが起動しているか確認
    try:
        health = requests.get('http://localhost:8001/health', timeout=5)
        if health.status_code == 200:
            print("✓ スクレイピングサービス稼働中\n")
            test_race_list_api()
        else:
            print("✗ スクレイピングサービスが正常に応答しません")
    except:
        print("✗ スクレイピングサービスが起動していません")
        print("起動コマンド: C:\\Users\\yuki2\\Documents\\ws\\keiba\\Scripts\\python.exe scraping_service_undetected.py")
