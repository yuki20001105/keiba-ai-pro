from __future__ import annotations

import csv
import io
import re
import subprocess
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional

import lightgbm as lgb
import numpy as np

try:
    import psutil
except Exception:  # pragma: no cover - optional dependency
    psutil = None


def detect_lightgbm_device(prefer_gpu: bool = True) -> Tuple[str, str]:
    """Detect usable LightGBM device.

    Returns:
        (device_type, reason)
        device_type is one of: "gpu", "cpu".
    """
    if not prefer_gpu:
        return "cpu", "GPU preference disabled"

    try:
        x = np.array([[0.0, 1.0], [1.0, 0.0], [0.2, 0.8], [0.8, 0.2]], dtype=np.float32)
        y = np.array([0.0, 1.0, 0.2, 0.8], dtype=np.float32)
        ds = lgb.Dataset(x, label=y, free_raw_data=True)

        params = {
            "objective": "regression",
            "metric": "rmse",
            "device_type": "gpu",
            "verbosity": -1,
            "seed": 42,
        }

        lgb.train(params, ds, num_boost_round=1)
        return "gpu", "LightGBM GPU backend is available"
    except Exception as exc:  # pragma: no cover - environment dependent
        return "cpu", f"GPU unavailable: {type(exc).__name__}: {exc}"


def _run_nvidia_smi(query_fields: List[str]) -> Dict[str, Any]:
    """Run nvidia-smi query and parse CSV output."""
    cmd = [
        "nvidia-smi",
        f"--query-gpu={','.join(query_fields)}",
        "--format=csv,noheader,nounits",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=5)
    except FileNotFoundError:
        return {"ok": False, "error": "nvidia-smi not found", "rows": [], "raw": ""}
    except Exception as exc:  # pragma: no cover - environment dependent
        return {"ok": False, "error": str(exc), "rows": [], "raw": ""}

    out = (res.stdout or "").strip()
    if res.returncode != 0:
        err = (res.stderr or "").strip() or f"nvidia-smi failed with code {res.returncode}"
        return {"ok": False, "error": err, "rows": [], "raw": out}
    if not out:
        return {"ok": False, "error": "nvidia-smi returned empty output", "rows": [], "raw": out}

    reader = csv.reader(io.StringIO(out))
    rows = []
    for line in reader:
        if not line:
            continue
        row = {}
        for i, field in enumerate(query_fields):
            row[field] = line[i].strip() if i < len(line) else ""
        rows.append(row)

    return {"ok": True, "error": "", "rows": rows, "raw": out}


def get_gpu_runtime_report() -> Dict[str, Any]:
    """Collect runtime GPU/LightGBM environment information."""
    fields = ["name", "memory.total", "memory.used", "utilization.gpu", "utilization.memory"]
    smi = _run_nvidia_smi(fields)
    cuda_version = ""
    try:
        res = subprocess.run(["nvidia-smi"], capture_output=True, text=True, check=False, timeout=5)
        raw = (res.stdout or "") + "\n" + (res.stderr or "")
        m = re.search(r"CUDA Version:\s*([0-9.]+)", raw)
        if m:
            cuda_version = m.group(1)
    except Exception:
        cuda_version = ""

    first = smi["rows"][0] if smi.get("ok") and smi.get("rows") else {}
    return {
        "lightgbm_version": lgb.__version__,
        "lightgbm_gpu_available": bool(smi.get("ok")),
        "cuda_version": cuda_version,
        "nvidia_smi_ok": bool(smi.get("ok")),
        "nvidia_smi_error": smi.get("error", ""),
        "nvidia_smi_raw": smi.get("raw", ""),
        "gpus": smi.get("rows", []),
        "gpu_name": first.get("name", ""),
        "gpu_memory_total_mb": _to_float(first.get("memory.total", "")),
        "gpu_memory_used_mb": _to_float(first.get("memory.used", "")),
    }


def get_system_usage_snapshot() -> Dict[str, Any]:
    """Collect CPU/GPU usage snapshot for trial logging."""
    cpu_pct = float(psutil.cpu_percent(interval=None)) if psutil is not None else float("nan")
    mem_pct = float(psutil.virtual_memory().percent) if psutil is not None else float("nan")

    fields = ["name", "memory.used", "memory.total", "utilization.gpu", "utilization.memory"]
    smi = _run_nvidia_smi(fields)
    first = smi["rows"][0] if smi.get("ok") and smi.get("rows") else {}

    return {
        "cpu_usage_pct": cpu_pct,
        "memory_percent": mem_pct,
        "gpu_usage_pct": _to_float(first.get("utilization.gpu", "")),
        "gpu_memory_used_mb": _to_float(first.get("memory.used", "")),
        "gpu_memory_total_mb": _to_float(first.get("memory.total", "")),
        "gpu_memory_usage_pct": _to_float(first.get("utilization.memory", "")),
        "gpu_name": first.get("name", ""),
        "nvidia_smi_ok": bool(smi.get("ok")),
        "nvidia_smi_error": smi.get("error", ""),
    }


def parse_rtx_tier(gpu_name: str) -> Optional[int]:
    """Extract NVIDIA RTX model tier number (e.g. 4070) from GPU name."""
    if not gpu_name:
        return None
    m = re.search(r"RTX\s*(\d{4})", str(gpu_name), re.IGNORECASE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def recommend_mode_from_gpu(gpu_name: str) -> Dict[str, Any]:
    """Recommend mode parameters from GPU model tier.

    Rules:
      - RTX 3060+ : prod-like (fast_mode=False, n_trials=100, boosting=dart)
      - RTX 3050- : fast-like (fast_mode=True, n_trials=30, boosting=gbdt)
    """
    tier = parse_rtx_tier(gpu_name)
    if tier is None:
        return {"tier": None, "fast_mode": None, "n_trials": None, "boosting_type": None}
    if tier >= 3060:
        return {"tier": tier, "fast_mode": False, "n_trials": 100, "boosting_type": "dart"}
    return {"tier": tier, "fast_mode": True, "n_trials": 30, "boosting_type": "gbdt"}


def _to_float(val: Any) -> float:
    try:
        return float(str(val).strip())
    except Exception:
        return float("nan")


def apply_device_to_params(params: Dict[str, Any], device_type: str) -> Dict[str, Any]:
    """Return a copy of params with device_type enforced."""
    out = dict(params)
    out["device_type"] = "gpu" if str(device_type).lower() == "gpu" else "cpu"
    return out


def estimate_remaining_seconds(avg_trial_seconds: float, done_trials: int, total_trials: int) -> float:
    """Estimate remaining time from average trial duration."""
    remain = max(int(total_trials) - int(done_trials), 0)
    return max(float(avg_trial_seconds), 0.0) * remain


def format_seconds(seconds: float) -> str:
    """Human friendly duration string."""
    s = int(max(seconds, 0))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m}m {sec}s"
    if m > 0:
        return f"{m}m {sec}s"
    return f"{sec}s"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _reports_dir() -> Path:
    p = _repo_root() / "reports"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _append_csv_row(path: Path, headers: List[str], row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        if not exists:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in headers})


def append_gpu_usage_log(trial: int) -> None:
    """Append a single GPU/CPU usage snapshot row.

    Output: reports/gpu_usage_log.csv
    Columns: timestamp, trial, gpu_util, gpu_memory_used, gpu_memory_total, cpu_percent, memory_percent
    """
    snap = get_system_usage_snapshot()
    _append_csv_row(
        _reports_dir() / "gpu_usage_log.csv",
        [
            "timestamp",
            "trial",
            "gpu_util",
            "gpu_memory_used",
            "gpu_memory_total",
            "cpu_percent",
            "memory_percent",
        ],
        {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "trial": int(trial),
            "gpu_util": snap.get("gpu_usage_pct", ""),
            "gpu_memory_used": snap.get("gpu_memory_used_mb", ""),
            "gpu_memory_total": snap.get("gpu_memory_total_mb", ""),
            "cpu_percent": snap.get("cpu_usage_pct", ""),
            "memory_percent": snap.get("memory_percent", ""),
        },
    )


def append_optuna_trial_log(
    *,
    trial: int,
    fold: int,
    elapsed: float,
    rmse: float,
    score: float,
    device: str,
    params: Dict[str, Any],
) -> None:
    """Append one Optuna fold log row.

    Output: reports/optuna_trial_log.csv
    Columns: trial, fold, elapsed, rmse, score, device, params
    """
    _append_csv_row(
        _reports_dir() / "optuna_trial_log.csv",
        ["trial", "fold", "elapsed", "rmse", "score", "device", "params"],
        {
            "trial": int(trial),
            "fold": int(fold),
            "elapsed": f"{float(elapsed):.6f}",
            "rmse": f"{float(rmse):.6f}",
            "score": f"{float(score):.6f}",
            "device": str(device),
            "params": json.dumps(params, ensure_ascii=False, sort_keys=True),
        },
    )
