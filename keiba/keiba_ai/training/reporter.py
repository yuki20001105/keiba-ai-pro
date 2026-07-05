from __future__ import annotations

import pandas as pd


def print_training_summary(
    *,
    device: str,
    training_total_seconds: float,
    avg_trial_seconds: float,
    best_rmse: float,
    best_params: dict,
    final_train_boosting: str,
    gpu_speedup: float,
    runtime_table_df: pd.DataFrame,
) -> None:
    print("\n=== Training Summary ===")
    print(f"device: {device}")
    print(f"total_seconds: {training_total_seconds:.3f}")
    print(f"avg_trial_seconds: {avg_trial_seconds:.3f}")
    print(f"best_rmse: {best_rmse}")
    print(f"boosting: {final_train_boosting}")
    print(f"gpu_speedup: {gpu_speedup}")
    print(f"best_params_keys: {sorted(best_params.keys())[:12]}")
    if isinstance(runtime_table_df, pd.DataFrame) and not runtime_table_df.empty:
        print(runtime_table_df.to_string(index=False))
