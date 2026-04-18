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
from sklearn.metrics import roc_auc_score, log_loss, brier_score_loss
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.isotonic import IsotonicRegression

try:
    import lightgbm as lgb
    _LGBM_AVAILABLE = True
except ImportError:
    _LGBM_AVAILABLE = False
    lgb = None  # type: ignore

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
    if target == "win_tie":
        # 1着 + 同タイム同着馬をすべて正例とする
        # "time" 列が存在しない場合は通常の "win" と同じ挙動にフォールバック
        if "time" not in df.columns:
            return (finish_numeric == 1).astype(int)
        df_tmp = df[["race_id", "time"]].copy()
        df_tmp["_fin"] = finish_numeric
        # 各レースの1着タイムを取得（1着が複数いる場合は最初の一つ）
        winner_time = (
            df_tmp[df_tmp["_fin"] == 1]
            .groupby("race_id")["time"]
            .first()
        )
        df_tmp["_winner_time"] = df_tmp["race_id"].map(winner_time)
        is_winner = finish_numeric == 1
        # タイム文字列が空でない かつ 1着タイムと一致する馬を同着とみなす
        is_tie = (
            df_tmp["time"].notna()
            & df_tmp["_winner_time"].notna()
            & (df_tmp["time"].astype(str) != "")
            & (df_tmp["_winner_time"].astype(str) != "")
            & (df_tmp["time"].astype(str) == df_tmp["_winner_time"].astype(str))
            & finish_numeric.notna()
        )
        return (is_winner | is_tie).astype(int)
    if target == "speed_deviation":
        # 速度偏差（距離×馬場種別グループ内 z-score）
        # speed_index = distance / time_seconds（m/s）をグループ正規化
        ts = pd.to_numeric(df["time_seconds"], errors="coerce")
        dist = pd.to_numeric(df["distance"], errors="coerce")
        spd = dist / ts.replace(0, np.nan)
        if "surface" in df.columns:
            grp = df["distance"].astype(str) + "_" + df["surface"].fillna("unknown").astype(str)
        else:
            grp = df["distance"].astype(str)
        grp_mean = spd.groupby(grp).transform("mean")
        grp_std = spd.groupby(grp).transform("std").replace(0, np.nan)
        return (spd - grp_mean) / grp_std
    if target == "rank":
        # ランキング学習用スコア（1着=最高スコア）
        # LGBMRanker の label_gain に対応した整数スコアに変換
        # 着順 1 → score=N_max, 着順N → score=1, 着順不明 → score=0
        fn = finish_numeric.copy()
        max_rank = fn.max()
        score = (max_rank - fn + 1).clip(lower=0).fillna(0).astype(int)
        return score
    raise ValueError(f"Unknown target: {target}")

def _build_feature_columns(df: pd.DataFrame) -> tuple:
    """FE 後の DataFrame から有効な学習特徴量列を動的に構築する。

    新しい特徴量列が feature_engineering.py で追加された場合、ここに候補として
    追記するだけで自動的にモデルに組み込まれる。df に存在しない列は無視される。

    Returns:
        (num_cols, cat_cols) — 実際に df に存在する列のみ。
    """
    # 数値特徴量の候補リスト（追加順はモデル解釈性のため維持）
    NUM_CANDIDATES = [
        # エントリー基本情報（db_ultimate_loader の実際の列名に合わせる）
        "horse_number", "bracket_number", "age", "horse_weight", "horse_weight_change",
        "odds", "popularity", "burden_weight",
        # コース特性
        "straight_length", "inner_bias", "inner_advantage",
        # レース条件・季節性
        "n_horses", "cos_date", "sin_date", "seasonal_sex", "frame_race_type",
        # 騎手・調教師統計
        "jockey_course_win_rate", "jockey_course_races",
        "jockey_place_rate_top2", "jockey_show_rate", "jockey_recent30_win_rate",
        "fe_trainer_win_rate", "trainer_place_rate_top2", "trainer_show_rate",
        "trainer_recent30_win_rate",
        "jt_combo_win_rate_smooth", "jt_combo_races",
        # 血統統計
        "sire_win_rate", "sire_show_rate", "damsire_win_rate", "damsire_show_rate",
        # 馬の条件別適性
        "horse_distance_win_rate", "horse_distance_avg_finish",
        "horse_surface_win_rate", "horse_surface_races",
        "horse_dist_band_win_rate", "horse_dist_band_races",
        "horse_venue_win_rate", "horse_venue_races",
        "horse_venue_surface_win_rate", "horse_venue_surface_races",
        "horse_dist_surface_win_rate", "horse_dist_surface_races",
        # 馬の近走成績
        "past3_avg_finish", "past5_avg_finish", "past10_avg_finish",
        "past3_win_rate", "past5_win_rate", "horse_win_rate",
        # スピード指標
        "prev_speed_index", "prev_speed_zscore",
        "prev_race_time_seconds", "prev_race_finish", "prev_race_distance",
        "prev_race_weight", "distance_change",
        # 前走からの日数
        "days_since_last_race",
        # オッズ関連
        "implied_prob_norm", "odds_rank_in_race", "odds_z_in_race",
        "market_entropy", "top3_probability",
        # 馬場バイアス
        "gate_win_rate",
        # ラップペース
        "race_pace_diff", "race_pace_ratio", "race_pace_front", "race_pace_back",
        # 上がり3F統計
        "past3_avg_last3f_time", "past5_avg_last3f_time",
        "past3_avg_last3f_rank", "past5_avg_tansho_log",
        # 脚質統計
        "running_style_num", "running_style_mean_5", "running_style_std_5",
        # レースクラス
        "race_class_num",
        # 欠損フラグ
        "prev_race_finish_is_missing", "days_since_last_race_is_missing",
        "prev_speed_index_is_missing", "horse_win_rate_is_missing",
        "odds_is_missing",
        "prev2_race_time_is_missing", "prev2_race_weight_is_missing",
        "prev2_race_distance_is_missing",
        "race_class_num_is_missing",
    ]
    CAT_CANDIDATES = [
        "sex", "jockey_id", "trainer_id",
        "venue_code", "track_type", "corner_radius",
        "running_style",
    ]

    existing = set(df.columns)
    num_cols = [c for c in NUM_CANDIDATES if c in existing]
    cat_cols = [c for c in CAT_CANDIDATES if c in existing]
    return num_cols, cat_cols


def _compute_feature_importance(model: Pipeline, feature_cols_num: list, feature_cols_cat: list) -> pd.DataFrame:
    """LogisticRegression の係数または LightGBM の feature_importances_ から重要度を計算"""
    clf = model.named_steps["clf"]

    # LightGBM の場合
    if hasattr(clf, 'feature_importances_'):
        try:
            pre = model.named_steps["pre"]
            try:
                feat_names = list(pre.get_feature_names_out())
            except AttributeError:
                feat_names = [f"feature_{i}" for i in range(len(clf.feature_importances_))]
            return pd.DataFrame({
                "feature":        feat_names[:len(clf.feature_importances_)],
                "importance":     clf.feature_importances_,
                "abs_coefficient": clf.feature_importances_,
            }).sort_values("importance", ascending=False).reset_index(drop=True)
        except Exception:
            return pd.DataFrame()

    # LogisticRegression の場合
    if not hasattr(clf, 'coef_'):
        return pd.DataFrame()
    coef = clf.coef_[0]
    preprocessor = model.named_steps["pre"]
    feature_names = list(feature_cols_num)
    if len(feature_cols_cat) > 0:
        cat_transformer = preprocessor.named_transformers_["cat"]
        onehot = cat_transformer.named_steps["onehot"]
        for i, col in enumerate(feature_cols_cat):
            for cat in onehot.categories_[i]:
                feature_names.append(f"{col}_{cat}")
    return pd.DataFrame({
        "feature":        feature_names[:len(coef)],
        "coefficient":    coef,
        "abs_coefficient": np.abs(coef),
    }).sort_values("abs_coefficient", ascending=False).reset_index(drop=True)

def _compute_ev_weights(
    X_proc: np.ndarray,
    y: pd.Series,
    entry_odds: pd.Series,
    random_seed: int,
    center: float = 0.0,
    tau: float = 0.5,
) -> np.ndarray:
    """OOF 予測を使った EV ベースの学習重みを計算する（動画: sigmoid 重み）。

    概要:
      1. 簡易 LightGBM で 5-fold OOF 予測確率 (pred_prob) を取得。
      2. net EV = pred_prob × odds − 1 を計算。
      3. weight = sigmoid((EV − center) / tau) + 1 で重み化。
         → EV > center の例（期待値プラスのレース）に 2 倍近くの重みを付与。
      4. 重み平均を 1.0 に正規化。

    Args:
        X_proc:      前処理済み学習特徴量 (ndarray)。
        y:           ターゲット (0/1 Series)。
        entry_odds:  馬のオッズ Series（X_train と同インデックス）。
        random_seed: 乱数シード。
        center:      sigmoid の中心 EV 値（0.0 = 損益分岐点）。
        tau:         sigmoid の幅（小さいほど急峻）。

    Returns:
        shape (n,) の float64 配列。
    """
    from sklearn.model_selection import cross_val_predict
    base_clf = lgb.LGBMClassifier(
        n_estimators=200, learning_rate=0.05, num_leaves=31,
        min_child_samples=10, verbose=-1, random_state=random_seed,
    )
    oof_prob = cross_val_predict(
        base_clf, X_proc, y.values, method='predict_proba', cv=5,
    )[:, 1]

    odds_arr = pd.to_numeric(entry_odds, errors='coerce').fillna(1.0).values
    ev       = oof_prob * odds_arr - 1.0
    weights  = 1.0 / (1.0 + np.exp(-(ev - center) / tau)) + 1.0
    weights  = weights / weights.mean()  # 平均重みを 1.0 に正規化
    return weights.astype(np.float64)


def _build_preprocessor(feature_cols_num: list, feature_cols_cat: list) -> ColumnTransformer:
    """LightGBM・LogisticRegression 共通の前処理パイプラインを構築"""
    numeric = Pipeline(steps=[
        ("imputer", SimpleImputer(strategy="median")),
    ])
    categorical = Pipeline(steps=[
        ("imputer", SimpleImputer(strategy="most_frequent")),
        ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
    ])
    return ColumnTransformer(
        transformers=[
            ("num", numeric, feature_cols_num),
            ("cat", categorical, feature_cols_cat),
        ],
        remainder="drop",
    )


def _train_lightgbm(
    X_train: pd.DataFrame, X_test: pd.DataFrame,
    y_train: pd.Series, y_test: pd.Series,
    feature_cols_num: list, feature_cols_cat: list,
    random_seed: int, early_stopping_rounds: int, num_boost_round: int,
    entry_odds_train: Optional[pd.Series] = None,
    ev_weighted: bool = False,
) -> tuple:
    """動画#14で解説: LightGBM + Early Stopping による学習。

    - Early Stopping: 検証スコアが改善しない場合に自動停止（過学習防止）
    - config.yaml の lgbm_early_stopping_rounds / lgbm_num_boost_round で制御
    - ev_weighted=True の場合、OOF 予測による EV 重み付き学習を実施（動画）

    Returns:
        (model_pipeline, auc, logloss, importance_df)
    """
    pre = _build_preprocessor(feature_cols_num, feature_cols_cat)
    X_train_proc = pre.fit_transform(X_train)
    X_test_proc  = pre.transform(X_test)

    # EV重み付き学習（オプション）
    sample_weight = None
    if ev_weighted and _LGBM_AVAILABLE and entry_odds_train is not None:
        print("  EV 重み付き学習: OOF 予測を計算中...")
        try:
            sample_weight = _compute_ev_weights(
                X_train_proc, y_train, entry_odds_train, random_seed
            )
            print(f"  EV 重み: min={sample_weight.min():.3f}, max={sample_weight.max():.3f}, "
                  f"mean={sample_weight.mean():.3f}")
        except Exception as _e:
            print(f"  ⚠ EV 重み計算に失敗しました ({_e})。均一重みで学習します。")
            sample_weight = None

    lgb_clf = lgb.LGBMClassifier(
        n_estimators=num_boost_round,
        learning_rate=0.05,
        num_leaves=63,
        min_child_samples=20,
        colsample_bytree=0.8,
        subsample=0.8,
        subsample_freq=1,
        reg_alpha=0.1,
        reg_lambda=0.1,
        random_state=random_seed,
        verbose=-1,
    )
    callbacks = [
        lgb.early_stopping(stopping_rounds=early_stopping_rounds, verbose=False),
        lgb.log_evaluation(100),
    ]
    lgb_clf.fit(
        X_train_proc, y_train,
        sample_weight=sample_weight,
        eval_set=[(X_test_proc, y_test)],
        callbacks=callbacks,
    )

    # Pipeline ラッパー（predict 時に pre.transform → lgb_clf.predict_proba のフローを確保）
    model = Pipeline(steps=[("pre", pre), ("clf", lgb_clf)])

    p   = lgb_clf.predict_proba(X_test_proc)[:, 1]
    auc = roc_auc_score(y_test, p) if len(np.unique(y_test)) > 1 else float("nan")
    ll  = log_loss(y_test, p)

    # 特徴量重要度
    try:
        feat_names = list(pre.get_feature_names_out())
    except AttributeError:
        feat_names = [f"feat_{i}" for i in range(len(lgb_clf.feature_importances_))]
    imp = pd.DataFrame({
        "feature":        feat_names[:len(lgb_clf.feature_importances_)],
        "importance":     lgb_clf.feature_importances_,
        "abs_coefficient": lgb_clf.feature_importances_,
    }).sort_values("importance", ascending=False).reset_index(drop=True)

    return model, auc, ll, imp


def _train_lightgbm_ranker(
    X_train: pd.DataFrame, X_test: pd.DataFrame,
    y_train: pd.Series, y_test: pd.Series,
    group_train: "list[int]", group_test: "list[int]",
    feature_cols_num: list, feature_cols_cat: list,
    random_seed: int, early_stopping_rounds: int, num_boost_round: int,
) -> tuple:
    """LambdaRank（ランキング学習）による学習。

    LightGBM の lambdarank objective を使い、レース内相対順位を直接最適化する。
    - group_train / group_test: レースごとの馬数リスト [16, 18, 12, ...]
    - y_train: ランキングスコア（高い=上位）。_make_target(df, "rank") の出力を使う
    - 返り値の auc は Spearman 相関（NDCG の代替指標）

    Returns:
        (preprocessor, booster, spearman_corr, importance_df)
        ※ preprocessor と booster を別々に返す（Pipeline での group パラメータ渡し非対応のため）
    """
    from scipy.stats import spearmanr as _spearmanr
    pre = _build_preprocessor(feature_cols_num, feature_cols_cat)
    X_train_proc = pre.fit_transform(X_train)
    X_test_proc  = pre.transform(X_test)

    train_data = lgb.Dataset(X_train_proc, label=y_train.values, group=group_train)
    valid_data = lgb.Dataset(X_test_proc,  label=y_test.values,  group=group_test)

    params = {
        "objective":       "lambdarank",
        "metric":          "ndcg",
        "ndcg_eval_at":    [1, 3, 5],
        "label_gain":      list(range(int(y_train.max()) + 2)),
        "num_leaves":      63,
        "learning_rate":   0.05,
        "min_data_in_leaf": 20,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq":    1,
        "reg_alpha":       0.1,
        "reg_lambda":      0.1,
        "verbose":         -1,
        "seed":            random_seed,
    }
    callbacks = [
        lgb.early_stopping(stopping_rounds=early_stopping_rounds, verbose=False),
        lgb.log_evaluation(100),
    ]
    booster = lgb.train(
        params, train_data,
        num_boost_round=num_boost_round,
        valid_sets=[valid_data],
        callbacks=callbacks,
    )

    # 評価: Spearman 相関
    scores = booster.predict(X_test_proc)
    sp, _ = _spearmanr(y_test.values, scores)
    sp = float(sp) if not np.isnan(sp) else 0.0

    # 特徴量重要度
    try:
        feat_names = list(pre.get_feature_names_out())
    except AttributeError:
        feat_names = [f"feat_{i}" for i in range(booster.num_feature())]
    imp_vals = booster.feature_importance(importance_type="gain")
    imp = pd.DataFrame({
        "feature":         feat_names[:len(imp_vals)],
        "importance":      imp_vals,
        "abs_coefficient": imp_vals,
    }).sort_values("importance", ascending=False).reset_index(drop=True)

    return pre, booster, sp, imp


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

    # 特徴量列を動的に構築（候補リストは _build_feature_columns を参照）
    feature_cols_num, feature_cols_cat = _build_feature_columns(df)

    X = df[feature_cols_num + feature_cols_cat].copy()
    y = _make_target(df, cfg.training.target)
    
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

    # ── モデル種別の学習（動画#14: LightGBM + Early Stopping）──────────────
    model_type = getattr(cfg.training, 'model_type', 'logistic')
    if model_type == 'lightgbm' and not _LGBM_AVAILABLE:
        print("\u26a0 LightGBM が未インストール。LogisticRegression にフォールバックします")
        model_type = 'logistic'

    if model_type == 'lightgbm':
        print("\n" + "=" * 60)
        print("【モデル学習】LightGBM + Early Stopping")
        print(f"  Early Stopping: {cfg.training.lgbm_early_stopping_rounds} ラウンド")
        print(f"  最大ブースト回数: {cfg.training.lgbm_num_boost_round}")
        print("=" * 60)
        model, auc, ll, feature_importance = _train_lightgbm(
            X_train, X_test, y_train, y_test,
            feature_cols_num, feature_cols_cat,
            random_seed=cfg.training.random_seed,
            early_stopping_rounds=cfg.training.lgbm_early_stopping_rounds,
            num_boost_round=cfg.training.lgbm_num_boost_round,
            entry_odds_train=df.loc[X_train.index, 'entry_odds'] if 'entry_odds' in df.columns else None,
            ev_weighted=getattr(cfg.training, 'ev_weighted', False),
        )
    elif model_type == 'lambdarank':
        print("\n" + "=" * 60)
        print("【モデル学習】LambdaRank（ランキング学習）")
        print(f"  Early Stopping: {cfg.training.lgbm_early_stopping_rounds} ラウンド")
        print(f"  最大ブースト回数: {cfg.training.lgbm_num_boost_round}")
        print("=" * 60)
        # race_id ごとの馬数リストを作成（group パラメータ）
        _tr_df = df.loc[X_train.index]
        _te_df = df.loc[X_test.index]
        group_train = _tr_df.groupby("race_id", sort=False).size().values.tolist()
        group_test  = _te_df.groupby("race_id", sort=False).size().values.tolist()
        pre_ranker, booster, auc, feature_importance = _train_lightgbm_ranker(
            X_train, X_test, y_train, y_test,
            group_train, group_test,
            feature_cols_num, feature_cols_cat,
            random_seed=cfg.training.random_seed,
            early_stopping_rounds=cfg.training.lgbm_early_stopping_rounds,
            num_boost_round=cfg.training.lgbm_num_boost_round,
        )
        ll = 0.0  # LambdaRank は logloss ではなく NDCG / Spearman で評価
        model = {"pre": pre_ranker, "booster": booster, "_is_ranker": True}
    else:
        print("\n【モデル学習】LogisticRegression")
        pre = ColumnTransformer(
            transformers=[
                ("num",
                 Pipeline(steps=[("imputer", SimpleImputer(strategy="median"))]),
                 feature_cols_num),
                ("cat",
                 Pipeline(steps=[
                     ("imputer", SimpleImputer(strategy="most_frequent")),
                     ("onehot", OneHotEncoder(handle_unknown="ignore")),
                 ]),
                 feature_cols_cat),
            ],
            remainder="drop",
        )
        clf   = LogisticRegression(max_iter=2000, n_jobs=None)
        model = Pipeline(steps=[("pre", pre), ("clf", clf)])
        model.fit(X_train, y_train)
        p   = model.predict_proba(X_test)[:, 1]
        auc = roc_auc_score(y_test, p) if len(np.unique(y_test)) > 1 else float("nan")
        ll  = log_loss(y_test, p)
        feature_importance = _compute_feature_importance(model, feature_cols_num, feature_cols_cat)

    # ===== 動画#7: キャリブレーション（アイソトニック回帰）=====
    # 機械学習モデルの出力スコアは「傾向スコア」であり確率とは限らない。
    # IsotonicRegression を用いてテストセット上で確率の校正を行い、
    # 期待値計算（p_umaren × odds 等）の精度を向上させる。
    # predict.py は bundle["calibrator"] を自動適用するため保存のみでOK。
    calibrator = None
    try:
        p_test = model.predict_proba(X_test)[:, 1]
        calibrator = IsotonicRegression(out_of_bounds='clip')
        calibrator.fit(p_test, y_test.values)
        p_cal = calibrator.predict(p_test)
        brier_before = brier_score_loss(y_test, p_test)
        brier_after  = brier_score_loss(y_test, p_cal)
        print(f"  Brier score  before={brier_before:.5f}  after={brier_after:.5f}")
    except Exception as _ce:
        print(f"  ⚠ キャリブレーション失敗 ({_ce})、スキップします")
        calibrator = None

    ts = datetime.now(tz=JST).strftime("%Y%m%d_%H%M%S")
    out_path = cfg.storage.models_dir / f"model_{cfg.training.target}_{ts}.joblib"

    if model_type == 'lambdarank' and isinstance(model, dict) and model.get("_is_ranker"):
        # LambdaRank はプリプロセッサと booster を分割保存
        joblib.dump({
            "model":            model["booster"],   # lgb.Booster (predict() でスコアを返す)
            "pre":              model["pre"],        # ColumnTransformer
            "feature_cols_num": feature_cols_num,
            "feature_cols_cat": feature_cols_cat,
            "target":           cfg.training.target,  # "rank"
            "model_type":       model_type,
            "metrics":          {"spearman": auc, "logloss": ll},
            "created_at":       ts,
            "feature_importance": feature_importance,
            "calibrator":       None,
            "_is_ranker":       True,
        }, out_path)
    else:
        joblib.dump({
            "model": model,
            "feature_cols_num": feature_cols_num,
            "feature_cols_cat": feature_cols_cat,
            "target": cfg.training.target,
            "model_type": model_type,
            "metrics": {"auc": auc, "logloss": ll},
            "created_at": ts,
            "feature_importance": feature_importance,
            "calibrator": calibrator,
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
