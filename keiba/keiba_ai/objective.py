"""Optuna objective factory built on shared CV and logging modules."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable, Dict, Sequence, Tuple

import numpy as np
import optuna

from .cv_runner import TrialTimeoutPruned, run_cv_trial
from .search_space import sample_optuna_lgb_params


def create_optuna_objective(
    *,
    base_params: Dict[str, Any],
    search_space: Dict[str, Any],
    cv_splits,
    X_train,
    y_train,
    cat_features: Sequence[str],
    feat_cols: Sequence[str],
    is_regression: bool,
    trial_timeout_sec: float,
    early_stopping_rounds: int,
    logger,
    device: str,
    mode_name: str,
    estimate_remaining_seconds: Callable[[float, int, int], float],
    format_seconds: Callable[[float], str],
    total_trials_getter: Callable[[], int],
    get_system_usage_snapshot: Callable[[], Dict[str, Any]],
) -> Callable[[optuna.Trial], float]:
    """Create an Optuna objective with timeout, pruning, and structured logging."""

    def _objective(trial: optuna.Trial) -> float:
        trial_started_at = datetime.now().isoformat()
        trial_started = time.perf_counter()
        num_round = np.nan
        params: Dict[str, Any] = {}

        print(f"[TRIAL START] trial={trial.number} started_at={trial_started_at}")

        try:
            params, num_round = sample_optuna_lgb_params(trial, base_params, search_space)
            result = run_cv_trial(
                cv_splits=cv_splits,
                X=X_train,
                y=y_train,
                cat_features=cat_features,
                feat_cols=feat_cols,
                params=params,
                num_boost_round=int(num_round),
                is_regression=is_regression,
                early_stopping_rounds=early_stopping_rounds,
                trial_timeout_sec=trial_timeout_sec,
                trial_started=trial_started,
                trial_number=trial.number,
                usage_snapshot_fn=get_system_usage_snapshot,
            )

            fold_scores = result["fold_scores"]
            trial_usages = result["trial_usages"]
            objective_value = float(result["objective_value"])

            for fold_record in result["fold_records"]:
                logger.log_trial_fold(
                    trial_number=int(trial.number),
                    num_boost_round=int(num_round),
                    learning_rate=float(params.get("learning_rate", np.nan)),
                    params=params,
                    fold_record=fold_record,
                )
                trial.report(float(np.mean(fold_scores[: int(fold_record["fold"])])), step=int(fold_record["fold"]))
                if trial.should_prune():
                    raise optuna.TrialPruned()

            trial_elapsed = time.perf_counter() - trial_started
            logger.log_trial_usage(int(trial.number), trial_usages)
            logger.log_trial_complete(
                trial_number=int(trial.number),
                started_at=trial_started_at,
                trial_elapsed_seconds=float(trial_elapsed),
                num_boost_round=int(num_round),
                learning_rate=float(params.get("learning_rate", np.nan)),
                objective=objective_value,
            )

            done_elapsed = [r["trial_elapsed_seconds"] for r in logger.trial_timing_rows if r.get("status") == "complete"]
            avg_trial = float(np.mean(done_elapsed)) if done_elapsed else float("nan")
            total_trials = int(total_trials_getter())
            remaining = estimate_remaining_seconds(avg_trial if np.isfinite(avg_trial) else 0.0, len(done_elapsed), total_trials)

            print(f"[TRIAL END] trial={trial.number} elapsed={trial_elapsed:.2f}s objective={objective_value:.6f}")
            print(f"[TRIAL ETA] avg={avg_trial:.2f}s remaining={format_seconds(remaining)}")
            return objective_value

        except TrialTimeoutPruned as exc:
            trial_elapsed = time.perf_counter() - trial_started
            logger.log_trial_pruned(
                trial_number=int(trial.number),
                started_at=trial_started_at,
                trial_elapsed_seconds=float(trial_elapsed),
                num_boost_round=float(num_round),
                learning_rate=float(params.get("learning_rate", np.nan)) if params else np.nan,
            )
            print(f"[TRIAL PRUNED-TIMEOUT] {exc}")
            raise optuna.TrialPruned() from exc
        except optuna.TrialPruned:
            trial_elapsed = time.perf_counter() - trial_started
            logger.log_trial_pruned(
                trial_number=int(trial.number),
                started_at=trial_started_at,
                trial_elapsed_seconds=float(trial_elapsed),
                num_boost_round=float(num_round),
                learning_rate=float(params.get("learning_rate", np.nan)) if params else np.nan,
            )
            print(f"[TRIAL PRUNED] trial={trial.number} elapsed={trial_elapsed:.2f}s")
            raise

    return _objective
