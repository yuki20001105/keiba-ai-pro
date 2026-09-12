"""
Pydantic リクエスト / レスポンスモデル
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from scraping.scrape_request_contract import parse_scrape_date, validate_scrape_date_range


class TrainRequest(BaseModel):
    """学習リクエスト"""
    model_config = {"protected_namespaces": ()}

    target: Literal["win", "place3", "win_tie", "speed_deviation", "rank"] = "win"
    model_type: Literal["lightgbm"] = "lightgbm"
    test_size: float = Field(0.2, ge=0.1, le=0.5)
    cv_folds: int = Field(5, ge=2, le=10)
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
        if not re.fullmatch(r"\d{4}-\d{2}", str(v)):
            raise ValueError("YYYY-MM 形式で入力してください (例: 2025-01)")
        try:
            datetime.strptime(str(v), "%Y-%m")
        except ValueError as exc:
            raise ValueError("存在する年月を入力してください") from exc
        return str(v)

    @model_validator(mode="after")
    def _validate_training_range(self) -> "TrainRequest":
        if (
            self.training_date_from
            and self.training_date_to
            and self.training_date_from > self.training_date_to
        ):
            raise ValueError("学習期間の開始年月は終了年月以前にしてください")
        return self


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
    include_explanation: bool = False
    ultimate_mode: bool = True  # Phase 0: 常に True（87特徴量モード固定）


class AnalyzeRaceResponse(BaseModel):
    """レース分析レスポンス"""
    model_config = {"protected_namespaces": ()}

    success: bool
    model_id: Optional[str] = None
    explanation_method: Optional[str] = None
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

    race_ids: List[str] = Field(min_length=1, max_length=100)
    model_id: Optional[str] = None
    include_explanation: bool = False
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
    dry_run: bool = False
    # Phase 3N operational execution binding. Deployed environments require
    # the complete tuple; explicit local/test mode may generate the two IDs.
    job_id: Optional[str] = None
    operation_id: Optional[str] = None
    authorization_id: Optional[str] = None
    reservation_id: Optional[str] = None
    review_id: Optional[str] = None
    review_version: Optional[int] = Field(None, ge=1)
    expected_authorization_version: Optional[int] = Field(None, ge=1)
    consume_request_id: Optional[str] = None

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def _validate_scrape_date(cls, value: object) -> str:
        parse_scrape_date(value)
        if not isinstance(value, str):
            raise ValueError("scrape date must be a string")
        return value

    @model_validator(mode="after")
    def _validate_scrape_date_range(self) -> "ScrapeRequest":
        validate_scrape_date_range(self.start_date, self.end_date)
        return self


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
