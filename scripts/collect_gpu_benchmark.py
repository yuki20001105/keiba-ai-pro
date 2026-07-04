from __future__ import annotations

import csv
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    reports = root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    out = reports / "gpu_benchmark.csv"

    # Preferred source: notebook-generated benchmark.
    src = root / "notebooks" / "reports" / "gpu_benchmark.csv"
    rows: list[dict[str, str]] = []

    if src.exists():
        with src.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                stage = r.get("stage") or "05_model_training"
                cpu = r.get("cpu_seconds") or ""
                gpu = r.get("gpu_seconds") or ""
                ratio = r.get("speedup_ratio") or ""
                if not ratio and cpu and gpu:
                    try:
                        ratio = f"{float(cpu) / float(gpu):.4f}" if float(gpu) > 0 else ""
                    except Exception:
                        ratio = ""
                rows.append(
                    {
                        "stage": str(stage),
                        "cpu_seconds": str(cpu),
                        "gpu_seconds": str(gpu),
                        "speedup_ratio": str(ratio),
                    }
                )

    # Fallback: transform per-device summary if available.
    if not rows:
        fallback = root / "reports" / "gpu_benchmark_raw.csv"
        if fallback.exists():
            cpu_seconds = None
            gpu_seconds = None
            with fallback.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    device = str(r.get("device", "")).upper()
                    try:
                        total = float(r.get("total_seconds", "nan"))
                    except Exception:
                        continue
                    if device == "CPU":
                        cpu_seconds = total
                    if device == "GPU":
                        gpu_seconds = total
            ratio = ""
            if cpu_seconds is not None and gpu_seconds and gpu_seconds > 0:
                ratio = f"{cpu_seconds / gpu_seconds:.4f}"
            rows.append(
                {
                    "stage": "05_model_training",
                    "cpu_seconds": "" if cpu_seconds is None else f"{cpu_seconds:.4f}",
                    "gpu_seconds": "" if gpu_seconds is None else f"{gpu_seconds:.4f}",
                    "speedup_ratio": ratio,
                }
            )

    # Ensure file exists even when no benchmark data is available.
    if not rows:
        rows.append(
            {
                "stage": "05_model_training",
                "cpu_seconds": "",
                "gpu_seconds": "",
                "speedup_ratio": "",
            }
        )

    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["stage", "cpu_seconds", "gpu_seconds", "speedup_ratio"])
        w.writeheader()
        for r in rows:
            w.writerow(r)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
