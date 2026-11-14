"""Read-only frozen-campaign quality and annual allocation research."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]

import pandas as pd

from betting.research_allocation import (
    allocation_policy, allocate_races, annual_flat_equivalents, quality_summary,
    summarize_allocations,
)
from experiments import cli, goal_loop
from experiments.common import file_sha256, read_json
from experiments.goal_evidence import checked_artifact, verify_trial
from experiments.search import freeze_json


def inventory(path: Path) -> dict:
    with sqlite3.connect(path.resolve(strict=True).as_uri() + "?mode=ro", uri=True) as db:
        return {
            "observed_at": datetime.now().astimezone().isoformat(),
            "races": db.execute("SELECT COUNT(*) FROM races_ultimate").fetchone()[0],
            "runner_rows": db.execute("SELECT COUNT(*) FROM race_results_ultimate").fetchone()[0],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--annual-budget-yen", type=int, default=10_000_000)
    parser.add_argument("--planned-races", type=int, default=3600)
    args = parser.parse_args()
    campaign = args.campaign.resolve(strict=True)
    output = args.output.resolve()
    if output.is_relative_to(campaign) or campaign.is_relative_to(output) or output.exists():
        raise ValueError("Use a new analysis directory outside the immutable campaign")
    policy = allocation_policy(args.annual_budget_yen, args.planned_races)
    task = goal_loop._approved_task(campaign)
    verification = read_json(campaign / "verification.json")
    if not verification["verified"]:
        raise ValueError("A verified completed campaign is required")
    state = cli.store_for(campaign).snapshot(campaign.name)
    if state["status"] != "completed":
        raise ValueError("Campaign workers must be settled before analysis")
    selected = verification["selected_trial"]
    row = next(row for row in state["trials"] if row["trial_id"] == selected)
    if row["result"]["target"] != "rank":
        raise ValueError("This diagnostic allocation experiment requires a ranking candidate")
    output.mkdir(parents=True)
    freeze_json(output / "policy.json", policy)
    _, verified_records = verify_trial(campaign, row, task)
    path = checked_artifact(campaign, row["result"]["artifacts"]["predictions"])
    predictions = pd.read_parquet(path)
    allocations, records = allocate_races(predictions, policy)
    if not records["race_id"].equals(
        verified_records.sort_values(["race_date", "race_id"], kind="stable")["race_id"].reset_index(drop=True)
    ):
        raise ValueError("Allocation cohort must match independently verified races")
    manifest = read_json(campaign / "dataset" / "dataset_manifest.json")
    diagnostics = read_json(campaign / "diagnostics.json")
    frame = pd.read_parquet(campaign / "dataset" / "dataset.parquet")
    report = {
        "schema": "goal-allocation-quality-research-v1",
        "campaign": str(campaign), "selected_trial": selected,
        "campaign_task_sha256": verification["task_sha256"],
        "dataset_sha256": manifest["dataset_sha256"],
        "prediction_sha256": row["result"]["artifacts"]["predictions"]["sha256"],
        "analysis_source_sha256": {
            str(path.relative_to(ROOT)): file_sha256(path)
            for path in (Path(__file__), ROOT / "python-api" / "betting" / "research_allocation.py")
        },
        "policy": policy,
        "annual_flat_10m_equivalents": annual_flat_equivalents(records, args.annual_budget_yen),
        "annual_allocations": summarize_allocations(allocations, policy),
        "data_quality": quality_summary(frame, manifest, diagnostics, task["config"]["development_years"]),
        "frozen_source_inventory": inventory(Path(task["snapshot"]["path"])),
        "live_inventory_read_only": inventory(ROOT / "keiba" / "data" / "keiba_ultimate.db"),
        "caveats": [
            "Annual budget is turnover, not initial bankroll or a target that must be spent.",
            "Amounts use fixed schedules and prior spending only; no future annual normalization.",
            "Race-date then race-id order is not verified intraday chronology.",
            "Drawdown uses daily settled P&L; no intraday financing or available cash proof.",
            "Score-gap tiers are hypotheses, not calibrated confidence, expected value or Kelly.",
            "No profit-based rule selection or production purchases; all production amounts are zero.",
            "No taxes, fees, impact, reinvestment, prospective validation or production acceptance.",
        ],
    }
    with (output / "race_allocations.csv").open("x", encoding="utf-8", newline="") as stream:
        allocations.to_csv(stream, index=False)
    freeze_json(output / "analysis.json", report)
    print(json.dumps({
        "output": str(output), "flat_equivalents": report["annual_flat_10m_equivalents"],
        "allocations": report["annual_allocations"],
    }, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
