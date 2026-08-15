from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "keiba"))

from keiba_ai.licensed_history import (
    apply_history_import,
    prepare_history_import,
)  # noqa: E402
from keiba_ai.point_in_time_odds import PointInTimeOddsPolicy  # noqa: E402


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate and optionally append a licensed 2019-2024 history export."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--decision-offset-minutes", type=int, default=5)
    parser.add_argument("--max-odds-age-minutes", type=int, default=30)
    args = parser.parse_args(argv)
    if args.apply and args.db is None:
        parser.error("--db is required with --apply")

    prepared = prepare_history_import(
        args.manifest,
        odds_policy=PointInTimeOddsPolicy(
            decision_offset_minutes=args.decision_offset_minutes,
            max_age_minutes=args.max_odds_age_minutes,
        ),
    )
    if args.apply:
        report = apply_history_import(args.db, prepared)
    else:
        report = {
            "schema": "licensed-keiba-history-validation-report-v1",
            "mode": "dry-run",
            "manifest_sha256": prepared.manifest_sha256,
            "provider": prepared.manifest["provider"],
            "coverage_start": prepared.coverage_start,
            "coverage_end": prepared.coverage_end,
            "validated_record_count": len(prepared.records),
            "database_modified": False,
        }
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
