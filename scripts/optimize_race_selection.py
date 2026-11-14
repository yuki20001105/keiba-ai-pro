"""Optimize a research-only race selector from a frozen OOT experiment."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python-api"))

from betting.research_race_selection import load_frozen_oof, optimize_race_selection
from experiments.common import active_pointer_hash


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.experiment_dir.resolve()):
        parser.error("Output must be separate from the frozen source experiment")
    pointer = active_pointer_hash()
    frame, provenance = load_frozen_oof(args.experiment_dir)
    print(json.dumps({
        "stage": "verified_input", "races": int(frame["race_id"].nunique()),
        "runner_rows": len(frame), "base_model_id": provenance["model_id"],
    }), flush=True)
    report = optimize_race_selection(
        frame, args.output, model_id=provenance["model_id"], prediction_provenance=provenance,
    )
    print(json.dumps({
        "report": str(args.output / "race_selection_report.json"),
        "model": report["probability_model"],
        "selection": {
            profile: {"rule": item["rule"], "admissible": item["admissible"],
                      "priority_supported": item["priority_supported"]}
            for profile, item in report["selection"].items()
        },
        "evaluation": {
            year: {
                "baseline_return_rate": item["baselines"]["base_top1"]["gross_return_rate_percent"],
                **{profile: {
                    "bets": metrics["selected_policy"]["bet_count"],
                    "return_rate": metrics["selected_policy"]["gross_return_rate_percent"],
                    "reference_return_rate": metrics["reference_rule"]["gross_return_rate_percent"],
                } for profile, metrics in item["profiles"].items()},
            } for year, item in report["evaluation"].items()
        },
        "active_pointer_unchanged": pointer == active_pointer_hash(),
        "automatic_purchase": False,
    }, ensure_ascii=True, indent=2), flush=True)


if __name__ == "__main__":
    main()
