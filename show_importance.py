import requests, json
r = requests.get('http://localhost:8000/api/features/importance?top_n=40', timeout=30)
d = r.json()
feats = d.get('features', [])
print('Model:', d.get('model_id', 'unknown'))
print()
print(f"{'Rank':>4}  {'Feature':<45} {'Gain%':>7}  {'Split%':>7}")
print('-'*70)
for i, f in enumerate(feats[:40], 1):
    nm = f.get('name', f.get('feature', ''))
    gp = f.get('gain_pct', f.get('gain', 0))
    sp = f.get('split_pct', f.get('split', 0))
    print(f"{i:>4}. {nm:<45} {float(gp):>6.2f}%  {float(sp):>6.2f}%")
