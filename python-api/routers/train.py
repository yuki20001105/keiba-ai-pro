"""
学習エンドポイント
POST /api/train
POST /api/train/start
GET  /api/train/status/{job_id}
"""
from __future__ import annotations

import asyncio
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import List

import joblib
import numpy as np
from fastapi import APIRouter, Depends, HTTPException

from app_config import (  # type: ignore
    SUPABASE_DATA_ENABLED,
    CONFIG_PATH,
    MODELS_DIR,
    ULTIMATE_DB,
    get_supabase_client,
    logger,
)
from deps.auth import require_premium  # type: ignore
from models import TrainRequest, TrainResponse  # type: ignore
from keiba_ai.constants import FUTURE_FIELDS  # type: ignore
from keiba_ai.feature_catalog import FeatureCatalog  # type: ignore
from scraping.jobs import _purge_old_jobs, _MAX_JOBS  # type: ignore

router = APIRouter()


# ---------------------------------------------------------------------------
# BetaCalibration ラッパー（モジュールレベルで定義 → joblib pickle 対応）
# ---------------------------------------------------------------------------
class BCWrap:
    """BetaCalibration を 1D 入力対応にラップ（既存 predict.py との互換性維持）"""

    def __init__(self, c: object) -> None:
        self._c = c

    def predict(self, x: "np.ndarray") -> "np.ndarray":
        return self._c.predict(np.asarray(x, float).reshape(-1, 1)).ravel()



# ジョブストア（インメモリ）
_train_jobs: dict = {}


def _extract_ym_from_df(df: "pd.DataFrame") -> list:  # noqa: F821
    # race_date (YYYYMMDD) が存在すれば先頭6桁 (YYYYMM) を使う。
    # race_id は YYYYVVKKNNRR 形式で venueCode が混入するため使用しない。
    if df.empty:
        return []
    if "race_date" in df.columns:
        yms = df["race_date"].dropna().astype(str).str[:6]
        valid = yms[yms.str.match(r"^\d{6}$")]
        if not valid.empty:
            return valid.tolist()
    return []


def _get_actual_date_from(df: "pd.DataFrame", fallback: "str | None") -> "str | None":  # noqa: F821
    yms = _extract_ym_from_df(df)
    if yms:
        ym = min(yms)
        return f"{ym[:4]}-{ym[4:6]}"
    return fallback


def _get_actual_date_to(df: "pd.DataFrame", fallback: "str | None") -> "str | None":  # noqa: F821
    yms = _extract_ym_from_df(df)
    if yms:
        ym = max(yms)
        return f"{ym[:4]}-{ym[4:6]}"
    return fallback


def _get_date8_from(df: "pd.DataFrame") -> str:  # noqa: F821
    """学習データ最小日付をYYYYMMDD形式で返す（race_dateカラム優先）"""
    if "race_date" in df.columns:
        dates = df["race_date"].dropna().astype(str).str.strip()
        valid = dates[dates.str.match(r"^\d{8}$")]
        if len(valid) > 0:
            return valid.min()
    # fallback: YYYYMM from race_id → YYYYMM01
    yms = _extract_ym_from_df(df)
    if yms:
        return min(yms) + "01"
    return datetime.now().strftime("%Y%m%d")


def _get_date8_to(df: "pd.DataFrame") -> str:  # noqa: F821
    """学習データ最大日付をYYYYMMDD形式で返す（race_dateカラム優先）"""
    if "race_date" in df.columns:
        dates = df["race_date"].dropna().astype(str).str.strip()
        valid = dates[dates.str.match(r"^\d{8}$")]
        if len(valid) > 0:
            return valid.max()
    yms = _extract_ym_from_df(df)
    if yms:
        return max(yms) + "28"
    return datetime.now().strftime("%Y%m%d")


# レース後確定フィールド（keiba_ai.constants.FUTURE_FIELDS を参照）


async def _do_train(request: TrainRequest, current_user: dict, progress_cb=None) -> TrainResponse:
    """モデル学習内部実装（progress_cb は任意のコールバック = (msg: str, pct: int | None) -> None）"""
    if progress_cb is None:
        def progress_cb(msg: str, pct: int = None): pass  # noqa: F811
    try:
        import pandas as pd
        from keiba_ai.db_ultimate_loader import load_ultimate_training_frame  # type: ignore
        from keiba_ai.feature_engineering import add_derived_features  # type: ignore
        from keiba_ai.lightgbm_feature_optimizer import (  # type: ignore
            prepare_for_lightgbm_ultimate,
        )
        from keiba_ai.optuna_optimizer import OptunaLightGBMOptimizer  # type: ignore

        # Phase 0: 87特徴量モード固定（入力値に関わらず常に ultimate LightGBM）
        request = request.model_copy(update={
            "ultimate_mode": True,
            "use_optimizer": True,
            "model_type": "lightgbm",
        })

        start_time = datetime.now()
        optuna_executed = False
        optuna_error = None

        print("\n" + "=" * 70)
        print("【学習リクエスト受信】")
        print("=" * 70)
        print(f"  target: {request.target}")
        print(f"  model_type: {request.model_type}")
        print(f"  use_optimizer: {request.use_optimizer}")
        print(f"  use_optuna: {request.use_optuna}")
        print(f"  optuna_trials: {request.optuna_trials}")
        print(f"  cv_folds: {request.cv_folds}")
        print("=" * 70 + "\n")

        # 常に ultimate DB を使用（87特徴量モード固定）
        db_path = ULTIMATE_DB

        progress_cb("データベース接続中...", 3)

        # Supabase → SQLite 同期（ブロッキング呼び出しを to_thread で分離）
        if SUPABASE_DATA_ENABLED and get_supabase_client():
            from app_config import sync_supabase_to_sqlite  # type: ignore
            if request.force_sync:
                logger.info("Supabase からデータを同期中...")
                db_path.parent.mkdir(parents=True, exist_ok=True)
                synced = await asyncio.to_thread(sync_supabase_to_sqlite, db_path)
                logger.info(f"同期完了: {synced} レース")
            else:
                logger.info("force_sync=False: Supabase同期スキップ")
                if not db_path.exists():
                    db_path.parent.mkdir(parents=True, exist_ok=True)
                    synced = await asyncio.to_thread(sync_supabase_to_sqlite, db_path)
                    logger.info(f"初回同期: {synced} レース")

        # データ読み辿み（常に ultimate モード）
        progress_cb("学習データ読み込み中...", 8)
        df = load_ultimate_training_frame(db_path)

        print(f"DEBUG: Loaded {len(df)} rows from database")
        progress_cb(f"データ読み込み完了 ({len(df):,} 行)", 15)

        # 学習期間フィルタ
        # race_date(YYYYMMDD, 100%充填)を優先。一致しない場合は race_id[:6] にフォールバック
        if request.training_date_from or request.training_date_to:
            if "race_date" in df.columns:
                _date_ym = df["race_date"].astype(str).str.strip().str[:6]  # YYYYMMDD → YYYYMM
                if request.training_date_from:
                    from_ym = request.training_date_from.replace("-", "")
                    df = df[_date_ym >= from_ym]
                    _date_ym = df["race_date"].astype(str).str.strip().str[:6]
                if request.training_date_to:
                    to_ym = request.training_date_to.replace("-", "")
                    df = df[_date_ym <= to_ym]
            elif "race_id" in df.columns:
                df["_race_ym"] = df["race_id"].astype(str).str[:6]
                if request.training_date_from:
                    from_ym = request.training_date_from.replace("-", "")
                    df = df[df["_race_ym"] >= from_ym]
                if request.training_date_to:
                    to_ym = request.training_date_to.replace("-", "")
                    df = df[df["_race_ym"] <= to_ym]
                df = df.drop(columns=["_race_ym"])

        if df.empty:
            raise HTTPException(
                status_code=400,
                detail=f"訓練データが見つかりません。DB: {db_path}",
            )

        # ターゲット変数を先に取得（FUTURE_FIELDSのdrop前にfinish列が必要）
        from keiba_ai.train import _make_target  # type: ignore
        y = _make_target(df, request.target)

        if request.target != "speed_deviation" and len(y.unique()) < 2:
            raise HTTPException(status_code=400, detail="2クラス以上が必要です")
        # 特徴量エンジニアリング（finish等を使うのでdrop前に実施）
        progress_cb("特徴量エンジニアリング中...", 20)
        df = add_derived_features(df, full_history_df=df)
        # NOTE: UltimateFeatureCalculator は feature_engineering.py で同等の特徴量を
        # ベクトル化计算済みのため除去（zero-variance 問題も解消）
        df = df.loc[:, ~df.columns.duplicated()]

        # レース後フィールド除去（特徴量エンジニアリング後・学習前に実施）
        drop_train = [c for c in FUTURE_FIELDS if c in df.columns]
        if drop_train:
            df = df.drop(columns=drop_train)

        _all_feature_columns: List[str] = []
        optimizer = None
        categorical_features = []
        feature_count = 0
        valid_num_cols = []
        valid_cat_cols = []
        model = None
        auc = logloss = cv_auc_mean = cv_auc_std = 0.0

        if request.use_optimizer and request.model_type == "lightgbm":
            try:
                print("\n=== LightGBM最適化モード ===")
                progress_cb("特徴量選択・最適化中...", 28)
                df_optimized, optimizer, categorical_features = prepare_for_lightgbm_ultimate(
                    df, target_col=request.target, is_training=True
                )
                exclude_cols = [
                    request.target, "race_id", "horse_id", "jockey_id",
                    "trainer_id", "owner_id", "finish_position",
                ]
                X = df_optimized.drop([c for c in exclude_cols if c in df_optimized.columns], axis=1)
                obj_cols = X.select_dtypes(include=["object"]).columns.tolist()
                if obj_cols:
                    X = X.drop(columns=obj_cols)
                feature_count = len(X.columns)
                _all_feature_columns = X.columns.tolist()

                from sklearn.model_selection import train_test_split
                from sklearn.metrics import roc_auc_score, log_loss
                import lightgbm as lgb

                # 時系列分割（ランダム分割より汎化性能検証に適切）
                X = X.reset_index(drop=True)
                y = y.reset_index(drop=True)
                _time_split = False
                if "race_date" in df.columns:
                    _dates = pd.to_datetime(
                        df["race_date"].reset_index(drop=True).astype(str).str[:8],
                        format="%Y%m%d", errors="coerce",
                    )
                    _cutoff = _dates.quantile(1.0 - request.test_size)
                    _tr_mask = (_dates <= _cutoff).values
                    _te_mask = ~_tr_mask
                    if int(_tr_mask.sum()) >= 200 and int(_te_mask.sum()) >= 50:
                        X_train = X.loc[_tr_mask]
                        X_test = X.loc[_te_mask]
                        y_train = y.loc[_tr_mask]
                        y_test = y.loc[_te_mask]
                        _time_split = True
                        logger.info(f"時系列分割: 学習 {_tr_mask.sum()}行, テスト {_te_mask.sum()}行")
                _is_regression = request.target == "speed_deviation"
                _is_ranking    = request.target == "rank"
                if not _time_split:
                    if _is_regression:
                        X_train, X_test, y_train, y_test = train_test_split(
                            X, y, test_size=request.test_size, random_state=42
                        )
                    elif _is_ranking:
                        X_train, X_test, y_train, y_test = train_test_split(
                            X, y, test_size=request.test_size, random_state=42
                        )
                    else:
                        X_train, X_test, y_train, y_test = train_test_split(
                            X, y, test_size=request.test_size, random_state=42, stratify=y
                        )
                # speed_deviation: NaN行を除外（タイム欠損馬）
                if _is_regression:
                    _valid_train = y_train.notna()
                    _valid_test  = y_test.notna()
                    X_train, y_train = X_train.loc[_valid_train], y_train.loc[_valid_train]
                    X_test,  y_test  = X_test.loc[_valid_test],   y_test.loc[_valid_test]

                categorical_indices = [X.columns.get_loc(c) for c in categorical_features if c in X.columns]

                if _is_ranking:
                    # ─── LambdaRank パス ─────────────────────────────────────
                    # race_id ごとのグループサイズを計算（train/test 分割後の df を参照）
                    _df_for_groups = df.reset_index(drop=True)
                    _y_full = y.reset_index(drop=True)
                    _tr_idx = X_train.index if hasattr(X_train, 'index') else range(len(X_train))
                    _te_idx = X_test.index  if hasattr(X_test,  'index') else range(len(X_test))
                    _race_ids_full = df_optimized["race_id"].reset_index(drop=True) if "race_id" in df_optimized.columns else pd.Series(["0"] * len(df_optimized))
                    _race_ids_tr   = _race_ids_full.loc[list(_tr_idx)].values
                    _race_ids_te   = _race_ids_full.loc[list(_te_idx)].values
                    from collections import Counter as _Counter
                    _ctr_tr = list(_Counter(_race_ids_tr).values())
                    _ctr_te = list(_Counter(_race_ids_te).values())
                    _max_label = int(y_train.max()) if len(y_train) > 0 else 18
                    train_data = lgb.Dataset(
                        X_train.values, label=y_train.values,
                        group=_ctr_tr,
                    )
                    valid_data = lgb.Dataset(
                        X_test.values, label=y_test.values,
                        group=_ctr_te,
                    )
                    params = {
                        "objective":        "lambdarank",
                        "metric":           "ndcg",
                        "ndcg_eval_at":     [1, 3, 5],
                        "label_gain":       list(range(_max_label + 2)),
                        "num_leaves":       31,
                        "min_data_in_leaf": 20,
                        "feature_fraction": 0.8,
                        "bagging_fraction": 0.8,
                        "bagging_freq":     5,
                        "reg_alpha":        0.1,
                        "reg_lambda":       0.1,
                        "verbose":          -1,
                        "seed":             42,
                    }
                    model = lgb.train(
                        params, train_data,
                        num_boost_round=500,
                        valid_sets=[valid_data],
                        callbacks=[
                            lgb.early_stopping(stopping_rounds=50, verbose=False),
                            lgb.log_evaluation(100),
                        ],
                    )
                    _scores = model.predict(X_test.values)
                    from scipy.stats import spearmanr as _spr
                    import numpy as _np_loc_r
                    _sp, _ = _spr(y_test.values, _scores)
                    auc = float(_sp) if not _np_loc_r.isnan(_sp) else 0.0
                    logloss = 0.0
                    cv_auc_mean = auc
                    cv_auc_std  = 0.0
                    best_round_cv = model.num_trees()
                    best_round_final = best_round_cv
                    y_pred_proba = _scores
                    _is_ranker_model = True
                else:
                    _is_ranker_model = False
                    train_data = lgb.Dataset(X_train, y_train, categorical_feature=categorical_indices)

                if _is_ranking:
                    pass  # 上のブロックで学習完了
                elif _is_regression:
                    params = {
                        "objective": "regression", "metric": "rmse",
                        "max_cat_to_onehot": 4, "learning_rate": 0.05,
                        "num_leaves": 31, "min_data_in_leaf": 20,
                        "feature_fraction": 0.8, "bagging_fraction": 0.8,
                        "bagging_freq": 5, "reg_alpha": 0.1, "reg_lambda": 0.1,
                        "verbose": -1, "random_state": 42,
                    }
                else:
                    params = {
                        "objective": "binary", "metric": "binary_logloss",
                        "max_cat_to_onehot": 4, "learning_rate": 0.05,
                        "num_leaves": 31, "min_data_in_leaf": 20,
                        "feature_fraction": 0.8, "bagging_fraction": 0.8,
                        "bagging_freq": 5, "reg_alpha": 0.1, "reg_lambda": 0.1,
                        "verbose": -1, "random_state": 42,
                    }

                if not _is_ranking:
                    # CV で最適ラウンド探索
                    progress_cb(f"CV学習中 ({request.cv_folds}折)...", 35)
                    _cv_total_rounds = 1000
                    def _cv_lgb_cb(env,
                                   _pcb=progress_cb,
                                   _folds=request.cv_folds,
                                   _tot=_cv_total_rounds):
                        if env.iteration % 20 != 0:
                            return
                        pct = 35 + int(22 * env.iteration / _tot)
                        _pcb(f"CV {_folds}折 — ラウンド {env.iteration}/{_tot}", min(pct, 56))
                    _cv_lgb_cb.order = 100
                    cv_result = lgb.cv(
                        params, train_data,
                        num_boost_round=1000, nfold=request.cv_folds,
                        stratified=(not _is_regression), return_cvbooster=True,
                        callbacks=[
                            lgb.early_stopping(stopping_rounds=50, verbose=False),
                            lgb.log_evaluation(period=0),
                            _cv_lgb_cb,
                        ],
                    )
                    if _is_regression:
                        _cv_rmse_mean = cv_result["valid rmse-mean"][-1]
                        _cv_rmse_std  = cv_result["valid rmse-stdv"][-1]
                        best_round_cv = len(cv_result["valid rmse-mean"])
                        cv_auc_mean = 1.0 - _cv_rmse_mean
                        cv_auc_std  = _cv_rmse_std
                    else:
                        cv_logloss_mean = cv_result["valid binary_logloss-mean"][-1]
                        cv_logloss_std  = cv_result["valid binary_logloss-stdv"][-1]
                        best_round_cv = len(cv_result["valid binary_logloss-mean"])
                        cv_auc_mean = 1.0 - cv_logloss_mean
                        cv_auc_std  = cv_logloss_std
                    best_round_final = int(best_round_cv * request.cv_folds / (request.cv_folds - 1))

                    progress_cb("最終モデル学習中...", 60)
                    full_train_data = lgb.Dataset(X_train, y_train, categorical_feature=categorical_indices)
                    _final_rounds = best_round_final
                    def _final_lgb_cb(env,
                                      _pcb=progress_cb,
                                      _tot=_final_rounds):
                        if env.iteration % 20 != 0:
                            return
                        pct = 60 + int(5 * env.iteration / max(_tot, 1))
                        _pcb(f"最終モデル — ラウンド {env.iteration}/{_tot}", min(pct, 64))
                    _final_lgb_cb.order = 100
                    model = lgb.train(params, full_train_data, num_boost_round=best_round_final,
                                      callbacks=[_final_lgb_cb])

                    y_pred_proba = model.predict(X_test)
                    if _is_regression:
                        from scipy.stats import spearmanr as _spearmanr
                        import numpy as _np_loc
                        _sp, _ = _spearmanr(y_test, y_pred_proba)
                        auc = float(_sp) if not _np_loc.isnan(_sp) else 0.0
                        logloss = float(_np_loc.sqrt(_np_loc.nanmean((y_test.values - y_pred_proba) ** 2)))
                        cv_auc_mean = auc
                    else:
                        auc = roc_auc_score(y_test, y_pred_proba)
                        logloss = log_loss(y_test, y_pred_proba)

            except Exception as e:
                traceback.print_exc()
                logger.error(f"LightGBM処理エラー:\n{traceback.format_exc()}")
                raise

            # Optuna
            if request.use_optuna:
                optuna_executed = True
                try:
                    print("\n=== Optunaハイパーパラメータ最適化 ===")
                    progress_cb(f"Optuna最適化中 ({request.optuna_trials}試行)...", 65)
                    if request.model_type == "lightgbm":
                        categorical_indices = [X.columns.get_loc(c) for c in categorical_features if c in X.columns]
                        optuna_optimizer = OptunaLightGBMOptimizer(
                            n_trials=request.optuna_trials, cv_folds=request.cv_folds,
                            random_state=42, timeout=300,
                        )
                        best_params, best_optuna_score = optuna_optimizer.optimize(
                            X, y, categorical_features=categorical_indices,
                        )
                        optimized_params = optuna_optimizer.get_best_model_params()
                        optuna_num_rounds = optimized_params.pop("n_estimators", 1000)
                        train_data_opt = lgb.Dataset(X_train, y_train, categorical_feature=categorical_indices)
                        _opt_num_rounds = optuna_num_rounds
                        def _opt_cv_lgb_cb(env,
                                           _pcb=progress_cb,
                                           _folds=request.cv_folds,
                                           _tot=_opt_num_rounds):
                            if env.iteration % 20 != 0:
                                return
                            pct = 67 + int(12 * env.iteration / max(_tot, 1))
                            _pcb(f"Optuna CV {_folds}折 — ラウンド {env.iteration}/{_tot}", min(pct, 78))
                        _opt_cv_lgb_cb.order = 100
                        cv_result_opt = lgb.cv(
                            optimized_params, train_data_opt,
                            num_boost_round=optuna_num_rounds, nfold=request.cv_folds,
                            stratified=(not _is_regression),
                            callbacks=[
                                lgb.early_stopping(stopping_rounds=50, verbose=False),
                                lgb.log_evaluation(period=0),
                                _opt_cv_lgb_cb,
                            ],
                        )
                        # どちらの評価指標が使われていても対応できるようにフォールバック
                        _logloss_key = "valid binary_logloss-mean"
                        _rmse_key    = "valid rmse-mean"
                        _auc_key     = "valid auc-mean"
                        if _is_regression and _rmse_key in cv_result_opt:
                            cv_auc_std  = cv_result_opt["valid rmse-stdv"][-1]
                            best_round_opt = len(cv_result_opt[_rmse_key])
                        elif _logloss_key in cv_result_opt:
                            cv_auc_mean = 1.0 - cv_result_opt[_logloss_key][-1]
                            cv_auc_std = cv_result_opt["valid binary_logloss-stdv"][-1]
                            best_round_opt = len(cv_result_opt[_logloss_key])
                        elif _auc_key in cv_result_opt:
                            cv_auc_mean = cv_result_opt[_auc_key][-1]
                            cv_auc_std = cv_result_opt["valid auc-stdv"][-1]
                            best_round_opt = len(cv_result_opt[_auc_key])
                        else:
                            cv_auc_mean = 0.0
                            cv_auc_std = 0.0
                            best_round_opt = optuna_num_rounds
                        best_round_opt_final = int(best_round_opt * request.cv_folds / (request.cv_folds - 1))
                        progress_cb("Optunaパラメータでモデル再学習中...", 82)
                        _opt_final_rounds = best_round_opt_final
                        def _opt_final_lgb_cb(env,
                                              _pcb=progress_cb,
                                              _tot=_opt_final_rounds):
                            if env.iteration % 20 != 0:
                                return
                            pct = 82 + int(5 * env.iteration / max(_tot, 1))
                            _pcb(f"Optuna最終モデル — ラウンド {env.iteration}/{_tot}", min(pct, 86))
                        _opt_final_lgb_cb.order = 100
                        model = lgb.train(optimized_params, train_data_opt, num_boost_round=best_round_opt_final,
                                          callbacks=[_opt_final_lgb_cb])
                        y_pred_proba = model.predict(X_test)
                        if _is_regression:
                            from scipy.stats import spearmanr as _spearmanr2
                            import numpy as _np_loc2
                            _sp2, _ = _spearmanr2(y_test, y_pred_proba)
                            auc = float(_sp2) if not _np_loc2.isnan(_sp2) else 0.0
                            logloss = float(_np_loc2.sqrt(_np_loc2.nanmean((y_test.values - y_pred_proba) ** 2)))
                            cv_auc_mean = auc
                        else:
                            auc = roc_auc_score(y_test, y_pred_proba)
                            logloss = log_loss(y_test, y_pred_proba)
                    else:
                        from keiba_ai.optuna_optimizer import optimize_model  # type: ignore
                        model_type_map = {
                            "logistic_regression": "logistic",
                            "random_forest": "random_forest",
                            "gradient_boosting": "gradient_boosting",
                        }
                        optimize_model(
                            model_type_map[request.model_type], X.values, y.values,
                            n_trials=request.optuna_trials, timeout=300,
                        )
                except Exception as e:
                    optuna_error = f"{type(e).__name__}: {str(e)}"
                    print(f"❌ Optuna最適化エラー: {optuna_error}")
                    traceback.print_exc()

        else:
            # Phase 0: 標準モード削除。use_optimizer=True/model_type='lightgbm' のみサポート。
            raise HTTPException(
                status_code=400,
                detail="use_optimizer=True / model_type='lightgbm' のみサポートされています（87特徴量モード）",
            )

        # 確率キャリブレーション（BetaCalibration優先、IsotonicRegressionにフォールバック）
        # speed_deviation（回帰）/ rank（ランキング）の場合はキャリブレーション不要
        progress_cb("確率キャリブレーション中...", 88)
        calibrator = None
        logloss_calibrated = logloss
        if request.target not in ("speed_deviation", "rank"):
            try:
                from sklearn.isotonic import IsotonicRegression as _IR
                _ir = _IR(out_of_bounds="clip")
                _ir.fit(y_pred_proba, y_test.values)
                calibrator = _ir
                # betacal が利用可能なら BetaCalibration で置き換える（より良い確率推定）
                try:
                    from betacal import BetaCalibration as _BC  # type: ignore

                    _bc = _BC(parameters="abm")
                    _bc.fit(y_pred_proba.reshape(-1, 1), y_test.values)
                    calibrator = BCWrap(_bc)
                    logger.info("BetaCalibration を使用してキャリブレーション学習完了")
                except ImportError:
                    logger.info("betacal 未インストール。IsotonicRegression でキャリブレーション学習完了")
                # キャリブレーション後の logloss を計算
                from sklearn.metrics import log_loss as _ll_fn
                _y_cal = calibrator.predict(y_pred_proba)
                logloss_calibrated = float(_ll_fn(y_test, _y_cal))
            except Exception as _cal_err:
                logger.warning(f"キャリブレーション学習失敗: {_cal_err}")

        # モデル保存 — IDはデータ日付範囲 + 作成日時（一意性を保証）
        progress_cb("モデルを保存中...", 93)
        date_from_8 = _get_date8_from(df)
        date_to_8 = _get_date8_to(df)
        saved_at = datetime.now().strftime("%Y%m%d_%H%M")
        model_id = f"{date_from_8}_{date_to_8}_{saved_at}"
        model_filename = f"model_{request.target}_{request.model_type}_{model_id}.joblib"
        model_path = MODELS_DIR / model_filename

        bundle = {
            "model": model,
            "calibrator": calibrator,
            "optimizer": optimizer if request.use_optimizer else None,
            "categorical_features": categorical_features,
            "feature_cols_num": valid_num_cols if not request.use_optimizer else None,
            "feature_cols_cat": valid_cat_cols if not request.use_optimizer else None,
            "feature_columns": _all_feature_columns,
            "target": request.target,
            "model_type": "lightgbm",
            "ultimate_mode": True,
            "use_optimizer": True,
            "pipeline_config": {
                "use_feature_engineering": True,
                "use_optimizer": request.use_optimizer,
                "optimizer_type": type(optimizer).__name__ if optimizer is not None else None,
                "requires_full_history": True,
                "feature_engineering_hash": __import__("hashlib").md5(
                    __import__("inspect").getsource(
                        __import__("keiba_ai.feature_engineering", fromlist=["add_derived_features"])
                    ).encode()
                ).hexdigest()[:12],
            },
            "metrics": {
                "auc": float(auc), "logloss": float(logloss),
                "logloss_calibrated": float(logloss_calibrated),
                "cv_auc_mean": float(cv_auc_mean), "cv_auc_std": float(cv_auc_std),
            },
            "data_count": len(df),
            "race_count": df["race_id"].nunique() if "race_id" in df.columns else 0,
            "created_at": saved_at,
            "training_date_from": _get_actual_date_from(df, request.training_date_from),
            "training_date_to": _get_actual_date_to(df, request.training_date_to),
        }
        # LambdaRank はランカー固有フラグを保存
        if request.target == "rank" and locals().get("_is_ranker_model"):
            bundle["_is_ranker"] = True
        joblib.dump(bundle, model_path)

        # カタログをモデルの特徴量で自動同期（新規特徴量を auto_synced ステージに追記）
        try:
            _catalog_path = Path(__file__).parent.parent.parent / "keiba" / "feature_catalog.yaml"
            if _catalog_path.exists():
                _cat = FeatureCatalog.load(_catalog_path)
                _new = _cat.sync_with_model_features(bundle.get("feature_columns", []))
                if _new:
                    _cat.save(_catalog_path)
                    logger.info(f"feature_catalog.yaml に {len(_new)} 件の新規特徴量を追記: {_new}")
        except Exception as _e:
            logger.warning(f"feature_catalog 同期スキップ: {_e}")

        # Supabase へモデルアップロード（ブロッキング I/O を to_thread で分離）
        if SUPABASE_DATA_ENABLED and get_supabase_client():
            from app_config import upload_model_to_supabase  # type: ignore
            await asyncio.to_thread(
                upload_model_to_supabase,
                model_path,
                model_id,
                {
                    "user_id": current_user.get("user_id"),
                    "model_id": model_id,
                    "target": request.target,
                    "model_type": "lightgbm",
                    "ultimate_mode": True,
                    "use_optimizer": True,
                    "auc": float(auc),
                    "cv_auc_mean": float(cv_auc_mean),
                    "data_count": len(df),
                    "race_count": int(df["race_id"].nunique()) if "race_id" in df.columns else 0,
                    "created_at": saved_at,
                    "training_date_from": _get_actual_date_from(df, request.training_date_from),
                    "training_date_to": _get_actual_date_to(df, request.training_date_to),
                },
            )

        progress_cb("学習完了", 98)
        training_time = (datetime.now() - start_time).total_seconds()
        return TrainResponse(
            success=True,
            model_id=model_id,
            model_path=str(model_path),
            metrics={
                "auc": float(auc), "logloss": float(logloss),
                "logloss_calibrated": float(logloss_calibrated),
                "cv_auc_mean": float(cv_auc_mean), "cv_auc_std": float(cv_auc_std),
            },
            data_count=len(df),
            race_count=df["race_id"].nunique() if "race_id" in df.columns else 0,
            feature_count=feature_count,
            training_time=training_time,
            message=f"モデル学習完了 (AUC: {auc:.4f}, LogLoss: {logloss:.4f}, LogLoss(Cal): {logloss_calibrated:.4f})",
            optuna_executed=optuna_executed,
            optuna_error=optuna_error,
            feature_columns=_all_feature_columns,
        )

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"学習中にエラーが発生: {str(e)}")


@router.post("/api/train", response_model=TrainResponse)
async def train_model(request: TrainRequest, current_user: dict = Depends(require_premium)):
    """モデル学習エンドポイント"""
    return await _do_train(request, current_user)


# ── 非同期ジョブ管理 ──────────────────────────────────────


async def _run_train_job(job_id: str, request: TrainRequest) -> None:
    job = _train_jobs[job_id]
    job["status"] = "running"
    job["pct"] = 0

    def _cb(msg: str, pct: int = None) -> None:
        job["progress"] = msg
        if pct is not None:
            job["pct"] = pct

    try:
        train_result = await _do_train(request, {"user_id": "background-job"}, progress_cb=_cb)
        job["status"] = "completed"
        job["result"] = train_result.dict()
        job["progress"] = "完了"
        job["pct"] = 100
    except HTTPException as e:
        job["status"] = "error"
        job["error"] = e.detail
        job["progress"] = f"エラー: {e.detail}"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        job["progress"] = f"エラー: {str(e)}"
        logger.error(f"学習ジョブ {job_id} 失敗:\n{traceback.format_exc()}")


@router.post("/api/train/start")
async def train_start(request: TrainRequest):
    """非同期学習ジョブを起動してすぐに job_id を返す"""
    _purge_old_jobs(_train_jobs)
    job_id = str(uuid.uuid4())
    _train_jobs[job_id] = {"status": "queued", "progress": "キュー待ち", "pct": 0, "result": None, "error": None}
    try:
        import threading
        def _bg() -> None:
            # 別スレッドで独立した event loop を持つことでメインループをブロックしない
            asyncio.run(_run_train_job(job_id, request))
        threading.Thread(target=_bg, daemon=True, name=f"train-{job_id}").start()
    except Exception as e:
        _train_jobs[job_id]["status"] = "error"
        _train_jobs[job_id]["error"] = f"タスク起動失敗: {e}"
    return {"job_id": job_id, "status": _train_jobs[job_id]["status"]}


@router.get("/api/train/status/{job_id}")
async def train_job_status(job_id: str):
    """学習ジョブの進捗・結果を返す"""
    job = _train_jobs.get(job_id)
    if not job:
        return {
            "job_id": job_id,
            "status": "not_found",
            "progress": "",
            "pct": 0,
            "result": None,
            "error": f"学習ジョブ {job_id} が見つかりません（サーバー再起動の可能性）",
        }
    return {
        "job_id": job_id,
        "status": job["status"],
        "progress": job["progress"],
        "pct": job.get("pct", 0),
        "result": job.get("result"),
        "error": job.get("error"),
    }
