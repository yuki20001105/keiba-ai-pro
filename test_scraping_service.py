import requests
import json

# スクレイピングサービスをテスト - 過去の完了したレース
race_id = '202406051211'  # 2024年 中山11R（完了済みレース）

print(f'Testing scraping service with race_id: {race_id}')
print('='*80)

url = 'http://localhost:8001/scrape/race'
data = {'race_id': race_id}

try:
    response = requests.post(url, json=data, timeout=120)
    print(f'Status: {response.status_code}')
    
    if response.status_code == 200:
        result = response.json()
        print(f'\nSuccess: {result.get("success")}')
        
        if result.get('success'):
            print(f'\n{"="*80}')
            print('SUCCESS!')
            print(f'{"="*80}')
            print(f'Race Name: {result.get("race_name")}')
            print(f'Race Data: {result.get("race_data")[:100]}...')
            print(f'Distance: {result.get("distance")}m')
            print(f'Track Type: {result.get("track_type")}')
            print(f'Weather: {result.get("weather")}')
            print(f'Field Condition: {result.get("field_condition")}')
            print(f'Results: {len(result.get("results", []))} horses')
            print(f'Payouts: {len(result.get("payouts", []))} items')
        else:
            print(f'Error: {result.get("error")}')
    else:
        print(f'HTTP Error: {response.text}')
        
except Exception as e:
    print(f'Connection Error: {e}')
    print('\nスクレイピングサービスが起動していない可能性があります。')
    print('別ウィンドウで以下のコマンドを実行してください：')
    print('C:\\Users\\yuki2\\.pyenv\\pyenv-win\\versions\\3.10.11\\python.exe scraping_service.py')
