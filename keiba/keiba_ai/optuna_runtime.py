"""Runtime helpers for notebook/CLI Optuna training orchestration.

This module keeps mode presets, search space normalization, and
runtime control logic (e.g. auto trial cap) out of notebooks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pandas as pd


def get_default_mode_presets() -> Dict[str, Dict[str, Any]]:
    """Return default mode presets used by training notebooks and scripts."""
    return {
        "quick_test": {
            "n_trials": 5,
            "n_splits": 2,
            "boosting": "gbdt",
            "num_boost_round": 150,
            "lr_low": 0.03,
            "lr_high": 0.10,
            "num_boost_round_min": 100,
            "num_boost_round_max": 150,
            "num_leaves_min": 31,
            "num_leaves_max": 96,
            "max_depth_min": 3,
            "max_depth_max": 8,
            "min_child_samples_min": 20,
            "min_child_samples_max": 80,
            "early_stopping_rounds": 30,
            "trial_timeout_sec": 90,
        },
        "fast": {
            "n_trials": 10,
            "n_splits": 3,
            "boosting": "gbdt",
            "num_boost_round": 200,
            "lr_low": 0.03,
            "lr_high": 0.10,
            "num_boost_round_min": 100,
            "num_boost_round_max": 200,
            "num_leaves_min": 31,
            "num_leaves_max": 96,
            "max_depth_min": 3,
            "max_depth_max": 8,
            "min_child_samples_min": 20,
            "min_child_samples_max": 100,
            "early_stopping_rounds": 40,
            "trial_timeout_sec": 120,
        },
        "audit": {
            "n_trials": 20,
            "n_splits": 3,
            "boosting": "gbdt",
            "num_boost_round": 150,
            "lr_low": 0.03,
            "lr_high": 0.10,
            "num_boost_round_min": 150,
            "num_boost_round_max": 150,
            "num_leaves_min": 31,
            "num_leaves_max": 96,
            "max_depth_min": 3,
            "max_depth_max": 8,
            "min_child_samples_min": 20,
            "min_child_samples_max": 100,
            "early_stopping_rounds": 40,
            "trial_timeout_sec": 180,
        },
        "prod": {
            "n_trials": 30,
            "n_splits": 5,
            "boosting": "gbdt",
            "num_boost_round": 500,
            "lr_low": 0.01,
            "lr_high": 0.10,
            "num_boost_round_min": 500,
            "num_boost_round_max": 500,
            "num_leaves_min": 31,
            "num_leaves_max": 127,
            "max_depth_min": 3,
            "max_depth_max": 10,
            "min_child_samples_min": 20,
            "min_child_samples_max": 120,
            "early_stopping_rounds": 50,
            "trial_timeout_sec": 360,
        },
    }


def merge_mode_presets_from_yaml(
    presets: Dict[str, Dict[str, Any]],
    yaml_path: Path,
    yaml_module: Any,
) -> Dict[str, Dict[str, Any]]:
    """Merge YAML mode settings into presets and return a new dict."""
    merged: Dict[str, Dict[str, Any]] = {k: dict(v) for k, v in presets.items()}
    if yaml_module is None or not yaml_path.exists():
        return merged

    loaded = yaml_module.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    loaded_modes = loaded.get("modes", {}) if isinstance(loaded, dict) else {}
    if not isinstance(loaded_modes, dict):
        return merged

    for mode_name, mode_values in loaded_modes.items():
        if not isinstance(mode_values, dict):
            continue
        key = str(mode_name).lower()
        merged[key] = {**merged.get(key, {}), **mode_values}
    return merged


def resolve_runtime_mode(runtime_mode: str, audit_mode: bool, quick_test: bool) -> str:
    """Resolve runtime mode with explicit override precedence."""
    if quick_test:
        return "quick_test"
    if audit_mode:
        return "audit"
    mode = str(runtime_mode or "prod").lower()
    return mode if mode else "prod"


def get_mode_cfg(presets: Dict[str, Dict[str, Any]], mode_name: str) -> Dict[str, Any]:
    """Return normalized mode config with sane defaults."""
    cfg = dict(presets.get(mode_name, presets.get("prod", {})))
    cfg.setdefault("n_trials", 30)
    cfg.setdefault("n_splits", 5)
    cfg.setdefault("boosting", "gbdt")
    cfg.setdefault("num_boost_round", 500)
    cfg.setdefault("lr_low", 0.03)
    cfg.setdefault("lr_high", 0.10)
    cfg.setdefault("num_boost_round_min", cfg["num_boost_round"])
    cfg.setdefault("num_boost_round_max", cfg["num_boost_round"])
    cfg.setdefault("num_leaves_min", 31)
    cfg.setdefault("num_leaves_max", 127)
    cfg.setdefault("max_depth_min", 3)
    cfg.setdefault("max_depth_max", 10)
    cfg.setdefault("min_child_samples_min", 10)
    cfg.setdefault("min_child_samples_max", 100)
    cfg.setdefault("early_stopping_rounds", 50)
    cfg.setdefault("trial_timeout_sec", 0)
    return cfg


def build_optuna_controls(mode_cfg: Dict[str, Any], is_regression: bool) -> Dict[str, Any]:
    """Build normalized optimization controls from mode config."""
    lr_low = float(mode_cfg.get("lr_low", 0.03))
    lr_high = float(mode_cfg.get("lr_high", 0.10))
    if lr_low > lr_high:
        lr_low, lr_high = lr_high, lr_low

    round_min = int(mode_cfg.get("num_boost_round_min", mode_cfg.get("num_boost_round", 100)))
    round_max = int(mode_cfg.get("num_boost_round_max", mode_cfg.get("num_boost_round", 200)))
    if round_min > round_max:
        round_min, round_max = round_max, round_min

    leaves_min = int(mode_cfg.get("num_leaves_min", 31))
    leaves_max = int(mode_cfg.get("num_leaves_max", 127))
    if leaves_min > leaves_max:
        leaves_min, leaves_max = leaves_max, leaves_min

    depth_min = int(mode_cfg.get("max_depth_min", 3))
    depth_max = int(mode_cfg.get("max_depth_max", 10))
    if depth_min > depth_max:
        depth_min, depth_max = depth_max, depth_min

    mcs_min = int(mode_cfg.get("min_child_samples_min", 10))
    mcs_max = int(mode_cfg.get("min_child_samples_max", 100))
    if mcs_min > mcs_max:
        mcs_min, mcs_max = mcs_max, mcs_min

    return {
        "is_regression": bool(is_regression),
        "lr_low": lr_low,
        "lr_high": lr_high,
        "num_boost_round_min": round_min,
        "num_boost_round_max": round_max,
        "num_boost_round_fixed": round_min if round_min == round_max else None,
        "num_leaves_min": leaves_min,
        "num_leaves_max": leaves_max,
        "max_depth_min": depth_min,
        "max_depth_max": depth_max,
        "min_child_samples_min": mcs_min,
        "min_child_samples_max": mcs_max,
        "early_stopping_rounds": int(mode_cfg.get("early_stopping_rounds", 50)),
        "trial_timeout_sec": float(mode_cfg.get("trial_timeout_sec", 0)),
    }


def auto_cap_trials_from_history(
    reports_dir: Path,
    mode_name: str,
    is_regression: bool,
    current_trials: int,
    min_completed: int = 5,
    margin: int = 5,
) -> int:
    """Suggest capped trial count based on previous timing/objective history."""
    timing_path = reports_dir / "optuna_trial_timing.csv"
    if not timing_path.exists() or current_trials <= 0:
        return current_trials

    try:
        hist = pd.read_csv(timing_path)
    except Exception:
        return current_trials

    if "status" in hist.columns:
        hist = hist[hist["status"].astype(str).str.lower() == "complete"]
    if "mode" in hist.columns:
        hist = hist[hist["mode"].astype(str).str.lower() == str(mode_name).lower()]
    if len(hist) < int(min_completed) or "objective" not in hist.columns or "trial" not in hist.columns:
        return current_trials

    if bool(is_regression):
        best_row = hist.loc[hist["objective"].astype(float).idxmin()]
    else:
        best_row = hist.loc[hist["objective"].astype(float).idxmax()]

    best_trial = int(best_row["trial"])
    suggested = max(int(min_completed), best_trial + int(margin))
    return int(min(current_trials, suggested))
