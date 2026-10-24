"""Compact read-only operator status, including future scheduled races."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT / "reports/generated/forward-runtime/h04-live-20260920-v2")
    parser.add_argument("--save", type=Path)
    parser.add_argument("--freeze-analysis", action="store_true", help="Freeze the read-only evaluation definition before the study starts")
    args = parser.parse_args()
    from forward_runtime.ledger import RuntimeLedger
    from forward_runtime.provider import aware, JST
    from forward_runtime.schedule import window_for, stamp
    from forward_runtime.service import exclusive_json, read_json
    from forward_reports.evaluation import evaluation_definition, evaluate_forward
    ledger = RuntimeLedger(args.root / "ledger.sqlite3")
    state, current = ledger.compact_status(), datetime.now(timezone.utc)
    owner = state["owner"]
    upcoming = next((r for r in state["races"] if r["status"] == "queued"), None)
    if upcoming:
        start, cutoff = window_for(upcoming["post_time"])
        upcoming = {**upcoming, "capture_start_jst": start.astimezone(JST).isoformat(), "cutoff_jst": cutoff.astimezone(JST).isoformat()}
    value = {"observed_at": stamp(current), "paper_only": True,
             "collector_alive": bool(owner["owner_id"] and owner["expires_at"] and aware(owner["expires_at"]) > current),
             "owner": owner, "planned_days": len(state["days"]),
             "all_day_status_counts": dict(Counter(d["status"] for d in state["days"])),
             "registered_flat_races": len(state["races"]), "all_race_status_counts": dict(Counter(r["status"] for r in state["races"])),
             "next": upcoming, "event_chain": ledger.verify_events(), "elapsed_period_metrics": ledger.aggregate()}
    analysis_path = args.root / "analysis-definition.json"
    if args.freeze_analysis:
        protocol = read_json(args.root / "protocol.json")
        if current >= aware(protocol["protocol"]["start_at"]) or any(r["has_snapshot"] for r in state["races"]):
            raise ValueError("freeze-analysis-before-study-and-observations")
        exclusive_json(analysis_path, {"frozen_at": stamp(current), "protocol_sha256": protocol["protocol_sha256"],
                                      "definition": evaluation_definition(), "outcomes_observed": 0})
    if analysis_path.exists():
        analysis = read_json(analysis_path)
        snapshot = ledger.snapshot()
        if analysis["definition"] != evaluation_definition() or analysis["protocol_sha256"] != snapshot["contract"]["protocol_sha256"]:
            raise ValueError("frozen-analysis-definition-changed")
        value["prediction_and_betting_evaluation"] = evaluate_forward(snapshot, as_of=current)
    if args.save:
        exclusive_json(args.save, value)
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
