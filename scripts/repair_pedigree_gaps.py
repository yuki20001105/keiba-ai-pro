"""Plan/apply a bounded, network-free JRA static-pedigree gap repair."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python-api"))
sys.path.insert(0, str(ROOT / "scripts"))

from scraping.pedigree_gap_repair import (apply_repair, checked_run_directory, file_sha,
                                        plan_repair, validate_plan)
from evaluate_db_shared_venues import preservation_receipt


def protected_check(expected):
    current = preservation_receipt()
    if not isinstance(expected, dict) or any(current.get(key) != value for key, value in expected.items() if key != "forward_processes"):
        raise ValueError("pedigree-protected-model-or-forward-source-changed")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Explicitly apply a previously sealed plan")
    parser.add_argument("--chunk-size", type=int, default=100)
    parser.add_argument("--limit", type=int, help="Bound the number of additional rows applied")
    args = parser.parse_args(argv)
    run = checked_run_directory(ROOT, args.run_dir, create=not args.apply)
    if args.apply:
        plan, _ = validate_plan(run)
        if (Path(plan["database"]).resolve() != (ROOT / "keiba/data/keiba_ultimate.db").resolve()
                or Path(plan["cache_database"]).resolve() != (ROOT / "keiba/data/pedigree_cache.db").resolve()
                or plan.get("cli_sha256") != file_sha(Path(__file__))):
            raise ValueError("pedigree-cli-source-or-database-binding-invalid")
        result = apply_repair(run, chunk_size=args.chunk_size, limit=args.limit, protected_check=protected_check)
    else:
        result = plan_repair(ROOT / "keiba/data/keiba_ultimate.db", ROOT / "keiba/data/pedigree_cache.db", run,
                             protected=preservation_receipt(), cli_sha256=file_sha(Path(__file__)))
    print(json.dumps({key: value for key, value in result.items() if key not in {"protected_before", "code_hashes"}}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
