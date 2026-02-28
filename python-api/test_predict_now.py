import sys, json, requests, base64
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# Admin token
h_b = base64.urlsafe_b64encode(json.dumps({"alg":"HS256","typ":"JWT"}).encode()).decode().rstrip('=')
p_b = base64.urlsafe_b64encode(json.dumps({"sub":"local-admin","app_metadata":{"role":"admin"},"user_metadata":{"subscription_tier":"premium"}}).encode()).decode().rstrip('=')
TOKEN = f"{h_b}.{p_b}.fakesig"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

print("=== 予測テスト ===")

# 最新モデル確認
r = requests.get("http://localhost:8000/api/models?ultimate=true", headers=HEADERS)
models = r.json().get("models", [])
latest = sorted(models, key=lambda m: m.get("created_at",""), reverse=True)[0]
model_id = latest["model_id"]
print("最新モデル: %s (AUC: %.4f, n_rows: %s)" % (model_id, latest.get("auc", 0), latest.get("n_rows")))

# 予測実行
try:
    r = requests.post("http://localhost:8000/api/analyze_race", headers=HEADERS,
        json={
            "race_id": "202610010412",
            "model_id": model_id,
            "bankroll": 10000,
            "risk_mode": "balanced",
            "ultimate_mode": True
        },
        timeout=30)
    print("STATUS: %d" % r.status_code)
    data = r.json()
    if r.status_code == 200:
        preds = data.get("predictions", [])
        print("予測成功 - 馬数: %d" % len(preds))
        print("--- 上位5頭 ---")
        for p in sorted(preds, key=lambda x: x.get("win_probability", 0), reverse=True)[:5]:
            hn = p.get("horse_number", "?")
            nm = p.get("horse_name", "?")
            wp = p.get("win_probability", 0)
            ev = p.get("expected_value", 0)
            odds = p.get("odds", 0)
            print("  #%s %s  勝率:%.1f%%  期待値:%.2f  オッズ:%s" % (hn, nm, wp*100, ev, odds))
        recs = data.get("recommendation", {})
        race_level = data.get("race_level", "?")
        best_bet_type = data.get("best_bet_type", "?")
        print("レースレベル: %s" % race_level)
        print("推奨券種: %s" % best_bet_type)
        if recs:
            print("推奨: %s × %s点 = ¥%s" % (recs.get("unit_price"), recs.get("purchase_count"), recs.get("total_cost")))
            print("戦略: %s" % (recs.get("strategy_explanation", "")[:60]))
        else:
            print("推奨情報なし")
    else:
        print("ERROR: " + json.dumps(data, ensure_ascii=False)[:500])
except requests.Timeout:
    print("TIMEOUT (30秒)")
except Exception as e:
    print("EXCEPTION: " + str(e))
