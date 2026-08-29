#!/usr/bin/env python3
"""Dry-run/apply the local pedigree cache into the research DB."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_API = ROOT / "python-api"
if str(PYTHON_API) not in sys.path:
    sys.path.insert(0, str(PYTHON_API))

from scraping.pedigree_backfill import sync_pedigree_cache  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync pedigree cache into the research DB")
    parser.add_argument(
        "--ultimate-db",
        type=Path,
        default=ROOT / "keiba" / "data" / "keiba_ultimate.db",
    )
    parser.add_argument(
        "--pedigree-db",
        type=Path,
        default=ROOT / "keiba" / "data" / "pedigree_cache.db",
    )
    parser.add_argument("--apply", action="store_true", help="Apply append/update-only reconciliation")
    args = parser.parse_args()
    report = sync_pedigree_cache(
        args.ultimate_db,
        args.pedigree_db,
        apply=args.apply,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
