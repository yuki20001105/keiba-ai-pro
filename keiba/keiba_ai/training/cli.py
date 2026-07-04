from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from .bundle import build_feature_importance_df, build_model_bundle, save_model_bundle
from .pipeline import run_notebook_pipeline
from .reporter import print_training_summary
from .runtime import get_default_mode_presets, merge_mode_presets_from_yaml


def _encode_object_cols(df: pd.DataFrame) -> pd.DataFrame:
    out = {}
    for col in df.columns:
        s = df[col]
        if hasattr(s, "cat"):
            codes = s.cat.codes.astype(np.float32)
            codes[codes == -1] = np.nan
            out[col] = codes
        elif s.dtype == object:
            codes_int, _ = pd.factorize(s)
            arr = codes_int.astype(np.float32)
            arr[arr == -1] = np.nan
            out[col] = pd.Series(arr, index=df.index)
        else:
            out[col] = s
    return pd.DataFrame(out, index=df.index)


def _load_features_or_synthetic(features_path: Path, target: str, seed: int):
    if features_path.exists():
        data = joblib.load(features_path)
        X_train = _encode_object_cols(pd.DataFrame(data["X_train"]).copy()).reset_index(drop=True)
        X_test = _encode_object_cols(pd.DataFrame(data["X_test"]).copy()).reset_index(drop=True)
        y_train = pd.Series(data["y_train"]).reset_index(drop=True)
        y_test = pd.Series(data["y_test"]).reset_index(drop=True)
        cat_features = [c for c in data.get("cat_features", []) if c in X_train.columns]
        return X_train, X_test, y_train, y_test, cat_features, False

    rng = np.random.default_rng(seed)
    cols = [f"f{i}" for i in range(1, 9)]
    X_train = pd.DataFrame(rng.normal(size=(500, len(cols))), columns=cols)
    X_test = pd.DataFrame(rng.normal(size=(120, len(cols))), columns=cols)
    if target in ("speed_deviation", "rank"):
        y_train = pd.Series(rng.normal(size=500), name=target)
        y_test = pd.Series(rng.normal(size=120), name=target)
    else:
        y_train = pd.Series(rng.integers(0, 2, size=500), name=target)
        y_test = pd.Series(rng.integers(0, 2, size=120), name=target)
    return X_train, X_test, y_train, y_test, [], True


def _snapshot_stub() -> dict:
    return {
        "cpu_usage_pct": np.nan,
        "memory_percent": np.nan,
        "gpu_usage_pct": np.nan,
        "gpu_memory_used_mb": np.nan,
        "gpu_memory_total_mb": np.nan,
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Unified training CLI")
    p.add_argument("--mode", choices=["QUICK_TEST", "FAST", "AUDIT", "PROD"], default="QUICK_TEST")
    p.add_argument("--target", default="speed_deviation")
    p.add_argument("--features", default="notebooks/data/features/features.pkl")
    p.add_argument("--reports-dir", default="notebooks/reports")
    p.add_argument("--models-dir", default="python-api/models")
    p.add_argument("--trials", type=int, default=None)
    p.add_argument("--splits", type=int, default=None)
    p.add_argument("--random-state", type=int, default=42)
    p.add_argument("--save-model", action="store_true")
    p.add_argument("--no-save-model", action="store_true")
    p.add_argument("--print-json", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    root = Path(__file__).resolve().parents[3]
    features_path = root / args.features
    reports_dir = root / args.reports_dir
    models_dir = root / args.models_dir

    mode = str(args.mode).upper()
    quick_test = mode == "QUICK_TEST"
    audit_mode = mode == "AUDIT"

    mode_presets = get_default_mode_presets()
    yaml_path = root / "configs" / "audit_modes.yaml"
    try:
        import yaml  # type: ignore
    except Exception:
        yaml = None
    mode_presets = merge_mode_presets_from_yaml(mode_presets, yaml_path, yaml)

    runtime = {"mode": mode.lower()}
    mode_cfg = mode_presets.get(mode.lower(), mode_presets.get("prod", {}))

    trials_ts = int(args.trials if args.trials is not None else mode_cfg.get("n_trials", 5 if quick_test else 30))
    splits_ts = int(args.splits if args.splits is not None else mode_cfg.get("n_splits", 2 if quick_test else 5))
    boosting_ts = str(mode_cfg.get("boosting", "gbdt"))
    boost_round_ts = int(mode_cfg.get("num_boost_round", 150 if quick_test else 500))

    lgb_defaults = {
        "binary": {
            "objective": "binary",
            "metric": "auc",
            "learning_rate": 0.05,
            "num_leaves": 63,
            "min_data_in_leaf": 30,
            "feature_fraction": 0.8,
            "bagging_fraction": 0.8,
            "bagging_freq": 5,
            "lambda_l1": 0.1,
            "lambda_l2": 0.1,
            "verbose": -1,
        },
        "regression": {
            "objective": "regression",
            "metric": "rmse",
            "learning_rate": 0.05,
            "num_leaves": 63,
            "min_data_in_leaf": 30,
            "feature_fraction": 0.8,
            "bagging_fraction": 0.8,
            "bagging_freq": 5,
            "verbose": -1,
        },
    }

    sys.path.insert(0, str(root / "keiba"))
    from keiba_ai.gpu_utils import detect_lightgbm_device, estimate_remaining_seconds, format_seconds, get_gpu_runtime_report

    device, _ = detect_lightgbm_device(prefer_gpu=True)
    gpu_env_report = get_gpu_runtime_report()
    if quick_test:
        gpu_env_report["lightgbm_gpu_available"] = False

    X_train, X_test, y_train, y_test, cat_features, synthetic_used = _load_features_or_synthetic(
        features_path, args.target, args.random_state
    )

    tr_mask_notna = pd.Series(y_train).notna().values
    te_mask_notna = pd.Series(y_test).notna().values
    drop_tr = int((~tr_mask_notna).sum())
    drop_te = int((~te_mask_notna).sum())
    if drop_tr > 0:
        X_train = X_train.loc[tr_mask_notna].reset_index(drop=True)
        y_train = pd.Series(y_train).loc[tr_mask_notna].reset_index(drop=True)
    if drop_te > 0:
        X_test = X_test.loc[te_mask_notna].reset_index(drop=True)
        y_test = pd.Series(y_test).loc[te_mask_notna].reset_index(drop=True)
    print(f"[SANITIZE] dropped train NaN targets: {drop_tr}")
    print(f"[SANITIZE] dropped test NaN targets: {drop_te}")

    feat_cols = list(X_train.columns)

    reports_dir.mkdir(parents=True, exist_ok=True)

    result = run_notebook_pipeline(
        target=args.target,
        runtime=runtime,
        audit_mode=audit_mode,
        quick_test=quick_test,
        random_state=args.random_state,
        use_optuna=True,
        use_median_pruner=True,
        mode_presets=mode_presets,
        reports_dir=reports_dir,
        gpu_env_report=gpu_env_report,
        device=device,
        boosting_ts=boosting_ts,
        boost_round_ts=boost_round_ts,
        trials_ts=trials_ts,
        splits_ts=splits_ts,
        X_train=X_train,
        y_train=y_train,
        X_test=X_test,
        y_test=y_test,
        cat_train=cat_features,
        cat_test=cat_features,
        feat_cols=feat_cols,
        lgb_params_default=lgb_defaults,
        run_gpu_benchmark=False,
        time_data_loading=0.0,
        time_feature_engineering=0.0,
        pipeline_started_at=time.perf_counter(),
        estimate_remaining_seconds=estimate_remaining_seconds,
        format_seconds=format_seconds,
        get_system_usage_snapshot=_snapshot_stub,
    )

    final_train_boosting = str(result["best_params"].get("boosting_type", "gbdt"))
    print_training_summary(
        device=device,
        training_total_seconds=float(result["training_total_seconds"]),
        avg_trial_seconds=float(result["avg_trial_seconds"]),
        best_rmse=float(result["best_rmse"]),
        best_params=result["best_params"],
        final_train_boosting=final_train_boosting,
        gpu_speedup=float(result["gpu_speedup"]),
        runtime_table_df=result["runtime_table_df"],
    )

    save_model = args.save_model and not args.no_save_model
    if save_model:
        imp = build_feature_importance_df(result["model"])
        bundle = build_model_bundle(
            model=result["model"],
            feature_cols=feat_cols,
            cat_features=cat_features,
            target=args.target,
            task=result["task"],
            metrics=result["metrics"],
            best_iteration=int(result["best_iter"]),
            params=result["best_params"],
            feature_importance=imp,
            test_days=120,
            cutoff_date=str(pd.Timestamp.now().date()),
            mode=result["mode_name"],
            fast_mode=(mode in ("QUICK_TEST", "FAST")),
            audit_mode=audit_mode,
            device_type=device,
            gpu_env_report=gpu_env_report,
            optuna_trials=int(result["trials_ts"]),
            cv_splits=int(result["splits_ts"]),
            total_training_seconds=float(result["training_total_seconds"]),
            avg_trial_seconds=float(result["avg_trial_seconds"]) if np.isfinite(float(result["avg_trial_seconds"])) else None,
            gpu_speedup=float(result["gpu_speedup"]) if np.isfinite(float(result["gpu_speedup"])) else None,
            runtime_breakdown=result["runtime_table_df"].to_dict(orient="records"),
            reports_dir=reports_dir,
        )
        out = save_model_bundle(
            bundle=bundle,
            target=args.target,
            models_dir=models_dir,
            feature_importance=imp,
        )
        print(f"saved model: {out['model_path']}")

    print(f"trial log: {result['trial_log_csv']}")
    print(f"trial timing: {result['trial_timing_csv']}")
    print(f"runtime breakdown: {result['runtime_breakdown_csv']}")
    if synthetic_used:
        print("note: synthetic dataset was used because features.pkl was not found")

    if args.print_json:
        serializable = {
            "mode": mode,
            "target": args.target,
            "device": device,
            "trials": int(result["trials_ts"]),
            "splits": int(result["splits_ts"]),
            "metrics": result["metrics"],
            "reports_dir": str(reports_dir),
            "synthetic_used": bool(synthetic_used),
        }
        print(json.dumps(serializable, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
