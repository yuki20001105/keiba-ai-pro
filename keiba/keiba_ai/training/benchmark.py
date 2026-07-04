"""Benchmark helpers for training package."""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_gpu_speedup(bench_df: pd.DataFrame) -> float:
    """Compute CPU/GPU speedup from benchmark table."""
    gpu_speedup = float("nan")
    if set(["CPU", "GPU"]).issubset(set(bench_df["device"].astype(str).str.upper())):
        cpu_val = float(bench_df[bench_df["device"].astype(str).str.upper() == "CPU"]["total_seconds"].iloc[-1])
        gpu_val = float(bench_df[bench_df["device"].astype(str).str.upper() == "GPU"]["total_seconds"].iloc[-1])
        if gpu_val > 0:
            gpu_speedup = cpu_val / gpu_val
    return gpu_speedup


def save_benchmark_csv(bench_rows, csv_path) -> pd.DataFrame:
    """Save benchmark rows as CSV and return DataFrame."""
    bench_df = pd.DataFrame(bench_rows).drop_duplicates(subset=["device", "n_trials", "total_seconds"])
    bench_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    return bench_df
