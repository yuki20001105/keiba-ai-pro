"""No-fit, no-metric verification of the sealed eight-fit segment campaign."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_name] = "2"
REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "python-api"), str(REPO / "keiba")]
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from threadpoolctl import threadpool_limits

FIT_IDS = [f"{g}-{m}-{f}" for g in ("B", "F") for m in ("logistic", "lightgbm")
           for f in ("development", "validation")]
KEYS = ["race_id", "horse_number", "race_date"]
PRIMARY_TABLES = {"all_segment_metrics.csv", "paired_day_bootstrap.csv", "segment_candidates.csv", "multiplicity.json"}
RECOVERY_TABLES = PRIMARY_TABLES | {"calibration_control_comparison.csv", "case_condition_comparison.csv"}
RECOVERY_WORKER = "scripts/run_market_edge_segments_finish.py"
PHASE_DATES = {"calibration_seed": ("20210101", "20210630"), "meta_fit": ("20210701", "20210930"),
               "development": ("20211001", "20211231"), "validation": ("20220101", "20221231")}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_text("utf-8"))


def require(value, message):
    if not value:
        raise ValueError(message)


def bound(directory, name):
    relative = Path(name.replace("\\", "/"))
    path = directory / relative
    require(not relative.is_absolute() and ".." not in relative.parts and not path.is_symlink(), "unsafe artifact path")
    target = path.resolve(strict=True)
    require(target.is_relative_to(directory.resolve()) and target.is_file(), "artifact escapes root")
    return target


def verify_files(directory, declared, *, required=()):
    names = {name.replace("\\", "/") for name in declared}
    require(set(required) <= names and len(names) == len(declared), "required artifact hash missing/duplicated")
    for name, value in declared.items():
        expected = value["sha256"] if isinstance(value, dict) else value
        require(sha256(bound(directory, name)) == expected, "artifact hash mismatch: " + name)
    return len(declared)


def _quality_check(plan):
    from market_segments.modeling import _quality_gate
    _quality_gate(plan["phase1_gate"])


def _protected_state(repo):
    """Hash old results/code without decoding historical evaluation outcomes."""
    old = repo / "reports/generated/model-experiments/market-edge-v1"
    original = read(old / "artifact_manifest.json")
    old_count = verify_files(old, original["files"])
    supplement = read(old / "supplement_manifest.json")
    require(sha256(old / "artifact_manifest.json") == supplement["original_manifest_sha256"], "old manifest changed")
    supplementary_count = verify_files(repo, supplement["repo_relative_files"])
    freeze = read(old / "research_freeze.json")
    source_count = verify_files(repo, freeze["source_sha256"])
    active = repo / "python-api/models/.active_model.json"
    track = repo / "reports/generated/forward-runtime/h04-live-20260920-v2"
    require(sha256(active) == freeze["active_model_sha256"], "active model pointer changed")
    require(sha256(track / "protocol.json") == freeze["track_a_protocol_file_sha256"], "Track A protocol changed")
    require(sha256(track / "analysis-definition.json") == freeze["track_a_analysis_file_sha256"], "Track A analysis changed")
    from market_edge.compute import CollectorMonitor
    health = CollectorMonitor(track).snapshot()
    require(health.get("healthy") is True, "Track A collector is not verified healthy: " + str(health.get("reasons")))
    return {"old_artifacts_checked": old_count, "old_supplement_files_checked": supplementary_count,
            "old_source_files_checked": source_count, "active_model_sha256": sha256(active),
            "track_a_protocol_sha256": sha256(track / "protocol.json"), "collector": health}


def _predict(frame, artifacts):
    from market_segments.modeling import predict_model
    with threadpool_limits(limits=2):
        return np.asarray(predict_model(frame, artifacts), dtype=float)


def _read_predictors(path, columns):
    # No finish, payout, speed-result labels or post-2022 rows are decoded.
    forbidden = {"finish", "time_seconds", "tansho_payout", "is_win", "label", "target", "h04_probability_fixed_reference"}
    require(not forbidden.intersection(columns), "outcome/reference column requested for replay")
    frame = pd.read_parquet(path, columns=list(dict.fromkeys([*KEYS, "phase", *columns])),
                            filters=[("race_date", ">=", "20210101"), ("race_date", "<=", "20221231")], use_threads=False)
    require(len(frame) == pq.read_metadata(path).num_rows, "dataset contains forbidden year rows")
    require(not frame.duplicated(["race_id", "horse_number"]).any(), "duplicate predictor key")
    require(frame.race_date.str.fullmatch(r"\d{8}").all() and frame.phase.isin(PHASE_DATES).all(), "invalid predictor phase/date")
    pd.to_datetime(frame.race_date, format="%Y%m%d", errors="raise")
    for phase, (start, end) in PHASE_DATES.items():
        require(frame.loc[frame.phase.eq(phase), "race_date"].between(start, end).all(), "predictor phase/date mismatch")
    grouped = frame.groupby("race_id", sort=False)
    require(grouped.race_date.nunique().eq(1).all() and grouped.phase.nunique().eq(1).all(), "race reused across phases/dates")
    require(grouped.size().eq(grouped.field_size.first()).all() and grouped.field_size.nunique().eq(1).all(),
            "incomplete predictor race")
    return frame


def _check_command(job, option, expected, *, path=False):
    command = json.loads(job["command_json"])
    require(command.count(option) == 1 and command.index(option) + 1 < len(command), "missing job argument: " + option)
    actual = command[command.index(option) + 1]
    require(Path(actual).resolve() == Path(expected).resolve() if path else actual == expected,
            "job argument binding mismatch: " + option)


def _ledger(directory, plan_hash, *, recovery=False):
    with sqlite3.connect((directory / "job_ledger.sqlite").as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        try:
            meta = db.execute("SELECT value FROM meta WHERE key='plan_sha256'").fetchone()
            rows = [dict(row) for row in db.execute("SELECT * FROM jobs ORDER BY job_id")]
        finally:
            db.rollback()
    require(meta is not None and meta[0] == plan_hash, "job ledger plan mismatch")
    expected = FIT_IDS + ["prepare", "diagnostics", "evaluate"] + (["evaluate-recovery"] if recovery else [])
    require({row["job_id"] for row in rows} == set(expected) and len(rows) == len(expected),
            "expected fixed analysis jobs and exactly eight fits")
    for row in rows:
        require(row["finished_at"] is not None and row["started_at"] is not None
                and row["started_at"] <= row["finished_at"], "job timestamps invalid")
        if recovery and row["job_id"] == "evaluate":
            require(row["status"] == "failed" and row["exit_code"] not in (None, 0)
                    and row["result_sha256"] is None, "original failed evaluation must remain failed")
            require(Path(row["result_path"]).resolve() == directory / "evaluate-result.json"
                    and not Path(row["result_path"]).exists(), "failed evaluation acquired a success result")
            continue
        require(row["status"] == "completed" and row["exit_code"] == 0, "job not completed: " + row["job_id"])
        path = Path(row["result_path"]).resolve(strict=True)
        require(path.is_relative_to(directory) and sha256(path) == row["result_sha256"], "ledger result hash/path mismatch")
    jobs = {row["job_id"]: row for row in rows}
    require(jobs["prepare"]["finished_at"] <= jobs["diagnostics"]["started_at"]
            and jobs["diagnostics"]["finished_at"] <= min(jobs[j]["started_at"] for j in FIT_IDS)
            and max(jobs[j]["finished_at"] for j in FIT_IDS) <= jobs["evaluate"]["started_at"], "preparation/diagnostics/fit/evaluation order violated")
    if recovery:
        require(jobs["evaluate"]["finished_at"] <= jobs["evaluate-recovery"]["started_at"], "recovery preceded failed evaluation")
    return jobs


def _result(directory, jobs, job_id, relative):
    path = bound(directory, relative)
    require(Path(jobs[job_id]["result_path"]).resolve() == path, "job result target mismatch")
    result = read(path)
    require(result.get("status") == "completed", "worker result not completed")
    return result


def _recovery_contract(directory, repo, plan_hash):
    path = directory / "evaluation_recovery_plan.json"
    if not path.exists():
        return None
    recovery = read(path)
    require(recovery.get("schema") == "market-segments-evaluation-format-recovery-v1"
            and recovery.get("original_plan_sha256") == plan_hash
            and recovery.get("preserve_failed_attempt") is True, "invalid recovery contract")
    require(recovery["worker_sha256"] == sha256(bound(repo, RECOVERY_WORKER)), "recovery worker changed")
    require(Path(recovery["output"]).resolve() == directory / "evaluation-recovery-v1", "recovery output changed")
    require(set(recovery["original_partial_tables"]) == PRIMARY_TABLES, "partial evaluation evidence differs")
    verify_files(directory / "evaluation", recovery["original_partial_tables"])
    return {"plan": recovery, "sha256": sha256(path)}


def _evaluation_result(directory, jobs, recovery, repo):
    if recovery is None:
        result = _result(directory, jobs, "evaluate", "evaluate-result.json")
        verify_files(directory / "evaluation", result["files"])
        conclusion = read(directory / "evaluation/conclusion.json")
        require(conclusion == result, "evaluation conclusion/result mismatch")
    else:
        result = _result(directory, jobs, "evaluate-recovery", "evaluate-recovery-result.json")
        job = jobs["evaluate-recovery"]
        _check_command(job, "--expected-recovery-sha256", recovery["sha256"])
        command = json.loads(job["command_json"])
        require(command.count("--execute") == 1 and len(command) >= 2
                and Path(command[1]).resolve() == (repo / RECOVERY_WORKER).resolve(), "unexpected recovery worker command")
        out = directory / "evaluation-recovery-v1"
        require(result["output_directory"] == str(out) and result["recovery_plan_sha256"] == recovery["sha256"]
                and result["original_plan_sha256"] == recovery["plan"]["original_plan_sha256"]
                and result.get("recovery_new_fits") == 0, "recovery receipt binding mismatch")
        require(set(result["files"]) == RECOVERY_TABLES, "recovery output set differs")
        verify_files(out, result["files"])
        require({p.name for p in out.iterdir() if p.is_file()} == RECOVERY_TABLES | {"conclusion.json"}, "unmanifested recovery output")
        require(sha256(out / "conclusion.json") == result["conclusion_sha256"], "recovery conclusion hash mismatch")
        conclusion = read(out / "conclusion.json")
        require({k: v for k, v in result.items() if k not in {"conclusion_sha256", "output_directory"}} == conclusion,
                "recovery conclusion/result mismatch")
        require(conclusion["primary_evaluation_byte_identical"] == dict.fromkeys(PRIMARY_TABLES, True), "recovery equality proof incomplete")
        require(all(sha256(out / name) == digest for name, digest in recovery["plan"]["original_partial_tables"].items()),
                "recovery altered primary evaluation")
    require(conclusion.get("new_model_fits") == 8 and conclusion.get("production_adoption") is False,
            "evaluation fit count or non-production contract differs")
    return result


def _cohort_hash(frame):
    keys = frame[["race_date", "race_id", "horse_number"]].astype(str).sort_values(["race_date", "race_id", "horse_number"])
    return hashlib.sha256(keys.to_csv(index=False, lineterminator="\n").encode()).hexdigest()


def verify_outputs(experiment_root, expected_plan_sha256, *, repo_root=REPO, tolerance=1e-12):
    """Only transform/predict; no fit, calibration, ranking/ROI/NLL calculation."""
    directory, repo = Path(experiment_root).resolve(), Path(repo_root).resolve()
    receipt = {"schema": "market-segments-output-verification-v1", "verified": False,
               "experiment_root": str(directory), "created_at": datetime.now(timezone.utc).isoformat(),
               "plan_sha256": expected_plan_sha256, "checks": {}, "issues": [], "replays": [],
               "source_script_sha256": sha256(Path(__file__)), "training_performed": False,
               "metrics_recomputed": False, "post_2022_outcomes_decoded": False}
    try:
        require(np.isfinite(tolerance) and 0 <= tolerance <= 1e-12, "unsafe score tolerance")
        plan_path = directory / "segment_plan.json"
        require(sha256(plan_path) == expected_plan_sha256, "frozen segment plan changed")
        plan = read(plan_path)
        require(plan.get("fit_budget") == 8, "fixed eight-fit budget required")
        code_count = verify_files(repo, plan["code_sha256"])
        _quality_check(plan)
        receipt["protected_before"] = _protected_state(repo)
        recovery = _recovery_contract(directory, repo, expected_plan_sha256)
        jobs = _ledger(directory, expected_plan_sha256, recovery=recovery is not None)
        results = {stage: _result(directory, jobs, stage, stage + "-result.json")
                   for stage in ("prepare", "diagnostics")}
        require(results["diagnostics"].get("diagnostics_before_training") is True, "missing pre-fit diagnostics")
        for stage, subfolder in (("prepare", "data"), ("diagnostics", "diagnostics"), ("evaluate", "evaluation")):
            _check_command(jobs[stage], "--plan", plan_path, path=True)
            _check_command(jobs[stage], "--expected-plan-sha256", expected_plan_sha256)
            _check_command(jobs[stage], "--stage", stage)
            required = ("model_input.parquet", "race_segments.parquet", "runner_segments.parquet", "data_contract.json") if stage == "prepare" else ()
            if stage != "evaluate":
                verify_files(directory / subfolder, results[stage]["files"], required=required)
        _evaluation_result(directory, jobs, recovery, repo)
        training, spec = read(directory / "training_plan.json"), read(directory / "feature_spec.json")
        training_hash, spec_hash = sha256(directory / "training_plan.json"), sha256(directory / "feature_spec.json")
        data_path, data_hash = directory / "data/model_input.parquet", sha256(directory / "data/model_input.parquet")
        require(all(training.get(key) == value for key, value in plan.items()), "training plan changed predeclared policy")
        require(training["segment_plan_sha256"] == expected_plan_sha256
                and training["feature_spec_sha256"] == spec_hash and training["dataset_sha256"] == data_hash
                and spec["dataset_sha256"] == data_hash
                and training["diagnostics_result_sha256"] == sha256(directory / "diagnostics-result.json"), "training input/diagnostic hash chain mismatch")
        contract = read(directory / "data/data_contract.json")
        require(contract["binding"]["phase1_gate"] == plan["phase1_gate"]
                and contract["binding"]["clean_runners_sha256"] == plan["clean_cohort_sha256"]
                and contract["source_snapshot_sha256"] == plan["snapshot_sha256"], "prepared quality/source binding mismatch")
        require({g for g, item in spec["groups"].items() if g != "A" and item.get("status") == "available"} == {"B", "F"}, "unexpected available fitted groups")
        columns = list(dict.fromkeys([*spec["groups"]["B"]["columns"], *spec["groups"]["F"]["columns"]]))
        predictors = _read_predictors(data_path, columns)
        require({p.name for p in (directory / "jobs").iterdir() if p.is_dir()} == set(FIT_IDS), "unregistered fitted-artifact directory")
        receipt["checks"]["plans_quality_and_completed_hash_chain"] = True
        for job_id in FIT_IDS:
            group, model, fold = job_id.split("-")
            folder = directory / "jobs" / job_id
            result = _result(directory, jobs, job_id, "jobs/" + job_id + "/result.json")
            require((result.get("group"), result.get("model"), result.get("fold")) == (group, model, fold), "fit result identity mismatch")
            provenance = result["provenance"]
            require(provenance["plan_sha256"] == training_hash and provenance["feature_spec_sha256"] == spec_hash
                    and provenance["dataset_sha256"] == data_hash, "fitted provenance changed")
            for option, value, is_path in (("--plan", directory / "training_plan.json", True),
                                          ("--expected-plan-sha256", training_hash, False),
                                          ("--expected-feature-spec-sha256", spec_hash, False),
                                          ("--group", group, False), ("--model", model, False), ("--fold", fold, False)):
                _check_command(jobs[job_id], option, value, path=is_path)
            required = ["predictions.parquet", "artifacts/model.json"]
            if model == "lightgbm": required.append("artifacts/residual.txt")
            verify_files(folder, result["artifact_sha256"], required=required)
            model_meta = read(folder / "artifacts/model.json")
            feature_columns = spec["groups"][group]["columns"]
            require(model_meta["model"] == model and model_meta["preprocessing"]["columns"] == feature_columns,
                    "model feature contract mismatch")
            view = predictors.loc[predictors.phase.eq(fold)].reset_index(drop=True)
            training_phases = ["meta_fit"] if fold == "development" else ["meta_fit", "development"]
            train = predictors.loc[predictors.phase.isin(training_phases)]
            require(len(view) == result["evaluation_rows"] and view.race_id.nunique() == result["evaluation_races"]
                    and len(train) == result["train_rows"] and train.race_id.nunique() == result["train_races"]
                    and str(train.race_date.min()) == result["train_date_from"]
                    and str(train.race_date.max()) == result["train_date_to"] < str(view.race_date.min())
                    and str(view.race_date.min()) == result["evaluation_date_from"]
                    and str(view.race_date.max()) == result["evaluation_date_to"], "fold date/count chronology differs")
            prep = model_meta["preprocessing"]
            require(prep["fit_rows"] == len(train) and prep["fit_date_from"] == str(train.race_date.min())
                    and prep["fit_date_to"] == str(train.race_date.max())
                    and result["train_cohort_sha256"] == _cohort_hash(train)
                    and result["evaluation_cohort_sha256"] == _cohort_hash(view), "preprocessing/cohort training boundary differs")
            saved_path = folder / "predictions.parquet"
            saved = pd.read_parquet(saved_path, columns=KEYS + ["probability"],
                filters=[("race_date", ">=", "20210101"), ("race_date", "<=", "20221231")], use_threads=False)
            require(len(saved) == pq.read_metadata(saved_path).num_rows and not saved.duplicated(KEYS[:2]).any(), "unscoped/duplicate saved predictions")
            require(saved[KEYS].sort_values(KEYS).reset_index(drop=True).equals(view[KEYS].sort_values(KEYS).reset_index(drop=True)), "extra/missing saved prediction keys")
            predicted = _predict(view[["race_id", *feature_columns]].copy(), folder / "artifacts")
            require(predicted.shape == (len(view),) and np.isfinite(predicted).all(), "incomplete/nonfinite model replay")
            reference = saved.set_index(KEYS).probability.reindex(pd.MultiIndex.from_frame(view[KEYS])).to_numpy(float)
            require(np.isfinite(reference).all(), "saved probabilities nonfinite")
            delta = abs(predicted - reference)
            item = {"job_id": job_id, "rows": len(view), "races": int(view.race_id.nunique()),
                    "exactly_equal": bool(np.array_equal(predicted, reference)), "maximum_absolute_difference": float(delta.max()),
                    "different_values": int((predicted != reference).sum()), "outside_tolerance": int((delta > tolerance).sum()),
                    "tolerance": tolerance, "fit_called": False}
            receipt["replays"].append(item)
            require(item["outside_tolerance"] == 0, "saved prediction/reinference disagreement: " + job_id)
        # Recheck the code, committed worker outputs, and protection after replay.
        verify_files(repo, plan["code_sha256"])
        require(_ledger(directory, expected_plan_sha256, recovery=recovery is not None) == jobs, "completed ledger changed during verification")
        require(_recovery_contract(directory, repo, expected_plan_sha256) == recovery, "recovery contract changed during verification")
        _evaluation_result(directory, jobs, recovery, repo)
        for stage, folder in (("prepare", "data"), ("diagnostics", "diagnostics")):
            verify_files(directory / folder, results[stage]["files"])
        for job_id in FIT_IDS:
            folder = directory / "jobs" / job_id
            verify_files(folder, read(folder / "result.json")["artifact_sha256"])
        require(sha256(data_path) == data_hash and sha256(directory / "training_plan.json") == training_hash
                and sha256(directory / "feature_spec.json") == spec_hash, "replay input contract changed during verification")
        _quality_check(plan)
        receipt["protected_after"] = _protected_state(repo)
        require(sha256(plan_path) == expected_plan_sha256, "plan changed during verification")
        receipt.update(verified=True, ledger_jobs=len(jobs), completed_jobs=sum(j["status"] == "completed" for j in jobs.values()),
                       preserved_failed_jobs=[key for key, j in jobs.items() if j["status"] == "failed"],
                       recovery_plan_sha256=recovery["sha256"] if recovery else None, verified_fit_count=8, frozen_source_files=code_count,
                       dataset_sha256=data_hash, training_plan_sha256=training_hash, feature_spec_sha256=spec_hash,
                       result_sha256={key: jobs[key]["result_sha256"] for key in jobs},
                       decoded_data_columns=[*KEYS, "phase", *columns])
        receipt["checks"]["eight_saved_models_reinfer_without_fit"] = True
        receipt["checks"]["old_artifacts_active_model_and_Track_A_unchanged"] = True
        if recovery:
            receipt["checks"]["failed_evaluation_preserved_and_four_primary_outputs_byte_identical"] = True
            receipt["recovery_worker_sha256"] = recovery["plan"]["worker_sha256"]
            receipt["recovery_primary_output_sha256"] = recovery["plan"]["original_partial_tables"]
    except (ValueError, TypeError, KeyError, OSError, AssertionError, sqlite3.Error) as exc:
        receipt["issues"].append(type(exc).__name__ + ": " + str(exc))
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment-root", type=Path, required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    args = parser.parse_args()
    if os.name == "nt":
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    result = verify_outputs(args.experiment_root, args.expected_plan_sha256)
    with (args.experiment_root / "artifact-verification.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["verified"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
