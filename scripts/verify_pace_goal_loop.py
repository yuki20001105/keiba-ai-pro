"""Versioned pandas compatibility repair; retain every frozen audit check."""
import argparse
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "python-api"), str(REPO / "keiba"), str(REPO / "scripts")]
from experiments.search import freeze_json
from training import db_shared_grouped as grouped
from training import pace_comparison as comparison
from training import pace_verification as frozen


def _grouping_dict(groups):
    return dict(iter(groups))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    root = parser.parse_args().root.resolve()
    comparison._task(root)
    protocol = {
        "schema": "pace-independent-verification-v2",
        "task_sha256": comparison.sha(root / "task-ledger.json"),
        "artifact_manifest_sha256": comparison.sha(root / "artifact-manifest.json"),
        "frozen_verifier_sha256": comparison.sha(frozen.__file__),
        "verifier_sha256": comparison.sha(__file__),
        "compatibility_repair": "dict(iter(DataFrameGroupBy)); no audit condition or training source changes",
    }
    freeze_json(root / "verification-v2-protocol.json", protocol)
    # The sealed v1 verifier has GroupBy.keys='race_id', not a callable mapping
    # method. Bind explicit iteration without mutating its module or checks.
    verify = grouped._bind(frozen.verify, {"dict": _grouping_dict})
    result = verify(root)
    result.update(verifier_version=protocol["schema"],
        verifier_sha256=protocol["verifier_sha256"],
        frozen_verifier_sha256=protocol["frozen_verifier_sha256"])
    comparison.write(root / "verification.json", result)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))


if __name__ == "__main__":
    main()
