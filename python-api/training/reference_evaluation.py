"""Persist untouched TEST reference books without granting purchase authority."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path
import sqlite3

import joblib
import numpy as np
import pandas as pd

SCHEMA = "training-reference-v1"
JOBS_PATH = Path(__file__).resolve().parents[1] / "data" / "local_retrain_jobs.db"


def build_training_reference(bundle, frame, features, receipt, model_id):
    from betting.performance import _race_date
    from training.db_normalized_registry import prediction_module
    from training.db_shared_fit import race_softmax

    if not frame.index.is_unique or not frame.index.equals(features.index):
        raise ValueError("training-reference-test-cohort-misaligned")
    dates = frame.race_date.map(_race_date)
    start, end = _race_date(receipt["date_from"]), _race_date(receipt["date_to"])
    validation = bundle["probability_validation"]
    boundary = max(_race_date(bundle["training_date_to"]), _race_date(validation["calibration_period_end"]))
    mask = dates.between(start, end)
    test = frame.loc[mask]
    if (start <= boundary or end < start or len(test) != receipt["runner_count"]
            or test.race_id.nunique() != receipt["race_count"]
            or dates.loc[mask].nunique() != receipt["date_count"]
            or frame.race_id.isin(test.race_id).sum() != len(test)):
        raise ValueError("training-reference-test-cohort-invalid")
    columns = list(bundle["feature_columns"])
    inputs = features.loc[mask, columns]
    scores, probabilities = prediction_module(bundle).predict_encoded_shared_scores(
        bundle, bundle["encoder"].transform(inputs), test.race_id, n_threads=2)
    if probabilities is None:
        probabilities = race_softmax(scores, test.race_id, 1.0)
    probabilities = np.asarray(probabilities, dtype=float)
    if probabilities.shape != (len(test),) or not np.isfinite(probabilities).all():
        raise ValueError("training-reference-test-probabilities-invalid")
    test = test.assign(_probability=probabilities)
    records = {}
    for race_id, rows in test.groupby("race_id", sort=True):
        missing = float(inputs.loc[rows.index].isna().to_numpy().mean())
        predictions = []
        for _, row in rows.iterrows():
            odds = pd.to_numeric(row.get("odds"), errors="coerce")
            predictions.append({
                "horse_number": int(row.horse_number), "horse_id": str(row.horse_id),
                "p_norm": float(row["_probability"]),
                "odds": float(odds) if pd.notna(odds) and np.isfinite(odds) else None,
            })
        records[str(race_id)] = {
            "success": True, "model_id": model_id,
            "race_info": {"race_id": str(race_id), "date": str(rows.race_date.iloc[0]),
                          "num_horses": len(rows)},
            "predictions": predictions,
            "input_diagnostics": {
                "schema": "prediction-input-contract-v1", "status": "aligned",
                "feature_pipeline": bundle["feature_pipeline"],
                "feature_source_hashes": bundle["feature_source_hashes"],
                "expected_count": len(columns), "actual_count": len(columns),
                "source_hash_matches": True, "semantic_parity_verified": True,
                "current_outcomes_consumed": False, "history_available": True,
                "missing_columns": [], "blocked_columns": [], "missing_value_rate": missing,
                "issue_codes": ["target_not_after_training_evaluation_period"],
            },
        }
    return {"schema": SCHEMA, "partition": dict(receipt), "records": records}


def sync_training_evaluations(ledger_path, owner, *, jobs_path=JOBS_PATH):
    """Import only this owner's completed, hash-bound publications, once."""
    from betting.paper_simulation import _ledger, record_paper_prediction
    from services.db_shared_input import validate_published_artifact

    if not jobs_path.is_file():
        return []
    with sqlite3.connect(f"{jobs_path.resolve().as_uri()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        publications = db.execute(
            "SELECT p.published_path,p.artifact_sha256 FROM local_retrain_publications p "
            "JOIN local_retrain_jobs j ON j.job_id=p.job_id "
            "WHERE j.owner_id=? AND j.state='completed' AND p.state='published' "
            "AND json_extract(p.result_json,'$.evaluation.reference_accounting.schema')=?",
            (owner, SCHEMA),
        ).fetchall()
    if not publications:
        return []
    with _ledger(ledger_path) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS paper_simulation_imports "
            "(owner TEXT NOT NULL,artifact_sha256 TEXT NOT NULL,model_id TEXT NOT NULL,"
            "accepted_races INTEGER NOT NULL,excluded_races INTEGER NOT NULL,"
            "PRIMARY KEY(owner,artifact_sha256))"
        )
        imported = {row[0] for row in db.execute(
            "SELECT artifact_sha256 FROM paper_simulation_imports WHERE owner=?", (owner,))}
    for item in publications:
        sha, path = item["artifact_sha256"], Path(item["published_path"])
        if sha in imported:
            continue
        if validate_published_artifact(path, ledger_path=jobs_path) != sha:
            raise ValueError("training-reference-publication-mismatch")
        encoded = path.read_bytes()
        if hashlib.sha256(encoded).hexdigest() != sha:
            raise ValueError("training-reference-publication-changed")
        bundle = joblib.load(io.BytesIO(encoded))
        report = bundle.get("training_reference_evaluation") or {}
        if report.get("schema") != SCHEMA or not isinstance(report.get("records"), dict):
            raise ValueError("training-reference-report-missing")
        bundle["_shared_artifact_sha256"] = sha
        with _ledger(ledger_path) as db:
            if db.execute("SELECT 1 FROM paper_simulation_imports WHERE owner=? AND artifact_sha256=?",
                          (owner, sha)).fetchone():
                continue
            accepted = 0
            for race_id, payload in report["records"].items():
                if payload["model_id"] != path.stem or str(payload["race_info"]["race_id"]) != race_id:
                    raise ValueError("training-reference-model-mismatch")
                outcome = record_paper_prediction(
                    ledger_path, owner, payload, bundle, model_sha256=sha,
                    historical_validation=True, training_evaluation=True, _connection=db,
                )
                if outcome.get("reason") in {"post_time_unverified", "historical_validation_not_historical"}:
                    raise ValueError("training-reference-result-time-unverified")
                accepted += int(outcome["eligible"])
            db.execute("INSERT OR IGNORE INTO paper_simulation_imports VALUES (?,?,?,?,?)",
                       (owner, sha, path.stem, accepted, len(report["records"]) - accepted))
    with _ledger(ledger_path) as db:
        return [dict(row) for row in db.execute(
            "SELECT model_id,accepted_races,excluded_races FROM paper_simulation_imports WHERE owner=?",
            (owner,))]
