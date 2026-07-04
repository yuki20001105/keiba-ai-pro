"""Cross-validation runner utilities for training workflows."""

from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import lightgbm as lgb
import numpy as np
from sklearn.metrics import mean_squared_error, roc_auc_score
from sklearn.model_selection import KFold, StratifiedKFold


class TrialTimeoutPruned(Exception):
    """Raised when a trial reaches its timeout limit."""


def build_cv_splits(
    X,
    y,
    is_regression: bool,
    n_splits: int,
    random_state: int,
) -> List[Tuple[np.ndarray, np.ndarray]]:
    """Build and cache CV split indices once."""
    if is_regression:
        splitter = KFold(n_splits=n_splits, shuffle=True, random_state=random_state)
        return list(splitter.split(X))

    splitter = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=random_state)
    return list(splitter.split(X, y))


def run_cv_trial(
    *,
    cv_splits: Sequence[Tuple[np.ndarray, np.ndarray]],
    X,
    y,
    cat_features: Sequence[str],
    feat_cols: Sequence[str],
    params: Dict[str, Any],
    num_boost_round: int,
    is_regression: bool,
    early_stopping_rounds: int,
    trial_timeout_sec: float,
    trial_started: float,
    trial_number: int,
    usage_snapshot_fn,
) -> Dict[str, Any]:
    """Run CV loop for one trial and return fold metrics and usage snapshots."""
    fold_scores: List[float] = []
    trial_usages: List[Dict[str, Any]] = []
    fold_records: List[Dict[str, Any]] = []

    for fold_idx, (tr_idx, va_idx) in enumerate(cv_splits, 1):
        elapsed = time.perf_counter() - trial_started
        if trial_timeout_sec > 0 and elapsed > trial_timeout_sec:
            raise TrialTimeoutPruned(f"trial={trial_number} elapsed={elapsed:.2f}s limit={trial_timeout_sec:.0f}s")

        fold_started = time.perf_counter()
        x_tr = X.iloc[tr_idx]
        x_va = X.iloc[va_idx]
        y_tr = y.iloc[tr_idx]
        y_va = y.iloc[va_idx]

        dtr = lgb.Dataset(
            x_tr,
            y_tr,
            categorical_feature=[c for c in cat_features if c in feat_cols],
            free_raw_data=False,
        )
        dva = lgb.Dataset(
            x_va,
            y_va,
            categorical_feature=[c for c in cat_features if c in feat_cols],
            reference=dtr,
            free_raw_data=False,
        )

        model_fold = lgb.train(
            params,
            dtr,
            num_boost_round=num_boost_round,
            valid_sets=[dva],
            callbacks=[
                lgb.early_stopping(early_stopping_rounds, verbose=False),
                lgb.log_evaluation(period=-1),
            ],
        )

        pred_va = model_fold.predict(x_va)
        if is_regression:
            rmse = float(np.sqrt(mean_squared_error(y_va, pred_va)))
            score = -rmse
            metric_for_log = rmse
        else:
            score = float(roc_auc_score(y_va, pred_va))
            metric_for_log = float("nan")

        fold_elapsed = time.perf_counter() - fold_started
        usage = usage_snapshot_fn()
        fold_scores.append(score)
        trial_usages.append(usage)
        fold_records.append(
            {
                "fold": int(fold_idx),
                "elapsed": float(round(fold_elapsed, 6)),
                "score": float(round(score, 6)),
                "rmse": float(round(metric_for_log, 6)) if np.isfinite(metric_for_log) else float("nan"),
            }
        )

    return {
        "fold_scores": fold_scores,
        "trial_usages": trial_usages,
        "fold_records": fold_records,
        "objective_value": float(np.mean(fold_scores)) if fold_scores else float("nan"),
    }
