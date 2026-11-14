"""Independently replay pedigree source proofs, repaired rows and cohort checks."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba"), str(ROOT / "scripts")]

import pandas as pd

import reacquire_pedigree as acquire
import repair_pedigree_identities as repair
from experiments.common import file_sha256, json_sha256, read_json
from experiments.search import freeze_json
from scraping.fetch_pipeline import decode_html_body


def verify_sources(archive, proofs):
    with closing(repair.readonly(archive)) as db:
        accepted = {horse: json.loads(raw) for horse, raw in db.execute(
            "SELECT horse_id,proof_json FROM queue WHERE status='accepted'")}
        if accepted != proofs:
            raise ValueError("pedigree-accepted-queue-proof-mismatch")
        for horse, proof in proofs.items():
            if (horse != proof["horse_id"] or json_sha256({
                key: value for key, value in proof.items() if key != "proof_sha256"
            }) != proof["proof_sha256"]):
                raise ValueError("pedigree-proof-hash-or-child-mismatch")
            row = db.execute("SELECT final_url,status,body,fetched_at FROM sources WHERE url=?",
                             (proof["source_url"],)).fetchone()
            if row is None:
                raise ValueError("pedigree-proof-source-missing")
            final_url, status, body, captured = row
            if (status != 200 or final_url.rstrip("/") != proof["source_url"].rstrip("/")
                    or captured is None or captured <= 0
                    or hashlib.sha256(body).hexdigest() != proof["raw_body_sha256"]
                    or datetime.fromtimestamp(captured, timezone.utc).isoformat()
                    != proof["source_observed_at_utc"]):
                raise ValueError("pedigree-proof-source-bytes-or-transport-mismatch")
            parents = repair.identity.parse_parent_identities(
                decode_html_body(body), horse, proof["source_url"])
            if parents != proof["parents"]:
                raise ValueError("pedigree-proof-parent-links-mismatch")
        return dict(db.execute("SELECT status,COUNT(*) FROM queue GROUP BY status"))


def verify(root):
    root = root.resolve(strict=True)
    if not (root / "verification.json").exists():
        raise ValueError("pedigree-reacquisition-not-finalized")
    task = acquire.validate(root)
    for name, expected in read_json(root / "artifacts.json").items():
        if file_sha256(root / name) != expected:
            raise ValueError(f"pedigree-artifact-changed: {name}")
    proofs = read_json(root / "proofs.json")
    counts = verify_sources(root / "source-archive.db", proofs)
    changed = repair.verify_rows(Path(task["source"]), root / "research.db", proofs, root.name)
    result = read_json(root / "verification.json")
    if changed != result["updated_raw_rows"] or counts != result["collection"]["counts"]:
        raise ValueError("pedigree-final-counts-mismatch")
    before = pd.read_parquet(Path(task["before_dataset"]) / "dataset.parquet")
    after = pd.read_parquet(root / "dataset" / "dataset.parquet")
    pd.testing.assert_frame_equal(before.drop(columns=repair.PEDIGREE_FEATURES),
                                  after.drop(columns=repair.PEDIGREE_FEATURES))
    manifest = read_json(root / "dataset" / "dataset_manifest.json")
    if file_sha256(root / "dataset" / "dataset.parquet") != manifest["dataset_sha256"]:
        raise ValueError("pedigree-dataset-manifest-hash-mismatch")
    summary = repair._summary(repair.diagnose_dataset(root / "dataset", [2023, 2024, 2025]))
    if summary != result["summaries"][-1]:
        raise ValueError("pedigree-final-missingness-summary-mismatch")
    scope = after.loc[after.race_date.le("20251231")]
    missing = scope.safe_sire_starts.isna() | scope.safe_damsire_starts.isna()
    with closing(repair.readonly(root / "source-archive.db")) as db:
        reasons = dict(db.execute("SELECT horse_id,status FROM queue"))
    missing_reasons = Counter(reasons.get(horse, "outside_acquisition_targets")
                              for horse in scope.loc[missing, "horse_id"])
    independent = {
        "verified": True, "proofs_independently_reparsed": len(proofs),
        "updated_raw_rows_reverified": changed, "all_artifact_and_source_hashes_match": True,
        "same_cohort_nonpedigree_unchanged": True,
        "all_targets_attempted": counts.get("pending", 0) == 0,
        "unaccepted_horses_by_status": {key: value for key, value in counts.items() if key != "accepted"},
        "collection_stop_reason": result["collection"]["stop_reason"],
        "fixed_scope": "2016-2025", "summary": summary,
        "remaining_parent_missing_rows_by_acquisition_status": dict(missing_reasons),
        "remaining_parent_missing_horses": scope.loc[missing, "horse_id"].nunique(),
        "global_zero_goal_met": summary["missing_feature_cells"] == 0,
        "historical_prestart_availability_proven": False,
    }
    freeze_json(root / "replay-verification.json", independent)
    return independent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.root), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
