"""Model bundle helpers for notebook/CLI shared save logic."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import joblib
import pandas as pd


def build_feature_importance_df(model: Any) -> pd.DataFrame:
    """Build a standard feature importance frame from LightGBM model."""
    return (
        pd.DataFrame(
            {
                "feature": model.feature_name(),
                "gain": model.feature_importance(importance_type="gain"),
                "split": model.feature_importance(importance_type="split"),
            }
        )
        .sort_values("gain", ascending=False)
        .reset_index(drop=True)
    )


def build_model_bundle(
    *,
    model: Any,
    feature_cols,
    cat_features,
    target: str,
    task: str,
    metrics: Dict[str, Any],
    best_iteration: int,
    params: Dict[str, Any],
    feature_importance: pd.DataFrame,
    test_days: int,
    cutoff_date: str,
    mode: str,
    fast_mode: bool,
    audit_mode: bool,
    device_type: str,
    gpu_env_report: Dict[str, Any],
    optuna_trials: int,
    cv_splits: int,
    total_training_seconds: float,
    avg_trial_seconds: Optional[float],
    gpu_speedup: Optional[float],
    runtime_breakdown,
    reports_dir: Path,
) -> Dict[str, Any]:
    """Build a portable model bundle dict used across execution modes."""
    return {
        "model": model,
        "feature_cols": list(feature_cols),
        "cat_features": list(cat_features),
        "target": target,
        "task": task,
        "metrics": metrics,
        "best_iteration": int(best_iteration),
        "params": params,
        "feature_importance": feature_importance,
        "test_days": test_days,
        "cutoff_date": str(cutoff_date),
        "mode": mode,
        "fast_mode": bool(fast_mode),
        "audit_mode": bool(audit_mode),
        "device_type": device_type,
        "lightgbm_version": gpu_env_report.get("lightgbm_version", ""),
        "lightgbm_gpu_available": gpu_env_report.get("lightgbm_gpu_available", False),
        "cuda_version": gpu_env_report.get("cuda_version", ""),
        "gpu_name": gpu_env_report.get("gpu_name", ""),
        "gpu_memory_total_mb": gpu_env_report.get("gpu_memory_total_mb", None),
        "optuna_trials": int(optuna_trials),
        "cv_splits": int(cv_splits),
        "total_training_seconds": float(total_training_seconds),
        "avg_trial_seconds": avg_trial_seconds,
        "gpu_speedup": gpu_speedup,
        "runtime_breakdown": runtime_breakdown,
        "trial_log_csv": str(reports_dir / "optuna_trial_log.csv"),
        "gpu_usage_log_csv": str(reports_dir / "gpu_usage_log.csv"),
        "system_usage_log_csv": str(reports_dir / "system_usage_log.csv"),
        "gpu_benchmark_csv": str(reports_dir / "gpu_benchmark.csv"),
        "created_at": datetime.now().isoformat(),
    }


def save_model_bundle(
    *,
    bundle: Dict[str, Any],
    target: str,
    models_dir: Path,
    feature_importance: pd.DataFrame,
    save_store_fn=None,
    model_store=None,
    feature_store=None,
) -> Dict[str, Path]:
    """Persist model bundle and metadata, optionally writing notebook stores."""
    models_dir.mkdir(parents=True, exist_ok=True)

    out = {
        "model_path": models_dir / f"lgb_model_{target}.pkl",
        "meta_path": models_dir / f"lgb_model_{target}_meta.json",
    }

    if save_store_fn is not None and model_store is not None:
        save_store_fn(bundle, model_store, f"lgb_model_{target}")

    joblib.dump(bundle, out["model_path"])

    meta = {k: v for k, v in bundle.items() if not hasattr(v, "predict")}
    meta["feature_importance"] = feature_importance.head(30).to_dict(orient="records")
    out["meta_path"].write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    if save_store_fn is not None and feature_store is not None:
        save_store_fn(feature_importance, feature_store, f"feature_importance_{target}")

    return out
