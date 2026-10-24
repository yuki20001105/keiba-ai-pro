"""Independent, outcome-blind verification of completed Track B artifacts.

Only the final verification receipt is written, exclusively. No training,
promotion, Track A mutation, labels/payout reads or confirmation-period data.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys

for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[name] = "2"
REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "python-api"), str(REPO / "keiba")]
EXPECTED = {
    "logistic-dev": ("logistic", "cpu", "development"), "logistic-val": ("logistic", "cpu", "validation"),
    "market-control-dev": ("market_logistic", "cpu", "development"), "market-control-val": ("market_logistic", "cpu", "validation"),
    "lightgbm-dev": ("lightgbm", "cpu", "development"),
    **{f"benchmark-{device}-{repeat}": ("lightgbm", device, "validation") for device in ("cpu", "gpu") for repeat in range(2)},
}
FEATURES = ["normalized_market_probability", "log_odds", "market_rank", "h04_score", "h04_rank",
            "h04_probability", "rank_difference", "probability_difference", "field_size"]
MARKET_FEATURES = ["normalized_market_probability", "log_odds", "market_rank", "field_size"]
SAFE_COLUMNS = ["race_id", "race_date", "horse_number", "phase", "expected_runner_count"] + FEATURES
TOLERANCE = 1e-10


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _within(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("artifact-outside-research-root")
    return path


def verify_freeze(root, repo, collector):
    freeze_path = root / "research_freeze.json"
    freeze = read(freeze_path)
    paths = {str(p.relative_to(repo)).replace("\\", "/") for p in (repo / "python-api/market_edge").glob("*.py")}
    paths |= {"scripts/run_market_edge.py", "scripts/run_market_edge_worker.py"}
    declared = {name.replace("\\", "/"): digest for name, digest in freeze["source_sha256"].items()}
    if paths != set(declared):
        raise ValueError("frozen-research-source-set-changed")
    for relative, expected in declared.items():
        for path in (_within(repo/relative, repo), _within(root/"code_snapshot"/relative, root)):
            if sha(path) != expected:
                raise ValueError("research-source-or-archive-changed:" + relative)
    checks = {"experiment_plan.json": freeze["plan_sha256"], "development.parquet": freeze["dataset_sha256"]}
    for relative, expected in checks.items():
        if sha(root/relative) != expected:
            raise ValueError("research-plan-or-data-changed")
    for path, key in ((repo/"python-api/models/.active_model.json", "active_model_sha256"),
                      (collector/"protocol.json", "track_a_protocol_file_sha256"),
                      (collector/"analysis-definition.json", "track_a_analysis_file_sha256")):
        if sha(path) != freeze[key]:
            raise ValueError("protected-track-A-or-active-model-changed")
    return freeze, sha(freeze_path)


def read_ledger(root):
    db = sqlite3.connect((root/"job_ledger.sqlite").resolve().as_uri()+"?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON"); db.execute("BEGIN")
        rows = [dict(r) for r in db.execute("SELECT * FROM jobs ORDER BY job_id")]
        plan = db.execute("SELECT value FROM meta WHERE key='plan_sha256'").fetchone()[0]
        return rows, plan
    finally:
        db.rollback(); db.close()


def validate_frame(frame):
    import numpy as np
    import pandas as pd
    if frame.empty or list(frame.columns) != SAFE_COLUMNS or frame["race_id"].isna().any():
        raise ValueError("nonempty-outcome-free-dataset-required")
    if frame.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("duplicate-dataset-runner")
    if not frame["race_date"].astype(str).str.fullmatch(r"202[12]\d{4}").all():
        raise ValueError("only-2021-2022-features-permitted")
    pd.to_datetime(frame["race_date"], format="%Y%m%d", errors="raise")
    if not set(frame.phase).issubset({"calibration_seed", "meta_fit", "development", "validation"}):
        raise ValueError("unrecognized-temporal-phase")
    ranges = {"calibration_seed": ("20210101", "20210630"), "meta_fit": ("20210701", "20210930"),
              "development": ("20211001", "20211231"), "validation": ("20220101", "20221231")}
    for phase, group in frame.groupby("phase"):
        if not group.race_date.astype(str).between(*ranges[phase]).all():
            raise ValueError("phase-date-mismatch")
    grouped = frame.groupby("race_id", sort=False)
    counts = grouped.race_id.transform("size")
    n = pd.to_numeric(frame.horse_number, errors="raise")
    if (counts.lt(2).any() or counts.gt(18).any() or not n.between(1, 18).all() or not n.eq(np.floor(n)).all()
            or not frame.expected_runner_count.eq(counts).all() or not frame.field_size.eq(counts).all()
            or grouped[["race_date", "phase"]].nunique(dropna=False).ne(1).any().any()):
        raise ValueError("incomplete-or-inconsistent-race-cohort")
    # Calibration-fit seed rows deliberately have no in-sample H04 probability.
    # They are neither residual-model training rows nor prediction rows.
    if not np.isfinite(frame.loc[~frame.phase.eq("calibration_seed"), FEATURES].to_numpy(float)).all():
        raise ValueError("nonfinite-frozen-features")


def verify_job(root, frame, spec, row, *, predictor):
    import numpy as np
    import pandas as pd
    if row["status"] != "completed" or row["exit_code"] != 0 or row["finished_at"] is None:
        raise ValueError("all-jobs-must-be-completed")
    if json.loads(row["spec_json"]) != spec or json.loads(row["command_json"]) != spec["command"]:
        raise ValueError("ledger-specification-mismatch")
    if (row["model"], row["device"], row["fold"]) != (spec["model"], spec["device"], spec["fold"]):
        raise ValueError("ledger-job-identity-mismatch")
    result_path = _within(row["result_path"], root)
    if result_path != Path(spec["result_path"]).resolve() or result_path.parent != root/"jobs"/spec["job_id"]:
        raise ValueError("result-path-job-binding-mismatch")
    if sha(result_path) != row["result_sha256"]:
        raise ValueError("completed-job-result-sha-mismatch")
    folder = result_path.parent
    result, model = read(result_path), read(folder/"model.json")
    for key in ("model", "device", "fold"):
        if result[key] != spec[key]:
            raise ValueError("result-job-binding-mismatch")
    if model["model"] != spec["model"] or model["device"] != spec["device"]:
        raise ValueError("model-job-binding-mismatch")
    features = MARKET_FEATURES if spec["model"] == "market_logistic" else FEATURES
    if model["features"] != features or result["feature_count"] != len(features) or result["rounds"] != 150:
        raise ValueError("fixed-feature-or-round-contract-mismatch")
    if spec["model"] == "lightgbm" and model["rounds"] != 150:
        raise ValueError("fixed-lightgbm-round-contract-mismatch")
    train_phases = ["meta_fit"] if spec["fold"] == "development" else ["meta_fit", "development"]
    train = frame.loc[frame.phase.isin(train_phases)]
    expected = frame.loc[frame.phase.eq(spec["fold"])].reset_index(drop=True)
    if train.empty or expected.empty or train.race_date.max() >= expected.race_date.min():
        raise ValueError("chronological-fit-prediction-overlap")
    if (model["train_rows"] != len(train) or model["train_races"] != train.race_id.nunique()
            or model["train_date_from"] != train.race_date.min() or model["train_date_to"] != train.race_date.max()
            or result["train_rows"] != len(train) or result["evaluation_rows"] != len(expected)):
        raise ValueError("training-or-evaluation-cohort-metadata-mismatch")
    paths = {name: folder/name for name in ("result.json", "model.json", "predictions.parquet")}
    if spec["model"] == "lightgbm":
        paths["residual.txt"] = folder/"residual.txt"
    hashes = {name: sha(path) for name, path in paths.items()}
    predictions = pd.read_parquet(paths["predictions.parquet"])
    if set(predictions) != {"race_id", "horse_number", "probability"} or predictions.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("invalid-or-duplicate-prediction-keys")
    joined = expected[["race_id", "horse_number"]].merge(predictions, how="outer", on=["race_id", "horse_number"], validate="one_to_one", indicator=True, sort=False)
    if len(joined) != len(expected) or not joined._merge.eq("both").all():
        raise ValueError("missing-or-extra-prediction-runner")
    predicted = joined.probability.to_numpy(float)
    if not np.isfinite(predicted).all() or (predicted <= 0).any() or (predicted > 1).any():
        raise ValueError("invalid-saved-probabilities")
    sums = joined.groupby("race_id").probability.sum()
    if float(abs(sums-1).max()) > TOLERANCE:
        raise ValueError("incomplete-probability-simplex")
    # Outer join checks exact sets; this second left join explicitly preserves
    # input order rather than relying on pandas outer-merge ordering.
    ordered = expected[["race_id", "horse_number"]].merge(predictions, how="left", on=["race_id", "horse_number"], validate="one_to_one", sort=False)
    recomputed = np.asarray(predictor(expected.copy(), folder), dtype=float)
    if recomputed.shape != (len(expected),) or not np.isfinite(recomputed).all():
        raise ValueError("invalid-recomputed-probabilities")
    delta = float(np.max(np.abs(recomputed-ordered.probability.to_numpy(float))))
    if delta > TOLERANCE:
        raise ValueError("stored-model-prediction-mismatch")
    if hashes != {name: sha(path) for name, path in paths.items()}:
        raise ValueError("job-artifact-changed-during-verification")
    return {"job_id": spec["job_id"], "model": spec["model"], "device": spec["device"], "fold": spec["fold"],
            "status": "verified", "rows": len(expected), "races": int(expected.race_id.nunique()),
            "feature_count": len(features), "prediction_max_absolute_difference": delta,
            "max_probability_sum_error": float(abs(sums-1).max()),
            "artifact_sha256": {str(paths[k].relative_to(root)).replace("\\", "/"): v for k, v in hashes.items()}}


def verify_outputs(root, *, repo=REPO, collector=None):
    import pandas as pd
    root, repo = Path(root).resolve(), Path(repo).resolve()
    _within(root, repo/"reports/generated/model-experiments")
    collector = Path(collector or repo/"reports/generated/forward-runtime/h04-live-20260920-v2").resolve()
    freeze, freeze_sha = verify_freeze(root, repo, collector)
    rows, plan_sha = read_ledger(root)
    compute_path = root/"compute_plan.json"; compute_sha = sha(compute_path)
    specs = read(compute_path)["jobs"]
    if {r["job_id"] for r in rows} != set(EXPECTED) or {r["job_id"] for r in specs} != set(EXPECTED) or len(rows) != 9 or len(specs) != 9:
        raise ValueError("exact-nine-predeclared-jobs-required")
    if plan_sha != freeze["plan_sha256"]:
        raise ValueError("ledger-experiment-plan-mismatch")
    for spec in specs:
        if (spec["model"], spec["device"], spec["fold"]) != EXPECTED[spec["job_id"]] or Path(spec["dataset"]).resolve() != root/"development.parquet":
            raise ValueError("predeclared-job-identity-mismatch")
    if any(r["status"] != "completed" for r in rows):
        raise ValueError("wait-for-all-nine-completed-jobs")
    frame = pd.read_parquet(root/"development.parquet", columns=SAFE_COLUMNS)
    validate_frame(frame)
    from market_edge.modeling import predict_model
    by_id = {r["job_id"]: r for r in rows}
    jobs = [verify_job(root, frame, spec, by_id[spec["job_id"]], predictor=predict_model) for spec in specs]
    if verify_freeze(root, repo, collector)[1] != freeze_sha or sha(compute_path) != compute_sha or read_ledger(root) != (rows, plan_sha):
        raise ValueError("freeze-or-ledger-changed-during-verification")
    return {"schema": "independent-market-edge-artifact-verification-v1", "verified_at": datetime.now(timezone.utc).isoformat(),
            "status": "verified", "research_root": str(root), "research_freeze_sha256": freeze_sha,
            "plan_sha256": freeze["plan_sha256"], "dataset_sha256": freeze["dataset_sha256"], "compute_plan_sha256": compute_sha,
            "verifier_source_sha256": sha(Path(__file__)), "jobs_verified": len(jobs), "jobs": jobs,
            "comparison_tolerance": TOLERANCE, "labels_or_payouts_loaded": False, "later_2023_plus_data_loaded": False,
            "model_fit_performed": False, "model_promotion_performed": False, "track_a_changed": False,
            "limitation": "Post-training independent content binding and deterministic prediction replay; not proof of cryptographically signed training provenance."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=REPO/"reports/generated/model-experiments/market-edge-v1")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if os.name == "nt":
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    report = verify_outputs(args.root)
    if not args.check_only:
        path = args.root/"job_artifact_verification.json"
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False); stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
    print(json.dumps({"status": report["status"], "jobs_verified": report["jobs_verified"],
                      "max_probability_difference": max(j["prediction_max_absolute_difference"] for j in report["jobs"]),
                      "receipt_written": not args.check_only}, indent=2))


if __name__ == "__main__":
    main()
