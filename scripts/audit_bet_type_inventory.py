"""Audit local seven-bet-type storage without HTTP, fitting, or DB writes."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "python-api"))
for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[name] = "1"
import pandas as pd
from bet_types.inventory import inspect_database, inspect_cache, summarize_coverage, sha256


def raw_inventory(repo):
    """Bounded, known storage locations; no decoding archives or future values."""
    paths = ("data/html", "keiba/data/html", "python-api/data/html", "keiba/data/live-validation-inputs",
             "keiba/data/db_export_for_chat", "keiba/data/db_quality_audit")
    output = []
    for relative in paths:
        root = repo / relative
        item = {"path": str(root), "exists": root.exists(), "files": [], "payloads_read": 0}
        if root.is_dir():
            for child in sorted(root.iterdir()):
                item["files"].append({"name": child.name, "directory": child.is_dir(),
                                      "size": child.stat().st_size if child.is_file() else None})
        output.append(item)
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("output must be absent or empty; audit receipts are never overwritten")
    frame = pd.read_parquet(args.cohort, columns=["race_id", "race_date", "horse_number", "expected_runner_count", "odds"],
                            filters=[("race_date", ">=", "20210101"), ("race_date", "<=", "20221231")])
    coverage, evidence = inspect_database(args.snapshot, frame)
    cache = inspect_cache(REPO / "keiba/data/fetch_cache.db", REPO / "keiba/data/parsed_fetch_cache.db", frame.race_id.astype(str))
    if cache["cohort_odds_url_candidates"]:
        raise ValueError("unresolved scoped odds cache candidates: do not assert absence")
    output.mkdir(parents=True, exist_ok=True)
    inventory = summarize_coverage(coverage)
    inventory.to_csv(output / "bet_type_data_inventory.csv", index=False, encoding="utf-8-sig", mode="x")
    coverage.to_csv(output / "bet_type_coverage.csv", index=False, encoding="utf-8-sig", mode="x")
    report = {"schema_version": "bet-type-storage-inventory-v1", "created_at": datetime.now(timezone.utc).isoformat(),
              "cohort": {"path": str(args.cohort.resolve()), "sha256": sha256(args.cohort), "rows": len(frame),
                         "races": int(frame.race_id.nunique()), "date_min": str(frame.race_date.min()), "date_max": str(frame.race_date.max())},
              "source": evidence, "cache": cache, "raw_storage_metadata": raw_inventory(REPO),
              "snapshot_content_hash_status": "expected hash inherited from frozen snapshot filename; full 3GB not rehashed in this bounded audit",
              "snapshot_expected_sha256": args.snapshot.stem,
              "timing_classes": {"A": "source and receipt proven pre-race", "B": "actual receipt proven pre-race, provider time unknown",
                                 "C": "result-page final/historical, reacquisition in 2026 is not T-5", "D": "observed odds with unknown timing",
                                 "NO_ODDS": "no observed quotes; not a timing-D quote"},
              "limitations": ["Payout rows prove recorded positive payouts and runner membership, not formal complete settlement for ties/refunds.",
                              "No full non-win odds found in the indexed DB schemas, shared HTTP cache, parsed cache, or configured raw HTML stores.",
                              "DB export samples and archives were inventoried by filename only, not decoded or treated as independent pre-race evidence; arbitrary unconfigured files are outside this bounded scope.",
                              "In-memory realtime odds are not historical archival evidence and were not requested from a live process.",
                              "Cache inventory is one read transaction of a live cache, not a claim about external or unconfigured storage.",
                              "No 2023+ outcome values are selected by this script; all-source metadata date/count queries are explicitly separated.",
                              "No model metrics, strategy returns, EV estimates, scraping, or model changes are performed."],
              "code_evidence": [{"path": "python-api/scraping/quality.py", "lines": "602-616", "meaning": "observations store individual win odds only; result lifecycle has no market payload"},
                                {"path": "python-api/routers/realtime_odds.py", "lines": "30-47", "meaning": "live odds cache is bounded in-memory and not historical proof"},
                                {"path": "keiba/config.yaml", "lines": "25", "meaning": "configured raw html location ./data/html"}],
              "safety": {"production_db_writes": False, "network_requests": 0, "future_outcome_analysis": False,
                         "nonwin_ev_roi_permitted": False, "prospective_odds_races": 0}}
    with (output / "odds_source_inventory.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    manifest = {"created_at": report["created_at"], "files": {p.name: {"sha256": sha256(p), "size": p.stat().st_size} for p in sorted(output.iterdir()) if p.is_file()},
                "source": {str(p.relative_to(REPO)): sha256(p) for p in (Path(__file__), REPO / "python-api/bet_types/inventory.py")}}
    with (output / "artifact_manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"output": str(output), "rows": len(frame), "races": int(frame.race_id.nunique()), "cache_metadata_rows": cache["metadata_rows"],
                      "inventory": inventory[["bet_type", "payout_races", "payout_rows", "all_combination_odds_races", "odds_timing_class"]].to_dict("records")}, ensure_ascii=True))


if __name__ == "__main__":
    main()
