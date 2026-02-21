import sqlite3, json
from pathlib import Path

# validation/ から実行しても root から実行しても動作するよう __file__ ベースで解決
_DB = Path(__file__).parent.parent / 'keiba' / 'data' / 'keiba_ultimate.db'
conn = sqlite3.connect(str(_DB))
cur = conn.cursor()

# race_results_ultimate JSON の欠損統計
cur.execute("SELECT data FROM race_results_ultimate")
all_rows = cur.fetchall()
total_r = len(all_rows)

print(f'=== race_results_ultimate JSONキー充填率 ({total_r}行) ===')

check_keys = [
    'horse_id', 'horse_name',
    'sire', 'dam', 'damsire',
    'horse_total_runs', 'horse_total_wins', 'horse_total_prize_money',
    'horse_birth_date', 'horse_owner', 'horse_breeder',
    'prev_race_date', 'prev_race_finish', 'prev_race_distance',
    'prev_race_weight', 'prev_race_time',
    'prev2_race_date', 'prev2_race_finish',
    'last_3f', 'last_3f_rank',
    'weight_kg', 'weight_change',
    'corner_positions',
]

counters = {k: 0 for k in check_keys}
all_json_keys = set()

for (data,) in all_rows:
    d = json.loads(data)
    all_json_keys.update(d.keys())
    for k in check_keys:
        v = d.get(k)
        if v is not None and str(v).strip() not in ('', 'None'):
            counters[k] += 1

for k in check_keys:
    pct = counters[k] / total_r * 100 if total_r else 0
    bar = '■' * int(pct // 5) + '□' * (20 - int(pct // 5))
    status = '✅' if pct >= 80 else ('⚠️' if pct >= 30 else '❌')
    print(f'  {status} {k:<35} {bar} {counters[k]:>5}/{total_r} ({pct:5.1f}%)')

print(f'\n全JSONキー一覧 ({len(all_json_keys)}個):')
for k in sorted(all_json_keys):
    print(f'  {k}')

# races_ultimate JSON の欠損統計
cur.execute("SELECT data FROM races_ultimate")
race_rows = cur.fetchall()
total_races = len(race_rows)
race_keys = ['race_name', 'kai', 'day', 'distance', 'track_type',
             'weather', 'field_condition', 'course_direction', 'post_time', 'race_class']
race_counters = {k: 0 for k in race_keys}
for (data,) in race_rows:
    d = json.loads(data)
    for k in race_keys:
        v = d.get(k)
        if v is not None and str(v).strip() not in ('', 'None', '0', 'none'):
            race_counters[k] += 1

print(f'\n=== races_ultimate JSONキー充填率 ({total_races}行) ===')
for k in race_keys:
    pct = race_counters[k] / total_races * 100 if total_races else 0
    bar = '■' * int(pct // 5) + '□' * (20 - int(pct // 5))
    status = '✅' if pct >= 80 else ('⚠️' if pct >= 30 else '❌')
    print(f'  {status} {k:<30} {bar} {race_counters[k]:>5}/{total_races} ({pct:5.1f}%)')

# races_ultimate JSONの全キー
cur.execute("SELECT data FROM races_ultimate LIMIT 1")
sample = cur.fetchone()
if sample:
    d = json.loads(sample[0])
    print(f'\nraces_ultimate JSONキー一覧: {sorted(d.keys())}')

# サンプルデータ
print('\n=== 最新3レース races_ultimate サンプル ===')
cur.execute("SELECT data FROM races_ultimate ORDER BY race_id DESC LIMIT 3")
for (data,) in cur.fetchall():
    d = json.loads(data)
    print(f'  race_id={d.get("race_id")} dist={d.get("distance")} track={d.get("track_type")} '
          f'weather={d.get("weather")} kai={d.get("kai")} day={d.get("day")} dir={d.get("course_direction")}')

print('\n=== 最新3馬 race_results_ultimate サンプル ===')
cur.execute("SELECT data FROM race_results_ultimate ORDER BY rowid DESC LIMIT 3")
for (data,) in cur.fetchall():
    d = json.loads(data)
    print(f'  horse={d.get("horse_name")} id={d.get("horse_id")} '
          f'sire={d.get("sire")!r} runs={d.get("horse_total_runs")} prev={d.get("prev_race_date")}')

conn.close()
