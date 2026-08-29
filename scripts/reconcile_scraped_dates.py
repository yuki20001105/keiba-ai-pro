"""Reconcile the local scrape coverage ledger from authoritative race rows."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_API = REPO_ROOT / "python-api"
if str(PYTHON_API) not in sys.path:
    sys.path.insert(0, str(PYTHON_API))

from scraping.storage import reconcile_scraped_dates_from_races  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=REPO_ROOT / "keiba" / "data" / "keiba_ultimate.db",
    )
    parser.add_argument("--min-races", type=int, default=6)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = reconcile_scraped_dates_from_races(
        args.db.resolve(),
        min_races=max(1, args.min_races),
        apply=bool(args.apply),
    )
    output = args.output
    if output is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = REPO_ROOT / "output" / f"scraped-dates-reconciliation-{timestamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"report: {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
