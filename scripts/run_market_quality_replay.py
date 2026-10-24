"""Run historical quality replay with the existing immutable resource protector."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]
from market_edge.compute import JobSpec, ProtectionLimits, run_jobs
from market_quality.lineage import sha256


def main():
    quality = ROOT / "reports/generated/data-quality/market-h04-clean-v1"
    output = ROOT / "reports/generated/model-experiments/market-quality-audit-v1"
    output.mkdir(parents=True, exist_ok=True)
    plan = output / "audit_plan.json"
    source = quality / "audit_plan.json"
    if not plan.exists():
        with plan.open("xb") as stream:
            stream.write(source.read_bytes())
    digest = sha256(plan)
    if sha256(source) != digest:
        raise ValueError("Frozen plan differs")
    result = output / "lineage-job-result.json"
    job = JobSpec("lineage-replay", "quality-audit-no-training", "cpu", str(source), "2013-2022",
        [str(ROOT / "python-api/.venv/Scripts/python.exe"), str(ROOT / "scripts/run_market_edge_quality_worker.py"),
         "--plan", str(plan), "--plan-sha256", digest, "--result", str(result)],
        str(ROOT), str(result), cpu_threads=2, memory_mb=5500, gpu_memory_mb=0)
    rows = run_jobs(output, [job], ROOT / "reports/generated/forward-runtime/h04-live-20260920-v2",
                    plan, digest, limits=ProtectionLimits(gpu_capacity=0), max_wait_seconds=300)
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
