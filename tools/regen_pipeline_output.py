"""
pipeline_output/ を最新モデル・特徴量で全ファイル再生成する軽量スクリプト。
verify_pipeline_full.py よりもシンプルで出力に集中。

Usage:
    python-api/.venv/Scripts/python.exe tools/regen_pipeline_output.py
"""
import sys, os, json, traceback, logging
from pathlib import Path
import warnings; warnings.filterwarnings("ignore")

# logging を全て抑制（stderr への出力が PowerShell NativeCommandError を引き起こすため）
logging.disable(logging.CRITICAL)

ROOT  = Path(__file__).parent.parent
PYAPI = ROOT / "python-api"
KEIBA = ROOT / "keiba"
sys.path.insert(0, str(PYAPI))
sys.path.insert(0, str(KEIBA))
os.chdir(str(PYAPI))

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OUT  = ROOT / "tools" / "pipeline_output"
OUT.mkdir(exist_ok=True)

import pandas as pd
import numpy as np
import joblib

from app_config import ULTIMATE_DB, MODELS_DIR, get_latest_model, load_model_bundle, verify_feature_columns, assert_feature_columns
from keiba_ai.quality_gate import filter_valid_races
from keiba_ai.db_ultimate_loader import load_ultimate_training_frame
from keiba_ai.feature_engineering import add_derived_features
from keiba_ai.ultimate_features import UltimateFeatureCalculator

# ── モデル読み込み（win モデル限定） ─────────────────────────────────────
# model_win_*_ultimate.joblib を最新で選択（rank/no_odds は除外）
win_models = sorted(MODELS_DIR.glob("model_win_*_ultimate.joblib"),
                    key=lambda p: p.stat().st_mtime, reverse=True)
model_path = win_models[0] if win_models else None
if model_path is None:
    print("ERROR: model_win_*_ultimate.joblib が見つかりません")
    sys.exit(1)

bundle    = load_model_bundle(model_path)
model     = bundle["model"]
optimizer = bundle["optimizer"]
feat_cols = bundle["feature_columns"]
cat_feats = bundle.get("categorical_features", [])
metrics   = bundle.get("metrics", {})

cv_mean = metrics.get('cv_auc_mean', None)
cv_std  = metrics.get('cv_auc_std', None)
cv_str  = f"{float(cv_mean):.4f} ± {float(cv_std):.4f}" if cv_mean is not None and cv_mean != '?' else "?"

print(f"Model : {model_path.name}")
print(f"CV AUC: {cv_str}")
print(f"Feats : {len(feat_cols)}")

# ── Step1: 生データ ─────────────────────────────────────────────────────
print("\n[Step1] DB 読み込み...")
df_raw = load_ultimate_training_frame(ULTIMATE_DB)
print(f"  {len(df_raw):,} 行 × {len(df_raw.columns)} 列 / {df_raw['race_id'].nunique()} レース")
# [S] Quality Gate: 不正レース（distance=0, odds欠損など）を除外
_n_races_before = df_raw['race_id'].nunique()
df_raw = filter_valid_races(df_raw, verbose=True)
_n_races_after = df_raw['race_id'].nunique()
if _n_races_before != _n_races_after:
    print(f"  [Quality Gate] {_n_races_before - _n_races_after} 不正レース除外 → {_n_races_after} レース残存")
else:
    print(f"  [Quality Gate] 全 {_n_races_after} レース合格")
df_raw.head(200).to_csv(OUT / "01_raw_data.csv", index=False, encoding="utf-8-sig")
print(f"  → 01_raw_data.csv 保存")

# ── Step2: 特徴量エンジニアリング ──────────────────────────────────────
print("\n[Step2] add_derived_features...")
df_fe = add_derived_features(df_raw.copy(), full_history_df=df_raw.copy())
print(f"  {len(df_fe.columns)} 列")

# ── Step3: Ultimate 特徴量 ─────────────────────────────────────────────
print("\n[Step3] UltimateFeatureCalculator...")
calc  = UltimateFeatureCalculator(str(ULTIMATE_DB))
df_ult = calc.add_ultimate_features(df_fe.copy())
df_ult = df_ult.loc[:, ~df_ult.columns.duplicated()]
print(f"  {len(df_ult.columns)} 列")

# ── Step4: optimizer.transform ────────────────────────────────────────
print("\n[Step4] optimizer.transform...")
df_opt = optimizer.transform(df_ult.copy())
avail  = [c for c in feat_cols if c in df_opt.columns]
print(f"  特徴量 {len(avail)}/{len(feat_cols)} 列利用可能")

df_opt[avail].head(200).to_csv(OUT / "02_feature_engineered.csv",
                                index=False, encoding="utf-8-sig")
df_opt[avail].describe().T.to_csv(OUT / "02b_feature_stats.csv",
                                   encoding="utf-8-sig")
print(f"  → 02_feature_engineered.csv / 02b_feature_stats.csv 保存")

# ── Step5: 特徴量重要度 ───────────────────────────────────────────────
print("\n[Step5] 特徴量重要度...")
try:
    fi    = model.feature_importance(importance_type="gain")
    fn    = model.feature_name() if hasattr(model, "feature_name") else feat_cols
    fi_df = (pd.DataFrame({"feature": fn[:len(fi)], "importance_gain": fi})
               .sort_values("importance_gain", ascending=False))
    fi_df.to_csv(OUT / "03_feature_importance.csv", index=False, encoding="utf-8-sig")
    print(f"  Top5: {list(fi_df['feature'].head(5))}")
    print(f"  → 03_feature_importance.csv 保存")
except Exception as e:
    print(f"  ⚠ {e}")

# ── Step6: サンプルレースで予測 ────────────────────────────────────────
print("\n[Step6] サンプルレース予測...")
import sqlite3

conn = sqlite3.connect(str(ULTIMATE_DB))
# 最新かつ頭数が多いレース
rows = conn.execute("""
    SELECT r.race_id, COUNT(*) cnt
    FROM races_ultimate r
    JOIN race_results_ultimate h ON h.race_id = r.race_id
    GROUP BY r.race_id ORDER BY r.race_id DESC LIMIT 100
""").fetchall()
conn.close()

target_race_id = max(rows, key=lambda x: x[1])[0] if rows else None
if target_race_id is None:
    print("  ⚠ 対象レースなし")
    sys.exit(0)

conn      = sqlite3.connect(str(ULTIMATE_DB))
race_row  = conn.execute("SELECT data FROM races_ultimate WHERE race_id=?",
                         (target_race_id,)).fetchone()
horse_rows = conn.execute(
    "SELECT data FROM race_results_ultimate WHERE race_id=? "
    "ORDER BY CAST(json_extract(data,'$.horse_number') AS INTEGER)",
    (target_race_id,)).fetchall()
conn.close()

race_data = json.loads(race_row[0])
print(f"  レース: {target_race_id} / {race_data.get('race_name','')} {race_data.get('date','')} "
      f"{len(horse_rows)}頭")

horse_records = []
for hr in horse_rows:
    hd = json.loads(hr[0])
    hd["race_id"] = target_race_id
    for k, v in race_data.items():
        if k not in hd or hd[k] is None:
            hd[k] = v
    horse_records.append(hd)

df_pred = pd.DataFrame(horse_records)

# カラム名統一（predict.py と同じマッピング）
# races_ultimate.data は track_type='芝'/'ダート' で保存、surface=None のため fillna で補完する
_pred_col_map = {
    "track_type": "surface", "last_3f": "last_3f_time", "weight_kg": "horse_weight",
}
for _old, _new in _pred_col_map.items():
    if _old in df_pred.columns:
        if _new not in df_pred.columns:
            df_pred[_new] = df_pred[_old]
        else:
            df_pred[_new] = df_pred[_new].fillna(df_pred[_old])

# レース後フィールドをクリア（リーク防止）
for c in ['finish','finish_position','time','finish_time','margin',
          'corner_1','corner_2','corner_3','corner_4','corner_positions',
          'last_3f','last_3f_time','last_3f_rank','prize_money','time_seconds']:
    if c in df_pred.columns:
        df_pred[c] = None

# 生データ保存
df_pred.to_csv(OUT / f"04_prediction_raw_{target_race_id}.csv",
               index=False, encoding="utf-8-sig")
print(f"  → 04_prediction_raw_{target_race_id}.csv 保存")

# 特徴量エンジニアリング（full_history_df=df_raw でDB全履歴を参照: rolling stats を正しく計算）
df_pred = add_derived_features(df_pred, full_history_df=df_raw)
df_pred = calc.add_ultimate_features(df_pred)
df_pred = df_pred.loc[:, ~df_pred.columns.duplicated()]
df_pred_opt = optimizer.transform(df_pred)

df_pred_opt.to_csv(OUT / f"05_prediction_features_{target_race_id}.csv",
                   index=False, encoding="utf-8-sig")
print(f"  → 05_prediction_features_{target_race_id}.csv 保存")

# 特徴量整形 → 予測
# [S/A-6] Quality Gate → assert → verify_feature_columns（NaN補完）
X_pred = df_pred_opt.copy()
# 識別子・未来情報列を除外
_regen_drop = {"win", "place", "race_id", "horse_id", "jockey_id", "trainer_id", "owner_id",
               "finish_position", "finish", "finish_time", "time_seconds",
               "corner_1", "corner_2", "corner_3", "corner_4", "corner_positions",
               "last_3f", "last_3f_rank", "last_3f_rank_normalized", "last_3f_time",
               "margin", "prize_money", "actual_finish"}
X_pred = X_pred.drop(columns=[c for c in _regen_drop if c in X_pred.columns])
assert_feature_columns(X_pred, bundle)
X_pred = verify_feature_columns(X_pred, bundle)
probs  = model.predict(X_pred)
# [L3-3] キャリブレーション適用
_cal = bundle.get("calibrator")
if _cal is not None:
    try:
        probs = _cal.predict(probs)
    except Exception:
        pass  # キャリブレーター失敗時はそのまま

preds = []
for i, rec in enumerate(horse_records):
    preds.append({
        "horse_number"   : int(rec.get("horse_number", i+1) or i+1),
        "horse_name"     : str(rec.get("horse_name", f"馬{i+1}")),
        "odds"           : float(rec.get("odds") or 0),
        "actual_finish"  : rec.get("finish_position") or rec.get("finish"),
        "win_probability": float(probs[i]),
    })
preds.sort(key=lambda x: x["win_probability"], reverse=True)
for rank, p in enumerate(preds, 1):
    p["predicted_rank"] = rank

result = {
    "race_id"    : target_race_id,
    "race_name"  : race_data.get("race_name", ""),
    "venue"      : race_data.get("venue", ""),
    "date"       : race_data.get("date", ""),
    "distance"   : race_data.get("distance", 0),
    "model_file" : model_path.name,
    "cv_auc"     : cv_str,
    "predictions": preds,
}
with open(OUT / f"06_predictions_{target_race_id}.json", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(f"  → 06_predictions_{target_race_id}.json 保存")

# 予測精度（実際の着順があれば）
if any(str(p["actual_finish"]).isdigit() and int(str(p["actual_finish"])) == 1
       for p in preds if p["actual_finish"] is not None):
    winner_num = next(p["horse_number"] for p in preds
                      if p["actual_finish"] is not None
                      and str(p["actual_finish"]).isdigit()
                      and int(str(p["actual_finish"])) == 1)
    hit = preds[0]["horse_number"] == winner_num
    print(f"\n  実際1着={winner_num}番 / 予測1位={preds[0]['horse_number']}番  "
          f"→ {'✓ 単勝的中' if hit else '✗ 外れ'}")

print("\n===== 完了 =====")
print(f"  {', '.join(p.name for p in sorted(OUT.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:7])}")
