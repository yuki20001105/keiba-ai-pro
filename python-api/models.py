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
    extra_exclude_features: List[str] = Field(default_factory=list)  # 追加で除外する特徴量列名
    feature_store_enabled: bool = True
    feature_set_name: Optional[str] = None
    enforce_feature_quality_gate: bool = False
    min_feature_quality_score: float = Field(95.0, ge=0.0, le=100.0)
    max_feature_validation_errors: int = Field(0, ge=0, le=100000)

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
    feature_store_version: Optional[str] = None
    feature_quality_score: Optional[float] = None
    experiment_id: Optional[str] = None
    model_registry_id: Optional[int] = None


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
    model_id: Optional[str] = None          # メインモデル（speed_deviation 推奨）
    place3_model_id: Optional[str] = None   # 複勝圏モデル（指定時は自動検索より優先）
    use_scenario_router: bool = False
    router_mode: Literal["off", "shadow", "active", "canary"] = "off"
    canary_percent: int = Field(0, ge=0, le=100)
    router_target: Optional[str] = None
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


class ScenarioAdoptionEvaluateRequest(BaseModel):
    """Scenario-aware Promotion Gate 評価リクエスト"""
    model_config = {"protected_namespaces": ()}

    baseline_model_id: str
    challenger_model_id: str
    scenario_segment_by: List[str] = Field(
        default_factory=lambda: ["expected_pace", "expected_bias", "winning_pattern"]
    )
    min_segment_overlap: int = Field(30, ge=1, le=100000)
    alpha: float = Field(0.05, ge=0.0001, le=0.5)
    fdr_alpha: float = Field(0.10, ge=0.0001, le=0.5)
    min_roi_lift: float = Field(0.05, ge=0.0, le=10.0)
    min_hit_rate_lift: float = Field(0.02, ge=0.0, le=1.0)
    min_ev_lift: float = Field(0.01, ge=0.0, le=10.0)
    require_positive_ci_lower: bool = True
    max_allowed_global_roi_drop: float = Field(0.02, ge=0.0, le=1.0)
    bootstrap_iters: int = Field(3000, ge=200, le=50000)
    permutation_iters: int = Field(5000, ge=500, le=50000)
    max_predictions: int = Field(5000, ge=100, le=200000)
    stake_per_race: int = Field(100, ge=1, le=100000)
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    save_decisions: bool = True
    save_policies: bool = True
    experiment_id: Optional[str] = None


class ScenarioRouterBacktestRequest(BaseModel):
    """Scenario Router Backtest リクエスト"""
    model_config = {"protected_namespaces": ()}

    date_from: Optional[str] = None
    date_to: Optional[str] = None
    target: Optional[str] = None
    router_mode: Literal["active", "shadow"] = "active"
    stake_per_race: int = Field(100, ge=1, le=100000)
    scenario_segment_by: List[str] = Field(
        default_factory=lambda: ["expected_pace", "expected_bias", "winning_pattern"]
    )
    min_races: int = Field(30, ge=1, le=100000)
    include_route_type_breakdown: bool = True
    include_scenario_breakdown: bool = True


class ScenarioRouterCanaryEvaluateRequest(BaseModel):
    """Scenario Router Canary 評価リクエスト"""
    model_config = {"protected_namespaces": ()}

    date_from: Optional[str] = None
    date_to: Optional[str] = None
    target: Optional[str] = None
    min_races: int = Field(30, ge=1, le=100000)
    canary_percent: Optional[int] = Field(None, ge=0, le=100)
    max_fallback_rate: float = Field(0.50, ge=0.0, le=1.0)
    max_no_model_rate: float = Field(0.05, ge=0.0, le=1.0)
    min_roi_lift: float = Field(-0.03, ge=-10.0, le=10.0)
    min_hit_rate_lift: float = Field(-0.02, ge=-1.0, le=1.0)
    stake_per_race: int = Field(100, ge=1, le=100000)


class ScenarioRouterPolicyOptimizeRequest(BaseModel):
    """Scenario Router Policy Optimizer リクエスト"""
    model_config = {"protected_namespaces": ()}

    date_from: Optional[str] = None
    date_to: Optional[str] = None
    target: Optional[str] = None
    stake_per_race: int = Field(100, ge=1, le=100000)
    scenario_segment_by: List[str] = Field(
        default_factory=lambda: ["expected_pace", "expected_bias", "winning_pattern"]
    )
    min_races: int = Field(30, ge=1, le=100000)
    min_roi_lift: float = Field(0.05, ge=0.0, le=10.0)
    min_hit_rate_lift: float = Field(0.01, ge=0.0, le=1.0)
    disable_if_roi_lift_below: float = Field(-0.03, ge=-10.0, le=10.0)
    disable_if_hit_rate_lift_below: float = Field(-0.02, ge=-1.0, le=1.0)
    max_fallback_rate: float = Field(0.40, ge=0.0, le=1.0)
    max_no_model_rate: float = Field(0.05, ge=0.0, le=1.0)
    priority_step: int = Field(10, ge=1, le=500)
    apply_updates: bool = False
    save_evaluations: bool = True


class ScenarioPolicyLifecycleRequest(BaseModel):
    """Scenario Policy Lifecycle 適用リクエスト"""
    model_config = {"protected_namespaces": ()}

    target: Optional[str] = None
    lookback_evaluations: int = Field(5, ge=1, le=100)
    raise_confirmations: int = Field(2, ge=1, le=20)
    disable_confirmations: int = Field(2, ge=1, le=20)
    watch_to_lower_threshold: int = Field(3, ge=1, le=50)
    needs_more_data_to_watch_threshold: int = Field(3, ge=1, le=50)
    cooldown_days: int = Field(7, ge=0, le=365)
    priority_step: int = Field(10, ge=1, le=500)
    apply_updates: bool = False


class ScenarioRouterRolloutEvaluateRequest(BaseModel):
    """Scenario Router Canary Rollout 評価リクエスト"""
    model_config = {"protected_namespaces": ()}

    target: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    stake_per_race: int = Field(100, ge=1, le=100000)
    min_races: int = Field(30, ge=1, le=100000)
    canary_percent: Optional[int] = Field(None, ge=0, le=100)
    max_fallback_rate: float = Field(0.50, ge=0.0, le=1.0)
    max_no_model_rate: float = Field(0.05, ge=0.0, le=1.0)
    min_roi_lift: float = Field(-0.03, ge=-10.0, le=10.0)
    min_hit_rate_lift: float = Field(-0.02, ge=-1.0, le=1.0)
    rollout_steps: List[int] = Field(default_factory=lambda: [5, 20, 50, 100])


class ScenarioRouterRolloutApplyRequest(BaseModel):
    """Scenario Router Canary Rollout 適用リクエスト"""
    model_config = {"protected_namespaces": ()}

    target: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    stake_per_race: int = Field(100, ge=1, le=100000)
    min_races: int = Field(30, ge=1, le=100000)
    canary_percent: Optional[int] = Field(None, ge=0, le=100)
    max_fallback_rate: float = Field(0.50, ge=0.0, le=1.0)
    max_no_model_rate: float = Field(0.05, ge=0.0, le=1.0)
    min_roi_lift: float = Field(-0.03, ge=-10.0, le=10.0)
    min_hit_rate_lift: float = Field(-0.02, ge=-1.0, le=1.0)
    rollout_steps: List[int] = Field(default_factory=lambda: [5, 20, 50, 100])
    apply_updates: bool = False


class ScenarioRouterRolloutScheduleRunRequest(BaseModel):
    """Scenario Router Rollout Scheduler 実行リクエスト"""
    model_config = {"protected_namespaces": ()}

    target: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    stake_per_race: int = Field(100, ge=1, le=100000)
    min_races: int = Field(30, ge=1, le=100000)
    max_fallback_rate: float = Field(0.50, ge=0.0, le=1.0)
    max_no_model_rate: float = Field(0.05, ge=0.0, le=1.0)
    min_roi_lift: float = Field(-0.03, ge=-10.0, le=10.0)
    min_hit_rate_lift: float = Field(-0.02, ge=-1.0, le=1.0)
    rollout_steps: List[int] = Field(default_factory=lambda: [5, 20, 50, 100])
    apply_updates: bool = False


class ScenarioRouterAlertEvaluateRequest(BaseModel):
    """Scenario Router Alert 評価リクエスト"""
    model_config = {"protected_namespaces": ()}

    target: Optional[str] = None
    source_run_id: Optional[str] = None
    max_fallback_rate: float = Field(0.50, ge=0.0, le=1.0)
    max_no_model_rate: float = Field(0.05, ge=0.0, le=1.0)
    min_roi_lift: float = Field(-0.03, ge=-10.0, le=10.0)
    min_hit_rate_lift: float = Field(-0.02, ge=-1.0, le=1.0)
    lookback_runs: int = Field(1, ge=1, le=200)


class ScenarioRouterAlertResolveRequest(BaseModel):
    """Scenario Router Alert 解決リクエスト"""
    model_config = {"protected_namespaces": ()}

    message: str = ""


class ScenarioRouterNotificationDispatchRequest(BaseModel):
    """Scenario Router 通知ディスパッチリクエスト"""
    model_config = {"protected_namespaces": ()}

    target: Optional[str] = None
    severity_min: Literal["INFO", "WARNING", "CRITICAL"] = "WARNING"
    channel_types: List[str] = Field(default_factory=list)
    apply_send: bool = False
    limit: int = Field(50, ge=1, le=500)


class ScenarioRouterNotificationTestRequest(BaseModel):
    """Scenario Router 通知テストリクエスト"""
    model_config = {"protected_namespaces": ()}

    channel_type: Literal["webhook", "slack", "email", "notion", "console"] = "webhook"
    name: str = "test_channel"
    config: Dict[str, Any] = Field(default_factory=dict)
    payload: Dict[str, Any] = Field(default_factory=dict)
    alert_id: Optional[str] = None
    include_runbook_summary: bool = False
    apply_send: bool = False


class ScenarioRouterRunbookGenerateRequest(BaseModel):
    """Scenario Router Incident Runbook 生成リクエスト"""
    model_config = {"protected_namespaces": ()}

    alert_id: str
    include_notification_summary: bool = True
    save_runbook: bool = True


class IncidentActionPreviewRequest(BaseModel):
    """Scenario Router Incident Action preview リクエスト"""
    model_config = {"protected_namespaces": ()}

    alert_id: Optional[str] = None
    runbook_id: Optional[str] = None


class IncidentActionExecuteRequest(BaseModel):
    """Scenario Router Incident Action execute リクエスト"""
    model_config = {"protected_namespaces": ()}

    action_type: Literal[
        "ROLLBACK_TO_SHADOW",
        "STOP_CANARY",
        "RESUME_SHADOW",
        "RESOLVE_ALERT",
        "RUN_CANARY_EVALUATE",
        "RUN_ROUTER_BACKTEST",
        "RUN_E2E_VALIDATION",
        "DISABLE_POLICY",
        "LOWER_POLICY_PRIORITY",
    ]
    alert_id: Optional[str] = None
    runbook_id: Optional[str] = None
    apply_updates: bool = False
    confirm: bool = False
    requested_by: str = ""
    approved_by: str = ""
    policy_id: Optional[str] = None
    priority_delta: int = Field(10, ge=1, le=500)
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    stake_per_race: int = Field(100, ge=1, le=100000)
    min_races: int = Field(30, ge=1, le=100000)


class IncidentResponsePrepareRequest(BaseModel):
    """Scenario Router Incident Response Package 生成リクエスト"""
    model_config = {"protected_namespaces": ()}

    alert_id: str
    save_response: bool = True
    include_runbook_summary: bool = True
    notification_channel_type: Literal["webhook", "slack", "email", "notion", "console"] = "slack"
    include_action_preview: bool = True


class AutoRecoveryEvaluateRequest(BaseModel):
    """Scenario Router Auto Recovery evaluate リクエスト"""
    model_config = {"protected_namespaces": ()}

    response_id: Optional[str] = None
    alert_id: Optional[str] = None
    include_action_preview: bool = True
    include_runbook_summary: bool = True
    notification_channel_type: Literal["webhook", "slack", "email", "notion", "console"] = "slack"


class AutoRecoveryExecuteRequest(BaseModel):
    """Scenario Router Auto Recovery execute リクエスト"""
    model_config = {"protected_namespaces": ()}

    response_id: Optional[str] = None
    alert_id: Optional[str] = None
    apply_updates: bool = False
    confirm: bool = False
    requested_by: str = ""
    approved_by: str = ""
    include_action_preview: bool = True
    include_runbook_summary: bool = True
    notification_channel_type: Literal["webhook", "slack", "email", "notion", "console"] = "slack"
