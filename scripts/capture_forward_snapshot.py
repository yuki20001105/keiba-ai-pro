"""One-shot local forward capture. No daemon, login mutation or live purchase.

Offline capture imports an already witnessed payload/receipts and is NOT
policy-ready without trusted builder/scorer callbacks. Use the Python API to
connect those adapters. ``observe --allow-network`` explicitly performs ONE
current HTTP observation, using the existing shared request pacer.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python-api"))
from prospective.capture import (CaptureError, capture_snapshot, freeze_capture_protocol,
                                 load_capture_protocol, observe_http, record_capture_failure)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, required=True)
    commands = parser.add_subparsers(dest="action", required=True)
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--protocol", type=Path, required=True)
    for key in ("model", "calibrator", "features", "rule"):
        freeze.add_argument("--" + key, type=Path, required=True)
    capture = commands.add_parser("capture")
    capture.add_argument("--payload", type=Path, required=True)
    observe = commands.add_parser("observe")
    observe.add_argument("--race-id", required=True)
    observe.add_argument("--post-time", required=True, help="Timezone-aware ISO timestamp")
    observe.add_argument("--kind", choices=("odds", "race_card"), required=True)
    observe.add_argument("--allow-network", action="store_true", help="Perform one fresh paced GET")
    failure = commands.add_parser("skip")
    failure.add_argument("--race-id", required=True)
    failure.add_argument("--reason", required=True)
    commands.add_parser("status")
    args = parser.parse_args(argv)
    failure_race = getattr(args, "race_id", None)
    try:
        if args.action == "freeze":
            result = freeze_capture_protocol(args.store, json.loads(args.protocol.read_text(encoding="utf-8")),
                                             {key: getattr(args, key) for key in ("model", "calibrator", "features", "rule")})
        elif args.action == "observe":
            if not args.allow_network:
                parser.error("observe requires explicit --allow-network; nothing fetched")
            result = asyncio.run(observe_http(args.store, args.race_id, args.post_time, args.kind))
        elif args.action == "capture":
            payload = json.loads(args.payload.read_text(encoding="utf-8"))
            failure_race = payload.get("race_id")
            result = capture_snapshot(args.store, payload)
        elif args.action == "skip":
            result = record_capture_failure(args.store, args.race_id, args.reason)
        else:
            protocol = load_capture_protocol(args.store)
            result = {"protocol_sha256": protocol["protocol_sha256"], "paper_only": True,
                      "capture_records": len(list((args.store / "captures").glob("*.json"))),
                      "failure_records": len(list((args.store / "failures").glob("*.json"))),
                      "scheduler_running": False, "note": "one-shot utility; no scheduler is started by this command"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (CaptureError, FileNotFoundError, json.JSONDecodeError) as exc:
        if failure_race and args.action in ("observe", "capture"):
            try:
                record_capture_failure(args.store, failure_race, str(exc)[:300])
            except (CaptureError, FileNotFoundError):
                pass  # No valid frozen store: report rejection, never fabricate one.
        print(json.dumps({"status": "rejected", "error": str(exc), "automatic_purchase": False}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
