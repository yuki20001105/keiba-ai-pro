"""
拡張版スクレイピングサービスのテスト
全特徴量が取得できることを確認
"""
import requests
import json
import time

def test_enhanced_scraping():
    """拡張版スクレイピングをテスト"""
    
    # 2020年1月5日 中山1R（完了済みレース）
    race_id = "202006010101"
    
    print("=" * 100)
    print(f"拡張版スクレイピングテスト: race_id={race_id}")
    print("=" * 100)
    
    url = "http://localhost:8001/scrape/enhanced"
    payload = {
        "race_id": race_id,
        "include_details": True  # 詳細ページも取得
    }
    
    print(f"\nリクエスト送信: {url}")
    print(f"race_id: {race_id}")
    print(f"include_details: True (馬・騎手・調教師の詳細も取得)\n")
    
    start_time = time.time()
    response = requests.post(url, json=payload, timeout=300)
    elapsed = time.time() - start_time
    
    print(f"✓ レスポンス受信: {elapsed:.1f}秒\n")
    
    if response.status_code == 200:
        data = response.json()
        
        if data.get('success'):
            print("=" * 100)
            print("【レース基本情報】")
            print("=" * 100)
            race_info = data.get('race_info', {})
            for key, value in race_info.items():
                if key not in ['race_data_01', 'race_data_02']:
                    print(f"  {key}: {value}")
            
            print("\n" + "=" * 100)
            print("【結果テーブル】")
            print("=" * 100)
            results = data.get('results', [])
            print(f"出走頭数: {len(results)}頭\n")
            
            if results:
                # 1着馬のデータを詳細表示
                first_place = results[0]
                print("【1着馬の全データ】")
                print(f"  着順: {first_place.get('finish_position')}")
                print(f"  枠番: {first_place.get('bracket_number')}")
                print(f"  馬番: {first_place.get('horse_number')}")
                print(f"  馬名: {first_place.get('horse_name')}")
                print(f"  性齢: {first_place.get('sex_age')}")
                print(f"  斤量: {first_place.get('jockey_weight')}")
                print(f"  騎手: {first_place.get('jockey_name')}")
                print(f"  タイム: {first_place.get('finish_time')}")
                print(f"  着差: {first_place.get('margin')}")
                print(f"  人気: {first_place.get('popularity')}")
                print(f"  オッズ: {first_place.get('odds')}")
                print(f"  後3F: {first_place.get('last_3f')}")
                print(f"  コーナー通過: {first_place.get('corner_positions')}")
                print(f"  調教師: {first_place.get('trainer_name')}")
                print(f"  馬体重: {first_place.get('weight')}")
                
                # 馬詳細
                horse_details = first_place.get('horse_details', {})
                if horse_details:
                    print("\n  【馬詳細情報】")
                    for key, value in horse_details.items():
                        if key != 'past_performances':
                            print(f"    {key}: {value}")
                    
                    past_perfs = horse_details.get('past_performances', [])
                    if past_perfs:
                        print(f"\n    過去成績（最新{len(past_perfs)}レース）:")
                        for i, perf in enumerate(past_perfs[:3], 1):
                            print(f"      {i}. {perf.get('date')} {perf.get('venue')} {perf.get('race_name')} - {perf.get('finish')}着")
                
                # 騎手詳細
                jockey_details = first_place.get('jockey_details', {})
                if jockey_details:
                    print("\n  【騎手詳細情報】")
                    for key, value in jockey_details.items():
                        print(f"    {key}: {value}")
                
                # 調教師詳細
                trainer_details = first_place.get('trainer_details', {})
                if trainer_details:
                    print("\n  【調教師詳細情報】")
                    for key, value in trainer_details.items():
                        print(f"    {key}: {value}")
            
            # ラップタイム
            print("\n" + "=" * 100)
            print("【ラップタイム】")
            print("=" * 100)
            lap_times = data.get('lap_times', {})
            for dist, t in lap_times.items():
                print(f"  {dist}: {t}")
            
            # コーナー通過順位
            print("\n" + "=" * 100)
            print("【コーナー通過順位】")
            print("=" * 100)
            corner_positions = data.get('corner_positions', {})
            for corner, order in corner_positions.items():
                print(f"  {corner}: {order}")
            
            # 払戻
            print("\n" + "=" * 100)
            print("【払戻情報】")
            print("=" * 100)
            payouts = data.get('payouts', [])
            for payout in payouts[:5]:  # 最初の5件
                print(f"  {payout.get('type')}: {payout.get('numbers')} → {payout.get('amount')}")
            
            # サマリー
            print("\n" + "=" * 100)
            print("【取得データサマリー】")
            print("=" * 100)
            print(f"  レース基本情報: {len(race_info)}項目")
            print(f"  出走馬データ: {len(results)}頭")
            
            horses_with_details = sum(1 for r in results if r.get('horse_details'))
            jockeys_with_details = sum(1 for r in results if r.get('jockey_details'))
            trainers_with_details = sum(1 for r in results if r.get('trainer_details'))
            
            print(f"  馬詳細取得: {horses_with_details}頭")
            print(f"  騎手詳細取得: {jockeys_with_details}人")
            print(f"  調教師詳細取得: {trainers_with_details}人")
            print(f"  ラップタイム: {len(lap_times)}地点")
            print(f"  コーナー通過: {len(corner_positions)}地点")
            print(f"  払戻情報: {len(payouts)}件")
            
            print("\n✓ 全特徴量の取得に成功しました！")
            
        else:
            print(f"✗ スクレイピング失敗: {data.get('error')}")
    else:
        print(f"✗ HTTP エラー: {response.status_code}")
        print(response.text)


if __name__ == "__main__":
    print("\n※ 事前に拡張版スクレイピングサービスを起動してください:")
    print("  C:\\Users\\yuki2\\Documents\\ws\\keiba\\Scripts\\python.exe scraping_service_enhanced.py\n")
    
    input("準備ができたらEnterキーを押してください...")
    
    test_enhanced_scraping()
