"""Configuration models for unified training pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class TrainingPipelineConfig:
    target: str
    runtime: Dict[str, Any]
    audit_mode: bool
    quick_test: bool
    random_state: int
    use_optuna: bool
    use_median_pruner: bool
    mode_presets: Dict[str, Dict[str, Any]]
    reports_dir: Any
    gpu_env_report: Dict[str, Any]
    device: str
    boosting_ts: str
    boost_round_ts: int
    trials_ts: int
    splits_ts: int
    lgb_params_default: Dict[str, Dict[str, Any]]
    run_gpu_benchmark: bool
    time_data_loading: float
    time_feature_engineering: float
    pipeline_started_at: float

    @classmethod
    def from_notebook(cls, **kwargs) -> "TrainingPipelineConfig":
        return cls(**kwargs)
