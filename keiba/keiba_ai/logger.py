"""Training logger helpers for Notebook/CLI shared use."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd


class TrainingLogger:
    """Collect trial/fold/system logs and persist them as CSV."""

    def __init__(self, mode_name: str, device: str) -> None:
        self.mode_name = mode_name
        self.device = device
        self.trial_log_rows: List[Dict[str, Any]] = []
        self.trial_timing_rows: List[Dict[str, Any]] = []
        self.gpu_usage_rows: List[Dict[str, Any]] = []
        self.system_usage_rows: List[Dict[str, Any]] = []

    def log_system_usage(self, phase: str, usage: Dict[str, Any]) -> None:
        self.system_usage_rows.append(
            {
                "timestamp": datetime.now().isoformat(),
                "phase": phase,
                "device": self.device,
                "cpu_percent": float(round(usage.get("cpu_usage_pct", np.nan), 2)) if np.isfinite(usage.get("cpu_usage_pct", np.nan)) else np.nan,
                "memory_percent": float(round(usage.get("memory_percent", np.nan), 2)) if np.isfinite(usage.get("memory_percent", np.nan)) else np.nan,
                "gpu_util": float(round(usage.get("gpu_usage_pct", np.nan), 2)) if np.isfinite(usage.get("gpu_usage_pct", np.nan)) else np.nan,
                "gpu_memory_used": float(round(usage.get("gpu_memory_used_mb", np.nan), 2)) if np.isfinite(usage.get("gpu_memory_used_mb", np.nan)) else np.nan,
                "gpu_memory_total": float(round(usage.get("gpu_memory_total_mb", np.nan), 2)) if np.isfinite(usage.get("gpu_memory_total_mb", np.nan)) else np.nan,
            }
        )

    def log_trial_fold(self, trial_number: int, num_boost_round: int, learning_rate: float, params: Dict[str, Any], fold_record: Dict[str, Any]) -> None:
        self.trial_log_rows.append(
            {
                "trial": int(trial_number),
                "fold": int(fold_record["fold"]),
                "mode": self.mode_name,
                "elapsed": float(fold_record["elapsed"]),
                "rmse": float(fold_record["rmse"]) if np.isfinite(fold_record["rmse"]) else np.nan,
                "score": float(fold_record["score"]),
                "device": self.device,
                "num_boost_round": int(num_boost_round),
                "learning_rate": float(learning_rate),
                "params": pd.Series(params).to_json(force_ascii=False),
            }
        )

    def log_trial_complete(self, trial_number: int, started_at: str, trial_elapsed_seconds: float, num_boost_round: int, learning_rate: float, objective: float) -> None:
        self.trial_timing_rows.append(
            {
                "trial": int(trial_number),
                "mode": self.mode_name,
                "status": "complete",
                "started_at": started_at,
                "finished_at": datetime.now().isoformat(),
                "trial_elapsed_seconds": float(round(trial_elapsed_seconds, 6)),
                "num_boost_round": int(num_boost_round),
                "learning_rate": float(learning_rate),
                "objective": float(objective),
            }
        )

    def log_trial_pruned(self, trial_number: int, started_at: str, trial_elapsed_seconds: float, num_boost_round: float, learning_rate: float) -> None:
        self.trial_timing_rows.append(
            {
                "trial": int(trial_number),
                "mode": self.mode_name,
                "status": "pruned",
                "started_at": started_at,
                "finished_at": datetime.now().isoformat(),
                "trial_elapsed_seconds": float(round(trial_elapsed_seconds, 6)),
                "num_boost_round": float(num_boost_round),
                "learning_rate": float(learning_rate),
                "objective": np.nan,
            }
        )

    def log_trial_usage(self, trial_number: int, usages: List[Dict[str, Any]]) -> None:
        u_cpu = [u.get("cpu_usage_pct", np.nan) for u in usages if np.isfinite(u.get("cpu_usage_pct", np.nan))]
        u_mem = [u.get("memory_percent", np.nan) for u in usages if np.isfinite(u.get("memory_percent", np.nan))]
        u_gpu = [u.get("gpu_usage_pct", np.nan) for u in usages if np.isfinite(u.get("gpu_usage_pct", np.nan))]
        u_gmem = [u.get("gpu_memory_used_mb", np.nan) for u in usages if np.isfinite(u.get("gpu_memory_used_mb", np.nan))]
        u_gmem_total = [u.get("gpu_memory_total_mb", np.nan) for u in usages if np.isfinite(u.get("gpu_memory_total_mb", np.nan))]

        self.gpu_usage_rows.append(
            {
                "timestamp": datetime.now().isoformat(),
                "trial": int(trial_number),
                "cpu_percent": float(np.mean(u_cpu)) if u_cpu else np.nan,
                "memory_percent": float(np.mean(u_mem)) if u_mem else np.nan,
                "gpu_util": float(np.mean(u_gpu)) if u_gpu else np.nan,
                "gpu_memory_used": float(np.mean(u_gmem)) if u_gmem else np.nan,
                "gpu_memory_total": float(np.mean(u_gmem_total)) if u_gmem_total else np.nan,
                "device": self.device,
            }
        )

    def average_completed_trial_seconds(self) -> float:
        elapsed = [r["trial_elapsed_seconds"] for r in self.trial_timing_rows if r.get("status") == "complete"]
        return float(np.mean(elapsed)) if elapsed else float("nan")

    def save_csvs(self, reports_dir: Path) -> Dict[str, Path]:
        reports_dir.mkdir(parents=True, exist_ok=True)
        out = {
            "trial_log": reports_dir / "optuna_trial_log.csv",
            "trial_timing": reports_dir / "optuna_trial_timing.csv",
            "gpu_usage": reports_dir / "gpu_usage_log.csv",
            "system_usage": reports_dir / "system_usage_log.csv",
        }
        if self.trial_log_rows:
            pd.DataFrame(self.trial_log_rows).to_csv(out["trial_log"], index=False, encoding="utf-8-sig")
        if self.trial_timing_rows:
            pd.DataFrame(self.trial_timing_rows).to_csv(out["trial_timing"], index=False, encoding="utf-8-sig")
        if self.gpu_usage_rows:
            pd.DataFrame(self.gpu_usage_rows).to_csv(out["gpu_usage"], index=False, encoding="utf-8-sig")
        if self.system_usage_rows:
            pd.DataFrame(self.system_usage_rows).to_csv(out["system_usage"], index=False, encoding="utf-8-sig")
        return out
