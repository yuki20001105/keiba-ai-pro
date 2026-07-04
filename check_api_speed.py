import requests, time
for d in ['20180317', '20180310', '20260613']:
    t = time.time()
    r = requests.get(f'http://localhost:8000/api/races/by_date?date={d}', timeout=10)
    ms = (time.time()-t)*1000
    cnt = r.json().get('count', '?')
    print(f'{d}: HTTP {r.status_code} count={cnt} ({ms:.0f}ms)')
