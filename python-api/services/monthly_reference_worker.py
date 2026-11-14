"""Isolated inference using the study's archived feature and calibration code."""
from pathlib import Path
import sys


def worker(request_path, response_path):
    import joblib

    request = joblib.load(request_path)
    root, snapshot = Path(request["root"]), Path(request["snapshot"])
    sys.path[:0] = [str(snapshot / "python-api"), str(snapshot / "keiba"),
                    str(root / "python-api"), str(root / "keiba")]
    try:
        from services.monthly_reference_input import load_published_bundle, diagnostics
        from training import db_shared_features as shared, db_shared_optimized as optimized
        from training.monthly_validation import build_holdout_features
        from threadpoolctl import threadpool_limits
        import numpy as np

        bundle = load_published_bundle(request["model_path"])
        if bundle["_shared_artifact_sha256"] != request["artifact_sha256"]:
            raise ValueError("monthly-reference-artifact-changed-before-worker")
        current = shared._current_card(request["race_info"], request["horses"])
        with threadpool_limits(limits=request["n_threads"]):
            with shared._database(request["db_path"]) as (connection, tables):
                history, _ = shared._load_history(
                    connection, tables, current, str(current.race_date.iloc[0]), shared._audit(), strict=True)
            if bundle["split_kind"] == "monthly_random":
                withheld = set(bundle["partition_membership"]["CAL"] + bundle["partition_membership"]["TEST"])
                raw_features = build_holdout_features(current, history, withheld)
            else:
                raw_features = shared.build_training_features(current, history)
            features = bundle["encoder"].transform(optimized.select_features(raw_features))
            raw = optimized._finite_predictions(bundle["model"], features, request["n_threads"])
            probabilities = bundle["calibrator"].predict_proba(raw, current.race_id)
        if not np.isfinite(probabilities).all() or not np.isclose(probabilities.sum(), 1):
            raise ValueError("monthly-reference-invalid-probabilities")
        result = {"features": features, "raw": raw, "probabilities": probabilities,
                  "calibrator_applied": True, "artifact_sha256": request["artifact_sha256"],
                  "diagnostics": diagnostics(bundle, missing_value_rate=float(features.isna().to_numpy().mean()),
                                              no_history_rows=int(raw_features.safe_prior_starts.eq(0).sum()))}
    except Exception as error:
        result = {"error": str(error)}
    joblib.dump(result, response_path)


if __name__ == "__main__":
    worker(sys.argv[1], sys.argv[2])
