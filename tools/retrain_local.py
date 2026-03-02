"""
ローカル再学習スクリプト
- Ultimate DB → 特徴量エンジニアリング → LightGBM 学習 → モデル保存
Usage:
    python-api/.venv/Scripts/python.exe tools/retrain_local.py
"""
import sys, os
from pathlib import Path

ROOT  = Path(__file__).parent.parent
PYAPI = ROOT / "python-api"
KEIBA = ROOT / "keiba"
sys.path.insert(0, str(PYAPI))
sys.path.insert(0, str(KEIBA))
os.chdir(PYAPI)

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore

import numpy as np
import pandas as pd
import joblib
import lightgbm as lgb
from datetime import datetime, timezone, timedelta
from sklearn.metrics import roc_auc_score, log_loss
from sklearn.model_selection import GroupKFold

from app_config import ULTIMATE_DB, MODELS_DIR, load_model_bundle  # type: ignore
from keiba_ai.db_ultimate_loader import load_ultimate_training_frame  # type: ignore
from keiba_ai.feature_engineering import add_derived_features  # type: ignore
from keiba_ai.ultimate_features import UltimateFeatureCalculator  # type: ignore
from keiba_ai.lightgbm_feature_optimizer import (  # type: ignore
    LightGBMFeatureOptimizer, prepare_for_lightgbm_ultimate
)

JST = timezone(timedelta(hours=9))
SECTION = lambda t: print(f"\n{'='*60}\n  {t}\n{'='*60}")

SECTION("Step 1: DB データ読み込み")
df = load_ultimate_training_frame(ULTIMATE_DB)
print(f"  → {len(df):,}行 × {len(df.columns)}列")

SECTION("Step 2: 特徴量エンジニアリング")
df = add_derived_features(df, full_history_df=df)
print(f"  → add_derived_features 後: {df.shape}")

SECTION("Step 3: Ultimate 特徴量")
calc = UltimateFeatureCalculator(str(ULTIMATE_DB))
df = calc.add_ultimate_features(df)
df = df.loc[:, ~df.columns.duplicated()]
print(f"  → UltimateFeatureCalculator 後: {df.shape}")

SECTION("Step 4: LightGBM 最適化")

# ── win/place3 ターゲット列を optimizer 前に生成 ──────────────────────
fin_col = 'finish' if 'finish' in df.columns else 'finish_position'
if fin_col in df.columns:
    fin_num = pd.to_numeric(df[fin_col], errors='coerce')
    df['win']    = (fin_num == 1).astype(float)
    df['place3'] = (fin_num <= 3).astype(float)
    # NaN行（取消・欠場）はターゲットも NaN に
    df.loc[fin_num.isna(), ['win', 'place3']] = np.nan
    print(f"  win 正例: {df['win'].sum():.0f} / {df['win'].notna().sum():,}")
else:
    print("  ✗ finish列が見つかりません - win列を手動設定できません")

optimizer = LightGBMFeatureOptimizer()
df_opt, cat_features = optimizer.fit_transform(df, target_col="win")
print(f"  → 最適化後: {df_opt.shape}")
print(f"  カテゴリカル特徴: {cat_features[:5]}...")

# 学習データ準備
EXCLUDE = {"race_id", "horse_id", "jockey_id", "trainer_id", "owner_id",
           "win", "place3", "finish", "finish_position"}
feature_cols = [c for c in df_opt.columns if c not in EXCLUDE and df_opt[c].dtype != object]
print(f"  特徴量数: {len(feature_cols)}")

target_col = "win"
if target_col not in df_opt.columns:
    print(f"  ✗ 'win'列がdf_optにありません。df_optのwarp済み列を確認してください")
    sys.exit(1)

X = df_opt[feature_cols].copy()
y = df_opt[target_col].copy()

# NaN を除去
valid = y.notna()
X, y = X[valid], y[valid]
if "race_id" in df_opt.columns:
    groups = df_opt.loc[valid, "race_id"]
else:
    groups = pd.Series(range(len(X)))

print(f"  学習データ: {len(X):,}行 / {y.sum():.0f}正例 ({y.mean()*100:.1f}%)")

SECTION("Step 5: モデル学習 (GroupKFold CV)")
params = {
    "objective": "binary",
    "metric": "auc",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "max_depth": -1,
    "min_child_samples": 20,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "lambda_l1": 0.1,
    "lambda_l2": 0.1,
    "verbose": -1,
    "n_jobs": -1,
}

gkf = GroupKFold(n_splits=3)
cv_aucs = []
for fold, (tr_idx, va_idx) in enumerate(gkf.split(X, y, groups), 1):
    X_tr, X_va = X.iloc[tr_idx], X.iloc[va_idx]
    y_tr, y_va = y.iloc[tr_idx], y.iloc[va_idx]
    ds_tr = lgb.Dataset(X_tr, y_tr, categorical_feature=cat_features)
    ds_va = lgb.Dataset(X_va, y_va, categorical_feature=cat_features)
    m = lgb.train(
        params, ds_tr,
        num_boost_round=200,
        valid_sets=[ds_va],
        callbacks=[lgb.early_stopping(20, verbose=False), lgb.log_evaluation(-1)],
    )
    preds = m.predict(X_va)
    auc = roc_auc_score(y_va, preds)
    cv_aucs.append(auc)
    print(f"  Fold{fold} AUC={auc:.4f}")

cv_mean = float(np.mean(cv_aucs))
cv_std  = float(np.std(cv_aucs))
print(f"  CV AUC: {cv_mean:.4f} ± {cv_std:.4f}")

# 全データで最終モデルを学習
ds_all = lgb.Dataset(X, y, categorical_feature=cat_features)
final_model = lgb.train(
    params, ds_all,
    num_boost_round=200,
    callbacks=[lgb.log_evaluation(-1)],
)

final_preds = final_model.predict(X)
final_auc   = roc_auc_score(y, final_preds)
final_ll    = log_loss(y, final_preds)
print(f"  全データ AUC={final_auc:.4f}  LogLoss={final_ll:.4f}")

SECTION("Step 6: モデル保存")
ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
model_path = MODELS_DIR / f"model_win_lightgbm_{ts}_ultimate.joblib"

bundle = {
    "model": final_model,
    "model_type": "lightgbm",
    "feature_columns": feature_cols,
    "categorical_features": cat_features,
    "optimizer": optimizer,
    "metrics": {
        "auc": final_auc,
        "logloss": final_ll,
        "cv_auc_mean": cv_mean,
        "cv_auc_std": cv_std,
    },
    "data_count": len(X),
    "race_count": int(groups.nunique()),
    "training_date_from": str(groups.min()) if len(groups) else "",
    "training_date_to":   str(groups.max()) if len(groups) else "",
    "timestamp": ts,
}

joblib.dump(bundle, model_path)
print(f"  → 保存: {model_path}")
print(f"  AUC={final_auc:.4f}  CV={cv_mean:.4f}±{cv_std:.4f}  特徴量={len(feature_cols)}  行={len(X):,}")

SECTION("完了")
print(f"  モデルファイル: {model_path.name}")
print(f"  AUC          : {final_auc:.4f}")
print(f"  CV AUC       : {cv_mean:.4f} ± {cv_std:.4f}")
print(f"  特徴量数     : {len(feature_cols)}")
print(f"  学習データ   : {len(X):,}行 / {int(groups.nunique())}レース")
