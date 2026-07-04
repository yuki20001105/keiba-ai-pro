from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent.parent))

from keiba_ai.training_pipeline import run_training_pipeline  # type: ignore


def _fake_usage_snapshot() -> dict:
    return {
        "cpu_usage_pct": 10.0,
        "memory_percent": 20.0,
        "gpu_usage_pct": np.nan,
        "gpu_memory_used_mb": np.nan,
        "gpu_memory_total_mb": np.nan,
    }


def test_run_training_pipeline_smoke(tmp_path: Path) -> None:
    rng = np.random.default_rng(42)
    n_train = 120
    n_test = 40
    cols = ["f1", "f2", "f3", "f4"]

    X_train = pd.DataFrame(rng.normal(size=(n_train, len(cols))), columns=cols)
    X_test = pd.DataFrame(rng.normal(size=(n_test, len(cols))), columns=cols)

    y_train = pd.Series(rng.normal(size=n_train), name="speed_deviation")
    y_test = pd.Series(rng.normal(size=n_test), name="speed_deviation")

    mode_presets = {
        "quick_test": {
            "n_trials": 2,
            "n_splits": 2,
            "boosting": "gbdt",
            "num_boost_round": 40,
            "early_stopping_rounds": 10,
            "trial_timeout_sec": 30,
        },
        "prod": {
            "n_trials": 2,
            "n_splits": 2,
            "boosting": "gbdt",
            "num_boost_round": 40,
            "early_stopping_rounds": 10,
            "trial_timeout_sec": 30,
        },
    }

    lgb_defaults = {
        "regression": {
            "objective": "regression",
            "metric": "rmse",
            "learning_rate": 0.05,
            "num_leaves": 31,
            "verbose": -1,
        },
        "binary": {
            "objective": "binary",
            "metric": "auc",
            "learning_rate": 0.05,
            "num_leaves": 31,
            "verbose": -1,
        },
    }

    result = run_training_pipeline(
        target="speed_deviation",
        runtime={"mode": "quick_test"},
        audit_mode=False,
        quick_test=True,
        random_state=42,
        use_optuna=False,
        use_median_pruner=True,
        mode_presets=mode_presets,
        reports_dir=tmp_path,
        gpu_env_report={"lightgbm_gpu_available": False},
        device="cpu",
        boosting_ts="gbdt",
        boost_round_ts=30,
        trials_ts=2,
        splits_ts=2,
        X_train=X_train,
        y_train=y_train,
        X_test=X_test,
        y_test=y_test,
        cat_train=[],
        cat_test=[],
        feat_cols=cols,
        lgb_params_default=lgb_defaults,
        run_gpu_benchmark=False,
        time_data_loading=0.1,
        time_feature_engineering=0.2,
        pipeline_started_at=0.0,
        estimate_remaining_seconds=lambda avg, done, total: max((total - done) * avg, 0.0),
        format_seconds=lambda s: f"{int(s)}s",
        get_system_usage_snapshot=_fake_usage_snapshot,
    )

    assert "model" in result
    assert "metrics" in result
    assert "runtime_breakdown_csv" in result
    assert Path(result["runtime_breakdown_csv"]).exists()
    assert Path(result["trial_timing_csv"]).exists() or result["trial_timing_csv"]
