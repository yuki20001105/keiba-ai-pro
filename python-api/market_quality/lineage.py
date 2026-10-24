"""Read-only H04 fold lineage and actual historical feature replay audits.

No fitting, calibration selection, source mutation, model promotion or later-year
outcome inspection. Event-time safety is distinct from verified T-5 observation:
historical race attributes and immutable pedigree identity are allowed proxies;
current profile totals, scraped latest-history fields and market inputs are not.
"""
from __future__ import annotations

import gc
import hashlib
import json
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _checked(path, digest):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not digest or sha256(path) != digest:
        raise ValueError(f"Artifact hash mismatch: {path.name}")
    return path


class _ScopedCursor:
    def __init__(self, cursor, checkpoint):
        self.cursor, self.checkpoint = cursor, checkpoint

    def __iter__(self):
        for count, row in enumerate(self.cursor, 1):
            if count % 5000 == 0:
                self.checkpoint(f"read historical source rows: {count}")
            yield row

    def fetchone(self):
        return self.cursor.fetchone()


class _ScopedConnection:
    """Restrict the frozen compact loader without changing its parsing semantics."""
    def __init__(self, connection, checkpoint):
        self.connection, self.checkpoint = connection, checkpoint

    def execute(self, sql):
        normalized = " ".join(sql.split())
        window = "race_id >= '2013' AND race_id < '2023'"
        if normalized == "SELECT name FROM sqlite_master WHERE type='table'":
            scoped = normalized
        elif normalized in {"SELECT 1 FROM licensed_history_entries LIMIT 1",
                            "SELECT 1 FROM official_history_entries LIMIT 1"}:
            scoped = normalized
        elif normalized == "SELECT race_id,data FROM races_ultimate":
            scoped = normalized + " WHERE " + window
        elif normalized == ("SELECT race_id,data FROM race_results_ultimate "
                            "WHERE substr(race_id,5,2) BETWEEN '01' AND '10' AND substr(race_id,1,4)>='2013'"):
            scoped = normalized + " AND " + window
        elif normalized == "SELECT race_id,lifecycle,quality_status,excluded_from_standard FROM scrape_race_acquisition_state":
            scoped = normalized + " WHERE " + window
        elif normalized == "SELECT race_id,combinations,payout FROM return_tables_ultimate WHERE bet_type='単勝'":
            scoped = normalized + " AND " + window
        else:
            raise ValueError("Unapproved historical source query")
        return _ScopedCursor(self.connection.execute(scoped), self.checkpoint)


def prepare_event_history(snapshot_path, checkpoint=None, *, expected_snapshot_sha256=None):
    """Return exact frozen-loader eligible history, scoped to 2013 through 2022.

    The caller schedules this moderately large read under collector protection.
    The DB is opened read-only/immutable, and the original eligibility and whole
    race filters are replayed unchanged. No source later than 2022 is decoded.
    """
    from experiments.dataset import _load_compact
    from training.eligibility import select_training_eligible_rows

    notify = checkpoint or (lambda stage: None)
    snapshot = Path(snapshot_path)
    if snapshot.is_symlink() or not snapshot.is_file():
        raise ValueError("Invalid immutable snapshot")
    snapshot = snapshot.resolve()
    wal = Path(str(snapshot) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("Snapshot has pending WAL")
    notify("verify immutable history snapshot")
    digest = sha256(snapshot)
    if expected_snapshot_sha256 is not None and digest != expected_snapshot_sha256:
        raise ValueError("Snapshot hash mismatch")
    with sqlite3.connect(snapshot.as_uri() + "?mode=ro&immutable=1", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        raw, quality, _payouts, source_audit = _load_compact(_ScopedConnection(connection, notify))
    notify("replay original historical eligibility")
    selection = select_training_eligible_rows(raw, target="speed_deviation", recorded_quality_states=quality)
    raw_counts = raw.groupby("race_id", sort=False).size()
    selected_counts = selection.frame.groupby("race_id", sort=False).size()
    partial = selected_counts.index[selected_counts.ne(raw_counts.reindex(selected_counts.index))]
    clean = selection.frame.loc[~selection.frame.race_id.isin(partial)].reset_index(drop=True)
    if clean.empty or not clean.race_date.astype(str).between("20130101", "20221231").all():
        raise ValueError("Historical replay scope invalid")
    receipt = {"snapshot_path": str(snapshot), "snapshot_sha256": digest,
               "scope": ["20130101", "20221231"], "post_2022_source_rows_decoded": False,
               "eligible_rows": len(clean), "eligible_races": int(clean.race_id.nunique()),
               "complete_runner_target_filter": True, "partial_races_excluded": len(partial),
               "source_audit": source_audit, "eligibility": selection.manifest.as_dict(),
               "actual_source_replay": True, "historical_event_attributes_allowed": True,
               "historical_t5_availability_verified": False}
    del raw, selection, _payouts, quality
    gc.collect()
    notify("verify history snapshot remained immutable")
    if sha256(snapshot) != digest:
        raise ValueError("Snapshot changed during history audit")
    return clean, receipt


def validate_fold_contract(fold, feature_columns, dataset_sha256):
    year = fold.get("year")
    contract = fold.get("contract", {})
    if (year not in (2021, 2022) or contract.get("evaluation_year") != year
            or contract.get("train_period") != ["20160101", f"{year - 1}1231"]
            or contract.get("dataset_sha256") != dataset_sha256
            or contract.get("feature_columns") != feature_columns
            or contract.get("target") != "rank_regression" or contract.get("loss") != "huber"
            or contract.get("task") != "ranking" or contract.get("score_is_probability") is not False
            or contract.get("validation_used_for_fit_or_early_stopping") is not False
            or contract.get("market_features") is not False):
        raise ValueError("H04 OOT fold chronology/feature/target contract invalid")


def verify_feature_sources(manifest, source_contract):
    """Bind the code being replayed to the original dataset/model provenance."""
    from experiments import dataset as dataset_module
    directory = Path(dataset_module.__file__).parent
    expected = dict(source_contract.get("modules", {}))
    original = manifest.get("source_contract", {})
    expected.update({"dataset.py": original.get("dataset_module_sha256"),
                     "features.py": original.get("features_module_sha256"),
                     "search_features.py": manifest.get("search_feature_contract", {}).get("search_features_module_sha256")})
    if not {"training.py", "dataset.py", "features.py", "search_features.py"} <= expected.keys():
        raise ValueError("Incomplete original source hash contract")
    for name, digest in expected.items():
        if name != Path(name).name:
            raise ValueError("Invalid source module name")
        _checked(directory / name, digest)
    if original.get("history_boundary") != "source_race_date < target_race_date":
        raise ValueError("Strict event-time history contract missing")
    return expected


def _keyed(frame):
    keys = ["race_id", "horse_number"]
    if frame.empty or not set(keys + ["race_date"]) <= set(frame) or frame[keys].isna().any().any():
        raise ValueError("Incomplete audit runner identity")
    copy = frame.copy()
    copy["race_id"] = copy.race_id.astype(str)
    numbers = pd.to_numeric(copy.horse_number, errors="raise")
    if not np.isfinite(numbers).all() or not numbers.between(1, 18).all() or not numbers.eq(numbers.round()).all():
        raise ValueError("Invalid audit runner number")
    copy["horse_number"] = numbers.astype(int)
    if copy.duplicated(keys).any():
        raise ValueError("Duplicate audit runner key")
    return copy.set_index(keys).sort_index()


def replay_fold_predictions(fold, inputs, saved, *, feature_columns, dataset_sha256,
                            checkpoint=None, chunk_size=4096, tolerance=1e-10):
    """Hash before deserialization; saved encoder.transform and model.predict only."""
    import joblib
    notify = checkpoint or (lambda stage: None)
    validate_fold_contract(fold, feature_columns, dataset_sha256)
    path = _checked(fold["artifact"], fold["sha256"])
    year = fold["year"]
    source, reference = _keyed(inputs), _keyed(saved)
    if not source.index.equals(reference.index) or not source.race_date.astype(str).equals(reference.race_date.astype(str)):
        raise ValueError("OOF input/prediction runner cohort mismatch")
    if (not source.race_date.astype(str).str.startswith(str(year)).all()
            or len(source) != fold["validation_rows"]
            or source.index.get_level_values("race_id").nunique() != fold["validation_races"]):
        raise ValueError("OOF fold row/date scope mismatch")
    if not reference.fold_year.eq(year).all() or not np.isfinite(reference.prediction.to_numpy(float)).all():
        raise ValueError("Saved OOF score/fold invalid")
    notify(f"{year}: load verified OOT bundle")
    bundle = joblib.load(path)
    if (bundle.get("contract") != fold["contract"] or bundle.get("feature_columns") != feature_columns
            or bundle["encoder"].feature_columns != feature_columns
            or bundle["encoder"].requested_columns != feature_columns):
        raise ValueError("Serialized model/encoder does not match frozen fold contract")
    differences = []
    with threadpool_limits(limits=2):
        for start in range(0, len(source), chunk_size):
            notify(f"{year}: verify saved OOF scores {start}/{len(source)}")
            chunk = source.iloc[start:start + chunk_size]
            # Physically remove current outcomes and market columns at inference.
            encoded = bundle["encoder"].transform(chunk[feature_columns])
            predicted = np.asarray(bundle["model"].predict(encoded, num_threads=2), dtype=float)
            if predicted.shape != (len(chunk),) or not np.isfinite(predicted).all():
                raise ValueError("Reinference failed to score every runner")
            differences.extend(abs(predicted - reference.prediction.iloc[start:start + len(chunk)].to_numpy(float)))
    del bundle
    gc.collect()
    delta = np.asarray(differences)
    return {"year": year, "rows": len(source), "races": int(fold["validation_races"]),
            "model_sha256": fold["sha256"], "max_absolute_score_difference": float(delta.max()),
            "mismatched_scores": int((delta > tolerance).sum()), "tolerance": tolerance,
            "pass": bool((delta <= tolerance).all()), "fit_called": False, "cpu_threads": 2}


def replay_event_features(event_history, saved_inputs, feature_columns, *, checkpoint=None):
    """Recreate all 99 inputs including supervised training rows, not only OOF.

    Requires exact eligible 2013..2022 canonical history, not the scraped prev_*
    or lifetime profile totals. Source tables and hashes are bound by the caller.
    """
    from experiments.features import ALLOWED_FEATURES, FEATURE_GROUPS, engineer_features
    from experiments.search_features import SEARCH_FEATURES, derive_search_features
    notify = checkpoint or (lambda stage: None)
    allowed = ALLOWED_FEATURES | set(SEARCH_FEATURES)
    if len(feature_columns) != 99 or len(set(feature_columns)) != 99 or not set(feature_columns) <= allowed:
        raise ValueError("Exact frozen safe 99-feature allowlist required")
    if not event_history.race_date.astype(str).between("20130101", "20221231").all():
        raise ValueError("Future/unscoped history rows are not allowed")
    if not saved_inputs.race_date.astype(str).between("20160101", "20221231").all():
        raise ValueError("Only original training and 2021/22 OOF inputs may be checked")
    notify("rebuild actual event-dated base/history/entity features")
    with threadpool_limits(limits=2):
        engineered, generated_audit = engineer_features(event_history)
        engineered = engineered.loc[engineered.race_date.astype(str).ge("20160101")].reset_index(drop=True)
        derived = derive_search_features(engineered)
        rebuilt = pd.concat([engineered, derived], axis=1)
    notify("compare all frozen input columns and runner identities")
    left, right = _keyed(rebuilt), _keyed(saved_inputs)
    if not left.index.equals(right.index) or not left.race_date.astype(str).equals(right.race_date.astype(str)):
        raise ValueError("Rebuilt supervised cohort differs from original dataset")
    rows, affected = [], np.zeros(len(right), dtype=bool)
    base = set(FEATURE_GROUPS["base"])
    for name in feature_columns:
        lval, rval = left[name], right[name]
        if pd.api.types.is_numeric_dtype(lval) and pd.api.types.is_numeric_dtype(rval):
            x, y = lval.to_numpy(float), rval.to_numpy(float)
            # This is a deterministic replay of the identical stored float32
            # construction, not a second approximate model. Require exact values.
            equal = (x == y) | (np.isnan(x) & np.isnan(y))
            finite = np.isfinite(x) & np.isfinite(y)
            largest = float(abs(x[finite] - y[finite]).max()) if finite.any() else None
        else:
            equal = lval.astype("string").fillna("<MISSING>").eq(rval.astype("string").fillna("<MISSING>")).to_numpy(bool)
            largest = None
        affected |= ~equal
        rows.append({"feature": name, "mismatched_rows": int((~equal).sum()),
                     "max_absolute_difference": largest, "missing_rows": int(rval.isna().sum()),
                     "source_family": "historical_race_attribute" if name in base else
                     "race_relative_prior_inputs" if name in SEARCH_FEATURES else "strict_prior_event_history"})
    pit = generated_audit["point_in_time_audit"]
    return {"pass": bool(not affected.any() and pit["same_day_future_references"] == 0
                         and pit["future_leakage_rows"] == 0),
            "features_checked": len(rows), "rows_checked": len(right),
            "races_checked": int(right.index.get_level_values("race_id").nunique()),
            "mismatched_feature_rows": int(affected.sum()), "per_feature": rows,
            "actual_event_replay": True, "same_day_future_references": pit["same_day_future_references"],
            "future_leakage_rows": pit["future_leakage_rows"], "source_history_rows": len(event_history),
            "historical_t5_availability_verified": False,
            "interpretation": "Exact event-time replay is not evidence of historical HTTP receipt before post time.",
            "input_policy": "Per-race fields and immutable pedigree identity; no scraped prev_*, current lifetime totals, current results or odds as model inputs."}


def audit_lineage(edge_root, search_root, dataset_dir=None, *, reinfer=False,
                  event_history=None, history_audit=None, checkpoint=None):
    """Return lineage and PIT receipts; missing actual replay leaves the gate false."""
    from market_edge.data import _source
    notify = checkpoint or (lambda stage: None)
    edge, search = Path(edge_root).resolve(), Path(search_root).resolve()
    directory = Path(dataset_dir).resolve() if dataset_dir else search / "dataset"
    original_manifest = _json(edge / "artifact_manifest.json")["files"]
    provenance_path = _checked(edge / "provenance.json", original_manifest.get("provenance.json"))
    provenance = _json(provenance_path)
    data_path = _checked(edge / "development.parquet", original_manifest.get("development.parquet"))
    if provenance["artifact"]["sha256"] != sha256(data_path):
        raise ValueError("Original market cohort provenance mismatch")
    result, predictions_path, source = _source(search)
    if (provenance["source"]["result_sha256"] != source["result_sha256"]
            or provenance["source"]["predictions_sha256"] != source["predictions_sha256"]
            or provenance["source"]["folds"] != source["folds"]):
        raise ValueError("Market cohort lineage does not bind the original OOT source")
    manifest_path = _checked(directory / "dataset_manifest.json", result["manifest_sha256"])
    manifest = _json(manifest_path)
    source_data = _checked(directory / "dataset.parquet", result["dataset_sha256"])
    if manifest["dataset_sha256"] != result["dataset_sha256"]:
        raise ValueError("Original input dataset manifest mismatch")
    module_hashes = verify_feature_sources(manifest, result["source_contract"])
    features = result["feature_columns"]
    for fold in result["folds"]:
        validate_fold_contract(fold, features, result["dataset_sha256"])
    notify("read only 2021/2022 saved OOF and original market cohort")
    market = pd.read_parquet(data_path, columns=["race_id", "horse_number", "race_date", "horse_id", "fold_year", "h04_score"], use_threads=False)
    saved = pd.read_parquet(predictions_path, columns=["race_id", "horse_number", "race_date", "horse_id", "fold_year", "prediction"], use_threads=False)
    if not saved.race_date.astype(str).str[:4].isin(["2021", "2022"]).all():
        raise ValueError("Post-2022 source predictions are forbidden")
    mk, sk = _keyed(market), _keyed(saved)
    if not mk.index.isin(sk.index).all():
        raise ValueError("Market cohort runner absent from original OOF")
    selected = sk.loc[mk.index]
    if (not np.array_equal(selected.prediction.to_numpy(), mk.h04_score.to_numpy())
            or not selected.horse_id.astype(str).equals(mk.horse_id.astype(str))
            or not selected.race_date.astype(str).equals(mk.race_date.astype(str))
            or not selected.fold_year.equals(mk.fold_year)):
        raise ValueError("Original market H04 scores/identity do not match saved OOF")
    replay, feature_receipt = [], None
    if reinfer or event_history is not None:
        notify("read scoped frozen 99-feature inputs; do not read 2023+ outcomes")
        lower = "20160101" if event_history is not None else "20210101"
        inputs = pd.read_parquet(source_data, columns=["race_id", "race_date", "horse_number", *features],
                                 filters=[("race_date", ">=", lower), ("race_date", "<=", "20221231")], use_threads=False)
        if event_history is not None:
            if (not history_audit or history_audit.get("snapshot_sha256") != manifest["snapshot_sha256"]
                    or history_audit.get("actual_source_replay") is not True
                    or history_audit.get("scope") != ["20130101", "20221231"]):
                raise ValueError("Unverified source history replay receipt")
            feature_receipt = replay_event_features(event_history, inputs, features, checkpoint=notify)
        if reinfer:
            for fold in result["folds"]:
                year = fold["year"]
                replay.append(replay_fold_predictions(fold, inputs.loc[inputs.race_date.str.startswith(str(year))],
                    saved.loc[saved.fold_year.eq(year)], feature_columns=features,
                    dataset_sha256=result["dataset_sha256"], checkpoint=notify))
    pit_pass = bool(feature_receipt and feature_receipt["pass"])
    inference_pass = bool(len(replay) == 2 and all(item["pass"] for item in replay))
    unresolved = ([] if pit_pass else ["Actual 99-feature historical replay absent or inconsistent"])
    if not inference_pass:
        unresolved.append("Saved OOT bundle reinference absent or inconsistent")
    return {"schema": "market-h04-quality-lineage-v1", "lineage_pass": True,
            "original_market_races": int(market.race_id.nunique()), "original_market_runners": len(market),
            "original_oot_races": int(saved.race_id.nunique()), "original_oot_runners": len(saved),
            "market_scores_exactly_match_oot": True, "serving_model_used": False,
            "feature_count": len(features), "feature_columns": features, "verified_source_modules": module_hashes,
            "source": source, "source_snapshot_sha256": manifest["snapshot_sha256"],
            "market_provenance_sha256": sha256(provenance_path), "market_dataset_sha256": sha256(data_path),
            "reinference": replay, "reinference_pass": inference_pass,
            "point_in_time": feature_receipt, "point_in_time_pass": pit_pass,
            "lineage_and_event_time_ready": inference_pass and pit_pass,
            "historical_t5_availability_verified": False, "unresolved": unresolved,
            "post_2022_outcomes_read": False, "training_performed": False,
            "notes": ["2021/2022 were reused to select H04 configuration; OOT weights do not make them an unseen configuration holdout.",
                      "This component is not the complete MODEL_RESEARCH_READY gate: canonical quality, payouts, odds and runner audits are independent requirements."]}
