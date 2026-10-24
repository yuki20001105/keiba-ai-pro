"""Isolated worker; launch through the protected Track B scheduler."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time

for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[name] = "2"
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]

if __name__ == "__main__":
    import psutil
    process = psutil.Process()
    if os.name == "nt":
        process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", required=True, choices=["logistic", "market_logistic", "lightgbm"])
    parser.add_argument("--device", default="cpu", choices=["cpu", "gpu"])
    parser.add_argument("--fold", default="development", choices=["development", "validation"])
    parser.add_argument("--rounds", default=150, type=int)
    args = parser.parse_args()
    stop = threading.Event()
    peaks = {"peak_ram_mb": 0., "gpu_memory_mb": None, "gpu_utilization_percent": None, "samples": 0}
    from market_edge.compute import ResourceMonitor
    monitor = ResourceMonitor(ROOT)
    def sample():
        while not stop.is_set():
            try:
                peaks["peak_ram_mb"] = max(peaks["peak_ram_mb"], process.memory_info().rss / 2**20)
                state = monitor.sample()
                gpu = state.get("gpu")
                if gpu:
                    peaks["gpu_memory_mb"] = max(peaks["gpu_memory_mb"] or 0, gpu["used_mb"])
                    peaks["gpu_utilization_percent"] = max(peaks["gpu_utilization_percent"] or 0, gpu["utilization_percent"])
                peaks["samples"] += 1
            except (OSError, psutil.Error):
                pass
            stop.wait(.1)
    sampling = threading.Thread(target=sample, daemon=True)
    sampling.start()
    from market_edge.modeling import run_worker
    try:
        result = run_worker(args.data, args.output, model=args.model, device=args.device,
                            fold=args.fold, rounds=args.rounds)
    finally:
        stop.set()
        sampling.join(timeout=5)
    result["resources"] = {**peaks, "gpu_scope": "system-wide used VRAM and utilization, not attributable solely to worker",
                           "rss_sampling_seconds": .1, "gpu_sampling": "cached resource monitor"}
    (args.output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
