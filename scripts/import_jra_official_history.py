from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "keiba"))

from keiba_ai.jra_official_history import (  # noqa: E402
    apply_official_results_import,
    prepare_official_results_import,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate and append JRA official result outcomes to a research DB copy."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.apply and args.db is None:
        parser.error("--db is required with --apply")
    prepared = prepare_official_results_import(args.manifest)
    if args.apply:
        report = apply_official_results_import(args.db, prepared)
    else:
        report = {
            "schema": "jra-official-results-validation-report-v1",
            "mode": "dry-run",
            "manifest_sha256": prepared.manifest_sha256,
            "coverage_start": prepared.coverage_start,
            "coverage_end": prepared.coverage_end,
            "validated_record_count": len(prepared.records),
            "database_modified": False,
            "point_in_time_odds_imported": False,
        }
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
