from __future__ import annotations
import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Sequence

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.metrics import roc_auc_score, log_loss
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression

from .config import load_config
from .db import connect, init_db, load_training_frame
from .feature_engineering import add_derived_features

JST = timezone(timedelta(hours=9))

def safe_int_convert(value):
    """安全にあらゆる型をintに変換（取消、欠場などの文字列対応）"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        if isinstance(value, str):
            value_str = value.strip()
            if value_str in ('取消', '欠場', '中止', '除外', '失格', '-', '', '取'):
                return None
            return int(float(value_str))
        if isinstance(value, pd.Series):
            return safe_int_convert(value.iloc[0])
        if hasattr(value, 'item'):
            return int(float(value.item()))
        if isinstance(value, (list, tuple)):
            return safe_int_convert(value[0])
        return int(float(value))
    except (ValueError, TypeError, IndexError, AttributeError):
        return None

def _make_target(df: pd.DataFrame, target: str) -> pd.Series:
    # finish列を数値に変換（文字列の場合はNaNになる）
    finish_numeric = df["finish"].apply(safe_int_convert)
    
    if target == "win":
        return (finish_numeric == 1).astype(int)
    if target == "place3":
        # NaNは除外（False扱い）
        return (finish_numeric <= 3).fillna(False).astype(int)
    raise ValueError(f"Unknown target: {target}")

def _compute_feature_importance(model: Pipeline, feature_cols_num: list, feature_cols_cat: list) -> pd.DataFrame:
    """LogisticRegressionの係数から特徴量重要度を計算"""
    clf = model.named_steps["clf"]
    coef = clf.coef_[0]
    
    # 特徴量名を取得
    preprocessor = model.named_steps["pre"]
    feature_names = []
    
    # 数値特徴量
    feature_names.extend(feature_cols_num)
    
    # カテゴリカル特徴量（OneHotEncoded）
    if len(feature_cols_cat) > 0:
        cat_transformer = preprocessor.named_transformers_["cat"]
        onehot = cat_transformer.named_steps["onehot"]
        for i, col in enumerate(feature_cols_cat):
            categories = onehot.categories_[i]
            for cat in categories:
                feature_names.append(f"{col}_{cat}")
    
    # 重要度データフレームを作成
    importance_df = pd.DataFrame({
        "feature": feature_names[:len(coef)],
        "coefficient": coef,
        "abs_coefficient": np.abs(coef)
    })
    
    # 絶対値でソート
    importance_df = importance_df.sort_values("abs_coefficient", ascending=False).reset_index(drop=True)
    
    return importance_df

def train(cfg_path: Path) -> Path:
    cfg = load_config(cfg_path)

    con = connect(cfg.storage.sqlite_path)
    init_db(con)
    df = load_training_frame(con)
    con.close()

    if df.empty:
        raise RuntimeError("No training data found. Run ingest for races first.")

    # 派生特徴量を追加
    print("派生特徴量を計算中...")
    df = add_derived_features(df, full_history_df=df)
    print(f"派生特徴量追加後のカラム数: {len(df.columns)}")

    # Basic feature set (pre-race-ish): use entry columns; keep results columns only for label.
    # NOTE: entry_odds may reflect final odds at off; still usable as pre-race signal.
    feature_cols_num = [
        "horse_no", "bracket", "age", "handicap", "weight", "weight_diff", 
        "entry_odds", "entry_popularity",
        # 新規追加: コース特性
        "straight_length", "inner_bias", "inner_advantage",
        # 新規追加: 統計特徴量
        "jockey_course_win_rate", "jockey_course_races",
        "horse_distance_win_rate", "horse_distance_avg_finish",
        "trainer_recent_win_rate"
    ]
    feature_cols_cat = [
        "sex", "jockey_id", "trainer_id",
        # 新規追加: コース特性（カテゴリ）
        "venue_code", "track_type", "corner_radius"
    ]

    # Some columns might be missing depending on parsing; add if absent.
    for c in feature_cols_num + feature_cols_cat:
        if c not in df.columns:
            df[c] = np.nan

    X = df[feature_cols_num + feature_cols_cat].copy()
    y = _make_target(df, cfg.training.target)
    
    # 全てNaNのカラムを除外（警告回避）
    non_null_counts = X.notna().sum()
    valid_num_cols = [c for c in feature_cols_num if c in X.columns and non_null_counts[c] > 0]
    valid_cat_cols = [c for c in feature_cols_cat if c in X.columns and non_null_counts[c] > 0]
    
    # 有効なカラムのみ使用
    X = X[valid_num_cols + valid_cat_cols].copy()
    feature_cols_num = valid_num_cols
    feature_cols_cat = valid_cat_cols
    
    # クラス数チェック
    unique_classes = y.unique()
    if len(unique_classes) < 2:
        class_counts = y.value_counts().to_dict()
        raise RuntimeError(
            f"学習データに2つ以上のクラスが必要ですが、{len(unique_classes)}種類しかありません。\n"
            f"クラス分布: {class_counts}\n"
            f"解決方法:\n"
            f"  1. もっと多くのレース結果を取得してください（推奨: 100レース以上）\n"
            f"  2. config.yamlのtarget設定を'place3'に変更してください\n"
            f"  3. 「1_データ取得」ページで追加データを取得してください"
        )

    # Simple time-based split using race_id date prefix (YYYYMMDD)
    # If parsing fails, fallback to random split.
    race_dates = pd.to_datetime(df["race_id"].str.slice(0, 8), format="%Y%m%d", errors="coerce")
    cutoff = race_dates.max() - pd.Timedelta(days=cfg.training.test_split_days) if race_dates.notna().any() else None

    if cutoff is not None and race_dates.notna().any():
        train_idx = race_dates < cutoff
        test_idx = ~train_idx
        if train_idx.sum() < 200 or test_idx.sum() < 50:
            # fallback
            X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=cfg.training.random_seed, stratify=y)
        else:
            X_train, X_test = X.loc[train_idx], X.loc[test_idx]
            y_train, y_test = y.loc[train_idx], y.loc[test_idx]
    else:
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=cfg.training.random_seed, stratify=y)

    numeric = Pipeline(steps=[
        ("imputer", SimpleImputer(strategy="median")),
    ])
    categorical = Pipeline(steps=[
        ("imputer", SimpleImputer(strategy="most_frequent")),
        ("onehot", OneHotEncoder(handle_unknown="ignore")),
    ])

    pre = ColumnTransformer(
        transformers=[
            ("num", numeric, feature_cols_num),
            ("cat", categorical, feature_cols_cat),
        ],
        remainder="drop",
    )

    clf = LogisticRegression(max_iter=2000, n_jobs=None)

    model = Pipeline(steps=[("pre", pre), ("clf", clf)])

    model.fit(X_train, y_train)
    p = model.predict_proba(X_test)[:, 1]

    auc = roc_auc_score(y_test, p) if len(np.unique(y_test)) > 1 else float("nan")
    ll = log_loss(y_test, p)

    # 特徴量重要度の計算（LogisticRegressionの係数から）
    feature_importance = _compute_feature_importance(model, feature_cols_num, feature_cols_cat)

    ts = datetime.now(tz=JST).strftime("%Y%m%d_%H%M%S")
    out_path = cfg.storage.models_dir / f"model_{cfg.training.target}_{ts}.joblib"
    joblib.dump({
        "model": model,
        "feature_cols_num": feature_cols_num,
        "feature_cols_cat": feature_cols_cat,
        "target": cfg.training.target,
        "metrics": {"auc": auc, "logloss": ll},
        "created_at": ts,
        "feature_importance": feature_importance,
    }, out_path)

    print(f"Saved model: {out_path}")
    print(f"Validation AUC={auc:.4f}  logloss={ll:.4f}  (split_days={cfg.training.test_split_days})")
    return out_path

def main(argv: Optional[Sequence[str]] = None) -> None:
    p = argparse.ArgumentParser(description="Train a baseline horse racing model from SQLite data.")
    p.add_argument("--config", default="config.yaml")
    args = p.parse_args(argv)
    train(Path(args.config))

if __name__ == "__main__":
    main()
