"""Start/status/stop the isolated forward paper collector. Never real betting."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("init", "run", "status", "stop", "discover"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--parent-store", type=Path)
    parser.add_argument("--clean-dir", type=Path)
    parser.add_argument("--daily-dir", type=Path)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--resume-stopped", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--all-calendar-months", action="store_true")
    parser.add_argument("--day", default="20260920")
    args = parser.parse_args()
    from forward_runtime.service import initialize, Runtime, exclusive_json, now
    from forward_runtime.ledger import RuntimeLedger
    if args.command == "init":
        if not all((args.parent_store, args.clean_dir, args.daily_dir)):
            parser.error("init requires parent-store, clean-dir, daily-dir")
        result = initialize(args.root, args.parent_store, args.clean_dir, args.daily_dir)
        print(json.dumps({"protocol_sha256": result["protocol_sha256"], "paper_only": True}))
    elif args.command in ("status", "stop"):
        ledger = RuntimeLedger(args.root / "ledger.sqlite3")
        if args.command == "stop":
            result = ledger.request_stop()
        else:
            result = {"summary": ledger.aggregate(), "events": ledger.verify_events(), "owner": ledger.snapshot()["owner"]}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if not args.allow_network:
            parser.error("run/discover requires explicit --allow-network")
        runtime = Runtime(args.root)
        if args.command == "run":
            asyncio.run(runtime.run(once=args.once, resume_stopped=args.resume_stopped))
        else:
            async def discover():
                import uuid
                from forward_runtime.schedule import study_days, stamp
                runtime.lease = runtime.ledger.acquire_owner("discovery-" + uuid.uuid4().hex)
                heart = asyncio.create_task(runtime.heartbeat_loop())
                try:
                    if args.all_calendar_months:
                        for year, month in sorted({(int(d[:4]), int(d[4:6])) for d in study_days()}):
                            days = await runtime.load_calendar(year, month)
                            for day, venues in days.items():
                                if day not in study_days():
                                    continue
                                receipt = runtime.calendar[day]["receipt"]
                                runtime.ledger.register_day(day, {"status": "unknown" if venues else "not_held",
                                    "calendar_verified": True, "active_venues": venues, "venue_race_counts": {},
                                    "expected_race_ids": [], "excluded_races": [], "calendar_receipt": receipt,
                                    "source_url": receipt["url"], "source_sha256": receipt["body_sha256"],
                                    "observed_at": receipt["received_at"], "received_at": receipt["received_at"],
                                    "reason": "race-IDs/post-times-pending-publication" if venues else None}, [], runtime.lease)
                    result = await runtime.discover(args.day)
                    print(json.dumps(result, ensure_ascii=False))
                finally:
                    heart.cancel()
                    try:
                        await heart
                    except asyncio.CancelledError:
                        pass
                    runtime.ledger.release_owner(runtime.lease)
            asyncio.run(discover())


if __name__ == "__main__":
    main()
