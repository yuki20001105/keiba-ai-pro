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
    SUPABASE_ENABLED,
    CONFIG_PATH,
    MODELS_DIR,
    ULTIMATE_DB,
    get_supabase_client,
    logger,
)
from deps.auth import require_premium  # type: ignore
from models import TrainRequest, TrainResponse  # type: ignore

router = APIRouter()

# ジョブストア（インメモリ）
_train_jobs: dict = {}
_MAX_JOBS = 50


def _purge_old_jobs(store: dict, max_keep: int = _MAX_JOBS) -> None:
    if len(store) <= max_keep:
        return
    finished = [k for k, v in store.items() if v.get("status") in ("completed", "error")]
    for key in finished[: len(store) - max_keep]:
        del store[key]


def _extract_ym_from_df(df: "pd.DataFrame") -> list:  # noqa: F821
    if "race_id" not in df.columns or df.empty:
        return []
    yms = df["race_id"].astype(str).str[:6]
    return yms[yms.str.match(r"^\d{6}$")].tolist()


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


# ── レース後確定フィールド（学習では除去する）
TRAIN_POST_RACE_DROP = [
    "finish_time", "time_seconds",
    "corner_1", "corner_2", "corner_3", "corner_4",
    "corner_positions", "corner_positions_list",
    "last_3f", "last_3f_rank", "last_3f_rank_normalized", "last_3f_time",
    "margin", "prize_money",
]


@router.post("/api/train", response_model=TrainResponse)
async def train_model(request: TrainRequest, current_user: dict = Depends(require_premium)):
    """モデル学習エンドポイント"""
    try:
        import pandas as pd
        from keiba_ai.db_ultimate_loader import load_ultimate_training_frame  # type: ignore
        from keiba_ai.feature_engineering import add_derived_features  # type: ignore
        from keiba_ai.lightgbm_feature_optimizer import (  # type: ignore
            prepare_for_lightgbm_ultimate,
        )
        from keiba_ai.optuna_optimizer import OptunaLightGBMOptimizer  # type: ignore
        from keiba_ai.ultimate_features import UltimateFeatureCalculator  # type: ignore

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

        # Supabase → SQLite 同期（ブロッキング呼び出しを to_thread で分離）
        if SUPABASE_ENABLED and get_supabase_client():
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
        df = load_ultimate_training_frame(db_path)

        print(f"DEBUG: Loaded {len(df)} rows from database")

        # 学習期間フィルタ
        if (request.training_date_from or request.training_date_to) and "race_id" in df.columns:
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

        # レース後フィールド除去
        drop_train = [c for c in TRAIN_POST_RACE_DROP if c in df.columns]
        if drop_train:
            df = df.drop(columns=drop_train)

        df = add_derived_features(df, full_history_df=df)

        calculator = UltimateFeatureCalculator(str(db_path))
        df = calculator.add_ultimate_features(df)
        df = df.loc[:, ~df.columns.duplicated()]

        from keiba_ai.train import _make_target  # type: ignore
        y = _make_target(df, request.target)

        if len(y.unique()) < 2:
            raise HTTPException(status_code=400, detail="2クラス以上が必要です")

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

                X_train, X_test, y_train, y_test = train_test_split(
                    X, y, test_size=request.test_size, random_state=42, stratify=y
                )
                categorical_indices = [X.columns.get_loc(c) for c in categorical_features if c in X.columns]
                train_data = lgb.Dataset(X_train, y_train, categorical_feature=categorical_indices)

                params = {
                    "objective": "binary", "metric": "auc",
                    "max_cat_to_onehot": 4, "learning_rate": 0.05,
                    "num_leaves": 31, "min_data_in_leaf": 20,
                    "feature_fraction": 0.8, "bagging_fraction": 0.8,
                    "bagging_freq": 5, "verbose": -1, "random_state": 42,
                }

                # CV で最適ラウンド探索
                cv_result = lgb.cv(
                    params, train_data,
                    num_boost_round=1000, nfold=request.cv_folds,
                    stratified=True, return_cvbooster=True,
                    callbacks=[
                        lgb.early_stopping(stopping_rounds=50, verbose=False),
                        lgb.log_evaluation(period=0),
                    ],
                )
                cv_auc_mean = cv_result["valid auc-mean"][-1]
                cv_auc_std = cv_result["valid auc-stdv"][-1]
                best_round_cv = len(cv_result["valid auc-mean"])
                best_round_final = int(best_round_cv * request.cv_folds / (request.cv_folds - 1))

                full_train_data = lgb.Dataset(X_train, y_train, categorical_feature=categorical_indices)
                model = lgb.train(params, full_train_data, num_boost_round=best_round_final)

                y_pred_proba = model.predict(X_test)
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
                    if request.model_type == "lightgbm":
                        categorical_indices = [X.columns.get_loc(c) for c in categorical_features if c in X.columns]
                        optuna_optimizer = OptunaLightGBMOptimizer(
                            n_trials=request.optuna_trials, cv_folds=request.cv_folds,
                            random_state=42, timeout=300,
                        )
                        best_params, best_optuna_score = optuna_optimizer.optimize(
                            X.values, y.values, categorical_features=categorical_indices,
                        )
                        optimized_params = optuna_optimizer.get_best_model_params()
                        optuna_num_rounds = optimized_params.pop("n_estimators", 1000)
                        train_data_opt = lgb.Dataset(X_train, y_train, categorical_feature=categorical_indices)
                        cv_result_opt = lgb.cv(
                            optimized_params, train_data_opt,
                            num_boost_round=optuna_num_rounds, nfold=request.cv_folds,
                            stratified=True,
                            callbacks=[
                                lgb.early_stopping(stopping_rounds=50, verbose=False),
                                lgb.log_evaluation(period=0),
                            ],
                        )
                        cv_auc_mean = cv_result_opt["valid auc-mean"][-1]
                        cv_auc_std = cv_result_opt["valid auc-stdv"][-1]
                        best_round_opt = len(cv_result_opt["valid auc-mean"])
                        best_round_opt_final = int(best_round_opt * request.cv_folds / (request.cv_folds - 1))
                        model = lgb.train(optimized_params, train_data_opt, num_boost_round=best_round_opt_final)
                        y_pred_proba = model.predict(X_test)
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

        # モデル保存
        model_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        mode_suffix = "_ultimate"
        model_filename = f"model_{request.target}_{request.model_type}_{model_id}{mode_suffix}.joblib"
        model_path = MODELS_DIR / model_filename

        bundle = {
            "model": model,
            "optimizer": optimizer if request.use_optimizer else None,
            "categorical_features": categorical_features,
            "feature_cols_num": valid_num_cols if not request.use_optimizer else None,
            "feature_cols_cat": valid_cat_cols if not request.use_optimizer else None,
            "feature_columns": _all_feature_columns,
            "target": request.target,
            "model_type": "lightgbm",
            "ultimate_mode": True,
            "use_optimizer": True,
            "metrics": {
                "auc": float(auc), "logloss": float(logloss),
                "cv_auc_mean": float(cv_auc_mean), "cv_auc_std": float(cv_auc_std),
            },
            "data_count": len(df),
            "race_count": df["race_id"].nunique() if "race_id" in df.columns else 0,
            "created_at": model_id,
            "training_date_from": _get_actual_date_from(df, request.training_date_from),
            "training_date_to": _get_actual_date_to(df, request.training_date_to),
        }
        joblib.dump(bundle, model_path)

        # Supabase へモデルアップロード（ブロッキング I/O を to_thread で分離）
        if SUPABASE_ENABLED and get_supabase_client():
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
                    "created_at": model_id,
                    "training_date_from": _get_actual_date_from(df, request.training_date_from),
                    "training_date_to": _get_actual_date_to(df, request.training_date_to),
                },
            )

        training_time = (datetime.now() - start_time).total_seconds()
        return TrainResponse(
            success=True,
            model_id=model_id,
            model_path=str(model_path),
            metrics={
                "auc": float(auc), "logloss": float(logloss),
                "cv_auc_mean": float(cv_auc_mean), "cv_auc_std": float(cv_auc_std),
            },
            data_count=len(df),
            race_count=df["race_id"].nunique() if "race_id" in df.columns else 0,
            feature_count=feature_count,
            training_time=training_time,
            message=f"モデル学習完了 (AUC: {auc:.4f}, LogLoss: {logloss:.4f})",
            optuna_executed=optuna_executed,
            optuna_error=optuna_error,
            feature_columns=_all_feature_columns,
        )

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"学習中にエラーが発生: {str(e)}")


# ── 非同期ジョブ管理 ──────────────────────────────────────


async def _run_train_job(job_id: str, request: TrainRequest) -> None:
    job = _train_jobs[job_id]
    job["status"] = "running"
    try:
        job["progress"] = "学習データ読み込み中..."
        train_result = await train_model(request)
        job["status"] = "completed"
        job["result"] = train_result.dict()
        job["progress"] = "完了"
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
    _train_jobs[job_id] = {"status": "queued", "progress": "キュー待ち", "result": None, "error": None}
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
            "result": None,
            "error": f"学習ジョブ {job_id} が見つかりません（サーバー再起動の可能性）",
        }
    return {
        "job_id": job_id,
        "status": job["status"],
        "progress": job["progress"],
        "result": job.get("result"),
        "error": job.get("error"),
    }
