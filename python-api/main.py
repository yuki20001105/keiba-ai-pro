"""
FastAPI機械学習サーバー
Streamlit版の機械学習パイプラインをREST APIとして提供
【3-3. 一括予測・購入推奨】機能を完全実装
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import sys
import os
import asyncio
from pathlib import Path
import joblib
import pandas as pd
import numpy as np
from datetime import datetime
import sqlite3
import logging
import json

# keiba_aiモジュールをインポート（親ディレクトリのkeibaから）
sys.path.insert(0, str(Path(__file__).parent.parent / "keiba"))

from keiba_ai.config import load_config
from keiba_ai.db import connect, init_db, load_training_frame
from keiba_ai.db_ultimate_loader import load_ultimate_training_frame
from keiba_ai.feature_engineering import add_derived_features
from keiba_ai.lightgbm_feature_optimizer import prepare_for_lightgbm_ultimate
from keiba_ai.optuna_optimizer import OptunaLightGBMOptimizer
from keiba_ai.ultimate_features import UltimateFeatureCalculator
from keiba_ai.optuna_all_models import optimize_model

# 購入推奨システムをインポート
from betting_strategy import BettingRecommender

# Supabase クライアント（永続化）
try:
    from supabase_client import (
        save_race_to_supabase,
        get_data_stats_from_supabase,
        sync_supabase_to_sqlite,
        upload_model_to_supabase,
        download_model_from_supabase,
        list_models_from_supabase,
        delete_model_from_supabase,
        get_client as get_supabase_client,
        get_pedigree_cache,
        save_pedigree_cache,
    )
    SUPABASE_ENABLED = True
except ImportError:
    SUPABASE_ENABLED = False
    logger.warning("supabase_client.py が見つかりません: Supabase 連携無効")

# ログ設定
log_file = Path(__file__).parent.parent / "optuna_debug.log"
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(log_file, mode="a", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)
logger.info("=" * 80)
logger.info("FastAPI起動 - ログ記録開始")
logger.info(f"ログファイル: {log_file}")
logger.info("=" * 80)

app = FastAPI(
    title="Keiba AI - Machine Learning API",
    description="競馬予測AIのための機械学習API",
    version="1.0.0",
)

# CORS設定（全オリジンを許可）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# モデル保存ディレクトリ
MODELS_DIR = Path(__file__).parent / "models"
MODELS_DIR.mkdir(exist_ok=True)

# Streamlit版のconfigを使用
CONFIG_PATH = Path(__file__).parent.parent / "keiba" / "config.yaml"


# ============================================
# Pydanticモデル
# ============================================


class TrainRequest(BaseModel):
    """学習リクエスト"""

    model_config = {"protected_namespaces": ()}

    target: str = "win"  # "win" or "place3"
    model_type: str = "logistic_regression"  # "logistic_regression" or "lightgbm"
    test_size: float = 0.2
    cv_folds: int = 5
    use_sqlite: bool = True  # Trueの場合はSQLiteから読み込み
    ultimate_mode: bool = False  # Ultimate版モード（90列特徴量）
    use_optimizer: bool = True  # LightGBM最適化を使用（推奨）
    use_optuna: bool = False  # Optunaでハイパーパラメータ最適化
    optuna_trials: int = 50  # Optunaの試行回数
    optuna_timeout: int = 300  # Optunaのタイムアウト（秒）
    training_date_from: Optional[str] = None  # 学習データ開始年月 "YYYY-MM"
    training_date_to: Optional[str] = None    # 学習データ終了年月 "YYYY-MM"


class TrainResponse(BaseModel):
    """学習レスポンス"""

    model_config = {"protected_namespaces": ()}

    success: bool
    model_id: str
    model_path: str
    metrics: Dict[str, float]
    data_count: int
    race_count: int
    feature_count: int
    training_time: float
    message: str
    optuna_executed: bool = False  # デバッグ用
    optuna_error: Optional[str] = None  # デバッグ用


class PredictRequest(BaseModel):
    """予測リクエスト"""

    model_config = {"protected_namespaces": ()}

    model_id: Optional[str] = None  # Noneの場合は最新モデルを使用
    horses: List[Dict[str, Any]]  # 馬のデータ（特徴量）


class PredictResponse(BaseModel):
    """予測レスポンス"""

    model_config = {"protected_namespaces": ()}

    success: bool
    predictions: List[Dict[str, Any]]
    model_id: str
    message: str


class ModelInfo(BaseModel):
    """モデル情報"""

    model_config = {"protected_namespaces": ()}

    model_id: str
    model_path: str
    created_at: str
    metrics: Dict[str, float]
    target: str
    model_type: str


class AnalyzeRaceRequest(BaseModel):
    """レース分析リクエスト（購入推奨）"""

    model_config = {"protected_namespaces": ()}

    race_id: str
    bankroll: int = 10000  # 総資金
    risk_mode: str = "balanced"  # conservative/balanced/aggressive
    use_kelly: bool = True  # ケリー基準使用
    dynamic_unit: bool = True  # 動的単価調整
    min_ev: float = 1.2  # 最低期待値フィルタ
    model_id: Optional[str] = None
    ultimate_mode: bool = False  # Ultimate版モード


class AnalyzeRaceResponse(BaseModel):
    """レース分析レスポンス"""

    model_config = {"protected_namespaces": ()}

    success: bool
    race_info: Dict[str, Any]
    pro_evaluation: Dict[str, Any]
    predictions: List[Dict[str, Any]]
    bet_types: Dict[str, List[Dict[str, Any]]]
    best_bet_type: str
    best_bet_info: Dict[str, float]
    race_level: str
    recommendation: Dict[str, Any]


class PurchaseHistoryRequest(BaseModel):
    """購入履歴保存リクエスト"""

    model_config = {"protected_namespaces": ()}

    race_id: str
    bet_type: str
    combinations: List[str]
    strategy_type: str
    purchase_count: int
    unit_price: int
    total_cost: int
    expected_value: float
    expected_return: float


class PurchaseHistoryResponse(BaseModel):
    """購入履歴保存レスポンス"""

    model_config = {"protected_namespaces": ()}

    success: bool
    purchase_id: int
    message: str


# ============================================
# ヘルパー関数
# ============================================


def get_latest_model() -> Optional[Path]:
    """最新のモデルファイルを取得"""
    models = list(MODELS_DIR.glob("model_*.joblib"))
    if not models:
        return None
    return max(models, key=lambda p: p.stat().st_mtime)


def _ensure_model_local(model_id: str) -> Optional[Path]:
    """モデルをローカルで探し、なければ Supabase からダウンロード"""
    # ローカルで探す
    local_files = list(MODELS_DIR.glob(f"*{model_id}*.joblib"))
    if local_files:
        return local_files[0]
    # Supabase からダウンロード
    if SUPABASE_ENABLED and get_supabase_client():
        dest = MODELS_DIR / f"model_{model_id}.joblib"
        if download_model_from_supabase(model_id, dest):
            return dest
    return None


def load_model_bundle(model_path: Path) -> Dict[str, Any]:
    """モデルバンドルをロード"""
    try:
        bundle = joblib.load(model_path)
        return bundle
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"モデルのロードに失敗: {str(e)}")


# ============================================
# APIエンドポイント
# ============================================


@app.get("/")
async def root():
    """ヘルスチェック"""
    return {
        "status": "ok",
        "service": "Keiba AI - Machine Learning API",
        "version": "1.0.0",
    }


@app.get("/api/debug")
async def debug_info():
    """Supabase接続状態のデバッグ情報"""
    import os
    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    result = {
        "supabase_enabled": SUPABASE_ENABLED,
        "supabase_url_set": bool(supabase_url),
        "supabase_key_set": bool(supabase_key),
        "supabase_url_prefix": supabase_url[:30] if supabase_url else "",
        "supabase_key_prefix": supabase_key[:15] if supabase_key else "",
    }
    if SUPABASE_ENABLED:
        try:
            client = get_supabase_client()
            result["client_created"] = client is not None
            if client:
                # テーブル存在確認
                res = client.table("races_ultimate").select("race_id", count="exact").limit(1).execute()
                result["races_ultimate_accessible"] = True
                result["races_count"] = res.count
        except Exception as e:
            result["client_error"] = str(e)
    return result


@app.post("/api/test-optuna-request")
async def test_optuna_request(request: TrainRequest):
    """Optunaリクエストのテスト用エンドポイント"""
    return {
        "received": {
            "target": request.target,
            "model_type": request.model_type,
            "use_optimizer": request.use_optimizer,
            "use_optuna": request.use_optuna,
            "optuna_trials": request.optuna_trials,
            "cv_folds": request.cv_folds,
        },
        "will_execute_optuna": request.use_optuna
        and request.model_type == "lightgbm"
        and request.use_optimizer,
        "message": "リクエストは正しく受信されました",
    }


@app.get("/api/data_stats")
async def get_data_stats(ultimate: bool = False):
    """
    データベース統計情報を取得

    Args:
        ultimate: Ultimate版DBを使用するかどうか
    """
    try:
        # Supabase が使える場合はそちらから取得
        if SUPABASE_ENABLED and get_supabase_client():
            return get_data_stats_from_supabase()

        # データベースパスを決定
        if ultimate:
            db_path = (
                Path(__file__).parent.parent / "keiba" / "data" / "keiba_ultimate.db"
            )
        else:
            cfg = load_config(CONFIG_PATH)
            db_path = cfg.storage.sqlite_path
            if not db_path.is_absolute():
                db_path = CONFIG_PATH.parent / db_path

        # データベースが存在しない場合
        if not db_path.exists():
            return {
                "total_races": 0,
                "total_horses": 0,
                "total_models": 0,
                "db_exists": False,
            }

        # データベース接続
        con = sqlite3.connect(db_path)
        cursor = con.cursor()

        # レース数を取得
        try:
            cursor.execute("SELECT COUNT(DISTINCT race_id) FROM races")
            total_races = cursor.fetchone()[0]
        except:
            total_races = 0

        # 馬数を取得
        try:
            cursor.execute("SELECT COUNT(DISTINCT horse_id) FROM entries")
            total_horses = cursor.fetchone()[0]
        except:
            try:
                cursor.execute("SELECT COUNT(*) FROM entries")
                total_horses = cursor.fetchone()[0]
            except:
                total_horses = 0

        con.close()

        # モデル数を取得
        total_models = len(list(MODELS_DIR.glob("model_*.joblib")))

        return {
            "total_races": total_races,
            "total_horses": total_horses,
            "total_models": total_models,
            "db_exists": True,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"統計取得エラー: {str(e)}")


def _extract_ym_from_df(df: "pd.DataFrame") -> list:
    """DataFrameの race_id 先頭6桁から YYYYMM リストを返す（数字6桁のみ）"""
    if "race_id" not in df.columns or df.empty:
        return []
    yms = df["race_id"].astype(str).str[:6]
    return yms[yms.str.match(r"^\d{6}$")].tolist()


def _get_actual_date_from(df: "pd.DataFrame", fallback: "str | None") -> "str | None":
    yms = _extract_ym_from_df(df)
    if yms:
        ym = min(yms)
        return f"{ym[:4]}-{ym[4:6]}"
    return fallback


def _get_actual_date_to(df: "pd.DataFrame", fallback: "str | None") -> "str | None":
    yms = _extract_ym_from_df(df)
    if yms:
        ym = max(yms)
        return f"{ym[:4]}-{ym[4:6]}"
    return fallback


@app.post("/api/train", response_model=TrainResponse)
async def train_model(request: TrainRequest):
    """
    モデル学習エンドポイント

    Streamlit版と同じパイプライン:
    1. SQLiteから訓練データ読み込み
    2. 60+次元の特徴量生成
    3. ColumnTransformer + Pipeline構築
    4. LogisticRegression or LightGBM
    5. 5-fold CV
    6. AUC, LogLoss評価
    7. joblib保存
    """
    try:
        start_time = datetime.now()

        # デバッグ用変数を初期化
        optuna_executed = False
        optuna_error = None

        # デバッグ: リクエスト内容を確認
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

        # 設定ファイルをロード
        if not CONFIG_PATH.exists():
            raise HTTPException(status_code=500, detail="config.yamlが見つかりません")

        cfg = load_config(CONFIG_PATH)

        # データベースパスを絶対パスに変換（Ultimate版モードに応じて切り替え）
        if request.ultimate_mode:
            # Ultimate版: keiba_ultimate.db
            db_path = (
                Path(__file__).parent.parent / "keiba" / "data" / "keiba_ultimate.db"
            )
        else:
            # 標準版: keiba.db
            db_path = cfg.storage.sqlite_path
            if not db_path.is_absolute():
                # config.yamlがあるディレクトリ（keiba/）からの相対パスとして解決
                db_path = CONFIG_PATH.parent / db_path

        # Supabase からローカル SQLite に同期（Render 環境での永続化対応）
        if SUPABASE_ENABLED and get_supabase_client() and request.ultimate_mode:
            logger.info("Supabase からデータを同期中...")
            db_path.parent.mkdir(parents=True, exist_ok=True)
            synced = sync_supabase_to_sqlite(db_path)
            logger.info(f"同期完了: {synced} レース")

        # 訓練データ読み込み（Ultimate版と通常版で分岐）
        if request.ultimate_mode:
            # Ultimate版: JSON形式のデータを読み込み
            df = load_ultimate_training_frame(db_path)
        else:
            # 通常版: SQLiteから読み込み
            con = connect(db_path)
            init_db(con)
            df = load_training_frame(con)
            con.close()

        print(f"DEBUG: Loaded {len(df)} rows from database")
        print(f"DEBUG: Database path: {db_path}")

        # 学習期間フィルタリング（race_idの先頭6桁 YYYYMM を使用）
        if (request.training_date_from or request.training_date_to) and "race_id" in df.columns:
            df["_race_ym"] = df["race_id"].astype(str).str[:6]
            if request.training_date_from:
                from_ym = request.training_date_from.replace("-", "")
                df = df[df["_race_ym"] >= from_ym]
            if request.training_date_to:
                to_ym = request.training_date_to.replace("-", "")
                df = df[df["_race_ym"] <= to_ym]
            df = df.drop(columns=["_race_ym"])
            print(f"DEBUG: After date filter ({request.training_date_from} ~ {request.training_date_to}): {len(df)} rows")

        if df.empty:
            raise HTTPException(
                status_code=400,
                detail=f"訓練データが見つかりません。先にデータ取得を実行してください。DB: {db_path}, Rows: {len(df)}",
            )

        # 派生特徴量を追加
        df = add_derived_features(df, full_history_df=df)

        # Ultimate版特徴量を追加（オプション）
        if request.ultimate_mode:
            print("\n=== Ultimate版特徴量計算 ===")
            calculator = UltimateFeatureCalculator(str(db_path))
            df = calculator.add_ultimate_features(df)
            
            # 重複カラムを削除
            df = df.loc[:, ~df.columns.duplicated()]
            print(f"  合計特徴量: {len(df.columns)}列（重複除去後）")

        # ターゲット作成
        from keiba_ai.train import _make_target

        y = _make_target(df, request.target)

        # クラス数チェック
        if len(y.unique()) < 2:
            raise HTTPException(
                status_code=400,
                detail="訓練データに2つ以上のクラスが必要です。より多くのレースデータを取得してください。",
            )

        # LightGBM最適化を使用する場合
        optimizer = None
        categorical_features = []
        feature_count = 0  # 特徴量数
        valid_num_cols = []  # 標準モード用
        valid_cat_cols = []  # 標準モード用

        if request.use_optimizer and request.model_type == "lightgbm":
            try:
                print("\n=== LightGBM最適化モード ===")

                # 特徴量最適化
                df_optimized, optimizer, categorical_features = (
                    prepare_for_lightgbm_ultimate(
                        df, target_col=request.target, is_training=True
                    )
                )

                # ID系と元のカテゴリカル変数（エンコード済み）を除外
                exclude_cols = [
                    request.target,
                    "race_id",
                    "horse_id",
                    "jockey_id",
                    "trainer_id",
                    "owner_id",
                    "finish_position",
                ]
                X = df_optimized.drop(
                    [col for col in exclude_cols if col in df_optimized.columns], axis=1
                )

                # object型のカラムを除外（元のカテゴリカル変数など）
                object_cols = X.select_dtypes(include=["object"]).columns.tolist()
                if object_cols:
                    print(f"  除外するobject型カラム: {object_cols}")
                    X = X.drop(columns=object_cols)

                # デバッグ情報
                feature_count = len(X.columns)
                print(f"  最終特徴量数: {feature_count}列")
                print(f"  データ型: {X.dtypes.value_counts().to_dict()}")

                # モデル学習（LightGBMネイティブAPI）
                from sklearn.model_selection import train_test_split
                from sklearn.metrics import roc_auc_score, log_loss
                import lightgbm as lgb

                X_train, X_test, y_train, y_test = train_test_split(
                    X, y, test_size=request.test_size, random_state=42, stratify=y
                )

                # categorical_featuresを整数インデックスに変換
                categorical_indices = []
                for cat_feature in categorical_features:
                    if cat_feature in X.columns:
                        categorical_indices.append(X.columns.get_loc(cat_feature))

                print(f"  カテゴリカル特徴量インデックス: {categorical_indices}")

                # LightGBMデータセット作成
                train_data = lgb.Dataset(
                    X_train, y_train, categorical_feature=categorical_indices
                )
                test_data = lgb.Dataset(X_test, y_test, reference=train_data)

                # パラメータ設定（最適化版）
                params = {
                    "objective": "binary",
                    "metric": "auc",
                    "max_cat_to_onehot": 4,
                    "learning_rate": 0.05,
                    "num_leaves": 31,
                    "min_data_in_leaf": 20,
                    "feature_fraction": 0.8,
                    "bagging_fraction": 0.8,
                    "bagging_freq": 5,
                    "verbose": -1,
                    "random_state": 42,
                }

                # ====================================================
                # CV best round → 全量再学習（ゴールドスタンダード）
                # ====================================================
                # 1) CVで各foldのbest_iterationを収集 → 平均を最適ラウンドとする
                # 2) 全学習データでその固定ラウンド数で再学習
                # → val分割1回限りの偶然性を排除し、データを無駄にしない
                n_train = len(X_train)
                max_rounds = 1000  # CVのearly_stoppingが実質的な上限を決める
                early_stopping_rounds = 50
                print(f"  ステップ1: {request.cv_folds}-fold CVで最適ラウンド数を探索 (max={max_rounds}, data={n_train}件)")

                # Step1: CVで最適ラウンド数を探索
                cv_result = lgb.cv(
                    params,
                    train_data,
                    num_boost_round=max_rounds,
                    nfold=request.cv_folds,
                    stratified=True,
                    return_cvbooster=True,  # 各foldのboosterを返す
                    callbacks=[
                        lgb.early_stopping(stopping_rounds=early_stopping_rounds, verbose=False),
                        lgb.log_evaluation(period=0),
                    ],
                )
                cv_auc_mean = cv_result["valid auc-mean"][-1]
                cv_auc_std = cv_result["valid auc-stdv"][-1]
                # CVが見つけた最適ラウンド数 = 返されたAUCリストの長さ
                best_round_cv = len(cv_result["valid auc-mean"])
                # CVはfold数分の1のデータで検証するため、全量再学習時はラウンド数を補正
                # 補正式: best_round_final = best_round_cv * n_folds / (n_folds - 1)
                best_round_final = int(best_round_cv * request.cv_folds / (request.cv_folds - 1))
                print(f"  CV最適ラウンド数: {best_round_cv} → 補正後: {best_round_final} (×{request.cv_folds}/{request.cv_folds-1})")

                # Step2: 全学習データで best_round_final ラウンド固定で再学習（early_stoppingなし）
                print(f"  ステップ2: 全学習データで{best_round_final}ラウンド再学習...")
                full_train_data = lgb.Dataset(X_train, y_train, categorical_feature=categorical_indices)
                model = lgb.train(
                    params,
                    full_train_data,
                    num_boost_round=best_round_final,
                )
                best_iteration = best_round_final
                print(f"  最終モデル: {best_round_final}ラウンドで学習完了 (CV best={best_round_cv})")

                # 評価（holdout test setで）
                y_pred_proba = model.predict(X_test)
                auc = roc_auc_score(y_test, y_pred_proba)
                logloss = log_loss(y_test, y_pred_proba)

                print(
                    f"✓ LightGBM最適化完了: AUC={auc:.4f}, CV AUC={cv_auc_mean:.4f}±{cv_auc_std:.4f}"
                )

            except Exception as lightgbm_error:
                print(f"\n[ERROR-LIGHTGBM] LightGBM処理中にエラーが発生しました")
                print(f"[ERROR-LIGHTGBM] 例外型: {type(lightgbm_error).__name__}")
                print(f"[ERROR-LIGHTGBM] 例外メッセージ: {str(lightgbm_error)}")
                import traceback

                print("[ERROR-LIGHTGBM] スタックトレース:")
                traceback.print_exc()
                logger.error(
                    f"[ERROR-LIGHTGBM] スタックトレース:\n{traceback.format_exc()}"
                )
                raise

            # デバッグ: use_optunaの値を確認
            optuna_executed = False
            optuna_error = None
            logger.info(f"\n[TRACE-001] request.use_optuna = {request.use_optuna}")
            logger.info(f"[TRACE-002] request.optuna_trials = {request.optuna_trials}")
            logger.info(f"[TRACE-003] request.use_optimizer = {request.use_optimizer}")
            logger.info(f"[TRACE-004] request.model_type = {request.model_type}")

            # Optunaによるハイパーパラメータ最適化（オプション）
            logger.info(
                f"[TRACE-005] Optunaブロック判定前: use_optuna={request.use_optuna}"
            )
            if request.use_optuna:
                logger.info("[TRACE-006] ★★★ Optunaブロックに入りました ★★★")
                try:
                    logger.info("[TRACE-007] try節に入りました")
                    optuna_executed = True
                    logger.info(
                        f"[TRACE-008] optuna_executed を True に設定: {optuna_executed}"
                    )
                    print("\n=== Optunaハイパーパラメータ最適化 ===")
                    logger.info("[TRACE-009] コンソール出力完了")

                    # モデルタイプに応じて最適化を実行
                    model_type_map = {
                        "logistic_regression": "logistic",
                        "random_forest": "random_forest",
                        "gradient_boosting": "gradient_boosting",
                    }

                    if request.model_type == "lightgbm":
                        # LightGBM（既存の処理）
                        # categorical_featuresを整数インデックスに変換
                        categorical_indices = []
                        for cat_feature in categorical_features:
                            logger.info(f"[TRACE-012] 処理中: {cat_feature}")
                            if cat_feature in X.columns:
                                idx = X.columns.get_loc(cat_feature)
                                categorical_indices.append(idx)
                                logger.info(
                                    f"[TRACE-013] {cat_feature} のインデックス: {idx}"
                                )

                        logger.info(
                            f"[TRACE-014] カテゴリカル特徴量変換完了: {len(categorical_indices)}個"
                        )
                        print(f"  カテゴリカル特徴量: {len(categorical_indices)}個")
                        print(f"  インデックス: {categorical_indices}")

                        logger.info("[TRACE-015] OptunaLightGBMOptimizer初期化開始")
                        print(f"[DEBUG] OptunaLightGBMOptimizerを初期化します...")
                        optuna_optimizer = OptunaLightGBMOptimizer(
                            n_trials=request.optuna_trials,
                            cv_folds=request.cv_folds,
                            random_state=42,
                            timeout=300,  # 5分タイムアウト
                        )
                        logger.info("[TRACE-016] OptunaLightGBMOptimizer初期化完了")

                        logger.info(
                            f"[TRACE-017] 最適化開始準備: trials={request.optuna_trials}, folds={request.cv_folds}"
                        )
                        print(
                            f"[DEBUG] 最適化を開始します（試行数: {request.optuna_trials}, CVフォールド: {request.cv_folds}）..."
                        )
                        # 最適パラメータを探索（ndarrayで渡す）
                        logger.info("[TRACE-018] データ変換開始")
                        X_array = X.values if hasattr(X, "values") else X
                        y_array = y.values if hasattr(y, "values") else y
                        logger.info(
                            f"[TRACE-019] データ変換完了: X={X_array.shape}, y={len(y_array)}"
                        )
                        print(
                            f"[DEBUG] データサイズ: X={X_array.shape}, y={len(y_array)}"
                        )

                        logger.info(
                            "[TRACE-020] ★★★ optimize()メソッド呼び出し直前 ★★★"
                        )
                        print("[DEBUG] ★★★ optimize()呼び出し直前 ★★★")
                        print(
                            f"[DEBUG] X_array type: {type(X_array)}, shape: {X_array.shape}"
                        )
                        print(
                            f"[DEBUG] y_array type: {type(y_array)}, shape: {y_array.shape}"
                        )
                        print(f"[DEBUG] categorical_indices: {categorical_indices}")

                        print("[DEBUG] optimize()メソッド実行開始...")
                        best_params, best_optuna_score = optuna_optimizer.optimize(
                            X_array,
                            y_array,  # 全データで最適化
                            categorical_features=categorical_indices,
                        )
                        print(f"[DEBUG] optimize()メソッド実行完了")
                        print(f"[DEBUG] best_score={best_optuna_score:.4f}")
                        logger.info(
                            "[TRACE-021] ★★★ optimize()メソッド呼び出し完了 ★★★"
                        )
                        logger.info(f"[TRACE-022] best_score={best_optuna_score:.4f}")

                        print(
                            f"[DEBUG] Optuna最適化完了。最良スコア: {best_optuna_score:.4f}"
                        )

                        # 最適パラメータで「CV best round → 全量再学習」方式
                        print("\n  最適パラメータで再学習中（CV best round方式）...")
                        optimized_params = optuna_optimizer.get_best_model_params()
                        # n_estimators はネイティブAPIでは num_boost_round なので除去
                        optuna_num_rounds = optimized_params.pop("n_estimators", 1000)

                        train_data_opt = lgb.Dataset(
                            X_train, y_train, categorical_feature=categorical_indices
                        )

                        # CVで最適ラウンド数探索
                        cv_result_opt = lgb.cv(
                            optimized_params,
                            train_data_opt,
                            num_boost_round=optuna_num_rounds,
                            nfold=request.cv_folds,
                            stratified=True,
                            return_cvbooster=False,
                            callbacks=[
                                lgb.early_stopping(stopping_rounds=50, verbose=False),
                                lgb.log_evaluation(period=0),
                            ],
                        )
                        cv_auc_mean = cv_result_opt["valid auc-mean"][-1]
                        cv_auc_std = cv_result_opt["valid auc-stdv"][-1]
                        best_round_opt = len(cv_result_opt["valid auc-mean"])
                        best_round_opt_final = int(best_round_opt * request.cv_folds / (request.cv_folds - 1))
                        print(f"  Optuna CV最適ラウンド: {best_round_opt} → 補正後: {best_round_opt_final}")

                        # 全学習データで補正済みラウンド数で再学習
                        model = lgb.train(
                            optimized_params,
                            train_data_opt,
                            num_boost_round=best_round_opt_final,
                        )

                        # 再評価
                        y_pred_proba = model.predict(X_test)
                        auc = roc_auc_score(y_test, y_pred_proba)
                        logloss = log_loss(y_test, y_pred_proba)

                        print(
                            f"✓ Optuna最適化完了: AUC={auc:.4f}, CV AUC={cv_auc_mean:.4f}±{cv_auc_std:.4f}"
                        )
                        print(f"  Optunaスコア改善: {best_optuna_score:.4f}")

                    elif request.model_type in model_type_map:
                        # LogisticRegression, RandomForest, GradientBoosting（新実装）
                        logger.info(f"[TRACE-NEW] {request.model_type}の最適化開始")
                        print(f"\n  {request.model_type}の最適化を開始...")

                        # データ変換
                        X_array = X.values if hasattr(X, "values") else X
                        y_array = y.values if hasattr(y, "values") else y

                        # 最適化実行
                        optuna_model_type = model_type_map[request.model_type]
                        best_params = optimize_model(
                            optuna_model_type,
                            X_array,
                            y_array,
                            n_trials=request.optuna_trials,
                            timeout=300,
                        )

                        logger.info(f"[TRACE-NEW] 最適化完了: {best_params}")
                        print(f"✓ Optuna最適化完了: {request.model_type}")
                        print(f"  最適パラメータ: {best_params}")

                        # 最適パラメータでモデル再構築（後続の処理で使用）
                        # ※ 標準モードの処理に任せる

                except Exception as e:
                    logger.error("[TRACE-ERROR] ★★★ 例外発生 ★★★")
                    logger.error(f"[TRACE-ERROR] 例外型: {type(e).__name__}")
                    logger.error(f"[TRACE-ERROR] 例外メッセージ: {str(e)}")
                    logger.error(
                        f"[TRACE-ERROR] スタックトレース:\n{traceback.format_exc()}"
                    )
                    optuna_error = f"{type(e).__name__}: {str(e)}"
                    print(f"\n❌ Optuna最適化中にエラーが発生: {optuna_error}")
                    import traceback

                    traceback.print_exc()
                    print("   標準の学習を継続します...")

        else:
            # 従来の方法（LogisticRegressionまたはLightGBM標準）
            print("\n=== 標準モード ===")

            # 特徴量定義
            feature_cols_num = [
                "horse_no",
                "bracket",
                "age",
                "handicap",
                "weight",
                "weight_diff",
                "entry_odds",
                "entry_popularity",
                "straight_length",
                "inner_bias",
                "inner_advantage",
                "jockey_course_win_rate",
                "jockey_course_races",
                "horse_distance_win_rate",
                "horse_distance_avg_finish",
                "trainer_recent_win_rate",
            ]
            feature_cols_cat = [
                "sex",
                "jockey_id",
                "trainer_id",
                "venue_code",
                "track_type",
                "corner_radius",
            ]

            # 欠損カラムの処理
            for c in feature_cols_num + feature_cols_cat:
                if c not in df.columns:
                    df[c] = np.nan

            # 全てNaNのカラムを除外
            non_null_counts = df.notna().sum()
            valid_num_cols = [
                c
                for c in feature_cols_num
                if c in df.columns and non_null_counts[c] > 0
            ]
            valid_cat_cols = [
                c
                for c in feature_cols_cat
                if c in df.columns and non_null_counts[c] > 0
            ]

            X = df[valid_num_cols + valid_cat_cols].copy()

            # 前処理パイプライン構築
            from sklearn.model_selection import train_test_split, cross_val_score
            from sklearn.compose import ColumnTransformer
            from sklearn.pipeline import Pipeline
            from sklearn.preprocessing import OneHotEncoder
            from sklearn.impute import SimpleImputer
            from sklearn.linear_model import LogisticRegression
            from sklearn.metrics import roc_auc_score, log_loss

            num_transformer = Pipeline(
                steps=[("imputer", SimpleImputer(strategy="median"))]
            )

            cat_transformer = Pipeline(
                steps=[
                    (
                        "imputer",
                        SimpleImputer(strategy="constant", fill_value="missing"),
                    ),
                    (
                        "onehot",
                        OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                    ),
                ]
            )

            preprocessor = ColumnTransformer(
                transformers=[
                    ("num", num_transformer, valid_num_cols),
                    ("cat", cat_transformer, valid_cat_cols),
                ]
            )

            # モデル選択
            if request.model_type == "lightgbm":
                try:
                    import lightgbm as lgb

                    clf = lgb.LGBMClassifier(
                        n_estimators=100,
                        max_depth=10,
                        learning_rate=0.05,
                        random_state=42,
                        class_weight="balanced",
                    )
                except ImportError:
                    raise HTTPException(
                        status_code=500, detail="LightGBMがインストールされていません"
                    )
            else:
                clf = LogisticRegression(
                    max_iter=1000, random_state=42, class_weight="balanced"
                )

            # パイプライン構築
            model = Pipeline(steps=[("pre", preprocessor), ("clf", clf)])

            # Train/Test分割
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=request.test_size, random_state=42, stratify=y
            )

            # モデル学習
            model.fit(X_train, y_train)

            # 評価
            y_pred_proba = model.predict_proba(X_test)[:, 1]
            auc = roc_auc_score(y_test, y_pred_proba)
            logloss = log_loss(y_test, y_pred_proba)

            # クロスバリデーション
            cv_scores = cross_val_score(
                model, X, y, cv=request.cv_folds, scoring="roc_auc"
            )
            cv_auc_mean = cv_scores.mean()
            cv_auc_std = cv_scores.std()

            # 特徴量数を設定（標準モード）
            feature_count = len(valid_num_cols) + len(valid_cat_cols)

        # モデル保存
        model_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        mode_suffix = (
            "_optimized"
            if (request.use_optimizer and request.model_type == "lightgbm")
            else ""
        )
        mode_suffix += "_ultimate" if request.ultimate_mode else ""
        model_filename = f"model_{request.target}_{request.model_type}_{model_id}{mode_suffix}.joblib"
        model_path = MODELS_DIR / model_filename

        # モデルバンドルを作成
        bundle = {
            "model": model,
            "optimizer": optimizer if request.use_optimizer else None,
            "categorical_features": categorical_features,
            "feature_cols_num": valid_num_cols if not request.use_optimizer else None,
            "feature_cols_cat": valid_cat_cols if not request.use_optimizer else None,
            "target": request.target,
            "model_type": request.model_type,
            "ultimate_mode": request.ultimate_mode,
            "use_optimizer": request.use_optimizer,
            "metrics": {
                "auc": float(auc),
                "logloss": float(logloss),
                "cv_auc_mean": float(cv_auc_mean),
                "cv_auc_std": float(cv_auc_std),
            },
            "data_count": len(df),
            "race_count": df["race_id"].nunique() if "race_id" in df.columns else 0,
            "created_at": model_id,
            "training_date_from": _get_actual_date_from(df, request.training_date_from),
            "training_date_to": _get_actual_date_to(df, request.training_date_to),
        }

        joblib.dump(bundle, model_path)

        # Supabase Storage にモデルをアップロード
        if SUPABASE_ENABLED and get_supabase_client():
            upload_model_to_supabase(model_path, model_id, {
                "model_id": model_id,
                "target": request.target,
                "model_type": request.model_type,
                "ultimate_mode": request.ultimate_mode,
                "use_optimizer": request.use_optimizer,
                "auc": float(auc),
                "cv_auc_mean": float(cv_auc_mean),
                "data_count": len(df),
                "race_count": int(df["race_id"].nunique()) if "race_id" in df.columns else 0,
                "created_at": model_id,
                "training_date_from": _get_actual_date_from(df, request.training_date_from),
                "training_date_to": _get_actual_date_to(df, request.training_date_to),
            })

        end_time = datetime.now()
        training_time = (end_time - start_time).total_seconds()

        return TrainResponse(
            success=True,
            model_id=model_id,
            model_path=str(model_path),
            metrics={
                "auc": float(auc),
                "logloss": float(logloss),
                "cv_auc_mean": float(cv_auc_mean),
                "cv_auc_std": float(cv_auc_std),
            },
            data_count=len(df),
            race_count=df["race_id"].nunique() if "race_id" in df.columns else 0,
            feature_count=feature_count,
            training_time=training_time,
            message=f"モデル学習完了 (AUC: {auc:.4f}, LogLoss: {logloss:.4f})",
            optuna_executed=optuna_executed,
            optuna_error=optuna_error,
        )

    except HTTPException:
        raise
    except Exception as e:
        print(f"\n[ERROR] 学習中にエラーが発生しました")
        print(f"[ERROR] 例外型: {type(e).__name__}")
        print(f"[ERROR] 例外メッセージ: {str(e)}")
        import traceback
        print(f"[ERROR] スタックトレース:")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"学習中にエラーが発生: {str(e)}")


@app.post("/api/predict", response_model=PredictResponse)
async def predict(request: PredictRequest):
    """
    予測エンドポイント

    学習済みモデルを使用して予測を実行
    """
    try:
        # モデルをロード
        if request.model_id:
            model_path = _ensure_model_local(request.model_id)
            if not model_path:
                raise HTTPException(
                    status_code=404,
                    detail=f"モデル {request.model_id} が見つかりません",
                )
        else:
            model_path = get_latest_model()
            if model_path is None:
                # Supabase から最新モデルを取得
                if SUPABASE_ENABLED and get_supabase_client():
                    sb_models = list_models_from_supabase()
                    if sb_models:
                        latest_id = sb_models[0]["model_id"]
                        model_path = _ensure_model_local(latest_id)
                if model_path is None:
                    raise HTTPException(
                        status_code=404,
                        detail="学習済みモデルが見つかりません。先に学習を実行してください。",
                    )

        # モデルバンドルをロード
        bundle = load_model_bundle(model_path)
        model = bundle["model"]
        optimizer = bundle.get("optimizer")
        use_optimizer = bundle.get("use_optimizer", False)
        bundle.get("categorical_features", [])

        # 入力データをDataFrameに変換
        df = pd.DataFrame(request.horses)

        # 派生特徴量を追加
        df = add_derived_features(df, full_history_df=None)

        # LightGBM最適化モードの場合
        if use_optimizer and optimizer is not None:
            # 最適化された特徴量変換を適用
            df_optimized = optimizer.transform(df)

            # ID系を除外
            exclude_cols = [
                "race_id",
                "horse_id",
                "jockey_id",
                "trainer_id",
                "owner_id",
                "finish_position",
            ]
            X = df_optimized.drop(
                [col for col in exclude_cols if col in df_optimized.columns], axis=1
            )

            # 予測実行
            proba = model.predict(X)
        else:
            # 従来の方法
            feature_cols_num = bundle["feature_cols_num"]
            feature_cols_cat = bundle["feature_cols_cat"]

            # 必要な特徴量カラムを確保（デフォルト値で埋める）
            for c in feature_cols_num:
                if c not in df.columns:
                    df[c] = 0.0  # 数値特徴量はデフォルト0

            for c in feature_cols_cat:
                if c not in df.columns:
                    df[c] = "unknown"  # カテゴリ特徴量はデフォルト"unknown"

            X = df[feature_cols_num + feature_cols_cat].copy()

            # 予測実行
            proba = model.predict_proba(X)[:, 1]

        # 結果を整形
        predictions = []
        for i, row in df.iterrows():
            horse_num = int(row.get("horse_number", row.get("horse_no", i + 1)))
            pred = {
                "index": int(i),
                "horse_number": horse_num,
                "horse_name": str(row.get("horse_name", f"Horse {horse_num}")),
                "probability": float(proba[i]),
                "odds": float(row.get("odds", row.get("entry_odds", 0.0))),
            }
            predictions.append(pred)

        # 予測確率でソート
        predictions.sort(key=lambda x: x["probability"], reverse=True)

        # 順位を追加
        for rank, pred in enumerate(predictions, start=1):
            pred["predicted_rank"] = rank

        model_id = bundle.get("model_id", bundle.get("created_at", "unknown"))

        return PredictResponse(
            success=True,
            predictions=predictions,
            model_id=model_id,
            message=f"{len(predictions)}頭の予測が完了しました",
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"予測中にエラーが発生: {str(e)}")


@app.get("/api/models")
async def list_models(ultimate: bool = False):
    """
    保存済みモデルの一覧を取得

    Args:
        ultimate: Ultimate版モデルのみフィルタリング
    """
    try:
        # Supabase からモデル一覧を取得
        if SUPABASE_ENABLED and get_supabase_client():
            sb_models = list_models_from_supabase()
            if ultimate:
                sb_models = [m for m in sb_models if m.get("ultimate_mode", False)]
            else:
                sb_models = [m for m in sb_models if not m.get("ultimate_mode", False)]
            return {"models": sb_models, "count": len(sb_models)}

        # ローカルファイルから取得（フォールバック）
        models = []
        for model_path in MODELS_DIR.glob("model_*.joblib"):
            try:
                bundle = joblib.load(model_path)
                is_ultimate = bundle.get("ultimate_mode", False)

                # ultimateフィルタリング
                if ultimate and not is_ultimate:
                    continue
                if not ultimate and is_ultimate:
                    continue

                models.append(
                    {
                        "model_id": bundle.get("created_at", "unknown"),
                        "model_path": str(model_path),
                        "created_at": bundle.get("created_at", "unknown"),
                        "target": bundle.get("target", "unknown"),
                        "model_type": bundle.get("model_type", "unknown"),
                        "ultimate_mode": is_ultimate,
                        "use_optimizer": bundle.get("use_optimizer", False),
                        "auc": bundle.get("metrics", {}).get("auc", 0.0),
                        "cv_auc_mean": bundle.get("metrics", {}).get("cv_auc_mean", 0.0),
                        "training_date_from": bundle.get("training_date_from"),
                        "training_date_to": bundle.get("training_date_to"),
                        "n_rows": bundle.get("data_count", 0),
                    }
                )
            except Exception as e:
                print(f"モデル読み込みエラー {model_path}: {e}")
                continue

        # AUC降順にソート
        models = sorted(models, key=lambda x: x.get("auc", 0), reverse=True)

        return {"models": models, "count": len(models)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"モデル一覧取得エラー: {str(e)}")


@app.delete("/api/models/{model_id}")
async def delete_model(model_id: str):
    """保存済みモデルを削除"""
    try:
        deleted = []
        # Supabase から削除
        if SUPABASE_ENABLED and get_supabase_client():
            delete_model_from_supabase(model_id)
            deleted.append(f"supabase:{model_id}")
        # ローカルからも削除
        model_files = list(MODELS_DIR.glob(f"*{model_id}*.joblib"))
        for f in model_files:
            f.unlink()
            deleted.append(f.name)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"モデル {model_id} が見つかりません")
        return {"success": True, "deleted": deleted}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"削除エラー: {str(e)}")


@app.post("/api/analyze_race", response_model=AnalyzeRaceResponse)
async def analyze_race(request: AnalyzeRaceRequest):
    """
    レース分析と購入推奨エンドポイント

    Streamlit 3_予測_batch.py の Tab1~Tab2 機能を統合:
    1. race_idからレース情報取得
    2. モデルで予測実行
    3. 期待値計算
    4. プロ戦略スコア評価
    5. 馬券種別候補生成
    6. ケリー基準・動的単価計算
    7. 購入推奨情報返却
    """
    try:
        # 設定ファイルをロード
        if not CONFIG_PATH.exists():
            raise HTTPException(status_code=500, detail="config.yamlが見つかりません")

        cfg = load_config(CONFIG_PATH)

        # データベースパスを決定（Ultimate版モードに応じて）
        if request.ultimate_mode:
            db_path = (
                Path(__file__).parent.parent / "keiba" / "data" / "keiba_ultimate.db"
            )
        else:
            db_path = cfg.storage.sqlite_path
            if not db_path.is_absolute():
                db_path = CONFIG_PATH.parent / db_path

        # tracking.dbパス（購入履歴用）
        CONFIG_PATH.parent / "data" / "tracking.db"

        # モデルロード（Ultimate版モデルを優先）
        if request.model_id:
            model_path = _ensure_model_local(request.model_id)
            if not model_path:
                raise HTTPException(
                    status_code=404,
                    detail=f"モデル {request.model_id} が見つかりません",
                )
        else:
            # Supabase からモデル一覧を取得して最新を使う
            if SUPABASE_ENABLED and get_supabase_client():
                sb_models = list_models_from_supabase()
                if request.ultimate_mode:
                    sb_models = [m for m in sb_models if m.get("ultimate_mode", False)]
                if sb_models:
                    model_path = _ensure_model_local(sb_models[0]["model_id"])
                else:
                    model_path = None
            else:
                model_path = None
            # ローカルフォールバック
            if not model_path:
                if request.ultimate_mode:
                    ultimate_models = [p for p in MODELS_DIR.glob("model_*_ultimate.joblib")]
                    model_path = max(ultimate_models, key=lambda p: p.stat().st_mtime) if ultimate_models else None
                else:
                    model_path = get_latest_model()
            if not model_path:
                raise HTTPException(
                    status_code=404, detail="訓練済みモデルが見つかりません"
                )

        bundle = load_model_bundle(model_path)
        model = bundle["model"]

        # データベースからレース情報取得
        if request.ultimate_mode:
            # ===== Ultimate版: races_ultimate + race_results_ultimate =====
            import sqlite3 as _sq3
            _conn = _sq3.connect(str(db_path))
            _cur = _conn.cursor()

            # races_ultimate からレース基本情報
            _cur.execute("SELECT data FROM races_ultimate WHERE race_id = ?", (request.race_id,))
            _rrow = _cur.fetchone()
            if not _rrow:
                _conn.close()
                raise HTTPException(status_code=404, detail=f"レース {request.race_id} が races_ultimate に見つかりません")
            _race_data = json.loads(_rrow[0])
            race_info = {
                "race_id": request.race_id,
                "race_name": _race_data.get("race_name", ""),
                "venue": _race_data.get("venue", ""),
                "date": _race_data.get("date", ""),
                "distance": _race_data.get("distance", 0),
                "track_type": _race_data.get("track_type", ""),
                "weather": _race_data.get("weather", ""),
                "field_condition": _race_data.get("field_condition", ""),
                "num_horses": _race_data.get("num_horses", 0),
            }

            # race_results_ultimate から出走馬データ
            _cur.execute("SELECT data FROM race_results_ultimate WHERE race_id = ? ORDER BY json_extract(data, '$.horse_number')", (request.race_id,))
            _hrows = _cur.fetchall()
            _conn.close()

            if not _hrows:
                raise HTTPException(status_code=404, detail=f"レース {request.race_id} の馬データが race_results_ultimate に見つかりません")

            # 学習と同じパイプラインで特徴量生成
            _horse_records = []
            for _hr in _hrows:
                _hd = json.loads(_hr[0])
                _hd["race_id"] = request.race_id
                # races_ultimate のメタ情報をマージ
                for _k, _v in _race_data.items():
                    if _k not in _hd or _hd[_k] is None:
                        _hd[_k] = _v
                _horse_records.append(_hd)

            df_pred = pd.DataFrame(_horse_records)

            # db_ultimate_loader と同じ前処理を適用
            from keiba_ai.db_ultimate_loader import load_ultimate_training_frame as _luf
            # カラムマッピング
            _col_map = {
                'finish_position': 'finish', 'finish_time': 'time',
                'track_type': 'surface', 'last_3f': 'last_3f_time',
                'weight_kg': 'horse_weight',
            }
            for _old, _new in _col_map.items():
                if _old in df_pred.columns and _new not in df_pred.columns:
                    df_pred[_new] = df_pred[_old]

            # ID抽出
            for _url_col, _id_col, _name_col in [
                ('jockey_url', 'jockey_id', 'jockey_name'),
                ('trainer_url', 'trainer_id', 'trainer_name'),
                ('horse_url', 'horse_id', 'horse_name'),
            ]:
                if _id_col not in df_pred.columns:
                    if _url_col in df_pred.columns:
                        df_pred[_id_col] = df_pred[_url_col].str.extract(r'/([^/]+)/?$')[0]
                    elif _name_col in df_pred.columns:
                        df_pred[_id_col] = df_pred[_name_col]

            # 数値変換
            _numeric_cols = [
                'bracket_number', 'horse_number', 'jockey_weight', 'odds', 'popularity',
                'horse_weight', 'age', 'distance', 'num_horses',
                'kai', 'day', 'corner_1', 'corner_2', 'corner_3', 'corner_4',
                'horse_total_runs', 'horse_total_wins', 'horse_total_prize_money',
                'prev_race_distance', 'prev_race_finish', 'prev_race_weight',
            ]
            for _c in _numeric_cols:
                if _c in df_pred.columns:
                    df_pred[_c] = pd.to_numeric(df_pred[_c], errors='coerce')

            # sex_age パース
            if 'sex_age' in df_pred.columns:
                if 'sex' not in df_pred.columns or df_pred['sex'].isna().all():
                    df_pred['sex'] = df_pred['sex_age'].str.extract(r'^([牡牝セ])')[0]
                if 'age' not in df_pred.columns or df_pred['age'].isna().all():
                    df_pred['age'] = pd.to_numeric(df_pred['sex_age'].str.extract(r'(\d+)$')[0], errors='coerce')

            # corner_positions_list
            if 'corner_positions' in df_pred.columns and 'corner_positions_list' not in df_pred.columns:
                def _parse_cp(s):
                    try:
                        if pd.isna(s) or s == '': return []
                        return [int(x) for x in str(s).split('-') if x.strip().isdigit()]
                    except: return []
                df_pred['corner_positions_list'] = df_pred['corner_positions'].apply(_parse_cp)

            # 派生特徴量・Ultimate特徴量を追加
            df_pred = add_derived_features(df_pred, full_history_df=df_pred)
            calculator = UltimateFeatureCalculator(str(db_path))
            df_pred = calculator.add_ultimate_features(df_pred)
            df_pred = df_pred.loc[:, ~df_pred.columns.duplicated()]

            # オプティマイザーで変換（学習時と同じ）
            bundle_optimizer = bundle.get("optimizer")
            bundle_cat_features = bundle.get("categorical_features", [])
            if bundle_optimizer:
                df_pred_opt = bundle_optimizer.transform(df_pred)
            else:
                from keiba_ai.lightgbm_feature_optimizer import prepare_for_lightgbm_ultimate
                df_pred_opt, _, bundle_cat_features = prepare_for_lightgbm_ultimate(
                    df_pred, is_training=False, optimizer=None
                )

            # 学習時と同じ除外カラム
            exclude_cols = ['win', 'place', 'race_id', 'horse_id', 'jockey_id',
                           'trainer_id', 'owner_id', 'finish_position', 'finish']
            X_pred = df_pred_opt.drop([c for c in exclude_cols if c in df_pred_opt.columns], axis=1)
            obj_cols = X_pred.select_dtypes(include=['object']).columns.tolist()
            if obj_cols:
                X_pred = X_pred.drop(columns=obj_cols)

            # モデルの学習時の特徴量と合わせる
            if hasattr(model, 'feature_name'):
                trained_features = model.feature_name()
            elif hasattr(model, 'booster_'):
                trained_features = model.booster_.feature_name()
            else:
                trained_features = list(X_pred.columns)
            missing = [f for f in trained_features if f not in X_pred.columns]
            for _mf in missing:
                X_pred[_mf] = 0.0
            extra = [c for c in X_pred.columns if c not in trained_features]
            X_pred = X_pred.drop(columns=extra, errors='ignore')
            X_pred = X_pred[trained_features]

            # 予測
            win_probs = model.predict(X_pred)

            predictions = []
            for i, _hr in enumerate(_horse_records):
                _raw_odds = _hr.get("odds") or _hr.get("win_odds")
                try:
                    _odds_float = float(_raw_odds) if _raw_odds not in (None, "", "---") else 5.0
                except (ValueError, TypeError):
                    _odds_float = 5.0
                _horse_num = _hr.get("horse_number") or _hr.get("horse_no") or (i + 1)
                predictions.append({
                    "horse_number": _horse_num,
                    "horse_no": _horse_num,
                    "horse_name": _hr.get("horse_name", ""),
                    "jockey_name": _hr.get("jockey_name", ""),
                    "trainer_name": _hr.get("trainer_name", ""),
                    "sex": _hr.get("sex", ""),
                    "age": _hr.get("age"),
                    "horse_weight": _hr.get("weight_kg") or _hr.get("horse_weight"),
                    "odds": _odds_float,
                    "popularity": _hr.get("popularity"),
                    "win_probability": float(win_probs[i]),
                    "expected_value": float(win_probs[i] * _odds_float),
                })

        else:
            # ===== 標準版: 旧テーブル =====
            con = connect(db_path)
            cursor = con.cursor()

            # レース基本情報
            cursor.execute(
                """
                SELECT race_id, race_name, venue, date, distance, track_type,
                       weather, field_condition, num_horses
                FROM races
                WHERE race_id = ?
            """,
                (request.race_id,),
            )
            race_row = cursor.fetchone()

            if not race_row:
                con.close()
                raise HTTPException(
                    status_code=404, detail=f"レース {request.race_id} が見つかりません"
                )

            race_info = {
                "race_id": race_row[0],
                "race_name": race_row[1],
                "venue": race_row[2],
                "date": race_row[3],
                "distance": race_row[4],
                "track_type": race_row[5],
                "weather": race_row[6],
                "field_condition": race_row[7],
                "num_horses": race_row[8],
            }

            cursor.execute(
                """
                SELECT umaban, horse_name, sex, age, kinryo, jockey_name, trainer_name,
                       tansho_odds, popularity, horse_weight, weight_change, wakuban
                FROM results
                WHERE race_id = ?
                ORDER BY umaban
            """,
                (request.race_id,),
            )
            horses_data = cursor.fetchall()
            con.close()

            if not horses_data:
                raise HTTPException(
                    status_code=404,
                    detail=f"レース {request.race_id} の出走馬データが見つかりません",
                )

            predictions = []
            for horse in horses_data:
                features = {
                    "horse_no": horse[0], "bracket": horse[11], "age": horse[3],
                    "handicap": horse[4], "weight": horse[9] or 460,
                    "weight_diff": horse[10] or 0, "entry_odds": horse[7] or 5.0,
                    "entry_popularity": horse[8] or 5,
                    "straight_length": 500, "inner_bias": 0, "inner_advantage": 0,
                    "jockey_course_win_rate": 0.1, "jockey_course_races": 10,
                    "horse_distance_win_rate": 0.1, "horse_distance_avg_finish": 5.0,
                    "trainer_recent_win_rate": 0.1,
                }
                feature_df = pd.DataFrame([features])
                try:
                    win_prob = model.predict_proba(feature_df)[0][1]
                except:
                    win_prob = 0.1
                predictions.append({
                    "horse_number": horse[0], "horse_name": horse[1],
                    "jockey_name": horse[5], "trainer_name": horse[6],
                    "sex": horse[2], "age": horse[3], "horse_weight": horse[9] or 460,
                    "odds": horse[7] or 5.0, "popularity": horse[8] or 5,
                    "win_probability": float(win_prob),
                    "expected_value": float(win_prob * (horse[7] or 5.0)),
                })

        # 購入推奨システム初期化
        recommender = BettingRecommender(
            bankroll=request.bankroll,
            risk_mode=request.risk_mode,
            use_kelly=request.use_kelly,
            dynamic_unit=request.dynamic_unit,
            min_ev=request.min_ev,
        )

        # 分析・推奨実行
        result = recommender.analyze_and_recommend(predictions, race_info)

        return AnalyzeRaceResponse(
            success=True,
            race_info=result["race_info"],
            pro_evaluation=result["pro_evaluation"],
            predictions=result["predictions"],
            bet_types=result["bet_types"],
            best_bet_type=result["best_bet_type"],
            best_bet_info=result["best_bet_info"],
            race_level=result["race_level"],
            recommendation=result["recommendation"],
        )

    except HTTPException:
        raise
    except Exception as e:
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"レース分析に失敗: {str(e)}")


@app.post("/api/purchase", response_model=PurchaseHistoryResponse)
async def save_purchase_history(request: PurchaseHistoryRequest):
    """
    購入履歴保存エンドポイント

    Streamlit 3_予測_batch.py の Tab3 購入ボタン機能:
    tracking.db の purchase_history テーブルに保存
    """
    try:
        # tracking.db接続
        tracking_db_path = CONFIG_PATH.parent / "data" / "tracking.db"
        tracking_db_path.parent.mkdir(parents=True, exist_ok=True)

        con = sqlite3.connect(str(tracking_db_path))
        cursor = con.cursor()

        # テーブル作成（存在しない場合）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS purchase_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_id TEXT NOT NULL,
                purchase_date TEXT,
                season TEXT,
                venue TEXT,
                bet_type TEXT NOT NULL,
                combinations TEXT,
                strategy_type TEXT,
                purchase_count INTEGER,
                unit_price INTEGER,
                total_cost INTEGER,
                expected_value REAL,
                expected_return REAL,
                actual_return INTEGER DEFAULT 0,
                is_hit INTEGER DEFAULT 0,
                recovery_rate REAL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 購入日とシーズン判定
        purchase_date = datetime.now().strftime("%Y-%m-%d")
        month = datetime.now().month
        if 3 <= month <= 5:
            season = "春"
        elif 6 <= month <= 8:
            season = "夏"
        elif 9 <= month <= 11:
            season = "秋"
        else:
            season = "冬"

        # データ挿入
        cursor.execute(
            """
            INSERT INTO purchase_history (
                race_id, purchase_date, season, bet_type, combinations, 
                strategy_type, purchase_count, unit_price, total_cost, 
                expected_value, expected_return
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                request.race_id,
                purchase_date,
                season,
                request.bet_type,
                ",".join(request.combinations),
                request.strategy_type,
                request.purchase_count,
                request.unit_price,
                request.total_cost,
                request.expected_value,
                request.expected_return,
            ),
        )

        purchase_id = cursor.lastrowid
        con.commit()
        con.close()

        return PurchaseHistoryResponse(
            success=True,
            purchase_id=purchase_id,
            message=f"購入履歴を保存しました (ID: {purchase_id})",
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"購入履歴の保存に失敗: {str(e)}")


@app.get("/api/purchase_history")
async def get_purchase_history(limit: int = 50):
    """
    購入履歴取得エンドポイント

    Streamlit 3_予測_batch.py の Tab4 検証結果表示機能
    """
    try:
        tracking_db_path = CONFIG_PATH.parent / "data" / "tracking.db"

        if not tracking_db_path.exists():
            return {
                "success": True,
                "history": [],
                "count": 0,
                "message": "購入履歴がまだありません",
            }

        con = sqlite3.connect(str(tracking_db_path))
        cursor = con.cursor()

        cursor.execute(
            """
            SELECT id, race_id, purchase_date, season, bet_type, combinations,
                   strategy_type, purchase_count, unit_price, total_cost,
                   expected_value, expected_return, actual_return, 
                   is_hit, recovery_rate, created_at
            FROM purchase_history
            ORDER BY created_at DESC
            LIMIT ?
        """,
            (limit,),
        )

        rows = cursor.fetchall()

        history = []
        for row in rows:
            history.append(
                {
                    "id": row[0],
                    "race_id": row[1],
                    "purchase_date": row[2],
                    "season": row[3],
                    "bet_type": row[4],
                    "combinations": row[5].split(",") if row[5] else [],
                    "strategy_type": row[6],
                    "purchase_count": row[7],
                    "unit_price": row[8],
                    "total_cost": row[9],
                    "expected_value": row[10],
                    "expected_return": row[11],
                    "actual_return": row[12],
                    "is_hit": bool(row[13]),
                    "recovery_rate": row[14],
                    "created_at": row[15],
                }
            )

        con.close()

        # 統計サマリー
        total_cost = sum(h["total_cost"] for h in history)
        total_return = sum(h["actual_return"] for h in history)
        hit_count = sum(1 for h in history if h["is_hit"])

        return {
            "success": True,
            "history": history,
            "count": len(history),
            "summary": {
                "total_cost": total_cost,
                "total_return": total_return,
                "recovery_rate": (
                    round(total_return / total_cost * 100, 1) if total_cost > 0 else 0
                ),
                "hit_count": hit_count,
                "hit_rate": round(hit_count / len(history) * 100, 1) if history else 0,
            },
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"購入履歴の取得に失敗: {str(e)}")


@app.get("/api/statistics")
async def get_statistics():
    """
    統計サマリー取得エンドポイント

    期間別回収率、馬券種別回収率、競馬場別回収率などの統計
    """
    try:
        tracking_db_path = CONFIG_PATH.parent / "data" / "tracking.db"

        if not tracking_db_path.exists():
            return {
                "success": True,
                "statistics": {},
                "message": "統計データがまだありません",
            }

        con = sqlite3.connect(str(tracking_db_path))
        cursor = con.cursor()

        # 馬券種別統計
        cursor.execute("""
            SELECT bet_type, 
                   COUNT(*) as count,
                   SUM(total_cost) as total_cost,
                   SUM(actual_return) as total_return,
                   SUM(is_hit) as hit_count
            FROM purchase_history
            GROUP BY bet_type
        """)

        bet_type_stats = []
        for row in cursor.fetchall():
            bet_type_stats.append(
                {
                    "bet_type": row[0],
                    "count": row[1],
                    "total_cost": row[2],
                    "total_return": row[3],
                    "recovery_rate": (
                        round(row[3] / row[2] * 100, 1) if row[2] > 0 else 0
                    ),
                    "hit_count": row[4],
                    "hit_rate": round(row[4] / row[1] * 100, 1) if row[1] > 0 else 0,
                }
            )

        # シーズン別統計
        cursor.execute("""
            SELECT season, 
                   COUNT(*) as count,
                   SUM(total_cost) as total_cost,
                   SUM(actual_return) as total_return
            FROM purchase_history
            GROUP BY season
        """)

        season_stats = []
        for row in cursor.fetchall():
            season_stats.append(
                {
                    "season": row[0],
                    "count": row[1],
                    "total_cost": row[2],
                    "total_return": row[3],
                    "recovery_rate": (
                        round(row[3] / row[2] * 100, 1) if row[2] > 0 else 0
                    ),
                }
            )

        con.close()

        return {
            "success": True,
            "statistics": {"by_bet_type": bet_type_stats, "by_season": season_stats},
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"統計の取得に失敗: {str(e)}")


@app.get("/api/models/{model_id}")
async def get_model_info(model_id: str):
    """
    特定のモデル情報を取得
    """
    try:
        model_files = list(MODELS_DIR.glob(f"model_*_{model_id}.joblib"))
        if not model_files:
            raise HTTPException(
                status_code=404, detail=f"モデル {model_id} が見つかりません"
            )

        bundle = load_model_bundle(model_files[0])

        return {
            "success": True,
            "model_id": model_id,
            "model_path": str(model_files[0]),
            "created_at": bundle.get("created_at", "unknown"),
            "target": bundle.get("target", "unknown"),
            "model_type": bundle.get("model_type", "unknown"),
            "metrics": bundle.get("metrics", {}),
            "data_count": bundle.get("data_count", 0),
            "race_count": bundle.get("race_count", 0),
            "feature_count": len(bundle.get("feature_cols_num", []))
            + len(bundle.get("feature_cols_cat", [])),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"モデル情報の取得に失敗: {str(e)}")


# ============================================
# スクレイピングエンドポイント
# ============================================

class ScrapeRequest(BaseModel):
    """スクレイピングリクエスト"""
    start_date: str  # YYYYMMDD形式
    end_date: str    # YYYYMMDD形式

class ScrapeResponse(BaseModel):
    """スクレイピングレスポンス"""
    success: bool
    message: str
    races_collected: int
    db_path: str
    elapsed_time: float

# ============================================
# 完全スクレイピング共通関数
# ============================================

VENUE_MAP = {
    '01': '札幌', '02': '函館', '03': '福島', '04': '新潟',
    '05': '東京', '06': '中山', '07': '中京', '08': '京都',
    '09': '阪神', '10': '小倉'
}

SCRAPE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
}


async def _scrape_race_full(session, race_id: str, date_hint: str = '') -> Optional[dict]:
    """
    単一レースの完全データをnetkeiba.comから取得。
    race_results_ultimate / races_ultimate 形式で返す。
    date_hint: YYYYMMDD 形式の日付（リストページから判明した場合に渡す）
    """
    import aiohttp
    from bs4 import BeautifulSoup
    import re

    url = f"https://db.netkeiba.com/race/{race_id}/"
    try:
        await asyncio.sleep(0.6)
        async with session.get(url) as resp:
            if resp.status != 200:
                logger.warning(f"HTTP {resp.status}: {url}")
                return None
            content = await resp.read()
            html = content.decode('euc-jp', errors='ignore')
    except Exception as e:
        logger.error(f"取得エラー {race_id}: {e}")
        return None

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')

    # ---- レース基本情報 ----
    # race_name: ロゴ h1 を除く最初の非空 h1
    race_name = ''
    for h1 in soup.find_all('h1'):
        txt = h1.get_text(strip=True)
        if txt:
            race_name = txt
            break

    # ---- info_text の取得: div.mainrace_data → fallback p.smalltxt → html全体 ----
    # 新HTML構造: div.mainrace_data に距離・天候・開催情報が集約されている
    mainrace_div = soup.find('div', class_='mainrace_data')
    if mainrace_div:
        info_text = mainrace_div.get_text(' ')
    else:
        # フォールバック: p.smalltxt（以前の class名）または div.smalltxt
        smalltxt = (soup.find('p', class_='smalltxt') or
                    soup.find('div', class_='smalltxt'))
        info_text = smalltxt.get_text(' ') if smalltxt else html[:3000]

    # ---- 距離・芝/ダート ----
    # 新形式: "芝左1400m" "ダ右2000m" "障害右3900m" など（方向文字が挟まる場合がある）
    dist_m = re.search(r'(芝|ダ)[右左直外内障]?\s*(\d+)m', info_text)
    if dist_m:
        track_type = '芝' if dist_m.group(1) == '芝' else 'ダート'
        distance = int(dist_m.group(2))
    else:
        track_type = ''
        distance = 0

    # ---- 天候 ----
    weather_m = re.search(r'天候\s*[:/：]\s*([^\s/]+)', info_text)
    weather = weather_m.group(1).strip() if weather_m else ''

    # ---- 馬場状態 ----
    # 新形式: "芝 : 良" "ダート : 稍重"（旧形式: "馬場 : 良"）
    cond_m = (re.search(r'(?:芝|ダート)\s*[:/：]\s*([^\s/]+)', info_text) or
              re.search(r'馬場\s*[:/：]\s*([^\s/]+)', info_text))
    field_condition = cond_m.group(1).strip() if cond_m else ''

    venue_code = race_id[4:6]
    venue = VENUE_MAP.get(venue_code, venue_code)

    # ---- 日付: date_hint 優先、次に HTML から抽出 ----
    date_str = date_hint  # YYYYMMDD
    if not date_str:
        # p.smalltxt の日付が最も信頼できる
        smalltxt_p = soup.find('p', class_='smalltxt')
        if smalltxt_p:
            stxt = smalltxt_p.get_text()
            sdm = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', stxt)
            if sdm:
                date_str = f"{sdm.group(1)}{int(sdm.group(2)):02d}{int(sdm.group(3)):02d}"
    if not date_str:
        body_date_m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', html)
        if body_date_m:
            date_str = f"{body_date_m.group(1)}{int(body_date_m.group(2)):02d}{int(body_date_m.group(3)):02d}"
    if not date_str:
        candidate = race_id[:8]
        try:
            datetime.strptime(candidate, '%Y%m%d')
            date_str = candidate
        except ValueError:
            date_str = ''
    logger.debug(f"race_id={race_id} 日付={date_str}")

    # ---- 発走時刻: info_text から直接抽出 ----
    post_time = ''
    pt_m = re.search(r'発走\s*[:/：]?\s*(\d{1,2}:\d{2})', info_text)
    if pt_m:
        post_time = pt_m.group(1)

    # ---- レースクラス（G1/G2/G3/重賞/新馬/未勝利/1勝クラス等） ----
    race_class = ''
    # p.smalltxt に「4歳以上1勝クラス」等が含まれる
    smalltxt_text = ''
    smalltxt_p2 = soup.find('p', class_='smalltxt')
    if smalltxt_p2:
        smalltxt_text = smalltxt_p2.get_text(' ')
    for src in [race_name, smalltxt_text, info_text]:
        if race_class:
            break
        for pat in [r'(G[1-3])', r'(新馬)', r'(未勝利)', r'([1-3]勝クラス)', r'(オープン)', r'(重賞)']:
            cm = re.search(pat, src)
            if cm:
                race_class = cm.group(1)
                break

    # ---- 開催回・日目: p.smalltxt から抽出 ----
    # 形式: "2025年01月12日 1回中京4日目 4歳以上1勝クラス"
    kai = None
    day = None
    kai_src = smalltxt_text or info_text
    kai_m = re.search(r'(\d+)回', kai_src)
    if kai_m:
        kai = int(kai_m.group(1))
    day_m = re.search(r'(\d+)日目', kai_src)
    if day_m:
        day = int(day_m.group(1))

    # ---- コース方向（右/左/直線） ----
    # 形式: "芝左1400m" "ダ右2000m"
    course_direction = ''
    dir_m = re.search(r'[芝ダ](右|左)(外)?', info_text)
    if dir_m:
        course_direction = dir_m.group(1) + (dir_m.group(2) or '')
    elif '直線' in info_text:
        course_direction = '直線'

    # ---- 結果テーブル ----
    table = soup.find('table', class_='race_table_01')
    if not table:
        logger.warning(f"race_table_01 not found: {race_id}")
        return None

    all_rows = table.find_all('tr')
    if not all_rows:
        return None

    # ヘッダー行から列インデックスを動的に決定
    header_row = all_rows[0]
    header_cells = header_row.find_all(['th', 'td'])
    header_texts = [c.get_text(strip=True) for c in header_cells]

    def col_idx(names, default=-1):
        """ヘッダーテキストから列インデックスを返す（複数候補を順に試す）"""
        for name in names:
            for i, h in enumerate(header_texts):
                if name in h:
                    return i
        return default

    # 各列のインデックス（ヘッダーから動的解決）
    IDX_FINISH    = col_idx(['着順'], 0)
    IDX_BRACKET   = col_idx(['枠番'], 1)
    IDX_HORSE_NUM = col_idx(['馬番'], 2)
    IDX_HORSE     = col_idx(['馬名'], 3)
    IDX_SEX_AGE   = col_idx(['性齢'], 4)
    IDX_JW        = col_idx(['斤量'], 5)
    IDX_JOCKEY    = col_idx(['騎手'], 6)
    IDX_TIME      = col_idx(['タイム'], 7)
    IDX_MARGIN    = col_idx(['着差'], 8)
    IDX_CORNER    = col_idx(['通過', 'コーナー'], 10)
    IDX_LAST3F    = col_idx(['上り'], 11)
    IDX_ODDS      = col_idx(['単勝'], 9)
    IDX_POP       = col_idx(['人気'], 10)
    IDX_WEIGHT    = col_idx(['馬体重'], 13)
    IDX_TRAINER   = col_idx(['調教師'], 14)
    IDX_PRIZE     = col_idx(['賞金'], 15)

    # ヘッダーに「タイム指数」があれば列オフセットを再計算
    has_time_index = any('ﾀｲﾑ指数' in h or 'タイム指数' in h for h in header_texts)
    if has_time_index:
        IDX_CORNER = col_idx(['通過', 'コーナー'], 10)
        IDX_LAST3F = col_idx(['上り'], 11)
        IDX_ODDS   = col_idx(['単勝'], 12)
        IDX_POP    = col_idx(['人気'], 13)
        IDX_WEIGHT = col_idx(['馬体重'], 14)
        IDX_TRAINER = col_idx(['調教師'], 18)
        IDX_PRIZE   = col_idx(['賞金'], 20)

    logger.debug(f"テーブルヘッダー({race_id}): {header_texts}")

    horse_rows = all_rows[1:]
    num_horses = len(horse_rows)
    horses = []

    for row in horse_rows:
        cols = row.find_all('td')
        if len(cols) < 8:
            continue
        try:
            def txt(i):
                return cols[i].get_text(strip=True) if i < len(cols) else ''

            def link_href(i):
                a = cols[i].find('a') if i < len(cols) else None
                href = a['href'] if a and 'href' in a.attrs else ''
                if href and not href.startswith('http'):
                    href = 'https://db.netkeiba.com' + href
                return href

            def link_text(i):
                a = cols[i].find('a') if i < len(cols) else None
                return a.get_text(strip=True) if a else txt(i)

            finish_pos = txt(IDX_FINISH)
            try:
                finish_position = int(finish_pos)
            except ValueError:
                finish_position = finish_pos  # "中止" 等

            bracket_t = txt(IDX_BRACKET)
            bracket_number = int(bracket_t) if bracket_t.isdigit() else None

            horse_num_t = txt(IDX_HORSE_NUM)
            horse_number = int(horse_num_t) if horse_num_t.isdigit() else None

            horse_name = link_text(IDX_HORSE)
            horse_url = link_href(IDX_HORSE)
            horse_id_m = re.search(r'/horse/(\d+)', horse_url)
            horse_id = horse_id_m.group(1) if horse_id_m else ''

            sex_age = txt(IDX_SEX_AGE)
            sex = sex_age[0] if sex_age else ''
            age_m = re.search(r'\d+', sex_age)
            age = int(age_m.group()) if age_m else None

            jw_t = txt(IDX_JW)
            jockey_weight = float(jw_t) if jw_t else None

            jockey_name = link_text(IDX_JOCKEY)
            jockey_url = link_href(IDX_JOCKEY)
            jockey_id_m = re.search(r'/jockey/(?:result/recent/)?(\d+)', jockey_url)
            jockey_id = jockey_id_m.group(1) if jockey_id_m else ''

            finish_time = txt(IDX_TIME)
            margin = txt(IDX_MARGIN)

            odds_t = txt(IDX_ODDS) if IDX_ODDS >= 0 and IDX_ODDS < len(cols) else ''
            try:
                odds = float(odds_t)
            except (ValueError, TypeError):
                odds = None

            pop_t = txt(IDX_POP) if IDX_POP >= 0 and IDX_POP < len(cols) else ''
            popularity = int(pop_t) if pop_t.isdigit() else None

            corner_positions = txt(IDX_CORNER) if IDX_CORNER >= 0 and IDX_CORNER < len(cols) else ''
            last_3f_str = txt(IDX_LAST3F) if IDX_LAST3F >= 0 and IDX_LAST3F < len(cols) else ''
            weight_text = txt(IDX_WEIGHT) if IDX_WEIGHT >= 0 and IDX_WEIGHT < len(cols) else ''

            weight_kg = None
            weight_change = None
            wm = re.match(r'(\d+)\(([+-]?\d+)\)', weight_text)
            if wm:
                weight_kg = int(wm.group(1))
                weight_change = int(wm.group(2))

            trainer_name = link_text(IDX_TRAINER) if IDX_TRAINER >= 0 and IDX_TRAINER < len(cols) else ''
            trainer_url = link_href(IDX_TRAINER) if IDX_TRAINER >= 0 and IDX_TRAINER < len(cols) else ''
            trainer_id_m = re.search(r'/trainer/(?:result/recent/)?(\d+)', trainer_url)
            trainer_id = trainer_id_m.group(1) if trainer_id_m else ''

            prize_t = txt(IDX_PRIZE) if IDX_PRIZE >= 0 and IDX_PRIZE < len(cols) else ''
            try:
                prize_money = float(prize_t.replace(',', '')) * 10000 if prize_t else None
            except ValueError:
                prize_money = None

            # corner_positions_list
            cp_list = []
            if corner_positions:
                cp_list = [int(x) for x in corner_positions.split('-') if x.strip().isdigit()]
            n_cp = len(cp_list)

            horses.append({
                'race_id': race_id,
                'finish_position': finish_position,
                'bracket_number': bracket_number,
                'horse_number': horse_number,
                'horse_name': horse_name,
                'horse_url': horse_url,
                'horse_id': horse_id,
                'sex_age': sex_age,
                'sex': sex,
                'age': age,
                'jockey_weight': jockey_weight,
                'jockey_name': jockey_name,
                'jockey_url': jockey_url,
                'jockey_id': jockey_id,
                'finish_time': finish_time,
                'margin': margin,
                'odds': odds,
                'popularity': popularity,
                'corner_positions': corner_positions,
                'corner_positions_list': cp_list,
                'corner_1': cp_list[0] if n_cp >= 1 else None,
                'corner_2': cp_list[1] if n_cp >= 2 else None,
                'corner_3': cp_list[2] if n_cp >= 3 else None,
                'corner_4': cp_list[3] if n_cp >= 4 else None,
                'last_3f': last_3f_str,
                'weight': weight_text,
                'weight_kg': weight_kg,
                'weight_change': weight_change,
                'trainer_name': trainer_name,
                'trainer_url': trainer_url,
                'trainer_id': trainer_id,
                'prize_money': prize_money,
            })
        except Exception as ex:
            logger.debug(f"row parse error {race_id}: {ex}")
            continue

    # last_3f_rank 計算
    last_3f_vals = []
    for h in horses:
        try:
            last_3f_vals.append(float(h['last_3f']))
        except (ValueError, TypeError):
            last_3f_vals.append(float('inf'))
    sorted_idx = sorted(range(len(last_3f_vals)), key=lambda i: last_3f_vals[i])
    ranks = [0] * len(horses)
    for rank, idx in enumerate(sorted_idx):
        if last_3f_vals[idx] != float('inf'):
            ranks[idx] = rank + 1
    for i, h in enumerate(horses):
        h['last_3f_rank'] = ranks[i] if ranks[i] > 0 else None

    # ---- ラップタイム解析 ----
    lap_cumulative = {}
    lap_sectional = {}
    for tbl in soup.find_all('table'):
        rows = tbl.find_all('tr')
        if len(rows) < 2:
            continue
        header_cells = rows[0].find_all(['th', 'td'])
        headers_text = [c.get_text(strip=True).replace('\u3000', '').replace(' ', '') for c in header_cells]
        dists = []
        for h_txt in headers_text:
            dm = re.match(r'^(\d+)m?$', h_txt)
            if dm:
                d = int(dm.group(1))
                if 100 <= d <= 4000 and d % 200 == 0:
                    dists.append(d)
        if len(dists) >= 3:
            time_cells = rows[1].find_all('td')
            for i, dist in enumerate(dists):
                if i < len(time_cells):
                    try:
                        t = float(time_cells[i].get_text(strip=True))
                        if 5.0 <= t <= 200.0:
                            lap_cumulative[dist] = t
                    except ValueError:
                        pass
            if lap_cumulative:
                sorted_dists = sorted(lap_cumulative.keys())
                prev = 0.0
                for d in sorted_dists:
                    lap_sectional[d] = round(lap_cumulative[d] - prev, 1)
                    prev = lap_cumulative[d]
                break

    # ---- 馬詳細スクレイピング（血統/通算成績/前走情報） 3並列 ----
    sem = asyncio.Semaphore(3)

    async def _fetch_detail_limited(h):
        hid = h.get('horse_id', '')
        hurl = h.get('horse_url', '')
        if not hid:
            return
        async with sem:
            detail = await _scrape_horse_detail(session, hid, hurl)
            h.update(detail)

    seen_horse_ids: set = set()
    unique_horses = []
    for h in horses:
        hid = h.get('horse_id', '')
        if hid and hid not in seen_horse_ids:
            seen_horse_ids.add(hid)
            unique_horses.append(h)

    await asyncio.gather(*[_fetch_detail_limited(h) for h in unique_horses])

    return {
        'race_info': {
            'race_id': race_id,
            'race_name': race_name,
            'venue': venue,
            'date': date_str,
            'post_time': post_time,
            'race_class': race_class,
            'kai': kai,
            'day': day,
            'course_direction': course_direction,
            'distance': distance,
            'track_type': track_type,
            'weather': weather,
            'field_condition': field_condition,
            'num_horses': num_horses,
            'surface': None,
            'lap_cumulative': lap_cumulative,
            'lap_sectional': lap_sectional,
        },
        'horses': horses,
    }


def _parse_blood_table(blood_table, result: dict):
    """blood_table (BeautifulSoup Tag) から sire/dam/damsire を抽出する共通ロジック。
    複数の HTML パターン（class 名・行構造の違い）に対応する。"""
    trs = blood_table.find_all('tr')
    if not trs:
        return

    half = len(trs) // 2  # 5世代=32行 → half=16

    # ---- 父 (sire) ----
    # パターン1: tr[0] の最初の td の <a>
    sire_tds = trs[0].find_all('td')
    if sire_tds:
        a = sire_tds[0].find('a')
        if a:
            result['sire'] = a.get_text(strip=True)

    # パターン2: class="b_ml" のセルから取得
    if not result.get('sire'):
        for td in blood_table.find_all('td', class_=lambda c: c and 'b_ml' in c):
            a = td.find('a')
            if a:
                result['sire'] = a.get_text(strip=True)
                break

    # ---- 母 (dam) / 母の父 (damsire) ----
    if half > 0 and len(trs) > half:
        dam_tds = trs[half].find_all('td')
        if dam_tds:
            a = dam_tds[0].find('a')
            if a:
                result['dam'] = a.get_text(strip=True)
        if len(dam_tds) >= 2:
            a = dam_tds[1].find('a')
            if a:
                result['damsire'] = a.get_text(strip=True)

    # パターン2: class="b_fml" から母を取得
    if not result.get('dam'):
        for td in blood_table.find_all('td', class_=lambda c: c and 'b_fml' in c):
            a = td.find('a')
            if a:
                result['dam'] = a.get_text(strip=True)
                break


async def _scrape_horse_detail(session, horse_id: str, horse_url: str = '') -> dict:
    """
    馬の詳細ページをスクレイピング。
    血統(sire/dam/damsire)、プロフィール、通算成績、直近2走を取得。
    """
    from bs4 import BeautifulSoup
    import re

    if not horse_id and not horse_url:
        return {}

    url = horse_url if horse_url.startswith('http') else f"https://db.netkeiba.com/horse/{horse_id}/"
    # horse_url の末尾スラッシュを保証
    if url and not url.endswith('/'):
        url = url + '/'
    result = {}
    try:
        await asyncio.sleep(0.4)
        async with session.get(url) as resp:
            if resp.status != 200:
                return result
            content = await resp.read()
            html = content.decode('euc-jp', errors='ignore')
    except Exception as e:
        logger.debug(f"馬詳細取得失敗 {horse_id}: {e}")
        return result

    soup = BeautifulSoup(html, 'html.parser')

    # ===== プロフィール（db_prof_table から取得） =====
    prof_table = soup.find('table', class_='db_prof_table')
    if prof_table:
        for row in prof_table.find_all('tr'):
            th = row.find('th')
            td = row.find('td')
            if not th or not td:
                continue
            key = th.get_text(strip=True)
            val = td.get_text(strip=True)
            if '生年月日' in key:
                result['horse_birth_date'] = val
            elif '毛色' in key:
                result['horse_coat_color'] = val
            elif '馬主' in key and 'horse_owner' not in result:
                result['horse_owner'] = val
            elif '生産者' in key and 'horse_breeder' not in result:
                result['horse_breeder'] = val
            elif '産地' in key and 'horse_breeding_farm' not in result:
                result['horse_breeding_farm'] = val
            elif '通算成績' in key:
                runs_m = re.search(r'(\d+)戦\s*(\d+)勝', val)
                if runs_m:
                    result['horse_total_runs'] = int(runs_m.group(1))
                    result['horse_total_wins'] = int(runs_m.group(2))
            elif '獲得賞金' in key and '中央' in key:
                prize_m = re.search(r'([\d,]+)', val)
                if prize_m:
                    try:
                        result['horse_total_prize_money'] = float(prize_m.group(1).replace(',', '')) * 10000
                    except ValueError:
                        pass
    else:
        # フォールバック: ページ全文から通算成績を取得
        full_text = soup.get_text()
        runs_m = re.search(r'(\d+)戦\s*(\d+)勝', full_text)
        if runs_m:
            result['horse_total_runs'] = int(runs_m.group(1))
            result['horse_total_wins'] = int(runs_m.group(2))
        prize_m = re.search(r'獲得賞金[^\d]*([\d,]+(?:\.\d+)?)\s*万円', full_text)
        if prize_m:
            try:
                result['horse_total_prize_money'] = float(prize_m.group(1).replace(',', '')) * 10000
            except ValueError:
                pass
        for table in soup.find_all('table'):
            for row in table.find_all('tr'):
                th_tag = row.find('th')
                td_tag = row.find('td')
                if not th_tag or not td_tag:
                    continue
                key = th_tag.get_text(strip=True)
                val = td_tag.get_text(strip=True)
                if '生年月日' in key:
                    result['horse_birth_date'] = val
                elif '馬主' in key and 'horse_owner' not in result:
                    result['horse_owner'] = val
                elif '生産者' in key and 'horse_breeder' not in result:
                    result['horse_breeder'] = val
                elif '産地' in key and 'horse_breeding_farm' not in result:
                    result['horse_breeding_farm'] = val

    # ===== 血統 (sire / dam / damsire) =====
    # 優先順位: 1) Supabase キャッシュ  2) メインページの blood_table
    #           3) ped 専用ページ（最大3回リトライ）  4) キャッシュ保存

    pedigree_cached = False

    # 1. Supabase キャッシュ確認（HTTP リクエスト不要）
    if SUPABASE_ENABLED and horse_id:
        try:
            cached = get_pedigree_cache(horse_id)
            if cached:
                result['sire'] = cached.get('sire') or ''
                result['dam'] = cached.get('dam') or ''
                result['damsire'] = cached.get('damsire') or ''
                pedigree_cached = True
                logger.debug(f"血統キャッシュヒット: {horse_id} sire={result['sire']}")
        except Exception as _e:
            logger.debug(f"血統キャッシュ確認失敗: {_e}")

    if not pedigree_cached:
        # 2. メインページの blood_table を確認（追加 HTTP リクエスト不要）
        blood_table_main = soup.find('table', class_='blood_table')
        if blood_table_main:
            _parse_blood_table(blood_table_main, result)
            logger.debug(f"メインページ血統: {horse_id} sire={result.get('sire')}")

        # 3. sire が未取得なら ped 専用ページから取得（最大3回リトライ）
        if not result.get('sire'):
            ped_url = f"https://db.netkeiba.com/horse/ped/{horse_id}/"
            for attempt in range(3):
                try:
                    wait = 0.4 + attempt * 1.5
                    await asyncio.sleep(wait)
                    async with session.get(ped_url) as ped_resp:
                        if ped_resp.status == 200:
                            ped_content = await ped_resp.read()
                            ped_html = ped_content.decode('euc-jp', errors='ignore')
                            ped_soup = BeautifulSoup(ped_html, 'html.parser')
                            blood_table = ped_soup.find('table', class_='blood_table')
                            if blood_table:
                                _parse_blood_table(blood_table, result)
                                if result.get('sire'):
                                    logger.debug(f"ped ページ血統取得成功: {horse_id} 試行{attempt+1} sire={result['sire']}")
                                    break
                        elif ped_resp.status == 429:
                            # レート制限: 待機してリトライ
                            await asyncio.sleep(5.0 + attempt * 3.0)
                            continue
                        else:
                            logger.debug(f"ped ページ HTTP {ped_resp.status}: {horse_id}")
                            break
                except Exception as _e:
                    logger.debug(f"ped ページ取得失敗 試行{attempt+1} {horse_id}: {_e}")
                    if attempt < 2:
                        await asyncio.sleep(2.0 ** attempt)

        # 4. 結果をキャッシュ保存（失敗時も空で保存して再取得を防ぐ）
        if SUPABASE_ENABLED and horse_id:
            try:
                save_pedigree_cache(
                    horse_id,
                    result.get('sire', ''),
                    result.get('dam', ''),
                    result.get('damsire', ''),
                )
            except Exception:
                pass

    # ===== 過去レース結果（最新2走）: /horse/result/{horse_id}/ から取得 =====
    result_url = f"https://db.netkeiba.com/horse/result/{horse_id}/"
    try:
        await asyncio.sleep(0.4)
        async with session.get(result_url) as res_resp:
            if res_resp.status == 200:
                res_content = await res_resp.read()
                res_html = res_content.decode('euc-jp', errors='ignore')
                res_soup = BeautifulSoup(res_html, 'html.parser')

                race_hist_table = None
                for tbl in res_soup.find_all('table'):
                    headers = [th.get_text(strip=True) for th in tbl.find_all('th')]
                    if '日付' in headers and ('着順' in headers or '着' in headers):
                        race_hist_table = tbl
                        break

                if race_hist_table:
                    # ヘッダー行のみから th を取得（全体の find_all('th') だとインデックスがずれる）
                    header_rows = [r for r in race_hist_table.find_all('tr') if r.find('th')]
                    if header_rows:
                        header_ths = header_rows[0].find_all('th')
                        headers = [th.get_text(strip=True) for th in header_ths]
                    else:
                        headers = []
                    # 逆引き辞書（重複キーは後勝ち）
                    cidx = {h: i for i, h in enumerate(headers)}
                    date_i   = cidx.get('日付', 0)
                    venue_i  = cidx.get('開催', 1)
                    finish_i = cidx.get('着順', cidx.get('着', -1))
                    time_i   = cidx.get('タイム', -1)
                    weight_i = cidx.get('馬体重', -1)
                    # 距離列: '距離' → '芝・距離' → 'コース' の順で探す
                    course_i = -1
                    for cname in ['距離', 'コース', '芝・距離']:
                        if cname in cidx:
                            course_i = cidx[cname]
                            break
                    if course_i == -1:
                        course_i = next((cidx[h] for h in headers if 'コース' in h or '距離' in h), -1)

                    data_rows = [r for r in race_hist_table.find_all('tr') if r.find('td')]
                    for i, row in enumerate(data_rows[:2]):
                        cols = row.find_all('td')
                        pfx = 'prev' if i == 0 else 'prev2'
                        try:
                            if date_i < len(cols):
                                result[f'{pfx}_race_date'] = cols[date_i].get_text(strip=True)
                            if venue_i < len(cols):
                                result[f'{pfx}_race_venue'] = cols[venue_i].get_text(strip=True)
                            if finish_i != -1 and finish_i < len(cols):
                                fin_t = cols[finish_i].get_text(strip=True)
                                if re.match(r'^\d+$', fin_t):
                                    result[f'{pfx}_race_finish'] = int(fin_t)
                            if time_i != -1 and time_i < len(cols):
                                t_t = cols[time_i].get_text(strip=True)
                                # "1:23.4" または "83.4" 形式
                                tm = re.match(r'(\d+):(\d+\.\d+)', t_t)
                                if tm:
                                    result[f'{pfx}_race_time'] = float(tm.group(1)) * 60 + float(tm.group(2))
                                else:
                                    try:
                                        result[f'{pfx}_race_time'] = float(t_t)
                                    except ValueError:
                                        pass
                            if weight_i != -1 and weight_i < len(cols):
                                w_t = cols[weight_i].get_text(strip=True)
                                w_m = re.match(r'(\d+)', w_t)
                                if w_m:
                                    result[f'{pfx}_race_weight'] = int(w_m.group(1))
                            if course_i != -1 and course_i < len(cols):
                                c_t = cols[course_i].get_text(strip=True)
                                d_m = re.search(r'(\d{3,4})', c_t)
                                if d_m:
                                    result[f'{pfx}_race_distance'] = int(d_m.group(1))
                                # 芝/ダート種別も取得
                                if '芝' in c_t:
                                    result[f'{pfx}_race_surface'] = '芝'
                                elif 'ダ' in c_t or 'ダート' in c_t:
                                    result[f'{pfx}_race_surface'] = 'ダート'
                        except Exception:
                            pass
    except Exception as e:
        logger.debug(f"過去成績ページ取得失敗 {horse_id}: {e}")

    return result


def _save_race_to_ultimate_db(race_data: dict, db_path: Path, overwrite: bool = True):
    """スクレイピング結果を keiba_ultimate.db と Supabase の両方に保存"""
    # Supabase に保存
    if SUPABASE_ENABLED:
        try:
            save_race_to_supabase(race_data)
        except Exception as e:
            logger.warning(f"Supabase 保存失敗（ローカル保存は継続）: {e}")
    import json as json_mod
    import sqlite3

    race_info = race_data['race_info']
    horses = race_data['horses']
    race_id = race_info['race_id']

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # races_ultimate
    cur.execute("""
        CREATE TABLE IF NOT EXISTS races_ultimate (
            race_id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute(
        "INSERT OR REPLACE INTO races_ultimate (race_id, data) VALUES (?, ?)",
        (race_id, json_mod.dumps(race_info, ensure_ascii=False))
    )

    # race_results_ultimate
    cur.execute("""
        CREATE TABLE IF NOT EXISTS race_results_ultimate (
            race_id TEXT,
            data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    if overwrite:
        cur.execute("DELETE FROM race_results_ultimate WHERE race_id = ?", (race_id,))
    for h in horses:
        cur.execute(
            "INSERT INTO race_results_ultimate (race_id, data) VALUES (?, ?)",
            (race_id, json_mod.dumps(h, ensure_ascii=False))
        )

    conn.commit()
    conn.close()


# ============================================
# 非同期スクレイピングジョブ管理
# POST /api/scrape/start  → 即座に job_id を返す
# GET  /api/scrape/status/{job_id} → 進捗/結果をポーリング
# ============================================

import uuid

# メモリ上のジョブストア（Render 再起動でリセット、それで問題なし）
_scrape_jobs: dict = {}    # job_id -> {"status", "progress", "result", "error"}


async def _run_scrape_job(job_id: str, start_date: str, end_date: str):
    """バックグラウンドでスクレイピングを実行しジョブストアを更新する"""
    import aiohttp
    import time as _time

    job = _scrape_jobs[job_id]
    job["status"] = "running"

    ULTIMATE_DB = Path(__file__).parent.parent / "keiba" / "data" / "keiba_ultimate.db"
    start_time = _time.time()

    try:
        from datetime import datetime as _dt, timedelta as _td
        def _parse(s):
            for fmt in ("%Y%m%d", "%Y/%m/%d", "%Y-%m-%d"):
                try:
                    return _dt.strptime(s, fmt)
                except ValueError:
                    pass
            raise ValueError(f"日付フォーマット不正: {s}")

        s_dt = _parse(start_date)
        e_dt = _parse(end_date)
        dates = []
        cur = s_dt
        while cur <= e_dt:
            if cur.weekday() in [5, 6]:
                dates.append(cur.strftime("%Y%m%d"))
            cur += _td(days=1)

        total = len(dates)
        job["progress"] = {"done": 0, "total": total, "message": f"0/{total}日処理済み"}

        timeout = aiohttp.ClientTimeout(total=30)
        connector = aiohttp.TCPConnector(limit=5, limit_per_host=3)
        saved_races = 0
        saved_horses = 0

        async with aiohttp.ClientSession(
            headers=SCRAPE_HEADERS, timeout=timeout, connector=connector
        ) as session:
            for i, date in enumerate(dates):
                list_url = f"https://db.netkeiba.com/race/list/{date}/"
                try:
                    await asyncio.sleep(0.5)
                    async with session.get(list_url) as resp:
                        if resp.status != 200:
                            continue
                        content = await resp.read()
                        html = content.decode('euc-jp', errors='ignore')

                    from bs4 import BeautifulSoup
                    import re as _re
                    soup = BeautifulSoup(html, 'html.parser')
                    race_ids = []
                    for a in soup.find_all('a', href=True):
                        m = _re.search(r'/race/(\d{12})/', a['href'])
                        if m and m.group(1) not in race_ids:
                            race_ids.append(m.group(1))

                    for race_id in race_ids:
                        race_data = await _scrape_race_full(session, race_id, date_hint=date)
                        if race_data and race_data['horses']:
                            _save_race_to_ultimate_db(race_data, ULTIMATE_DB, overwrite=True)
                            saved_races += 1
                            saved_horses += len(race_data['horses'])

                except Exception as e:
                    logger.error(f"ジョブ {job_id} {date} エラー: {e}")

                job["progress"] = {
                    "done": i + 1,
                    "total": total,
                    "message": f"{i+1}/{total}日処理済み / {saved_races}レース取得",
                    "saved_races": saved_races,
                    "saved_horses": saved_horses,
                }

        elapsed = _time.time() - start_time
        job["status"] = "completed"
        job["result"] = {
            "success": True,
            "races_collected": saved_races,
            "saved_horses": saved_horses,
            "elapsed_time": elapsed,
            "message": f"{saved_races}レース・{saved_horses}頭のデータを収集しました",
        }
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        logger.error(f"スクレイピングジョブ失敗 {job_id}: {e}")


@app.post("/api/scrape/start")
async def scrape_start(request: ScrapeRequest, background_tasks: BackgroundTasks):
    """スクレイピングをバックグラウンドで開始し、即座に job_id を返す（Vercel プロキシ対応）"""
    job_id = str(uuid.uuid4())[:8]
    _scrape_jobs[job_id] = {
        "status": "queued",
        "progress": {"done": 0, "total": 0, "message": "開始待ち"},
        "result": None,
        "error": None,
    }
    background_tasks.add_task(_run_scrape_job, job_id, request.start_date, request.end_date)
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/scrape/status/{job_id}")
async def scrape_status(job_id: str):
    """スクレイピングジョブの進捗・結果を返す"""
    job = _scrape_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"ジョブ {job_id} が見つかりません")
    return {
        "job_id": job_id,
        "status": job["status"],       # queued / running / completed / error
        "progress": job["progress"],
        "result": job.get("result"),
        "error": job.get("error"),
    }


@app.post("/api/scrape", response_model=ScrapeResponse)
async def scrape_data(request: ScrapeRequest):
    """
    期間指定でnetkeiba.comから完全データを自動収集し keiba_ultimate.db に保存。

    取得フィールド:
      - 馬名/馬ID/馬URL, 騎手/騎手ID, 調教師/調教師ID
      - 馬体重/体重増減, 斤量, オッズ, 人気
      - タイム, 着差, コーナー通過順, 上がり3F, 上がり3F順位
      - 距離, 芝/ダート, 天候, 馬場状態, 開催場
    """
    import aiohttp
    import time

    logger.info(f"完全スクレイピング開始: {request.start_date} ～ {request.end_date}")
    start_time = time.time()

    ULTIMATE_DB = Path(__file__).parent.parent / "keiba" / "data" / "keiba_ultimate.db"

    # 日付リスト生成
    from datetime import datetime, timedelta
    def _parse_date(s):
        for fmt in ("%Y%m%d", "%Y/%m/%d", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        raise ValueError(f"日付フォーマット不正: {s} (YYYYMMDD または YYYY/MM/DD)")
    start = _parse_date(request.start_date)
    end = _parse_date(request.end_date)
    dates = []
    cur = start
    while cur <= end:
        if cur.weekday() in [5, 6]:   # 土日のみ
            dates.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)

    logger.info(f"対象日数: {len(dates)}日")

    BASE_URL = "https://db.netkeiba.com"
    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(limit=5, limit_per_host=3)

    saved_races = 0
    saved_horses = 0

    async with aiohttp.ClientSession(
        headers=SCRAPE_HEADERS, timeout=timeout, connector=connector
    ) as session:
        for date in dates:
            list_url = f"{BASE_URL}/race/list/{date}/"
            logger.info(f"レース一覧取得: {date}")
            try:
                await asyncio.sleep(0.5)
                async with session.get(list_url) as resp:
                    if resp.status != 200:
                        continue
                    content = await resp.read()
                    html = content.decode('euc-jp', errors='ignore')

                from bs4 import BeautifulSoup
                import re
                soup = BeautifulSoup(html, 'html.parser')
                race_ids = []
                for a in soup.find_all('a', href=True):
                    m = re.search(r'/race/(\d{12})/', a['href'])
                    if m and m.group(1) not in race_ids:
                        race_ids.append(m.group(1))

                logger.info(f"  {len(race_ids)}レース発見")

                for race_id in race_ids:
                    race_data = await _scrape_race_full(session, race_id, date_hint=date)
                    if race_data and race_data['horses']:
                        _save_race_to_ultimate_db(race_data, ULTIMATE_DB, overwrite=True)
                        saved_races += 1
                        saved_horses += len(race_data['horses'])
                        logger.info(
                            f"  保存: {race_id} "
                            f"{race_data['race_info']['race_name']} "
                            f"({len(race_data['horses'])}頭)"
                        )

            except Exception as e:
                logger.error(f"{date} エラー: {e}")

    elapsed = time.time() - start_time
    logger.info(f"完全スクレイピング完了: {saved_races}レース/{saved_horses}頭, {elapsed:.1f}秒")

    return ScrapeResponse(
        success=True,
        message=f"{saved_races}レース・{saved_horses}頭のデータを収集しました（完全版）",
        races_collected=saved_races,
        db_path=str(ULTIMATE_DB),
        elapsed_time=elapsed,
    )


class RescrapeResponse(BaseModel):
    success: bool
    message: str
    updated_races: int
    updated_horses: int
    elapsed_time: float


@app.post("/api/rescrape_incomplete")
async def rescrape_incomplete(limit: int = 50) -> RescrapeResponse:
    """
    keiba_ultimate.db 内の不完全レコード（trainer_name=NULL など）を
    netkeiba.com から再スクレイピングして上書き保存する。

    Args:
        limit: 一度に処理するレース数の上限（デフォルト50）
    """
    import aiohttp
    import json as json_mod
    import time

    ULTIMATE_DB = Path(__file__).parent.parent / "keiba" / "data" / "keiba_ultimate.db"
    start_time = time.time()

    # 不完全な race_id を特定（trainer_name が NULL）
    conn = sqlite3.connect(str(ULTIMATE_DB))
    rows = conn.execute("SELECT DISTINCT race_id FROM race_results_ultimate").fetchall()
    all_race_ids = [r[0] for r in rows]

    incomplete_ids = []
    for rid in all_race_ids:
        sample = conn.execute(
            "SELECT data FROM race_results_ultimate WHERE race_id = ? LIMIT 1", (rid,)
        ).fetchone()
        if sample:
            d = json_mod.loads(sample[0])
            if d.get('trainer_name') is None and d.get('horse_weight') is None:
                incomplete_ids.append(rid)
    conn.close()

    to_process = incomplete_ids[:limit]
    logger.info(f"不完全レース: {len(incomplete_ids)}件中 {len(to_process)}件を再取得")

    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(limit=5, limit_per_host=3)

    updated_races = 0
    updated_horses = 0

    async with aiohttp.ClientSession(
        headers=SCRAPE_HEADERS, timeout=timeout, connector=connector
    ) as session:
        for race_id in to_process:
            race_data = await _scrape_race_full(session, race_id)
            if race_data and race_data['horses']:
                _save_race_to_ultimate_db(race_data, ULTIMATE_DB, overwrite=True)
                updated_races += 1
                updated_horses += len(race_data['horses'])
                logger.info(f"  更新: {race_id} ({len(race_data['horses'])}頭)")
            else:
                logger.warning(f"  スキップ: {race_id} (取得失敗)")

    elapsed = time.time() - start_time
    remaining = len(incomplete_ids) - updated_races

    return RescrapeResponse(
        success=True,
        message=(
            f"{updated_races}レース/{updated_horses}頭を更新。"
            f"残り不完全: {remaining}件"
        ),
        updated_races=updated_races,
        updated_horses=updated_horses,
        elapsed_time=elapsed,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
