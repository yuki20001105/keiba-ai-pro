"""CPU-only market-segments worker; run through the protected research scheduler."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading

for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[name] = "2"
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("data", "feature-spec", "plan", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--expected-feature-spec-sha256", required=True)
    parser.add_argument("--group", required=True)
    parser.add_argument("--model", required=True, choices=["market", "logistic", "lightgbm"])
    parser.add_argument("--fold", required=True, choices=["development", "validation"])
    parser.add_argument("--device", choices=["cpu"], default="cpu")
    args = parser.parse_args()
    experiment = args.plan.resolve().parent
    allowed = ROOT / "reports" / "generated" / "model-experiments"
    if (not experiment.is_relative_to(allowed) or experiment == allowed or
            not args.output.resolve().is_relative_to(experiment) or args.output.resolve() == experiment):
        parser.error("Output must be an isolated child of the new experiment plan directory")
    import psutil
    process = psutil.Process()
    if os.name == "nt":
        process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    stop = threading.Event()
    resources = {"peak_ram_mb": 0., "samples": 0, "rss_sampling_seconds": .1, "gpu_used": False}

    def sample():
        while not stop.is_set():
            try:
                resources["peak_ram_mb"] = max(resources["peak_ram_mb"], process.memory_info().rss / 2**20)
                resources["samples"] += 1
            except psutil.Error:
                pass
            stop.wait(.1)

    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    try:
        from market_segments.modeling import train_predict
        from run_market_edge_segments_analysis import verify_plan
        verify_plan(args.plan, args.expected_plan_sha256)
        result = train_predict(args.data, args.feature_spec, group=args.group, model=args.model,
                               fold=args.fold, output=args.output, plan_path=args.plan,
                               expected_plan_sha256=args.expected_plan_sha256,
                               expected_feature_spec_sha256=args.expected_feature_spec_sha256)
        verify_plan(args.plan, args.expected_plan_sha256)
    finally:
        stop.set()
        thread.join(timeout=5)
    # Resource telemetry is a separate immutable sidecar; do not rewrite hashed results.
    with (args.output / "resources.json").open("x", encoding="utf-8") as stream:
        json.dump(resources, stream, indent=2, allow_nan=False)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
