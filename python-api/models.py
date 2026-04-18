"""
Pydantic リクエスト / レスポンスモデル
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class TrainRequest(BaseModel):
    """学習リクエスト"""
    model_config = {"protected_namespaces": ()}

    target: str = "win"
    model_type: str = "logistic_regression"
    test_size: float = 0.2
    cv_folds: int = 5
    use_sqlite: bool = True
    ultimate_mode: bool = True  # Phase 0: 常に True（87特徴量モード固定）
    use_optimizer: bool = True
    use_optuna: bool = False
    optuna_trials: int = Field(50, ge=1, le=1000)
    optuna_timeout: int = Field(300, ge=30, le=3600)
    training_date_from: Optional[str] = None
    training_date_to: Optional[str] = None
    force_sync: bool = True

    @field_validator("training_date_from", "training_date_to", mode="before")
    @classmethod
    def _validate_ym(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        if not re.match(r"^\d{4}-\d{2}$", str(v)):
            raise ValueError("YYYY-MM 形式で入力してください (例: 2025-01)")
        return v


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
    optuna_executed: bool = False
    optuna_error: Optional[str] = None
    feature_columns: List[str] = []


class PredictRequest(BaseModel):
    """予測リクエスト"""
    model_config = {"protected_namespaces": ()}

    model_id: Optional[str] = None
    horses: List[Dict[str, Any]]


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
    bankroll: int = 10000
    risk_mode: Literal["aggressive", "balanced", "conservative"] = "balanced"
    use_kelly: bool = True
    dynamic_unit: bool = True
    min_ev: float = Field(1.2, ge=1.0, le=10.0)
    model_id: Optional[str] = None
    ultimate_mode: bool = True  # Phase 0: 常に True（87特徴量モード固定）


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


class BatchAnalyzeRequest(BaseModel):
    """一括レース分析リクエスト"""
    model_config = {"protected_namespaces": ()}

    race_ids: List[str]
    model_id: Optional[str] = None
    bankroll: int = Field(10000, ge=100, le=10_000_000)
    risk_mode: Literal["aggressive", "balanced", "conservative"] = "balanced"
    use_kelly: bool = True
    dynamic_unit: bool = True
    min_ev: float = 1.2


class PurchaseHistoryRequest(BaseModel):
    """購入履歴保存リクエスト"""
    model_config = {"protected_namespaces": ()}

    race_id: str
    venue: Optional[str] = None
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


class ScrapeRequest(BaseModel):
    """スクレイピングリクエスト"""
    start_date: str
    end_date: str
    force_rescrape: bool = False


class ScrapeResponse(BaseModel):
    """スクレイピングレスポンス"""
    success: bool
    message: str
    races_collected: int
    db_path: str
    elapsed_time: float


class RescrapeResponse(BaseModel):
    success: bool
    message: str
    updated_races: int
    updated_horses: int
    elapsed_time: float
