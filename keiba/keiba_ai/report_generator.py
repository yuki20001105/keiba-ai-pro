"""Training report output helpers."""

from __future__ import annotations

from typing import Any, Dict

import numpy as np


def print_training_summary(
    *,
    device: str,
    training_total_seconds: float,
    avg_trial_seconds: float,
    best_rmse: float,
    best_params: Dict[str, Any],
    final_train_boosting: str,
    gpu_speedup: float,
    runtime_table_df,
) -> None:
    """Print a standard training summary block."""
    print("\n" + "=" * 50)
    print("Training Summary")
    print("=" * 50)
    print(f"Device                : {str(device).upper()}")
    print(f"Total Time            : {int(training_total_seconds)} sec")
    print(
        f"Average Trial         : {int(avg_trial_seconds) if np.isfinite(avg_trial_seconds) else 'N/A'} sec"
    )
    print(f"Best RMSE             : {best_rmse if np.isfinite(best_rmse) else 'N/A'}")
    print(f"Best Parameters       : {best_params}")
    print(f"Final Boosting        : {final_train_boosting}")
    print(
        f"GPU Speedup           : {round(gpu_speedup, 2) if np.isfinite(gpu_speedup) else 'N/A'} 倍"
    )
    print("=" * 50)
    print("\nRuntime Breakdown Table")
    print(runtime_table_df.to_string(index=False))
