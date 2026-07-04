"""Callback helpers for Optuna and LightGBM workflows."""

from __future__ import annotations

import time
from typing import Callable

import lightgbm as lgb
import optuna


def create_optuna_progress_callback(
    *,
    optuna_started_at: float,
    total_trials_getter: Callable[[], int],
    estimate_remaining_seconds: Callable[[float, int, int], float],
    format_seconds: Callable[[float], str],
):
    """Create a standard Optuna progress callback."""

    def _progress_cb(study: optuna.Study, _trial: optuna.trial.FrozenTrial) -> None:
        finished = len(
            [
                t
                for t in study.trials
                if t.state
                in (
                    optuna.trial.TrialState.COMPLETE,
                    optuna.trial.TrialState.PRUNED,
                    optuna.trial.TrialState.FAIL,
                )
            ]
        )
        elapsed = time.perf_counter() - optuna_started_at
        avg = elapsed / max(finished, 1)
        total_trials = int(total_trials_getter())
        remain = estimate_remaining_seconds(avg, finished, total_trials)
        print(f"Trial {finished}/{total_trials}")
        print(f"Average Trial : {int(avg)} sec")
        print(f"Estimated Remaining : {format_seconds(remain)}")

    return _progress_cb


def build_lgb_train_callbacks(early_stopping_rounds: int, log_period: int = 50):
    """Callbacks for final train stage."""
    return [
        lgb.early_stopping(stopping_rounds=int(early_stopping_rounds), verbose=False),
        lgb.log_evaluation(period=int(log_period)),
    ]
