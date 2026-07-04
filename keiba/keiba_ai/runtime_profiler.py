"""Runtime profiling helpers."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def build_runtime_breakdown_df(
    *,
    total_pipeline_seconds: float,
    preprocess_seconds: float,
    data_loading_seconds: float,
    feature_engineering_seconds: float,
    optuna_seconds: float,
    training_seconds: float,
    format_seconds,
) -> pd.DataFrame:
    """Build a runtime breakdown table DataFrame."""
    return pd.DataFrame(
        [
            {
                "stage": "Total",
                "seconds": total_pipeline_seconds,
                "formatted": format_seconds(total_pipeline_seconds),
            },
            {
                "stage": "Preprocessing",
                "seconds": preprocess_seconds,
                "formatted": format_seconds(preprocess_seconds),
            },
            {
                "stage": "Data Loading",
                "seconds": data_loading_seconds,
                "formatted": format_seconds(data_loading_seconds),
            },
            {
                "stage": "Feature Engineering",
                "seconds": feature_engineering_seconds,
                "formatted": format_seconds(feature_engineering_seconds),
            },
            {
                "stage": "Optuna",
                "seconds": optuna_seconds,
                "formatted": format_seconds(optuna_seconds),
            },
            {
                "stage": "Training",
                "seconds": training_seconds,
                "formatted": format_seconds(training_seconds),
            },
        ]
    )


def save_runtime_breakdown_csv(df: pd.DataFrame, csv_path: Path) -> None:
    """Save runtime breakdown to CSV."""
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
