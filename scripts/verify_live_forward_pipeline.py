"""Technical pipeline benchmark, explicitly NOT a prospective observation.

Uses actual fresh race card/odds and frozen H04; never touches a capture ledger,
changes the fixed protocol, places orders or claims a T-5 capture after the fact.
"""
import argparse
import asyncio
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-store", required=True, type=Path)
    parser.add_argument("--daily-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--race-id", required=True)
    parser.add_argument("--post-time", required=True)
    parser.add_argument("--allow-network", action="store_true")
    args = parser.parse_args()
    if not args.allow_network:
        parser.error("explicit --allow-network required")
    from forward_runtime.service import Runtime, read_json, file_hash, now, exclusive_json, source_manifest
    from forward_runtime import provider
    from forward_runtime.schedule import sha256
    import joblib
    prior = read_json(args.parent_store / "protocol.json")
    if sha256(prior["protocol"]) != prior["protocol_sha256"]:
        raise ValueError("parent-integrity")
    for sha in prior["protocol"]["artifact_hashes"].values():
        if file_hash(args.parent_store / "objects" / sha) != sha:
            raise ValueError("artifact-integrity")
    daily = read_json(args.daily_dir / "preparation_manifest.json")
    for filename, key in (("history_state.json", "state_sha256"), ("prior_bloodlines.json", "bloodline_sha256")):
        if file_hash(args.daily_dir / filename) != daily[key]:
            raise ValueError("daily-history-integrity")
    race = {"race_id": args.race_id, "post_time": args.post_time,
            "race_date": provider.aware(args.post_time).astimezone(provider.JST).strftime("%Y%m%d")}
    if daily["target_date"] != race["race_date"]:
        raise ValueError("daily-date-mismatch")
    # Construct only the pure inference consumer. Deliberately no ledger,
    # ownership lease or capture protocol; capture() cannot be used here.
    inference = object.__new__(Runtime)
    inference.root, inference.clock = args.parent_store, now
    inference.protocol = dict(prior["protocol"])
    model_body = (args.parent_store / "objects" / prior["protocol"]["artifact_hashes"]["model"]).read_bytes()
    inference.protocol["feature_categories"] = joblib.load(BytesIO(model_body))["encoder"].categories
    inference.daily = daily
    loaded = time.perf_counter()
    inference.state = read_json(args.daily_dir / "history_state.json")
    inference.blood = read_json(args.daily_dir / "prior_bloodlines.json")
    state_load = time.perf_counter() - loaded
    # Transport evidence is stored separately under the technical report root.
    transport = object.__new__(Runtime)
    transport.root, transport.clock, transport.transport = args.output, now, None
    transport.envelope = {"protocol_sha256": prior["protocol_sha256"]}
    start = time.perf_counter()
    card_receipt, card = await transport.fetch(f"https://race.netkeiba.com/race/shutuba.html?race_id={args.race_id}")
    odds_receipt, odds_body = await transport.fetch(f"https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={args.race_id}&type=1&action=update")
    network = time.perf_counter() - start
    odds = provider.parse_odds(odds_body, args.race_id, received_at=odds_receipt["received_at"])
    inferred = inference.infer(card, race)
    total = time.perf_counter() - start
    numbers = {r["horse_number"] for r in inferred["runners"]}
    if numbers != set(odds["odds"]):
        raise ValueError("real-card-odds-cohort-mismatch")
    repeats = []
    for _ in range(2):
        repeats.append(inference.infer(card, race)["feature_inference_seconds"])
    report = {"mode": "technical-live-outside-fixed-window", "recorded_at": now().isoformat(),
              "race_id": args.race_id, "post_time": args.post_time, "runner_count": len(numbers),
              "parent_protocol_sha256": prior["protocol_sha256"], "artifact_hashes": prior["protocol"]["artifact_hashes"],
              "source_manifest": source_manifest(), "state_sha256": daily["state_sha256"],
              "history_max_source_date": daily["history_max_source_date"], "state_load_seconds_outside_window": state_load,
              "network_seconds": network, "total_two_fresh_HTTP_plus_feature_and_inference_seconds": total,
              "feature_inference_seconds_first": inferred["feature_inference_seconds"], "feature_inference_repeat_seconds": repeats,
              "probability_sum": sum(r["win_probability"] for r in inferred["runners"]),
              "score_not_probability": True, "input_quality": inferred["input_quality"],
              "card_receipt": card_receipt, "odds_receipt": odds_receipt, "provider_time": odds["provider_time"],
              "fixed_window_budget_seconds": 30, "observed_runtime_fits_budget": total <= 30,
              "actual_T5_capture_demonstrated": False, "prospective_denominator_credit": 0,
              "actual_bets": 0, "active_model_changed": False,
              "limitation": "Real provider parsing and latency proof only. Fresh T-5 provenance must be demonstrated by the resident collector at a future race."}
    exclusive_json(args.output / "pipeline-validation.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
