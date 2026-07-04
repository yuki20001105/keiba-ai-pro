"""日本ダービーデータ品質チェック"""
import sqlite3, json

conn = sqlite3.connect("keiba/data/keiba_ultimate.db")

# Find Japanese Derby 2026
races = conn.execute(
    "SELECT race_id, data FROM races_ultimate WHERE race_id LIKE '202601%'"
).fetchall()

derby = None
for (race_id, data) in races:
    d = json.loads(data)
    if "ダービー" in d.get("race_name", "") and d.get("date", "").startswith("20260531"):
        derby = (race_id, d)
        break

if not derby:
    # Try by race class
    rows = conn.execute(
        "SELECT race_id, data FROM races_ultimate ORDER BY race_id DESC LIMIT 200"
    ).fetchall()
    for (race_id, data) in rows:
        d = json.loads(data)
        if "ダービー" in d.get("race_name", ""):
            derby = (race_id, d)
            print(f"Found: {race_id} {d['race_name']} date={d.get('date')}")
            break

if derby:
    race_id, race_data = derby
    print(f"\n=== {race_data['race_name']} ({race_id}) ===")
    print(f"  日付: {race_data.get('date')}  競馬場: {race_data.get('venue')}")
    print(f"  コース: {race_data.get('surface')} {race_data.get('distance')}m")
    print(f"  天候: {race_data.get('weather')}  馬場: {race_data.get('field_condition')}")
    print(f"  頭数: {race_data.get('num_horses')}")

    # Get horse entries for this race
    entries = conn.execute(
        "SELECT data FROM race_results_ultimate WHERE race_id = ?", (race_id,)
    ).fetchall()
    print(f"\n出走馬: {len(entries)}頭")
    print(f"\n{'馬名':<20} {'sire':<20} {'birth_date':<15} {'odds':>6} {'prev':<12} {'total_runs'}")
    print("-" * 95)
    for (d,) in entries[:18]:
        h = json.loads(d)
        name = h.get("horse_name", "?")[:18]
        sire = (h.get("sire") or "")[:18]
        bday = (h.get("horse_birth_date") or "")[:12]
        odds = h.get("odds") or ""
        prev = (h.get("prev_race_date") or "")[:10]
        runs = h.get("horse_total_runs", "-")
        print(f"{name:<20} {sire:<20} {bday:<15} {str(odds):>6} {prev:<12} {runs}")
else:
    print("日本ダービーが見つかりません")

conn.close()
