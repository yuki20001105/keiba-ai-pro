"""
パイプライン検証スクリプト
対象日: 2026/02/01

実行内容:
  1. Render API で 2026/02/01 のレースをスクレイプ（まだなければ）
  2. /api/train を呼び出し Ultimate モードで学習
  3. 学習に使用した全特徴量を一覧表示
"""

import json
import sys
import time
import textwrap
from pprint import pformat

import requests

API_BASE = "https://keiba-ai-api.onrender.com"
TARGET_DATE = "20260201"
TIMEOUT = 600  # スクレイプポーリング最大秒数
TRAIN_TIMEOUT = None  # 学習はタイムアウトなし（Supabase同期+LightGBM CVが長い）


def separator(title: str = "", char: str = "=", width: int = 70):
    if title:
        side = (width - len(title) - 2) // 2
        print(char * side + f" {title} " + char * side)
    else:
        print(char * width)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 0: API ヘルスチェック
# ─────────────────────────────────────────────────────────────────────────────
print()
separator("STEP 0: API ヘルスチェック")
try:
    r = requests.get(f"{API_BASE}/", timeout=30)
    print(f"  Status: {r.status_code}  Response: {r.text[:200]}")
except requests.exceptions.ConnectionError:
    print("  ⚠WARNING: API に接続できません。Render がスリープ中の可能性があります。")
    print("  30 秒待ってリトライします...")
    time.sleep(30)
    try:
        r = requests.get(f"{API_BASE}/", timeout=30)
        print(f"  Status: {r.status_code}  Response: {r.text[:200]}")
    except Exception as e:
        print(f"  ✗ API 接続失敗: {e}")
        sys.exit(1)
except Exception as e:
    print(f"  ✗ API エラー: {e}")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1: 2026/02/01 スクレイプ
# ─────────────────────────────────────────────────────────────────────────────
print()
separator("STEP 1: 2026/02/01 スクレイプ開始")
try:
    payload = {"start_date": TARGET_DATE, "end_date": TARGET_DATE}
    r = requests.post(f"{API_BASE}/api/scrape/start", json=payload, timeout=30)
    r.raise_for_status()
    job = r.json()
    job_id = job.get("job_id") or job.get("id")
    print(f"  ✓ job_id: {job_id}")
    print(f"  Response: {json.dumps(job, ensure_ascii=False, indent=2)[:500]}")
except Exception as e:
    print(f"  ✗ スクレイプ開始エラー: {e}")
    print("  → 既存データで学習を試みます")
    job_id = None

if job_id:
    # ─── STEP 1b: ジョブ完了まで待機 ───────────────────────────────────────
    print()
    separator("STEP 1b: スクレイプ完了待機", char="-")
    waited = 0
    interval = 20
    while waited < TIMEOUT:
        time.sleep(interval)
        waited += interval
        try:
            r = requests.get(f"{API_BASE}/api/scrape/status/{job_id}", timeout=30)
            st = r.json()
            status = st.get("status", "unknown")
            progress = st.get("progress", "")
            races_done = st.get("races_scraped", st.get("completed", "-"))
            print(f"  [{waited:4d}s] status={status}  races_done={races_done}  {str(progress)[:80]}")

            if status in ("done", "completed", "finished", "success"):
                print(f"\n  ✓ スクレイプ完了: {races_done} レース取得")
                break
            elif status == "error":
                print(f"\n  ✗ スクレイプエラー: {st}")
                break
        except Exception as e:
            print(f"  ポーリングエラー: {e}")
    else:
        print(f"  ⚠ タイムアウト({TIMEOUT}s) — 収集済みデータで学習を続行します")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2: /api/train/start (非同期)  (ultimate_mode / lightgbm / use_optimizer)
# ─────────────────────────────────────────────────────────────────────────────
print()
separator("STEP 2: /api/train/start 呼び出し（非同期）")
train_payload = {
    "target": "win",
    "model_type": "lightgbm",
    "test_size": 0.2,
    "cv_folds": 2,            # 短縮：標準5→検証用2fold
    "ultimate_mode": True,
    "use_optimizer": True,
    "use_optuna": False,
    "force_sync": True,
}
print(f"  Request: {json.dumps(train_payload, indent=2)}")

# 非同期エンドポイントが存在する場合はそちらを使う
train_job_id = None
result = None

try:
    r = requests.post(f"{API_BASE}/api/train/start", json=train_payload, timeout=30)
    r.raise_for_status()
    job = r.json()
    train_job_id = job.get("job_id")
    print(f"  ✓ train job_id: {train_job_id}")
except Exception as e:
    print(f"  ⚠ /api/train/start 未対応 ({e.__class__.__name__}): 旧エンドポイントにフォールバック")
    train_job_id = None

if train_job_id:
    # ─── STEP 2b: 学習完了まで待機 ───────────────────────────────────────
    print()
    separator("STEP 2b: 学習完了待機（ポーリング）", char="-")
    print("  ※ Supabase同期 + LightGBM CV のため5〜15分かかります")
    waited = 0
    interval = 30
    max_wait = 1800  # 最大30分
    while waited < max_wait:
        time.sleep(interval)
        waited += interval
        try:
            r = requests.get(f"{API_BASE}/api/train/status/{train_job_id}", timeout=30)
            st = r.json()
            status = st.get("status", "unknown")
            progress = st.get("progress", "")
            print(f"  [{waited:5d}s] status={status}  progress={str(progress)[:100]}")
            if status == "completed":
                result = st.get("result", {})
                print(f"\n  ✓ 学習完了!")
                break
            elif status == "error":
                print(f"\n  ✗ 学習エラー: {st.get('error', '不明なエラー')}")
                break
        except Exception as e:
            print(f"  ポーリングエラー: {e}")
    else:
        print(f"  ⚠ ポーリングタイムアウト({max_wait}s)")
else:
    # フォールバック: 旧同期エンドポイント
    print()
    separator("フォールバック: /api/train（同期・タイムアウトなし）", char="-")
    print("  ※ 完了まで長時間かかります。中断しないでください。")
    try:
        r = requests.post(f"{API_BASE}/api/train", json=train_payload, timeout=TRAIN_TIMEOUT)
        r.raise_for_status()
        result = r.json()
    except requests.exceptions.HTTPError as e:
        print(f"  ✗ HTTP エラー {e.response.status_code}: {e.response.text[:2000]}")
        sys.exit(1)
    except Exception as e:
        print(f"  ✗ エラー: {e}")
        sys.exit(1)

if not result:
    print("\n学習結果を取得できませんでした。")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3: 結果表示
# ─────────────────────────────────────────────────────────────────────────────
print()
separator("STEP 3: 学習結果")
print(f"  success       : {result.get('success')}")
print(f"  model_id      : {result.get('model_id')}")
print(f"  data_count    : {result.get('data_count')} 行")
print(f"  race_count    : {result.get('race_count')} レース")
print(f"  feature_count : {result.get('feature_count')} 特徴量")
print(f"  training_time : {result.get('training_time', 0):.1f} 秒")

metrics = result.get("metrics", {})
print()
separator("【評価指標】", char="-")
print(f"  AUC           : {metrics.get('auc', 'N/A')}")
print(f"  LogLoss       : {metrics.get('logloss', 'N/A')}")
print(f"  CV-AUC mean   : {metrics.get('cv_auc_mean', 'N/A')}")
print(f"  CV-AUC std    : {metrics.get('cv_auc_std', 'N/A')}")
print(f"  message       : {result.get('message')}")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4: 全特徴量一覧
# ─────────────────────────────────────────────────────────────────────────────
feature_columns: list = result.get("feature_columns", [])

print()
separator("STEP 4: 学習前の入力データ (全特徴量一覧)")
print(f"  合計: {len(feature_columns)} 特徴量\n")

# カテゴリ別に分類して表示
CATS = {
    "【カテゴリ: エンコード済み】(_encoded 末尾)": [c for c in feature_columns if c.endswith("_encoded")],
    "【カテゴリ: 統計（騎手）】(jockey_*)": [c for c in feature_columns if c.startswith("jockey_") and not c.endswith("_encoded")],
    "【カテゴリ: 統計（調教師）】(trainer_*)": [c for c in feature_columns if c.startswith("trainer_") and not c.endswith("_encoded")],
    "【カテゴリ: 統計（父馬）】(sire_*)": [c for c in feature_columns if c.startswith("sire_")],
    "【カテゴリ: 統計（母父馬）】(damsire_*)": [c for c in feature_columns if c.startswith("damsire_")],
    "【カテゴリ: 馬体重・斤量】": [c for c in feature_columns if "weight" in c or c in ("burden_weight",)],
    "【カテゴリ: コーナー通過】(corner_*)": [c for c in feature_columns if c.startswith("corner_")],
    "【カテゴリ: ラップタイム】(lap_*)": [c for c in feature_columns if c.startswith("lap_")],
    "【カテゴリ: 前走情報】(prev_*)": [c for c in feature_columns if c.startswith("prev_")],
    "【カテゴリ: バイナリ特徴】(is_*, *_flag)": [c for c in feature_columns if c.startswith("is_") or c.startswith("sex_") or c.startswith("rest_") or c.startswith("pace_") or c.startswith("pop_") or c.endswith("_flag")],
    "【カテゴリ: 馬過去成績】(horse_*, past_*)": [c for c in feature_columns if (c.startswith("horse_") or c.startswith("past_")) and not c.endswith("_encoded")],
    "【カテゴリ: 日付派生】(*_year, *_month, *_day, *_dayofweek)": [c for c in feature_columns if any(c.endswith(s) for s in ("_year", "_month", "_day", "_dayofweek"))],
}

already_shown = set()
for cat_title, cols in CATS.items():
    if cols:
        print(f"  {cat_title}")
        for c in sorted(cols):
            if c not in already_shown:
                print(f"    - {c}")
                already_shown.add(c)
        print()

# 未分類
remaining = [c for c in feature_columns if c not in already_shown]
if remaining:
    print("  【その他】")
    for c in sorted(remaining):
        print(f"    - {c}")
    print()

# 番号付き完全リスト
print()
separator("【番号付き完全特徴量リスト】", char="-")
for i, col in enumerate(feature_columns, 1):
    print(f"  {i:3d}. {col}")

print()
separator("検証完了")
print(f"  feature_count = {len(feature_columns)}")
if result.get("success"):
    print("  ✓ スクレイプ → 特徴エンジニアリング → LightGBM 学習 パイプライン全体が正常動作")
else:
    print("  ✗ 学習が失敗しました。上記のエラーを確認してください。")
print()
