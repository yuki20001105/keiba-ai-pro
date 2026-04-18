"""
全期間再スクレイプ→Optuna学習→本日予測 の自動パイプライン
"""
import urllib.request, urllib.error, json, time, sys
from datetime import datetime

BASE = "http://localhost:8000"

def api_post(path, body=None, timeout=30):
    body_bytes = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=body_bytes,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"__error__": e.code, "__body__": e.read().decode()}
    except Exception as e:
        return {"__error__": str(e)}

def api_get(path, timeout=30):
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"__error__": e.code, "__body__": e.read().decode()}
    except Exception as e:
        return {"__error__": str(e)}

def poll_job(status_url, timeout=3600, interval=5, label=""):
    """ジョブが completed or error になるまでポーリング"""
    start = time.time()
    last_msg = ""
    while time.time() - start < timeout:
        r = api_get(status_url)
        status = r.get("status", "unknown")
        prog = r.get("progress", {})
        msg = prog.get("message", "") if isinstance(prog, dict) else str(prog)
        if msg != last_msg:
            print(f"  [{label}] {status}: {msg}")
            last_msg = msg
        if status in ("completed", "done"):
            return r
        if status in ("error", "failed"):
            print(f"  [{label}] FAILED: {r.get('error', r.get('result',''))}")
            return r
        time.sleep(interval)
    return {"status": "timeout"}

# ── Step 1: スクレイプ開始 ──────────────────────────────────────
print("=" * 60)
print("Step 1: 2016/01/01 〜 2026/03/31 データ再取得")
print("=" * 60)

# まず今日(2026/04/12)のデータを確認
r_today = api_get("/api/races/by_date?date=20260412")
today_races = r_today.get("races", [])
print(f"  今日(20260412)のDB内レース数: {len(today_races)}")

# 全期間スクレイプ開始
r_scrape = api_post("/api/scrape/start", {
    "start_date": "20160101",
    "end_date": "20260331",
    "force_rescrape": False
})
if "__error__" in r_scrape:
    print(f"  ⚠ スクレイプ開始エラー: {r_scrape}")
    scrape_job_id = None
else:
    scrape_job_id = r_scrape.get("job_id")
    print(f"  ✓ スクレイプジョブ開始: job_id={scrape_job_id}")

# ── Step 2: 2026/04/12 のスクレイプも起動 ──────────────────────
print("\nStep 2: 今日(20260412)の最新データ取得")
r_today_scrape = api_post("/api/scrape/start", {
    "start_date": "20260412",
    "end_date": "20260412",
    "force_rescrape": True  # 強制更新（オッズ最新化）
})
if "__error__" not in r_today_scrape:
    today_job_id = r_today_scrape.get("job_id")
    print(f"  ✓ 今日のスクレイプ開始: job_id={today_job_id}")
    # 今日分は短いので完了まで待つ (最大10分)
    print("  ポーリング中... (最大15分)")
    r_today_result = poll_job(f"/api/scrape/status/{today_job_id}", timeout=900, interval=5, label="today")
    today_result = r_today_result.get("result", {})
    print(f"  ✓ 今日のスクレイプ完了: {today_result.get('races_collected', 0)}レース, {today_result.get('horses_collected', 0)}頭")
else:
    print(f"  ⚠ 今日のスクレイプ開始エラー: {r_today_scrape}")

# ── Step 3: Optuna学習 ──────────────────────────────────────────
print("\n" + "=" * 60)
print("Step 3: Optunaモデル学習 (2016-2026データ使用)")
print("=" * 60)

r_train = api_post("/api/train/start", {
    "target": "win",
    "model_type": "lightgbm",
    "test_size": 0.2,
    "cv_folds": 5,
    "use_sqlite": True,
    "use_optimizer": True,
    "use_optuna": True,
    "optuna_trials": 50,
    "optuna_timeout": 1800,  # 30分タイムアウト
    "ultimate_mode": True,
    "training_date_from": "2016-01",
    "training_date_to": "2026-03"
})

if "__error__" in r_train:
    print(f"  ⚠ 学習開始エラー: {r_train}")
    train_model_id = None
else:
    train_job_id = r_train.get("job_id")
    print(f"  ✓ 学習ジョブ開始: job_id={train_job_id}")
    print("  ポーリング中... (最大60分)")
    r_train_result = poll_job(f"/api/train/status/{train_job_id}", timeout=3600, interval=10, label="train")
    
    if r_train_result.get("status") == "completed":
        result = r_train_result.get("result", {})
        train_model_id = result.get("model_id")
        metrics = result.get("metrics", {})
        print(f"  ✓ 学習完了!")
        print(f"    model_id: {train_model_id}")
        print(f"    AUC: {metrics.get('auc','?')}")
        print(f"    CV_AUC: {metrics.get('cv_auc_mean','?')}")
        print(f"    データ数: {result.get('data_count','?')}")
    else:
        print(f"  ⚠ 学習失敗: {r_train_result}")
        train_model_id = None

# ── Step 4: 本日予測 ────────────────────────────────────────────
print("\n" + "=" * 60)
print("Step 4: 2026/04/12 予測実行")
print("=" * 60)

# 今日のレース取得
r_today2 = api_get("/api/races/by_date?date=20260412")
today_races2 = r_today2.get("races", [])
print(f"  今日のレース数: {len(today_races2)}")

if today_races2:
    race_id = today_races2[0]["race_id"]
    race_name = today_races2[0].get("race_name", "?")
    venue = today_races2[0].get("venue", "?")
    print(f"  最初のレース: {race_id} {venue} {race_name}")
    
    # 馬データ取得
    r_horses = api_get(f"/api/races/{race_id}/horses")
    horses = r_horses.get("horses", [])
    print(f"  出走頭数: {len(horses)}")
    
    if horses:
        # 予測実行
        payload = {"race_id": race_id}
        if train_model_id:
            payload["model_id"] = train_model_id
        payload["bankroll"] = 100000
        payload["risk_mode"] = "conservative"
        
        r_pred = api_post("/api/analyze_race", payload, timeout=120)
        if "__error__" in r_pred:
            print(f"  ⚠ 予測エラー: {r_pred}")
        else:
            scores = r_pred.get("scores", [])
            print(f"  ✓ 予測成功! {len(scores)}頭分の予測")
            print(f"  上位3頭:")
            for s in sorted(scores, key=lambda x: x.get("win_prob", 0), reverse=True)[:3]:
                print(f"    {s.get('horse_num','?')}番 {s.get('horse_name','?')[:15]} p={s.get('win_prob',0):.3f} EV={s.get('ev',0):.2f}")
    else:
        print("  ⚠ 出走馬データなし")

# ── Step 5: 全期間スクレイプの進捗確認 ──────────────────────────
if scrape_job_id:
    print(f"\n" + "=" * 60)
    print(f"Step 5: 全期間スクレイプ進捗確認 (job_id={scrape_job_id})")
    print("=" * 60)
    r_status = api_get(f"/api/scrape/status/{scrape_job_id}")
    status = r_status.get("status", "unknown")
    prog = r_status.get("progress", {})
    msg = prog.get("message", "") if isinstance(prog, dict) else str(prog)
    done = prog.get("done", 0) if isinstance(prog, dict) else 0
    total = prog.get("total", 0) if isinstance(prog, dict) else 0
    print(f"  状態: {status}")
    print(f"  進捗: {done}/{total}")
    print(f"  メッセージ: {msg}")
    print(f"\n  ※ バックグラウンド継続中。全完了まで数時間かかる場合があります。")
    print(f"  ※ UI: http://localhost:3000/data-collection でリアルタイム確認可能")

print("\n" + "=" * 60)
print("自動パイプライン完了")
print("=" * 60)
print(f"  完了時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
