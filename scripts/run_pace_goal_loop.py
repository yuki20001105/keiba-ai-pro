"""Isolated extension of the local prepare/approve/run/verify goal protocol."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "python-api"), str(REPO / "keiba"), str(REPO / "scripts")]
from training import pace_comparison as comparison


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "approve", "run", "verify", "status"])
    parser.add_argument("--root", type=Path, required=True)
    for name in ("source", "baseline-report", "kit", "previous"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--actor")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.action == "prepare":
        if any(value is None for value in (args.source, args.baseline_report, args.kit, args.previous)):
            parser.error("prepare requires --source, --baseline-report, --kit and --previous")
        result = comparison.prepare(root, args.source, args.baseline_report, args.kit, args.previous)
    elif args.action == "approve":
        result = comparison.approve(root, args.actor)
    elif args.action == "run":
        if args.worker:
            result = comparison.run(root)
        else:
            command = [sys.executable, "-X", "utf8", str(Path(__file__).resolve()),
                       "run", "--root", str(root), "--worker"]
            try:
                completed = subprocess.run(command, timeout=comparison.PROTOCOL["maximum_seconds"])
            except subprocess.TimeoutExpired:
                comparison.write(root / "run-state.json", {"status": "budget_exhausted"})
                comparison._event(root, "budget_exhausted", {"maximum_seconds": comparison.PROTOCOL["maximum_seconds"]})
                raise SystemExit(124)
            raise SystemExit(completed.returncode)
    elif args.action == "verify":
        from training.pace_verification import verify
        result = verify(root)
    else:
        result = comparison.read(root / "run-state.json") if (root / "run-state.json").exists() else {
            "status": "prepared" if (root / "task-ledger.json").exists() else "not_prepared"}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))


if __name__ == "__main__":
    main()
