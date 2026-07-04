import requests, json
for d in ['20260613','20260614','20260615']:
    resp = requests.get(f'http://localhost:8000/api/races/by_date?date={d}', timeout=15)
    r = resp.json()
    ids = [x.get('race_id') for x in r.get('races', [])[:3]]
    print(f'{d}: count={r.get("count")}, sample_ids={ids}')
