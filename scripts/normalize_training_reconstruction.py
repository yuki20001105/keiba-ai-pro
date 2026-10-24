"""Stream lossless auxiliary normalization; no actual feature generation or fit."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack, contextmanager
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[key] = "2"
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "scripts"), str(ROOT / "keiba")]
from training_reconstruction.normalization import EVENT_FIELDS, MEASUREMENT_FIELDS, SLOTS, candidate_registry, normalize_event, require, schema_definition

OUTPUTS = ["normalized_training_schema.json", "feature_candidate_registry.json", "training_pit_classification.csv",
           "training_missingness_classification.csv", "normalized_training_events.parquet", "normalized_training_measurements.parquet",
           "normalization-result.json"]


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def verify_prior(prior):
    manifest_path = prior / "artifact_manifest.json"
    manifest = read(manifest_path)
    require(read(prior / "training-audit-result.json")["status"] == "completed", "prior audit incomplete")
    require(read(prior / "independent-verification.json")["verified"] is True, "prior audit unverified")
    for name, value in manifest["files"].items():
        path = (prior / name).resolve()
        require(path.is_relative_to(prior) and sha(path) == value["sha256"], "prior audit changed: " + name)
    return sha(manifest_path)


@contextmanager
def readonly_snapshot(snapshot):
    # sqlite3.Connection.__exit__ manages transactions, not lifetime. Do not
    # schedule close before its transaction __exit__ via ExitStack.
    db = sqlite3.connect(Path(snapshot).as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        yield db
    finally:
        db.close()


def execute(snapshot, expected_sha, prior, output):
    import pyarrow as pa
    import pyarrow.parquet as pq
    import psutil
    from snapshot_feature_pit_audit import protected
    snapshot, prior, output = (Path(path).resolve() for path in (snapshot, prior, output))
    require(output == ROOT / "reports/generated/model-experiments/training-data-reconstruction-v1", "isolated output required")
    require(not any((output / name).exists() for name in OUTPUTS), "normalization outputs already exist; no overwrite")
    require(psutil.virtual_memory().available >= 3 * 1024**3, "3 GB memory reserve required")
    require(sha(snapshot) == expected_sha, "snapshot hash mismatch")
    wal = Path(str(snapshot) + "-wal")
    require(not wal.exists() or wal.stat().st_size == 0, "immutable snapshot has pending WAL")
    prior_manifest_sha = verify_prior(prior)
    require(read(prior / "audit_plan.json")["snapshot_sha256"] == expected_sha, "snapshot/prior audit mismatch")
    expected_rows = read(prior / "training_data_inventory.json")["rows"]
    source_files = ("scripts/normalize_training_reconstruction.py", "python-api/training_reconstruction/normalization.py")
    code_sha = {name: sha(ROOT / name) for name in source_files}
    before = protected()
    output.mkdir(parents=True, exist_ok=True)
    definition = schema_definition()
    definition.update(source_db_sha256=expected_sha, prior_manifest_sha256=prior_manifest_sha, source_sha256=code_sha,
                      normalization_started_at=datetime.now(timezone.utc).isoformat())
    write(output / OUTPUTS[0], definition)
    write(output / OUTPUTS[1], candidate_registry())
    types = {"string": pa.string(), "int64": pa.int64(), "bool": pa.bool_(), "float64": pa.float64()}
    event_schema = pa.schema([(name, types[kind]) for name, kind in EVENT_FIELDS])
    measurement_schema = pa.schema([(name, types[kind]) for name, kind in MEASUREMENT_FIELDS])
    pit_fields = ["event_id", "source_training_id", "race_id", "horse_number", "identity_verified", "event_validity",
                  "pit_class", "legacy_pit_class", "capture_provenance", "raw_original_available", "historical_backfill",
                  "quarantined", "quarantine_reasons", "distance_mapping_verified", "model_input_allowed"]
    missing_fields = ["event_id", "source_training_id", "race_id", "measurement_kind", "original_slot", "original_value",
                      "course_normalized", "track_condition", "missing_reason", "pit_class", "quarantined"]
    events, measures = [], []
    counts, validity, quarantine, missing_by_slot = Counter(), Counter(), Counter(), Counter()
    race_ids, ids = set(), set()
    with ExitStack() as stack:
        db = stack.enter_context(readonly_snapshot(snapshot))
        previous = stack.enter_context((prior / "training_runner_join_audit.csv").open(encoding="utf-8-sig", newline=""))
        previous_rows = iter(csv.DictReader(previous))
        pit_handle = stack.enter_context((output / OUTPUTS[2]).open("x", encoding="utf-8", newline=""))
        missing_handle = stack.enter_context((output / OUTPUTS[3]).open("x", encoding="utf-8", newline=""))
        pit_csv, missing_csv = csv.DictWriter(pit_handle, pit_fields), csv.DictWriter(missing_handle, missing_fields)
        pit_csv.writeheader(); missing_csv.writeheader()
        events_handle = stack.enter_context((output / OUTPUTS[4]).open("xb"))
        measures_handle = stack.enter_context((output / OUTPUTS[5]).open("xb"))
        event_writer = stack.enter_context(pq.ParquetWriter(events_handle, event_schema, compression="zstd"))
        measurement_writer = stack.enter_context(pq.ParquetWriter(measures_handle, measurement_schema, compression="zstd"))
        def flush():
            if events:
                event_writer.write_table(pa.Table.from_pylist(events, schema=event_schema))
                measurement_writer.write_table(pa.Table.from_pylist(measures, schema=measurement_schema))
                events.clear(); measures.clear()
        for source in db.execute("SELECT * FROM training_data ORDER BY id"):
            prior_row = next(previous_rows, None)
            require(prior_row is not None, "prior audit lost rows")
            event, clocks = normalize_event(dict(source), prior_row, expected_sha)
            require(event["source_training_id"] not in ids, "duplicate training id")
            ids.add(event["source_training_id"]); race_ids.add(event["race_id"])
            events.append(event); measures.extend(clocks)
            counts["events"] += 1; counts["measurements"] += len(clocks)
            counts[event["pit_class"]] += 1; counts["quarantined_events"] += int(event["quarantined"])
            validity[event["event_validity"]] += 1
            quarantine.update(json.loads(event["quarantine_reasons"]))
            pit_csv.writerow({name: event[name] for name in pit_fields})
            for clock in clocks:
                counts[clock["value_status"]] += 1
                if clock["value_status"] == "MISSING":
                    missing_by_slot[clock["original_slot"]] += 1
                    missing_csv.writerow({name: clock[name] for name in missing_fields})
            if len(events) >= 1000:
                flush()
        flush()
        require(next(previous_rows, None) is None, "prior audit has extra rows")
    require(counts["events"] == expected_rows and counts["measurements"] == expected_rows * len(SLOTS), "normalization row loss")
    require(sha(snapshot) == expected_sha and verify_prior(prior) == prior_manifest_sha, "source changed during normalization")
    require(all(sha(ROOT / name) == digest for name, digest in code_sha.items()), "normalization source changed during run")
    after = protected()
    result = {"schema": "training-normalization-result-v1", "status": "completed", "source_snapshot_sha256": expected_sha,
        "prior_manifest_sha256": prior_manifest_sha, "source_sha256": code_sha, "counts": dict(counts), "distinct_races": len(race_ids),
        "event_validity": dict(validity), "quarantine_reason_counts_nonexclusive": dict(quarantine),
        "missing_by_original_slot": dict(missing_by_slot),
        "missingness_class_counts": {"STRUCTURAL_NOT_REPORTED": 0, "SOURCE_MISSING": 0, "PARSER_MISSING": 0, "UNKNOWN": counts["MISSING"]},
        "distance_mapping_verified_measurements": 0, "strict_pit_events": 0, "verified_historical_backfill_events": 0,
        "historical_backfill_unknown_events": counts["events"], "raw_original_available_events": 0,
        "feature_candidates": {"READY": 0, "HOLD": 19, "REJECT": 1}, "comment_model_input": "EXCLUDED",
        "features_generated": 0, "model_fits": 0, "network_requests": 0, "source_modified": False,
        "TRAINING_FEATURE_RESEARCH_READY": "NO", "protected_before": before, "protected_after": after,
        "files": {name: {"sha256": sha(output / name), "bytes": (output / name).stat().st_size} for name in OUTPUTS[:-1]}}
    write(output / OUTPUTS[-1], result)
    print(json.dumps({"status": "completed", "counts": dict(counts), "result_sha256": sha(output / OUTPUTS[-1])}, ensure_ascii=False))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--expected-snapshot-sha256", required=True)
    parser.add_argument("--prior-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.name == "nt":
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    execute(args.snapshot, args.expected_snapshot_sha256, args.prior_audit, args.output)
