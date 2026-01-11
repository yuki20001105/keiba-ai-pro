"""
FastAPI機械学習サーバー
Streamlit版の機械学習パイプラインをREST APIとして提供
【3-3. 一括予測・購入推奨】機能を完全実装
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import sys
import os
from pathlib import Path
import joblib
import pandas as pd
import numpy as np
from datetime import datetime
import sqlite3
import logging
import traceback

# keiba_aiモジュールをインポート（親ディレクトリのkeibaから）
sys.path.insert(0, str(Path(__file__).parent.parent / "keiba"))

from keiba_ai.config import load_config
from keiba_ai.db import connect, init_db, load_training_frame
from keiba_ai.train import train as train_model_internal
from keiba_ai.feature_engineering import add_derived_features
from keiba_ai.lightgbm_feature_optimizer import prepare_for_lightgbm_ultimate
from keiba_ai.optuna_optimizer import OptunaLightGBMOptimizer

# 購入推奨システムをインポート
from betting_strategy import BettingRecommender, ProBettingStrategy, RaceAnalyzer

# ログ設定
log_file = Path(__file__).parent.parent / "optuna_debug.log"
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, mode='a', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
logger.info("=" * 80)
logger.info("FastAPI起動 - ログ記録開始")
logger.info(f"ログファイル: {log_file}")
logger.info("=" * 80)

app = FastAPI(
    title="Keiba AI - Machine Learning API",
    description="競馬予測AIのための機械学習API",
    version="1.0.0"
)

# CORS設定（Next.jsからのアクセスを許可）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
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
        "version": "1.0.0"
    }


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
            "cv_folds": request.cv_folds
        },
        "will_execute_optuna": request.use_optuna and request.model_type == "lightgbm" and request.use_optimizer,
        "message": "リクエストは正しく受信されました"
    }


@app.get("/api/data_stats")
async def get_data_stats(ultimate: bool = False):
    """
    データベース統計情報を取得
    
    Args:
        ultimate: Ultimate版DBを使用するかどうか
    """
    try:
        # データベースパスを決定
        if ultimate:
            db_path = Path(__file__).parent.parent / "keiba" / "data" / "keiba_ultimate.db"
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
                "db_exists": False
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
            "db_exists": True
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"統計取得エラー: {str(e)}")


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
        print("\n" + "="*70)
        print("【学習リクエスト受信】")
        print("="*70)
        print(f"  target: {request.target}")
        print(f"  model_type: {request.model_type}")
        print(f"  use_optimizer: {request.use_optimizer}")
        print(f"  use_optuna: {request.use_optuna}")
        print(f"  optuna_trials: {request.optuna_trials}")
        print(f"  cv_folds: {request.cv_folds}")
        print("="*70 + "\n")
        
        # 設定ファイルをロード
        if not CONFIG_PATH.exists():
            raise HTTPException(status_code=500, detail="config.yamlが見つかりません")
        
        cfg = load_config(CONFIG_PATH)
        
        # データベースパスを絶対パスに変換（Ultimate版モードに応じて切り替え）
        if request.ultimate_mode:
            # Ultimate版: keiba_ultimate.db
            db_path = Path(__file__).parent.parent / "keiba" / "data" / "keiba_ultimate.db"
        else:
            # 標準版: keiba.db
            db_path = cfg.storage.sqlite_path
            if not db_path.is_absolute():
                # config.yamlがあるディレクトリ（keiba/）からの相対パスとして解決
                db_path = CONFIG_PATH.parent / db_path
        
        # データベース接続
        con = connect(db_path)
        init_db(con)
        
        # 訓練データ読み込み
        df = load_training_frame(con)
        con.close()
        
        print(f"DEBUG: Loaded {len(df)} rows from database")
        print(f"DEBUG: Database path: {db_path}")
        
        if df.empty:
            raise HTTPException(
                status_code=400,
                detail=f"訓練データが見つかりません。先にデータ取得を実行してください。DB: {db_path}, Rows: {len(df)}"
            )
        
        # 派生特徴量を追加
        df = add_derived_features(df, full_history_df=df)
        
        # ターゲット作成
        from keiba_ai.train import _make_target
        y = _make_target(df, request.target)
        
        # クラス数チェック
        if len(y.unique()) < 2:
            raise HTTPException(
                status_code=400,
                detail="訓練データに2つ以上のクラスが必要です。より多くのレースデータを取得してください。"
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
                df_optimized, optimizer, categorical_features = prepare_for_lightgbm_ultimate(
                    df,
                    target_col=request.target,
                    is_training=True
                )
                
                # ID系と元のカテゴリカル変数（エンコード済み）を除外
                exclude_cols = [request.target, 'race_id', 'horse_id', 'jockey_id', 'trainer_id', 'owner_id', 'finish_position']
                X = df_optimized.drop([col for col in exclude_cols if col in df_optimized.columns], axis=1)
                
                # object型のカラムを除外（元のカテゴリカル変数など）
                object_cols = X.select_dtypes(include=['object']).columns.tolist()
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
                    X_train, y_train,
                    categorical_feature=categorical_indices
                )
                test_data = lgb.Dataset(
                    X_test, y_test,
                    reference=train_data
                )
                
                # パラメータ設定（最適化版）
                params = {
                    'objective': 'binary',
                    'metric': 'auc',
                    'max_cat_to_onehot': 4,
                    'learning_rate': 0.05,
                    'num_leaves': 31,
                    'min_data_in_leaf': 20,
                    'feature_fraction': 0.8,
                    'bagging_fraction': 0.8,
                    'bagging_freq': 5,
                    'verbose': -1,
                    'random_state': 42
                }
                
                # 学習
                model = lgb.train(
                    params,
                    train_data,
                    num_boost_round=100,
                    valid_sets=[train_data, test_data],
                    valid_names=['train', 'test']
                )
                
                # 評価
                y_pred_proba = model.predict(X_test)
                auc = roc_auc_score(y_test, y_pred_proba)
                logloss = log_loss(y_test, y_pred_proba)
                
                # クロスバリデーション
                cv_result = lgb.cv(
                    params,
                    train_data,
                    num_boost_round=100,
                    nfold=request.cv_folds,
                    stratified=True,
                    return_cvbooster=False
                )
                cv_auc_mean = cv_result['valid auc-mean'][-1]
                cv_auc_std = cv_result['valid auc-stdv'][-1]
                
                print(f"✓ LightGBM最適化完了: AUC={auc:.4f}, CV AUC={cv_auc_mean:.4f}±{cv_auc_std:.4f}")
                
            except Exception as lightgbm_error:
                print(f"\n[ERROR-LIGHTGBM] LightGBM処理中にエラーが発生しました")
                print(f"[ERROR-LIGHTGBM] 例外型: {type(lightgbm_error).__name__}")
                print(f"[ERROR-LIGHTGBM] 例外メッセージ: {str(lightgbm_error)}")
                import traceback
                print("[ERROR-LIGHTGBM] スタックトレース:")
                traceback.print_exc()
                logger.error(f"[ERROR-LIGHTGBM] スタックトレース:\n{traceback.format_exc()}")
                raise
            
            # デバッグ: use_optunaの値を確認
            optuna_executed = False
            optuna_error = None
            logger.info(f"\n[TRACE-001] request.use_optuna = {request.use_optuna}")
            logger.info(f"[TRACE-002] request.optuna_trials = {request.optuna_trials}")
            logger.info(f"[TRACE-003] request.use_optimizer = {request.use_optimizer}")
            logger.info(f"[TRACE-004] request.model_type = {request.model_type}")
            
            # Optunaによるハイパーパラメータ最適化（オプション）
            logger.info(f"[TRACE-005] Optunaブロック判定前: use_optuna={request.use_optuna}")
            if request.use_optuna:
                logger.info("[TRACE-006] ★★★ Optunaブロックに入りました ★★★")
                try:
                    logger.info("[TRACE-007] try節に入りました")
                    optuna_executed = True
                    logger.info(f"[TRACE-008] optuna_executed を True に設定: {optuna_executed}")
                    print("\n=== Optunaハイパーパラメータ最適化 ===")
                    logger.info("[TRACE-009] コンソール出力完了")
                    
                    # categorical_featuresを整数インデックスに変換
                    categorical_indices = []
                    for cat_feature in categorical_features:
                        logger.info(f"[TRACE-012] 処理中: {cat_feature}")
                        if cat_feature in X.columns:
                            idx = X.columns.get_loc(cat_feature)
                            categorical_indices.append(idx)
                            logger.info(f"[TRACE-013] {cat_feature} のインデックス: {idx}")
                    
                    logger.info(f"[TRACE-014] カテゴリカル特徴量変換完了: {len(categorical_indices)}個")
                    print(f"  カテゴリカル特徴量: {len(categorical_indices)}個")
                    print(f"  インデックス: {categorical_indices}")
                    
                    logger.info("[TRACE-015] OptunaLightGBMOptimizer初期化開始")
                    print(f"[DEBUG] OptunaLightGBMOptimizerを初期化します...")
                    optuna_optimizer = OptunaLightGBMOptimizer(
                        n_trials=request.optuna_trials,
                        cv_folds=request.cv_folds,
                        random_state=42,
                        timeout=300  # 5分タイムアウト
                    )
                    logger.info("[TRACE-016] OptunaLightGBMOptimizer初期化完了")
                    
                    logger.info(f"[TRACE-017] 最適化開始準備: trials={request.optuna_trials}, folds={request.cv_folds}")
                    print(f"[DEBUG] 最適化を開始します（試行数: {request.optuna_trials}, CVフォールド: {request.cv_folds}）...")
                    # 最適パラメータを探索（ndarrayで渡す）
                    logger.info("[TRACE-018] データ変換開始")
                    X_array = X.values if hasattr(X, 'values') else X
                    y_array = y.values if hasattr(y, 'values') else y
                    logger.info(f"[TRACE-019] データ変換完了: X={X_array.shape}, y={len(y_array)}")
                    print(f"[DEBUG] データサイズ: X={X_array.shape}, y={len(y_array)}")
                    
                    logger.info("[TRACE-020] ★★★ optimize()メソッド呼び出し直前 ★★★")
                    print("[DEBUG] ★★★ optimize()呼び出し直前 ★★★")
                    print(f"[DEBUG] X_array type: {type(X_array)}, shape: {X_array.shape}")
                    print(f"[DEBUG] y_array type: {type(y_array)}, shape: {y_array.shape}")
                    print(f"[DEBUG] categorical_indices: {categorical_indices}")
                    
                    print("[DEBUG] optimize()メソッド実行開始...")
                    best_params, best_optuna_score = optuna_optimizer.optimize(
                        X_array, y_array,  # 全データで最適化
                        categorical_features=categorical_indices
                    )
                    print(f"[DEBUG] optimize()メソッド実行完了")
                    print(f"[DEBUG] best_score={best_optuna_score:.4f}")
                    logger.info("[TRACE-021] ★★★ optimize()メソッド呼び出し完了 ★★★")
                    logger.info(f"[TRACE-022] best_score={best_optuna_score:.4f}")
                    
                    print(f"[DEBUG] Optuna最適化完了。最良スコア: {best_optuna_score:.4f}")
                    
                    # 最適パラメータで再学習
                    print("\n  最適パラメータで再学習中...")
                    optimized_params = optuna_optimizer.get_best_model_params()
                    
                    train_data = lgb.Dataset(
                        X_train, y_train,
                        categorical_feature=categorical_indices
                    )
                    test_data = lgb.Dataset(
                        X_test, y_test,
                        categorical_feature=categorical_indices,
                        reference=train_data
                    )
                    
                    model = lgb.train(
                        optimized_params,
                        train_data,
                        valid_sets=[train_data, test_data],
                        valid_names=['train', 'test'],
                        callbacks=[lgb.early_stopping(stopping_rounds=50, verbose=False)]
                    )
                    
                    # 再評価
                    y_pred_proba = model.predict(X_test)
                    auc = roc_auc_score(y_test, y_pred_proba)
                    logloss = log_loss(y_test, y_pred_proba)
                    
                    # クロスバリデーション
                    cv_result = lgb.cv(
                        optimized_params,
                        train_data,
                        num_boost_round=optimized_params.get('n_estimators', 100),
                        nfold=request.cv_folds,
                        stratified=True,
                        return_cvbooster=False,
                        callbacks=[lgb.early_stopping(stopping_rounds=50, verbose=False)]
                    )
                    cv_auc_mean = cv_result['valid auc-mean'][-1]
                    cv_auc_std = cv_result['valid auc-stdv'][-1]
                    
                    print(f"✓ Optuna最適化完了: AUC={auc:.4f}, CV AUC={cv_auc_mean:.4f}±{cv_auc_std:.4f}")
                    print(f"  Optunaスコア改善: {best_optuna_score:.4f}")
                
                except Exception as e:
                    logger.error("[TRACE-ERROR] ★★★ 例外発生 ★★★")
                    logger.error(f"[TRACE-ERROR] 例外型: {type(e).__name__}")
                    logger.error(f"[TRACE-ERROR] 例外メッセージ: {str(e)}")
                    logger.error(f"[TRACE-ERROR] スタックトレース:\n{traceback.format_exc()}")
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
                "horse_no", "bracket", "age", "handicap", "weight", "weight_diff", 
                "entry_odds", "entry_popularity",
                "straight_length", "inner_bias", "inner_advantage",
                "jockey_course_win_rate", "jockey_course_races",
                "horse_distance_win_rate", "horse_distance_avg_finish",
                "trainer_recent_win_rate"
            ]
            feature_cols_cat = [
                "sex", "jockey_id", "trainer_id",
                "venue_code", "track_type", "corner_radius"
            ]
            
            # 欠損カラムの処理
            for c in feature_cols_num + feature_cols_cat:
                if c not in df.columns:
                    df[c] = np.nan
            
            # 全てNaNのカラムを除外
            non_null_counts = df.notna().sum()
            valid_num_cols = [c for c in feature_cols_num if c in df.columns and non_null_counts[c] > 0]
            valid_cat_cols = [c for c in feature_cols_cat if c in df.columns and non_null_counts[c] > 0]
            
            X = df[valid_num_cols + valid_cat_cols].copy()
            
            # 前処理パイプライン構築
            from sklearn.model_selection import train_test_split, cross_val_score
            from sklearn.compose import ColumnTransformer
            from sklearn.pipeline import Pipeline
            from sklearn.preprocessing import OneHotEncoder
            from sklearn.impute import SimpleImputer
            from sklearn.linear_model import LogisticRegression
            from sklearn.metrics import roc_auc_score, log_loss
            
            num_transformer = Pipeline(steps=[
                ("imputer", SimpleImputer(strategy="median"))
            ])
            
            cat_transformer = Pipeline(steps=[
                ("imputer", SimpleImputer(strategy="constant", fill_value="missing")),
                ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False))
            ])
            
            preprocessor = ColumnTransformer(
                transformers=[
                    ("num", num_transformer, valid_num_cols),
                    ("cat", cat_transformer, valid_cat_cols)
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
                        class_weight="balanced"
                    )
                except ImportError:
                    raise HTTPException(status_code=500, detail="LightGBMがインストールされていません")
            else:
                clf = LogisticRegression(
                    max_iter=1000,
                    random_state=42,
                    class_weight="balanced"
                )
            
            # パイプライン構築
            model = Pipeline(steps=[
                ("pre", preprocessor),
                ("clf", clf)
            ])
            
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
            cv_scores = cross_val_score(model, X, y, cv=request.cv_folds, scoring="roc_auc")
            cv_auc_mean = cv_scores.mean()
            cv_auc_std = cv_scores.std()
            
            # 特徴量数を設定（標準モード）
            feature_count = len(valid_num_cols) + len(valid_cat_cols)
        
        # モデル保存
        model_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        mode_suffix = "_optimized" if (request.use_optimizer and request.model_type == "lightgbm") else ""
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
                "cv_auc_std": float(cv_auc_std)
            },
            "data_count": len(df),
            "race_count": df["race_id"].nunique() if "race_id" in df.columns else 0,
            "created_at": model_id
        }
        
        joblib.dump(bundle, model_path)
        
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
                "cv_auc_std": float(cv_auc_std)
            },
            data_count=len(df),
            race_count=df["race_id"].nunique() if "race_id" in df.columns else 0,
            feature_count=feature_count,
            training_time=training_time,
            message=f"モデル学習完了 (AUC: {auc:.4f}, LogLoss: {logloss:.4f})",
            optuna_executed=optuna_executed,
            optuna_error=optuna_error
        )
        
    except HTTPException:
        raise
    except Exception as e:
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
            model_files = list(MODELS_DIR.glob(f"*{request.model_id}*.joblib"))
            if not model_files:
                raise HTTPException(status_code=404, detail=f"モデル {request.model_id} が見つかりません")
            model_path = model_files[0]
        else:
            model_path = get_latest_model()
            if model_path is None:
                raise HTTPException(status_code=404, detail="学習済みモデルが見つかりません。先に学習を実行してください。")
        
        # モデルバンドルをロード
        bundle = load_model_bundle(model_path)
        model = bundle["model"]
        optimizer = bundle.get("optimizer")
        use_optimizer = bundle.get("use_optimizer", False)
        categorical_features = bundle.get("categorical_features", [])
        
        # 入力データをDataFrameに変換
        df = pd.DataFrame(request.horses)
        
        # 派生特徴量を追加
        df = add_derived_features(df, full_history_df=None)
        
        # LightGBM最適化モードの場合
        if use_optimizer and optimizer is not None:
            # 最適化された特徴量変換を適用
            df_optimized = optimizer.transform(df)
            
            # ID系を除外
            exclude_cols = ['race_id', 'horse_id', 'jockey_id', 'trainer_id', 'owner_id', 'finish_position']
            X = df_optimized.drop([col for col in exclude_cols if col in df_optimized.columns], axis=1)
            
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
                "odds": float(row.get("odds", row.get("entry_odds", 0.0)))
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
            message=f"{len(predictions)}頭の予測が完了しました"
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
                
                models.append({
                    "model_id": bundle.get("created_at", "unknown"),
                    "model_path": str(model_path),
                    "created_at": bundle.get("created_at", "unknown"),
                    "target": bundle.get("target", "unknown"),
                    "model_type": bundle.get("model_type", "unknown"),
                    "ultimate_mode": is_ultimate,
                    "use_optimizer": bundle.get("use_optimizer", False),
                    "auc": bundle.get("metrics", {}).get("auc", 0.0),
                    "cv_auc_mean": bundle.get("metrics", {}).get("cv_auc_mean", 0.0)
                })
            except Exception as e:
                print(f"モデル読み込みエラー {model_path}: {e}")
                continue
        
        # AUC降順にソート
        models = sorted(models, key=lambda x: x.get("auc", 0), reverse=True)
        
        return {"models": models, "count": len(models)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"モデル一覧取得エラー: {str(e)}")


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
            db_path = Path(__file__).parent.parent / "keiba" / "data" / "keiba_ultimate.db"
        else:
            db_path = cfg.storage.sqlite_path
            if not db_path.is_absolute():
                db_path = CONFIG_PATH.parent / db_path
        
        # tracking.dbパス（購入履歴用）
        tracking_db_path = CONFIG_PATH.parent / "data" / "tracking.db"
        
        # モデルロード（Ultimate版モデルを優先）
        if request.model_id:
            model_files = list(MODELS_DIR.glob(f"model_*_{request.model_id}*.joblib"))
            if not model_files:
                raise HTTPException(status_code=404, detail=f"モデル {request.model_id} が見つかりません")
            model_path = model_files[0]
        else:
            # Ultimate版モデルを優先的に検索
            if request.ultimate_mode:
                ultimate_models = [p for p in MODELS_DIR.glob("model_*_ultimate.joblib")]
                if ultimate_models:
                    model_path = max(ultimate_models, key=lambda p: p.stat().st_mtime)
                else:
                    raise HTTPException(status_code=404, detail="Ultimate版モデルが見つかりません")
            else:
                model_path = get_latest_model()
                if not model_path:
                    raise HTTPException(status_code=404, detail="訓練済みモデルが見つかりません")
        
        bundle = load_model_bundle(model_path)
        model = bundle["model"]
        
        # データベースからレース情報取得
        con = connect(db_path)
        cursor = con.cursor()
        
        # レース基本情報
        cursor.execute("""
            SELECT race_id, race_name, venue, date, distance, track_type, 
                   weather, field_condition, num_horses
            FROM races 
            WHERE race_id = ?
        """, (request.race_id,))
        race_row = cursor.fetchone()
        
        if not race_row:
            con.close()
            raise HTTPException(status_code=404, detail=f"レース {request.race_id} が見つかりません")
        
        race_info = {
            'race_id': race_row[0],
            'race_name': race_row[1],
            'venue': race_row[2],
            'date': race_row[3],
            'distance': race_row[4],
            'track_type': race_row[5],
            'weather': race_row[6],
            'field_condition': race_row[7],
            'num_horses': race_row[8]
        }
        
        # レース結果データ取得（予測用特徴量）
        cursor.execute("""
            SELECT umaban, horse_name, sex, age, kinryo, jockey_name, trainer_name,
                   tansho_odds, popularity, horse_weight, weight_change, wakuban
            FROM results 
            WHERE race_id = ?
            ORDER BY umaban
        """, (request.race_id,))
        horses_data = cursor.fetchall()
        con.close()
        
        if not horses_data:
            raise HTTPException(
                status_code=404, 
                detail=f"レース {request.race_id} の出走馬データが見つかりません"
            )
        
        # 予測実行
        predictions = []
        for horse in horses_data:
            # 特徴量構築（簡易版、本来はpipeline_daily.pyで生成）
            features = {
                'horse_no': horse[0],
                'bracket': horse[11],
                'age': horse[3],
                'handicap': horse[4],
                'weight': horse[9] or 460,
                'weight_diff': horse[10] or 0,
                'entry_odds': horse[7] or 5.0,
                'entry_popularity': horse[8] or 5,
                # その他の特徴量はデフォルト値
                'straight_length': 500,
                'inner_bias': 0,
                'inner_advantage': 0,
                'jockey_course_win_rate': 0.1,
                'jockey_course_races': 10,
                'horse_distance_win_rate': 0.1,
                'horse_distance_avg_finish': 5.0,
                'trainer_recent_win_rate': 0.1
            }
            
            # DataFrame作成
            feature_df = pd.DataFrame([features])
            
            # 予測（勝率）
            try:
                win_prob = model.predict_proba(feature_df)[0][1]
            except:
                win_prob = 0.1  # デフォルト
            
            predictions.append({
                'horse_no': horse[0],
                'horse_name': horse[1],
                'jockey_name': horse[5],
                'trainer_name': horse[6],
                'sex': horse[2],
                'age': horse[3],
                'weight': horse[9] or 460,
                'odds': horse[7] or 5.0,
                'popularity': horse[8] or 5,
                'win_probability': float(win_prob),
                'expected_value': float(win_prob * (horse[7] or 5.0))
            })
        
        # 購入推奨システム初期化
        recommender = BettingRecommender(
            bankroll=request.bankroll,
            risk_mode=request.risk_mode,
            use_kelly=request.use_kelly,
            dynamic_unit=request.dynamic_unit,
            min_ev=request.min_ev
        )
        
        # 分析・推奨実行
        result = recommender.analyze_and_recommend(predictions, race_info)
        
        return AnalyzeRaceResponse(
            success=True,
            race_info=result['race_info'],
            pro_evaluation=result['pro_evaluation'],
            predictions=result['predictions'],
            bet_types=result['bet_types'],
            best_bet_type=result['best_bet_type'],
            best_bet_info=result['best_bet_info'],
            race_level=result['race_level'],
            recommendation=result['recommendation']
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
        purchase_date = datetime.now().strftime('%Y-%m-%d')
        month = datetime.now().month
        if 3 <= month <= 5:
            season = '春'
        elif 6 <= month <= 8:
            season = '夏'
        elif 9 <= month <= 11:
            season = '秋'
        else:
            season = '冬'
        
        # データ挿入
        cursor.execute("""
            INSERT INTO purchase_history (
                race_id, purchase_date, season, bet_type, combinations, 
                strategy_type, purchase_count, unit_price, total_cost, 
                expected_value, expected_return
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            request.race_id,
            purchase_date,
            season,
            request.bet_type,
            ','.join(request.combinations),
            request.strategy_type,
            request.purchase_count,
            request.unit_price,
            request.total_cost,
            request.expected_value,
            request.expected_return
        ))
        
        purchase_id = cursor.lastrowid
        con.commit()
        con.close()
        
        return PurchaseHistoryResponse(
            success=True,
            purchase_id=purchase_id,
            message=f"購入履歴を保存しました (ID: {purchase_id})"
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
                "message": "購入履歴がまだありません"
            }
        
        con = sqlite3.connect(str(tracking_db_path))
        cursor = con.cursor()
        
        cursor.execute("""
            SELECT id, race_id, purchase_date, season, bet_type, combinations,
                   strategy_type, purchase_count, unit_price, total_cost,
                   expected_value, expected_return, actual_return, 
                   is_hit, recovery_rate, created_at
            FROM purchase_history
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,))
        
        rows = cursor.fetchall()
        
        history = []
        for row in rows:
            history.append({
                'id': row[0],
                'race_id': row[1],
                'purchase_date': row[2],
                'season': row[3],
                'bet_type': row[4],
                'combinations': row[5].split(',') if row[5] else [],
                'strategy_type': row[6],
                'purchase_count': row[7],
                'unit_price': row[8],
                'total_cost': row[9],
                'expected_value': row[10],
                'expected_return': row[11],
                'actual_return': row[12],
                'is_hit': bool(row[13]),
                'recovery_rate': row[14],
                'created_at': row[15]
            })
        
        con.close()
        
        # 統計サマリー
        total_cost = sum(h['total_cost'] for h in history)
        total_return = sum(h['actual_return'] for h in history)
        hit_count = sum(1 for h in history if h['is_hit'])
        
        return {
            "success": True,
            "history": history,
            "count": len(history),
            "summary": {
                "total_cost": total_cost,
                "total_return": total_return,
                "recovery_rate": round(total_return / total_cost * 100, 1) if total_cost > 0 else 0,
                "hit_count": hit_count,
                "hit_rate": round(hit_count / len(history) * 100, 1) if history else 0
            }
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
                "message": "統計データがまだありません"
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
            bet_type_stats.append({
                'bet_type': row[0],
                'count': row[1],
                'total_cost': row[2],
                'total_return': row[3],
                'recovery_rate': round(row[3] / row[2] * 100, 1) if row[2] > 0 else 0,
                'hit_count': row[4],
                'hit_rate': round(row[4] / row[1] * 100, 1) if row[1] > 0 else 0
            })
        
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
            season_stats.append({
                'season': row[0],
                'count': row[1],
                'total_cost': row[2],
                'total_return': row[3],
                'recovery_rate': round(row[3] / row[2] * 100, 1) if row[2] > 0 else 0
            })
        
        con.close()
        
        return {
            "success": True,
            "statistics": {
                "by_bet_type": bet_type_stats,
                "by_season": season_stats
            }
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
            raise HTTPException(status_code=404, detail=f"モデル {model_id} が見つかりません")
        
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
            "feature_count": len(bundle.get("feature_cols_num", [])) + len(bundle.get("feature_cols_cat", []))
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"モデル情報の取得に失敗: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
