import urllib.request, json, time

url = "http://localhost:8000/api/analyze_race"
data = json.dumps({"race_id": "202605021211"}).encode()
req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
t0 = time.time()
resp = urllib.request.urlopen(req, timeout=120)
body = json.loads(resp.read())
elapsed = time.time() - t0

print(f"=== 日本ダービー予測 ({elapsed:.2f}s) ===")
ri = body.get("race_info", {})
race_name = ri.get("race_name", "?")
venue = ri.get("venue", "?")
dist = ri.get("distance", "?")
track = ri.get("track_type", "?")
weather = ri.get("weather", "?")
condition = ri.get("field_condition", "?")
print(f"  {race_name} {venue} {dist}m {track} | 天候:{weather} 馬場:{condition}")

pe = body.get("pro_evaluation", {})
print(f"  難易度: {pe.get('difficulty_score', '?')} | 推奨: {pe.get('recommended_action', '?')}")
print(f"  モデル信頼度: {pe.get('confidence', '?')}")

preds = body.get("predictions", [])
print(f"\n馬番  馬名                   勝率    複勝率   オッズ  kelly   推奨")
print("-" * 75)
for p in preds:
    no = p.get("horse_number", "?")
    nm = (p.get("horse_name") or "?")[:18]
    wp = p.get("win_probability", 0)
    pp = p.get("place_probability", 0)
    odds = p.get("odds") or "-"
    kelly = p.get("kelly_fraction", 0)
    rec = (p.get("recommendation") or "")[:15]
    print(f" {str(no):>2}  {nm:<18} {wp:.3f}   {pp:.3f}   {str(odds):>6}  {kelly:.3f}  {rec}")

br = body.get("recommendation", {})
print(f"\n推奨: {br.get('strategy_explanation','')[:120]}")
print(f"ベストBet: {body.get('best_bet_type','')} | race_level: {body.get('race_level','')}")
