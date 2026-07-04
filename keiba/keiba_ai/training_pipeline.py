"""Training pipeline orchestration for notebook/CLI shared execution."""

from __future__ import annotations

import time
from typing import Any, Dict

import lightgbm as lgb
import numpy as np
import optuna
import pandas as pd
from optuna.pruners import MedianPruner
from sklearn.metrics import mean_squared_error

from .callbacks import build_lgb_train_callbacks, create_optuna_progress_callback
from .cv_runner import build_cv_splits
from .gpu_monitor import GPUMonitor
from .logger import TrainingLogger
from .objective import create_optuna_objective
from .optuna_runtime import auto_cap_trials_from_history, get_mode_cfg, resolve_runtime_mode
from .runtime_profiler import build_runtime_breakdown_df, save_runtime_breakdown_csv
from .search_space import build_search_space


def run_training_pipeline(
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
):
    """Run Optuna + final train pipeline and return artifacts/results."""
    is_reg = target in ("speed_deviation", "rank")
    task = "regression" if is_reg else "binary"

    reports_dir.mkdir(parents=True, exist_ok=True)
    trial_log_csv = reports_dir / "optuna_trial_log.csv"
    trial_timing_csv = reports_dir / "optuna_trial_timing.csv"
    gpu_usage_csv = reports_dir / "gpu_usage_log.csv"
    system_usage_csv = reports_dir / "system_usage_log.csv"
    gpu_benchmark_csv = reports_dir / "gpu_benchmark.csv"
    runtime_breakdown_csv = reports_dir / "training_runtime_breakdown.csv"

    mode_name = resolve_runtime_mode(runtime.get("mode", "prod"), audit_mode, quick_test)
    train_logger = TrainingLogger(mode_name=mode_name, device=device.upper())
    monitor = GPUMonitor(
        get_snapshot=get_system_usage_snapshot,
        on_snapshot=lambda phase, snap: train_logger.log_system_usage(phase, snap),
        interval_sec=1.0,
    )
    monitor.start()

    base_params = lgb_params_default[task].copy()
    base_params.update(
        {
            "objective": "regression" if is_reg else "binary",
            "metric": "rmse" if is_reg else "auc",
            "boosting_type": boosting_ts,
            "device_type": device,
            "verbosity": -1,
            "seed": random_state,
            "num_threads": max(1, int(__import__("os").cpu_count() or 1)),
        }
    )

    if device == "cpu":
        base_params["boosting_type"] = "gbdt"
    if device == "gpu":
        base_params["device_type"] = "gpu"
        base_params["max_bin"] = 255
        base_params["gpu_use_dp"] = False

    mode_cfg = get_mode_cfg(mode_presets, mode_name)
    search_space = build_search_space(mode_cfg)
    early_stopping_rounds = int(search_space["early_stopping_rounds"])
    trial_timeout_sec = float(search_space["trial_timeout_sec"])

    if str(__import__("os").getenv("AUTO_TRIAL_CAP", "1")) == "1":
        old_trials = trials_ts
        trials_ts = auto_cap_trials_from_history(reports_dir, mode_name, is_reg, trials_ts)
        if trials_ts < old_trials:
            print(f"[AUTO-TRIAL-CAP] TRIALS_TS: {old_trials} -> {trials_ts}")

    if use_optuna:
        base_params["boosting_type"] = "gbdt"

    cv_splits_cached = build_cv_splits(
        X_train,
        y_train,
        is_regression=is_reg,
        n_splits=splits_ts,
        random_state=random_state,
    )

    ltr = lgb.Dataset(
        X_train,
        label=y_train,
        categorical_feature=[c for c in cat_train if c in feat_cols],
        free_raw_data=False,
    )
    lte = lgb.Dataset(
        X_test,
        label=y_test,
        categorical_feature=[c for c in cat_test if c in feat_cols],
        reference=ltr,
        free_raw_data=False,
    )

    training_total_start = time.perf_counter()
    optuna_total_seconds = 0.0
    training_model_seconds = 0.0
    cv_seconds = 0.0
    avg_trial_seconds = float("nan")
    best_rmse = float("nan")

    def run_cv_once(device_for_cv: str, num_round: int = 200):
        params = dict(base_params)
        params["device_type"] = device_for_cv
        params["boosting_type"] = "gbdt" if device_for_cv == "cpu" else params.get("boosting_type", "gbdt")
        if device_for_cv == "gpu":
            params["max_bin"] = 255
            params["gpu_use_dp"] = False
        else:
            params.pop("gpu_use_dp", None)

        started = time.perf_counter()
        scores = []
        for tr_idx, va_idx in cv_splits_cached:
            x_tr = X_train.iloc[tr_idx]
            x_va = X_train.iloc[va_idx]
            y_tr = y_train.iloc[tr_idx]
            y_va = y_train.iloc[va_idx]

            dtr = lgb.Dataset(x_tr, y_tr, categorical_feature=[c for c in cat_train if c in feat_cols], free_raw_data=False)
            dva = lgb.Dataset(x_va, y_va, categorical_feature=[c for c in cat_train if c in feat_cols], reference=dtr, free_raw_data=False)
            model = lgb.train(
                params,
                dtr,
                num_boost_round=int(num_round),
                valid_sets=[dva],
                callbacks=[lgb.early_stopping(early_stopping_rounds, verbose=False), lgb.log_evaluation(period=-1)],
            )
            pred = model.predict(x_va)
            if is_reg:
                scores.append(float(np.sqrt(mean_squared_error(y_va, pred))))
            else:
                from sklearn.metrics import roc_auc_score

                scores.append(float(roc_auc_score(y_va, pred)))
        return time.perf_counter() - started, float(np.mean(scores))

    try:
        if use_optuna:
            monitor.set_phase("optuna")
            print("Optuna ハイパーパラメータ最適化 開始…")
            print(f"Trials={trials_ts}, Splits={splits_ts}, Boosting={base_params['boosting_type']}")
            print(f"MedianPruner enabled: {use_median_pruner}")

            optuna_started = time.perf_counter()
            objective = create_optuna_objective(
                base_params=base_params,
                search_space=search_space,
                cv_splits=cv_splits_cached,
                X_train=X_train,
                y_train=y_train,
                cat_features=cat_train,
                feat_cols=feat_cols,
                is_regression=is_reg,
                trial_timeout_sec=trial_timeout_sec,
                early_stopping_rounds=early_stopping_rounds,
                logger=train_logger,
                device=device,
                mode_name=mode_name,
                estimate_remaining_seconds=estimate_remaining_seconds,
                format_seconds=format_seconds,
                total_trials_getter=lambda: trials_ts,
                get_system_usage_snapshot=get_system_usage_snapshot,
            )

            study = optuna.create_study(
                direction="minimize" if is_reg else "maximize",
                sampler=optuna.samplers.TPESampler(seed=random_state, multivariate=True),
                pruner=(MedianPruner(n_startup_trials=5, n_warmup_steps=2, interval_steps=1) if use_median_pruner else optuna.pruners.NopPruner()),
            )
            progress_cb = create_optuna_progress_callback(
                optuna_started_at=optuna_started,
                total_trials_getter=lambda: trials_ts,
                estimate_remaining_seconds=estimate_remaining_seconds,
                format_seconds=format_seconds,
            )
            study.optimize(objective, n_trials=trials_ts, n_jobs=1, gc_after_trial=True, callbacks=[progress_cb])

            optuna_total_seconds = time.perf_counter() - optuna_started
            cv_seconds = optuna_total_seconds
            completed_trials = len([t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE])
            avg_trial_seconds = optuna_total_seconds / max(completed_trials, 1)

            best_params = dict(base_params)
            best_params.update(study.best_params)
            best_params["boosting_type"] = "gbdt"
            best_params["device_type"] = device
            opt_score = study.best_value
        else:
            best_params = dict(base_params)
            best_params["boosting_type"] = "gbdt" if device == "cpu" else boosting_ts
            best_params["device_type"] = device
            best_params["num_boost_round"] = boost_round_ts
            opt_score = float("nan")

        best_params.setdefault("verbose", -1)
        num_boost_round = int(best_params.pop("num_boost_round", boost_round_ts))
        if mode_name == "prod":
            num_boost_round = max(num_boost_round, int(mode_presets.get("prod", {}).get("num_boost_round", 500)))

        final_train_boosting = "gbdt"
        if mode_name == "prod" and device != "cpu":
            final_train_boosting = "dart"
        best_params["boosting_type"] = final_train_boosting

        monitor.set_phase("training")
        train_started = time.perf_counter()
        model = lgb.train(
            best_params,
            ltr,
            num_boost_round=num_boost_round,
            valid_sets=[lte],
            callbacks=build_lgb_train_callbacks(early_stopping_rounds, log_period=50),
        )
        training_model_seconds = time.perf_counter() - train_started

        best_iter = model.best_iteration
        y_pred = model.predict(X_test)
        metrics: Dict[str, Any] = {}
        if task == "binary":
            from sklearn.metrics import accuracy_score, log_loss, roc_auc_score

            metrics["auc"] = round(roc_auc_score(y_test, y_pred), 4)
            metrics["log_loss"] = round(log_loss(y_test, y_pred), 4)
            metrics["acc"] = round(accuracy_score(y_test, (y_pred > 0.5)), 4)
        else:
            best_rmse = float(np.sqrt(mean_squared_error(y_test, y_pred)))
            metrics["rmse"] = round(best_rmse, 4)
            metrics["corr"] = round(float(pd.Series(y_pred).corr(pd.Series(y_test.values))), 4)

        training_total_seconds = time.perf_counter() - training_total_start
        total_pipeline_seconds = time.perf_counter() - pipeline_started_at

        bench_rows = [
            {
                "device": device.upper(),
                "n_trials": trials_ts,
                "total_seconds": float(training_total_seconds),
                "avg_trial_seconds": float(avg_trial_seconds) if np.isfinite(avg_trial_seconds) else np.nan,
                "cv_seconds": float(cv_seconds),
            }
        ]

        if run_gpu_benchmark and gpu_env_report.get("lightgbm_gpu_available", False):
            bench_cpu_seconds, _ = run_cv_once("cpu", num_round=120)
            bench_gpu_seconds, _ = run_cv_once("gpu", num_round=120)
            bench_rows.append({"device": "CPU", "n_trials": 1, "total_seconds": float(bench_cpu_seconds), "avg_trial_seconds": float(bench_cpu_seconds), "cv_seconds": float(bench_cpu_seconds)})
            bench_rows.append({"device": "GPU", "n_trials": 1, "total_seconds": float(bench_gpu_seconds), "avg_trial_seconds": float(bench_gpu_seconds), "cv_seconds": float(bench_gpu_seconds)})

        bench_df = pd.DataFrame(bench_rows).drop_duplicates(subset=["device", "n_trials", "total_seconds"])
        bench_df.to_csv(gpu_benchmark_csv, index=False, encoding="utf-8-sig")

        gpu_speedup = float("nan")
        if set(["CPU", "GPU"]).issubset(set(bench_df["device"].astype(str).str.upper())):
            cpu_val = float(bench_df[bench_df["device"].astype(str).str.upper() == "CPU"]["total_seconds"].iloc[-1])
            gpu_val = float(bench_df[bench_df["device"].astype(str).str.upper() == "GPU"]["total_seconds"].iloc[-1])
            if gpu_val > 0:
                gpu_speedup = cpu_val / gpu_val

        preprocess_seconds = float(time_data_loading + time_feature_engineering)
        runtime_table_df = build_runtime_breakdown_df(
            total_pipeline_seconds=total_pipeline_seconds,
            preprocess_seconds=preprocess_seconds,
            data_loading_seconds=time_data_loading,
            feature_engineering_seconds=time_feature_engineering,
            optuna_seconds=optuna_total_seconds,
            training_seconds=training_model_seconds,
            format_seconds=format_seconds,
        )
        save_runtime_breakdown_csv(runtime_table_df, runtime_breakdown_csv)

    finally:
        monitor.stop(join_timeout=2.0)

    saved_logs = train_logger.save_csvs(reports_dir)

    return {
        "task": task,
        "is_regression": is_reg,
        "mode_name": mode_name,
        "trials_ts": trials_ts,
        "splits_ts": splits_ts,
        "model": model,
        "best_iter": best_iter,
        "best_params": best_params,
        "opt_score": opt_score,
        "metrics": metrics,
        "best_rmse": best_rmse,
        "training_total_seconds": training_total_seconds,
        "training_model_seconds": training_model_seconds,
        "optuna_total_seconds": optuna_total_seconds,
        "cv_seconds": cv_seconds,
        "avg_trial_seconds": avg_trial_seconds,
        "gpu_speedup": gpu_speedup,
        "runtime_table_df": runtime_table_df,
        "runtime_breakdown_csv": runtime_breakdown_csv,
        "trial_log_csv": saved_logs["trial_log"],
        "trial_timing_csv": saved_logs["trial_timing"],
        "gpu_usage_csv": saved_logs["gpu_usage"],
        "system_usage_csv": saved_logs["system_usage"],
        "gpu_benchmark_csv": gpu_benchmark_csv,
    }
