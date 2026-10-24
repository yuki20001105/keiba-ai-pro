"""Isolated, scheduler-owned quality replay; never trains or changes Track A."""
from pathlib import Path
import argparse
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]
from market_quality.lineage import audit_lineage, prepare_event_history, sha256


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    quality = ROOT / "reports/generated/data-quality/market-h04-clean-v1"
    plan = Path(args.plan)
    if sha256(plan) != args.plan_sha256 or sha256(quality / "audit_plan.json") != args.plan_sha256:
        raise ValueError("Quality plan changed")
    config = json.loads(plan.read_text(encoding="utf-8"))
    digest = config["snapshot_sha256"]
    snapshot = ROOT / f"reports/generated/model-experiments/20260919-jra-safe-v1/snapshots/{digest}.db"
    start = time.monotonic()
    def progress(stage):
        if sha256(plan) != args.plan_sha256:
            raise ValueError("Quality plan changed during replay")
        print(f"{time.monotonic() - start:.1f}s {stage}", flush=True)
    history, history_audit = prepare_event_history(snapshot, progress, expected_snapshot_sha256=digest)
    write_new(quality / "history_source_audit.json", history_audit)
    receipt = audit_lineage(ROOT / "reports/generated/model-experiments/market-edge-v1",
        ROOT / "reports/generated/model-experiments/20260919-feature-search-v1",
        reinfer=True, event_history=history, history_audit=history_audit, checkpoint=progress)
    receipt["elapsed_seconds"] = time.monotonic() - start
    receipt["audit_plan_sha256"] = args.plan_sha256
    write_new(quality / "lineage.json", receipt)
    write_new(Path(args.result), {"status": "completed", "lineage_path": str(quality / "lineage.json"),
        "lineage_sha256": sha256(quality / "lineage.json"),
        "lineage_and_event_time_ready": receipt["lineage_and_event_time_ready"],
        "elapsed_seconds": receipt["elapsed_seconds"], "training_performed": False})


if __name__ == "__main__":
    main()
