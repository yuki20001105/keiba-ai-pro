"""One current race, immutable card/quote/raw46 research capture.

The independent H04 collector, active model and registry are never touched.
`capture --allow-network` explicitly makes fresh paced requests; no daemon or
task scheduler is installed. `replay` uses sealed past sources without earning
live provenance, and `inspect` verifies every stored artifact's SHA256.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for _module_root in (ROOT / "python-api", ROOT / "keiba", ROOT / "scripts"):
    if str(_module_root) not in sys.path:
        sys.path.insert(0, str(_module_root))
from research_capture.prestart import (CaptureError, CapturePolicy, DEFAULT_STORE, JST,
                                      acquire_current_card, attach_direct_features, make_bundle, persist_bundle,
                                      prepared_raw46_features, prepare_history_state,
                                      read_bundle, record_failure, replay_sealed_sources)


def parse_post(value):
    if value == "auto":
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("timezone missing")
        return result.astimezone(timezone.utc)
    except ValueError as error:
        raise argparse.ArgumentTypeError("post time requires timezone-aware ISO timestamp") from error


async def execute(args):
    if args.action == "prepare":
        return prepare_history_state(args.db_path, args.target_date, args.store)
    if args.action == "inspect":
        bundle = read_bundle(args.directory)
        return {"status": "verified_objects", "race_id": bundle["race_id"],
                "captured_at": bundle["captured_at"], "capture_validation": bundle["capture_validation"],
                "feature_count": len(bundle.get("features46", {}).get("columns", [])),
                "research_only": True, "eligible_for_ev": False, "purchase_approved": False}
    policy = CapturePolicy(args.lead_seconds, args.tolerance_seconds,
                           args.max_source_age_seconds, args.max_card_quote_skew_seconds)
    existing_features = None
    parent_capture_sha = None
    if args.action == "rebuild":
        original = read_bundle(args.source_directory)
        snapshot = original["card"]
        args.race_id = original["race_id"]
        args.post_time = parse_post(original["post_time"])
        sources = [{**row, "body": (args.source_directory / row["body_file"]).read_bytes()}
                   for row in original["source_receipts"]]
        parent_capture_sha = json.loads((args.source_directory / "artifact_manifest.json").read_bytes())["capture_sha256"]
        canonical = args.source_directory / "sources/canonical_inputs.json"
        history = args.source_directory / "sources/history.parquet"
        if canonical.exists() and history.exists():
            canonical_payload = json.loads(canonical.read_bytes())
            if canonical_payload.get("history_scope") == "ALL complete eligible JRA flat rows since 2013; strictly prior target day":
                def existing_features(_snapshot, _db):
                    return {**deepcopy(original["features46"]), "canonical_source_snapshot": canonical_payload,
                            "_canonical_history_parquet": history.read_bytes()}, deepcopy(original["history_audit"])
        mode = "sealed_replay"
    elif args.action == "replay":
        snapshot, sources = await replay_sealed_sources(args.card_receipt, args.odds_receipt,
                                                        args.objects, args.race_id, args.race_date)
        mode = "sealed_replay"
    else:
        if not args.allow_network:
            raise CaptureError("explicit_allow_network_required_nothing_fetched")
        # Fail before ANY HTTP access when the frozen acquisition window is not
        # open. An invocation after post can never scrape result retroactively.
        now = datetime.now(timezone.utc)
        if args.post_time is not None:
            opens, deadline = policy.window(args.post_time)
        elif not args.outside_window_probe:
            raise CaptureError("auto_post_requires_unverified_probe_nothing_fetched")
        if (args.post_time is not None and not opens <= now <= deadline
                and not args.outside_window_probe):
            raise CaptureError("fixed_capture_window_not_open_nothing_fetched")
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90),
                                         headers={"User-Agent": "Mozilla/5.0"}) as session:
            snapshot, sources = await acquire_current_card(session, args.race_id, args.race_date,
                                                           allow_network=True)
        mode = "live"
    post_time = args.post_time
    if post_time is None:
        info = snapshot.get("race_info") or {}
        try:
            post_time = datetime.strptime(str(info.get("date")) + " " + str(info.get("post_time")),
                                          "%Y%m%d %H:%M").replace(tzinfo=JST)
        except ValueError as error:
            raise CaptureError("source_post_time_unverified") from error
    if args.prepared_state:
        def existing_features(card, _unused_db):
            return prepared_raw46_features(card, args.prepared_state)
    bundle, files = make_bundle(snapshot=snapshot, sources=sources, race_id=args.race_id,
                               post_time=post_time, db_path=args.db_path, mode=mode, policy=policy,
                               feature_builder=existing_features)
    if parent_capture_sha:
        bundle["parent_capture_sha256"] = parent_capture_sha
    if args.with_extended:
        bundle = attach_direct_features(bundle, files)
    if args.action == "capture" and args.outside_window_probe:
        # A diagnostic probe does not substitute for the fixed-time study.
        bundle["capture_validation"]["point_in_time_verified"] = False
        bundle["capture_validation"]["reason_codes"].append("diagnostic_probe_not_fixed_time_study_capture")
    return persist_bundle(args.store, bundle, files)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    inspect = commands.add_parser("inspect", help="Verify an already saved research bundle")
    inspect.add_argument("--directory", type=Path, required=True)
    prepare = commands.add_parser("prepare", help="Precompute whole-world prior-day history, zero network calls")
    prepare.add_argument("--target-date", required=True, help="YYYYMMDD; no same-day/future history")
    prepare.add_argument("--db-path", type=Path, default=ROOT / "keiba/data/keiba_ultimate.db")
    prepare.add_argument("--store", type=Path, default=DEFAULT_STORE)
    for name in ("capture", "replay", "rebuild"):
        command = commands.add_parser(name)
        command.add_argument("--race-id", required=name != "rebuild")
        command.add_argument("--race-date", required=name != "rebuild", help="YYYYMMDD; the page must independently confirm it")
        command.add_argument("--post-time", type=parse_post, required=name != "rebuild",
                             help="Timezone-aware ISO, or auto for an unverified outside-window probe")
        command.add_argument("--db-path", type=Path, default=ROOT / "keiba/data/keiba_ultimate.db")
        command.add_argument("--store", type=Path, default=DEFAULT_STORE)
        command.add_argument("--lead-seconds", type=int, default=300)
        command.add_argument("--tolerance-seconds", type=int, default=60)
        command.add_argument("--max-source-age-seconds", type=int, default=300)
        command.add_argument("--max-card-quote-skew-seconds", type=int, default=60)
        command.add_argument("--with-extended", action="store_true",
                             help="Attach fixed 88 DB features plus4 quote features with train/current parity proof")
        command.add_argument("--prepared-state", type=Path,
                             help="Verified matching-target-day immutable history state; no SQLite scan at cutoff")
        if name == "capture":
            command.add_argument("--allow-network", action="store_true")
            command.add_argument("--outside-window-probe", action="store_true",
                                 help="Manual diagnostic only; never verified fixed-time study capture")
        elif name == "replay":
            command.add_argument("--card-receipt", type=Path, required=True)
            command.add_argument("--odds-receipt", type=Path, required=True)
            command.add_argument("--objects", type=Path, required=True)
        else:
            command.add_argument("--source-directory", type=Path, required=True,
                                 help="Read verified earlier bundle; reconstruct without another HTTP request")
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(execute(args))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        # Persist reason code only; no URL headers, cookie or exception with
        # possible credential-bearing text enters the output/store.
        reason = str(error) if isinstance(error, CaptureError) else "capture_failed_" + type(error).__name__
        result = {"status": "rejected", "reason": reason,
                  "research_only": True, "eligible_for_ev": False, "purchase_approved": False}
        if args.action not in {"inspect", "prepare"}:
            try:
                result["failure_receipt"] = record_failure(args.store, args.race_id, reason,
                                                           post_time=args.post_time)
            except (CaptureError, OSError):
                result["failure_receipt_unavailable"] = True
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
