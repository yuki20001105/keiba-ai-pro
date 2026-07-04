from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent.parent))

import keiba_ai.training.pipeline as tp  # type: ignore
from keiba_ai.training.config import TrainingPipelineConfig  # type: ignore


def test_run_notebook_pipeline_delegates(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    def _fake_run_training_pipeline(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "task": "regression", "mode_name": "quick_test", "trials_ts": 1, "splits_ts": 2,
                "model": object(), "best_iter": 1, "best_params": {}, "opt_score": 0.0, "metrics": {},
                "best_rmse": 0.0, "training_total_seconds": 0.0, "training_model_seconds": 0.0,
                "optuna_total_seconds": 0.0, "cv_seconds": 0.0, "avg_trial_seconds": 0.0, "gpu_speedup": 0.0,
                "runtime_table_df": pd.DataFrame(), "runtime_breakdown_csv": tmp_path / 'runtime.csv',
                "trial_log_csv": tmp_path / 'trial.csv', "trial_timing_csv": tmp_path / 'timing.csv',
                "gpu_usage_csv": tmp_path / 'gpu.csv', "system_usage_csv": tmp_path / 'sys.csv',
                "gpu_benchmark_csv": tmp_path / 'bench.csv'}

    monkeypatch.setattr(tp, "run_training_pipeline", _fake_run_training_pipeline)

    out = tp.run_notebook_pipeline(
        target="speed_deviation",
        runtime={"mode": "quick_test"},
        audit_mode=False,
        quick_test=True,
        random_state=42,
        use_optuna=False,
        use_median_pruner=True,
        mode_presets={"quick_test": {}},
        reports_dir=tmp_path,
        gpu_env_report={},
        device="cpu",
        boosting_ts="gbdt",
        boost_round_ts=10,
        trials_ts=1,
        splits_ts=2,
        X_train=pd.DataFrame({"x": [0.1, 0.2]}),
        y_train=pd.Series([0.1, 0.2]),
        X_test=pd.DataFrame({"x": [0.3, 0.4]}),
        y_test=pd.Series([0.3, 0.4]),
        cat_train=[],
        cat_test=[],
        feat_cols=["x"],
        lgb_params_default={"regression": {}, "binary": {}},
        run_gpu_benchmark=False,
        time_data_loading=0.0,
        time_feature_engineering=0.0,
        pipeline_started_at=0.0,
        estimate_remaining_seconds=lambda *_: 0.0,
        format_seconds=lambda s: str(s),
        get_system_usage_snapshot=lambda: {},
    )

    assert out.get("ok") is True
    assert captured.get("target") == "speed_deviation"
    assert captured.get("device") == "cpu"


def test_training_pipeline_config_from_notebook() -> None:
    cfg = TrainingPipelineConfig.from_notebook(
        target="win",
        runtime={"mode": "prod"},
        audit_mode=False,
        quick_test=False,
        random_state=42,
        use_optuna=True,
        use_median_pruner=True,
        mode_presets={"prod": {}},
        reports_dir=Path("."),
        gpu_env_report={},
        device="cpu",
        boosting_ts="gbdt",
        boost_round_ts=100,
        trials_ts=3,
        splits_ts=3,
        lgb_params_default={"binary": {}, "regression": {}},
        run_gpu_benchmark=False,
        time_data_loading=0.0,
        time_feature_engineering=0.0,
        pipeline_started_at=0.0,
    )
    assert cfg.target == "win"
    assert cfg.runtime["mode"] == "prod"
