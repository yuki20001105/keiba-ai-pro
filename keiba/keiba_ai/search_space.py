"""Search-space helpers for LightGBM + Optuna."""

from __future__ import annotations

from typing import Any, Dict, Tuple


def build_search_space(mode_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize mode config into a robust search-space definition."""
    lr_low = float(mode_cfg.get("lr_low", 0.03))
    lr_high = float(mode_cfg.get("lr_high", 0.10))
    if lr_low > lr_high:
        lr_low, lr_high = lr_high, lr_low

    round_min = int(mode_cfg.get("num_boost_round_min", mode_cfg.get("num_boost_round", 200)))
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
        "learning_rate": (lr_low, lr_high),
        "num_boost_round": (round_min, round_max),
        "num_boost_round_fixed": (round_min if round_min == round_max else None),
        "num_leaves": (leaves_min, leaves_max),
        "max_depth": (depth_min, depth_max),
        "min_child_samples": (mcs_min, mcs_max),
        "early_stopping_rounds": int(mode_cfg.get("early_stopping_rounds", 50)),
        "trial_timeout_sec": float(mode_cfg.get("trial_timeout_sec", 0)),
    }


def sample_optuna_lgb_params(trial: Any, base_params: Dict[str, Any], space: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    """Sample trial params from Optuna using a normalized search space."""
    params = dict(base_params)

    lr_low, lr_high = space["learning_rate"]
    leaves_min, leaves_max = space["num_leaves"]
    depth_min, depth_max = space["max_depth"]
    mcs_min, mcs_max = space["min_child_samples"]

    params.update(
        {
            "learning_rate": trial.suggest_float("learning_rate", lr_low, lr_high),
            "num_leaves": trial.suggest_int("num_leaves", leaves_min, leaves_max),
            "max_depth": trial.suggest_int("max_depth", depth_min, depth_max),
            "min_child_samples": trial.suggest_int("min_child_samples", mcs_min, mcs_max),
            "feature_fraction": trial.suggest_float("feature_fraction", 0.6, 1.0),
            "bagging_fraction": trial.suggest_float("bagging_fraction", 0.6, 1.0),
            "bagging_freq": trial.suggest_int("bagging_freq", 0, 10),
            "lambda_l1": trial.suggest_float("lambda_l1", 1e-8, 10.0, log=True),
            "lambda_l2": trial.suggest_float("lambda_l2", 1e-8, 10.0, log=True),
        }
    )
    params["boosting_type"] = "gbdt"

    round_fixed = space.get("num_boost_round_fixed")
    if round_fixed is not None:
        return params, int(round_fixed)

    round_min, round_max = space["num_boost_round"]
    num_round = trial.suggest_int("num_boost_round", int(round_min), int(round_max))
    return params, int(num_round)
