import requests

# トップページから取得したrace_id
race_id = '202606010411'

print(f'Testing race_id: {race_id}')
print(f'(2026年1月11日 中山11R フェアリーS(G3))')
print('='*80)

api_url = 'http://localhost:3000/api/netkeiba/race'
test_user_id = '00000000-0000-0000-0000-000000000000'

# 1. testOnly mode
print('\n1. testOnly mode')
try:
    response = requests.post(
        api_url,
        json={'raceId': race_id, 'userId': test_user_id, 'testOnly': True},
        timeout=30
    )
    print(f'Status: {response.status_code}')
    result = response.json()
    print(f'Response: {result}')
    
    if result.get('success') or result.get('exists'):
        # 2. Full scrape
        print('\n2. Full scrape')
        response = requests.post(
            api_url,
            json={'raceId': race_id, 'userId': test_user_id},
            timeout=30
        )
        print(f'Status: {response.status_code}')
        
        if response.status_code == 200:
            data = response.json()
            print(f'\n{"="*80}')
            print('SUCCESS!')
            print(f'{"="*80}')
            print(f'Race Name: {data.get("raceName")}')
            print(f'Venue: {data.get("venue")}')
            print(f'Distance: {data.get("distance")}m')
            print(f'Track Type: {data.get("trackType")}')
            print(f'Weather: {data.get("weather")}')
            print(f'Field Condition: {data.get("fieldCondition")}')
            print(f'Results: {len(data.get("results", []))} horses')
            print(f'Payouts: {len(data.get("payouts", []))} items')
            
            # 最初の3頭の結果を表示
            if data.get('results'):
                print(f'\nTop 3 horses:')
                for i, horse in enumerate(data.get('results', [])[:3], 1):
                    print(f'  {i}. {horse.get("horse_name")} - {horse.get("finish_time")} - オッズ{horse.get("odds")}')
        else:
            print(f'Error: {response.text[:200]}')
    else:
        print('\n✗ Race does not exist or cannot be accessed')
        
except Exception as e:
    print(f'\n✗ Error: {e}')
