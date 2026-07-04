"""Unified training pipeline entrypoint for notebook/CLI/CI."""

from __future__ import annotations

from typing import Any, Dict

from ..training_pipeline import run_training_pipeline
from .config import TrainingPipelineConfig


def run(config: TrainingPipelineConfig, *, X_train, y_train, X_test, y_test, cat_train, cat_test, feat_cols, estimate_remaining_seconds, format_seconds, get_system_usage_snapshot) -> Dict[str, Any]:
    """Run unified pipeline with structured config and data arguments."""
    return run_training_pipeline(
        target=config.target,
        runtime=config.runtime,
        audit_mode=config.audit_mode,
        quick_test=config.quick_test,
        random_state=config.random_state,
        use_optuna=config.use_optuna,
        use_median_pruner=config.use_median_pruner,
        mode_presets=config.mode_presets,
        reports_dir=config.reports_dir,
        gpu_env_report=config.gpu_env_report,
        device=config.device,
        boosting_ts=config.boosting_ts,
        boost_round_ts=config.boost_round_ts,
        trials_ts=config.trials_ts,
        splits_ts=config.splits_ts,
        X_train=X_train,
        y_train=y_train,
        X_test=X_test,
        y_test=y_test,
        cat_train=cat_train,
        cat_test=cat_test,
        feat_cols=feat_cols,
        lgb_params_default=config.lgb_params_default,
        run_gpu_benchmark=config.run_gpu_benchmark,
        time_data_loading=config.time_data_loading,
        time_feature_engineering=config.time_feature_engineering,
        pipeline_started_at=config.pipeline_started_at,
        estimate_remaining_seconds=estimate_remaining_seconds,
        format_seconds=format_seconds,
        get_system_usage_snapshot=get_system_usage_snapshot,
    )


def run_notebook_pipeline(
    *,
    target: str,
    runtime: Dict[str, Any],
    audit_mode: bool,
    quick_test: bool,
    random_state: int,
    use_optuna: bool,
    use_median_pruner: bool,
    mode_presets: Dict[str, Dict[str, Any]],
    reports_dir,
    gpu_env_report: Dict[str, Any],
    device: str,
    boosting_ts: str,
    boost_round_ts: int,
    trials_ts: int,
    splits_ts: int,
    X_train,
    y_train,
    X_test,
    y_test,
    cat_train,
    cat_test,
    feat_cols,
    lgb_params_default: Dict[str, Dict[str, Any]],
    run_gpu_benchmark: bool,
    time_data_loading: float,
    time_feature_engineering: float,
    pipeline_started_at: float,
    estimate_remaining_seconds,
    format_seconds,
    get_system_usage_snapshot,
) -> Dict[str, Any]:
    """Notebook-friendly wrapper around unified training run()."""
    config = TrainingPipelineConfig.from_notebook(
        target=target,
        runtime=runtime,
        audit_mode=audit_mode,
        quick_test=quick_test,
        random_state=random_state,
        use_optuna=use_optuna,
        use_median_pruner=use_median_pruner,
        mode_presets=mode_presets,
        reports_dir=reports_dir,
        gpu_env_report=gpu_env_report,
        device=device,
        boosting_ts=boosting_ts,
        boost_round_ts=boost_round_ts,
        trials_ts=trials_ts,
        splits_ts=splits_ts,
        lgb_params_default=lgb_params_default,
        run_gpu_benchmark=run_gpu_benchmark,
        time_data_loading=time_data_loading,
        time_feature_engineering=time_feature_engineering,
        pipeline_started_at=pipeline_started_at,
    )
    return run(
        config,
        X_train=X_train,
        y_train=y_train,
        X_test=X_test,
        y_test=y_test,
        cat_train=cat_train,
        cat_test=cat_test,
        feat_cols=feat_cols,
        estimate_remaining_seconds=estimate_remaining_seconds,
        format_seconds=format_seconds,
        get_system_usage_snapshot=get_system_usage_snapshot,
    )
