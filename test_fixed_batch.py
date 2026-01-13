"""修正版バッチスクレイピングのテスト"""
import requests
import json
import time

print('[テスト] 2レースのバッチスクレイピング（修正版）')
print('=' * 60)

start_time = time.time()

response = requests.post(
    'http://localhost:8001/scrape/ultimate/batch',
    json={
        'race_ids': ['202405021201', '202405021202'],
        'include_details': False
    },
    timeout=120
)

elapsed = time.time() - start_time

if response.status_code == 200:
    data = response.json()
    print(f'\n[OK] 取得成功！ 所要時間: {elapsed:.2f}秒\n')
    
    results = data.get('results', {})
    print(f'取得レース数: {len(results)}')
    
    for race_id, race_data in results.items():
        race_info = race_data.get('race_info', {})
        horses = race_data.get('results', [])
        
        print(f'\n[レース {race_id}]')
        print(f'  レース名: {race_info.get("race_name", "N/A")}')
        print(f'  出走頭数: {len(horses)}頭')
        
        if horses:
            print(f'\n  最初の3頭:')
            for i, h in enumerate(horses[:3], 1):
                print(f'    {i}. {h.get("horse_name", "N/A")} (ID:{h.get("horse_id", "N/A")})')
                print(f'       騎手: {h.get("jockey_name", "N/A")} (ID:{h.get("jockey_id", "N/A")})')
                print(f'       調教師: {h.get("trainer_name", "N/A")} (ID:{h.get("trainer_id", "N/A")})')
                print(f'       馬体重: {h.get("weight_kg", "N/A")}kg')
                print(f'       特徴量数: {len(h)}列')
        
    stats = data.get('stats', {})
    print(f'\n[統計]')
    print(f'  成功: {stats.get("success", 0)}/{stats.get("total", 0)}')
    print(f'  所要時間: {stats.get("elapsed_seconds", 0):.2f}秒')
    print(f'  高速化率: {stats.get("speedup_vs_sequential", 0):.2f}倍')
    
else:
    print(f'[ERROR] Status: {response.status_code}')
    print(response.text[:500])
