from __future__ import annotations

import subprocess
import time
from typing import Any

import lightgbm as lgb


def _run_nvidia_smi() -> str:
    try:
        out = subprocess.check_output(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,driver_version", "--format=csv,noheader"], stderr=subprocess.STDOUT, text=True, timeout=3)
        return out.strip()
    except Exception:
        return ""


def get_gpu_runtime_report() -> dict[str, Any]:
    raw = _run_nvidia_smi()
    gpu_name = ""
    mem_total_mb = float("nan")
    mem_used_mb = float("nan")
    cuda_version = ""
    if raw:
        first = raw.splitlines()[0]
        parts = [p.strip() for p in first.split(",")]
        if parts:
            gpu_name = parts[0]
        if len(parts) >= 3:
            try:
                mem_total_mb = float(parts[1].split()[0])
                mem_used_mb = float(parts[2].split()[0])
            except Exception:
                pass
    return {
        "lightgbm_version": getattr(lgb, "__version__", ""),
        "lightgbm_gpu_available": bool(raw),
        "gpu_name": gpu_name,
        "gpu_memory_total_mb": mem_total_mb,
        "gpu_memory_used_mb": mem_used_mb,
        "cuda_version": cuda_version,
        "nvidia_smi_raw": raw,
    }


def detect_lightgbm_device(prefer_gpu: bool = True) -> tuple[str, str]:
    report = get_gpu_runtime_report()
    if prefer_gpu and report.get("lightgbm_gpu_available"):
        return "gpu", "nvidia-smi detected GPU"
    return "cpu", "GPU not available"


def recommend_mode_from_gpu(gpu_name: str) -> dict[str, Any]:
    name = (gpu_name or "").lower()
    if any(k in name for k in ("rtx", "a100", "h100", "l40", "4090", "3090")):
        return {"fast_mode": False, "n_trials": 40, "boosting_type": "gbdt"}
    if gpu_name:
        return {"fast_mode": True, "n_trials": 20, "boosting_type": "gbdt"}
    return {"fast_mode": True, "n_trials": 10, "boosting_type": "gbdt"}


def format_seconds(seconds: float | int | None) -> str:
    if seconds is None:
        return "N/A"
    s = float(seconds)
    if s < 60:
        return f"{s:.1f}s"
    m, rs = divmod(int(s), 60)
    if m < 60:
        return f"{m}m {rs}s"
    h, rm = divmod(m, 60)
    return f"{h}h {rm}m {rs}s"


def estimate_remaining_seconds(elapsed_seconds: float, done: int, total: int) -> float | None:
    if total <= 0 or done <= 0 or done > total:
        return None
    per_item = float(elapsed_seconds) / float(done)
    return per_item * float(total - done)


def get_system_usage_snapshot() -> dict[str, Any]:
    return {
        "timestamp": time.time(),
        "cpu_percent": float("nan"),
        "memory_percent": float("nan"),
    }
