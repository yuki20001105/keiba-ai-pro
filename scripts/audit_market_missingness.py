"""Exclusive Phase-1 missingness receipt for the fixed 2021/22 research cohort.

Reads filtered safe predictors, not later outcomes; never mutates source DBs,
models or old artifacts. Supplemental metadata presence is NOT as-of usability.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[variable] = "2"
REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "python-api"), str(REPO / "keiba")]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def object_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def exclusive_json(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def supplemental_metadata(snapshot, cohort):
    """Target-key-only metadata; no speed/workout values are loaded or used."""
    import pandas as pd
    ids = sorted(cohort.race_id.unique())
    db = sqlite3.connect(snapshot.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    evidence, presence = {}, cohort[["race_id", "race_date", "horse_number"]].copy()
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        for table in ("training_data", "speed_figures"):
            columns = [dict(row) for row in db.execute("PRAGMA table_info(" + table + ")")]
            names = {row["name"] for row in columns}
            selected = [c for c in ("id", "race_id", "horse_number", "training_date", "created_at") if c in names]
            rows = []
            if {"race_id", "horse_number"}.issubset(names):
                for start in range(0, len(ids), 400):
                    batch = ids[start:start + 400]
                    sql = "SELECT " + ",".join(selected) + " FROM " + table + " WHERE race_id IN (" + ",".join("?" for _ in batch) + ") ORDER BY race_id,horse_number,id"
                    rows.extend(dict(row) for row in db.execute(sql, batch))
            scoped = pd.DataFrame(rows)
            if rows:
                counts = scoped.groupby(["race_id", "horse_number"]).size().rename(table + "_stored_record_count").reset_index()
                presence = presence.merge(counts, how="left", on=["race_id", "horse_number"], validate="one_to_one", sort=False)
                present_keys = set(zip(scoped.race_id, scoped.horse_number))
            else:
                presence[table + "_stored_record_count"] = 0
                present_keys = set()
            presence[table + "_stored_record_count"] = presence[table + "_stored_record_count"].fillna(0).astype(int)
            present = presence[table + "_stored_record_count"].gt(0)
            created = sorted({str(row["created_at"]) for row in rows if row.get("created_at") is not None})
            evidence[table] = {"schema": columns, "metadata_rows": rows, "metadata_sha256": object_sha(rows),
                               "target_race_records": len(rows), "target_races_with_records": len({row["race_id"] for row in rows}),
                               "target_runners_with_records": int(present.sum()),
                               "target_runner_races_with_records": int(presence.loc[present, "race_id"].nunique()),
                               "record_keys_outside_target_runners": len(present_keys - set(zip(cohort.race_id, cohort.horse_number))),
                               "created_at_min": created[0] if created else None, "created_at_max": created[-1] if created else None,
                               "source_available_timestamp_columns": sorted(names & {"source_time", "source_timestamp", "source_observed_at", "available_at", "received_at"}),
                               "asof_usable": False,
                               "asof_reason": "No demonstrated contemporaneous source availability; retrospective created_at is not historical availability",
                               "values_loaded_or_used": False}
    finally:
        db.rollback()
        db.close()
    return evidence, presence


def run(output, search_root, market_root, snapshot_manifest):
    import numpy as np
    import pandas as pd
    import pyarrow.parquet as pq
    from market_edge.data import _source
    from market_quality.observations import audit_missingness

    output = Path(output).resolve()
    if not output.is_relative_to(REPO / "reports/generated/data-quality"):
        raise ValueError("isolated-data-quality-output-required")
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("exclusive-output-already-exists")
    result, _, source = _source(search_root)
    features = source["feature_columns"]
    dataset = search_root / "dataset/dataset.parquet"
    manifest_path = search_root / "dataset/dataset_manifest.json"
    if sha(dataset) != source["dataset_sha256"] or sha(manifest_path) != source["manifest_sha256"]:
        raise ValueError("H04-source-dataset-or-manifest-hash-mismatch")
    manifest = read(manifest_path)
    if len(features) != 99 or not set(features).issubset(manifest["feature_allowlist"]):
        raise ValueError("fixed-H04-99-feature-contract-required")
    provenance = read(market_root / "provenance.json")
    market_path = market_root / "development.parquet"
    if sha(market_path) != provenance["artifact"]["sha256"]:
        raise ValueError("frozen-market-dataset-changed")
    study = pd.read_parquet(market_path, use_threads=False)
    if len(study) != 84128 or study.race_id.nunique() != 6109 or not study.race_date.astype(str).str[:4].isin(["2021", "2022"]).all():
        raise ValueError("exact-original-research-cohort-required")
    keys = ["race_id", "race_date", "horse_number"]
    if study.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("duplicate-study-runner")
    auxiliary_candidates = ["horse_weight", "horse_weight_change", "jockey_id", "trainer_id", "surface", "distance"]
    names = pq.ParquetFile(dataset).schema_arrow.names
    auxiliary = [c for c in auxiliary_candidates if c in names and c not in study]
    columns = list(dict.fromkeys(keys + features + auxiliary))
    filters = [("race_date", ">=", "20210101"), ("race_date", "<=", "20221231")]
    source_frame = pd.read_parquet(dataset, columns=columns, filters=filters, use_threads=False)
    if not source_frame.race_date.astype(str).str[:4].isin(["2021", "2022"]).all():
        raise ValueError("future-feature-row-unexpected")
    if source_frame.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("duplicate-source-feature-runner")
    work = study.merge(source_frame, on=keys, how="left", validate="one_to_one", indicator=True, sort=False)
    if len(work) != 84128 or not work._merge.eq("both").all():
        raise ValueError("incomplete-study-to-H04-feature-join")
    work = work.drop(columns="_merge")
    snapshot_info = read(snapshot_manifest)
    snapshot = Path(snapshot_info["path"])
    if snapshot_info["sha256"] != manifest["snapshot_sha256"] or snapshot.name != manifest["snapshot_sha256"] + ".db":
        raise ValueError("source-snapshot-binding-mismatch")
    before_stat = snapshot.stat()
    if before_stat.st_size != snapshot_info["size_bytes"]:
        raise ValueError("snapshot-size-changed")
    evidence, presence = supplemental_metadata(snapshot, study)
    after_stat = snapshot.stat()
    if (before_stat.st_size, before_stat.st_mtime_ns) != (after_stat.st_size, after_stat.st_mtime_ns):
        raise ValueError("snapshot-changed-during-metadata-audit")
    categorical = set(manifest["categorical_features"]) | {"jockey_id", "trainer_id", "surface"}
    specs = {c: {"kind": "categorical"} for c in categorical}
    for column in ("training_data", "speed_figures"):
        work[column] = np.nan
        specs[column] = {"missing_reason": "historical_unavailable", "reason_evidence": "missingness_sources.json#supplemental_metadata/" + column}
    research = [c for c in ["h04_score", "h04_probability", "odds", "popularity", "implied_probability",
                            "normalized_market_probability", "log_odds", "market_rank", "h04_rank",
                            "rank_difference", "probability_difference", "field_size", "h04_probability_fixed_reference"] if c in work]
    extras = [c for c in auxiliary_candidates if c in work]
    audited_columns = list(dict.fromkeys(features + research + extras + ["training_data", "speed_figures"]))
    report = audit_missingness(work, audited_columns, specs)
    report["feature_role"] = report.feature.map(lambda c: "fixed_H04_input" if c in features else "historical_unavailable_supplement" if c in evidence else "research_reference_not_model_input" if c == "h04_probability_fixed_reference" else "research_or_source_metadata")
    top = report.loc[report.feature.isin(features)].sort_values("missing_rate", ascending=False, kind="stable").head(15)
    summary = {"schema": "fixed-market-H04-cohort-missingness-v1", "created_at": datetime.now(timezone.utc).isoformat(),
               "runners": len(work), "races": int(work.race_id.nunique()), "date_from": str(work.race_date.min()), "date_to": str(work.race_date.max()),
               "H04_feature_count": len(features), "total_audited_columns": len(report), "categorical_H04_columns": sorted(set(features) & categorical),
               "H04_columns_with_missing": int(report.loc[report.feature.isin(features), "missing_count"].gt(0).sum()),
               "H04_invalid_cells": int(report.loc[report.feature.isin(features), "invalid_count"].sum()),
               "H04_missing_cells": int(report.loc[report.feature.isin(features), "missing_count"].sum()),
               "top_missing_H04_columns": top.to_dict("records"),
               "supplemental": {name: {k: v for k, v in item.items() if k not in {"schema", "metadata_rows"}} for name, item in evidence.items()},
               "future_period_decoded": False, "phase2_run": False, "models_fitted_or_changed": False,
               "unknown_missing_reasons_are_not_assumed_parser_failure": True,
               "prior_history_absence_reason": "reason_unknown unless explicit evidence; observed zero history is not proof of complete career",
               "snapshot_content_hash_recomputed_this_audit": False,
               "gate_scope": "missingness report only; not overall MODEL_RESEARCH_READY"}
    sources = {"schema": "market-H04-missingness-sources-v1", "source_verification": source,
               "research_dataset": {"path": str(market_path), "sha256": sha(market_path)},
               "source_dataset": {"path": str(dataset), "sha256": sha(dataset), "decoded_columns": columns, "filters": filters,
                                  "filtered_rows_before_exact_cohort_join": len(source_frame), "actual_audited_rows": len(work)},
               "source_manifest": {"path": str(manifest_path), "sha256": sha(manifest_path)},
               "source_snapshot": {"path": str(snapshot), "declared_sha256": snapshot_info["sha256"], "size_bytes": before_stat.st_size,
                                   "content_hash_recomputed": False, "size_and_mtime_unchanged": True,
                                   "read_mode": "mode=ro; query_only; one read transaction; target race IDs only"},
               "supplemental_metadata": evidence, "feature_specs": specs,
               "module_sha256": sha(REPO / "python-api/market_quality/observations.py"), "script_sha256": sha(Path(__file__))}
    output.mkdir(parents=True, exist_ok=True)
    with (output / "missingness_report.csv").open("x", encoding="utf-8", newline="") as stream:
        report.to_csv(stream, index=False)
    with (output / "supplemental_runner_presence.csv").open("x", encoding="utf-8", newline="") as stream:
        presence.to_csv(stream, index=False)
    exclusive_json(output / "missingness_summary.json", summary)
    exclusive_json(output / "missingness_sources.json", sources)
    exclusive_json(output / "artifact_manifest.json", {name: sha(output / name) for name in
        ("missingness_report.csv", "missingness_summary.json", "missingness_sources.json", "supplemental_runner_presence.csv")})
    return {k: v for k, v in summary.items() if k not in {"top_missing_H04_columns", "supplemental"}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=REPO / "reports/generated/data-quality/market-h04-clean-v1/missingness")
    args = parser.parse_args()
    if os.name == "nt":
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    print(json.dumps(run(args.output, REPO / "reports/generated/model-experiments/20260919-feature-search-v1",
                         REPO / "reports/generated/model-experiments/market-edge-v1",
                         REPO / "reports/generated/model-experiments/20260919-jra-safe-v1/snapshot.json"), indent=2))


if __name__ == "__main__":
    main()
