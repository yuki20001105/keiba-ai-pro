"""Synthetic no-fit tests for the independent sealed-output verifier."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3

import numpy as np
import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/verify_market_segments_outputs.py"
spec = importlib.util.spec_from_file_location("market_segments_output_verification_under_test", SCRIPT)
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def seal_result(root, job_id):
    with sqlite3.connect(root / "job_ledger.sqlite") as db:
        target = Path(db.execute("SELECT result_path FROM jobs WHERE job_id=?", (job_id,)).fetchone()[0])
        db.execute("UPDATE jobs SET result_sha256=? WHERE job_id=?", (verify.sha256(target), job_id))


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    repo, root = tmp_path, tmp_path / "campaign"
    root.mkdir()
    source = repo / "frozen.py"
    source.write_text("# immutable synthetic source", encoding="utf-8")
    recovery_worker = repo / verify.RECOVERY_WORKER
    recovery_worker.parent.mkdir()
    recovery_worker.write_text("# format-only synthetic recovery", encoding="utf-8")
    plan = {"fit_budget": 8, "code_sha256": {"frozen.py": verify.sha256(source)},
            "phase1_gate": {"quality_release_sha256": "a" * 64},
            "clean_cohort_sha256": "b" * 64, "snapshot_sha256": "c" * 64}
    write(root / "segment_plan.json", plan)
    plan_hash = verify.sha256(root / "segment_plan.json")
    frame = pd.DataFrame([
        {"race_id": rid, "race_date": day, "horse_number": number, "phase": phase,
         "normalized_market_probability": .5, "field_size": 2, "safe_value": 1.0,
         "finish": number, "h04_probability_fixed_reference": .8}
        for rid, day, phase in [("seed", "20210501", "calibration_seed"), ("train", "20210801", "meta_fit"),
                                ("dev", "20211001", "development"), ("val", "20220501", "validation")]
        for number in (1, 2)])
    (root / "data").mkdir()
    frame.to_parquet(root / "data/model_input.parquet", index=False)
    frame[["race_id"]].drop_duplicates().to_parquet(root / "data/race_segments.parquet", index=False)
    frame[verify.KEYS].to_parquet(root / "data/runner_segments.parquet", index=False)
    write(root / "data/data_contract.json", {"binding": {"phase1_gate": plan["phase1_gate"],
        "clean_runners_sha256": plan["clean_cohort_sha256"]}, "source_snapshot_sha256": plan["snapshot_sha256"]})
    write(root / "prepare-result.json", {"status": "completed", "files": {p.name: verify.sha256(p) for p in (root / "data").iterdir()}})
    (root / "diagnostics").mkdir()
    (root / "diagnostics/fixed.csv").write_text("schema-only\n", encoding="utf-8")
    write(root / "diagnostics-result.json", {"status": "completed", "diagnostics_before_training": True,
        "files": {"fixed.csv": verify.sha256(root / "diagnostics/fixed.csv")}})
    dataset_hash = verify.sha256(root / "data/model_input.parquet")
    feature_spec = {"dataset_sha256": dataset_hash, "groups": {
        "A": {"status": "available", "columns": []},
        "B": {"status": "available", "columns": ["normalized_market_probability", "field_size"]},
        "F": {"status": "available", "columns": ["normalized_market_probability", "field_size", "safe_value"]},
        "C": {"status": "unavailable"}}}
    write(root / "feature_spec.json", feature_spec)
    spec_hash = verify.sha256(root / "feature_spec.json")
    training = {**plan, "segment_plan_sha256": plan_hash, "dataset_sha256": dataset_hash,
                "feature_spec_sha256": spec_hash, "diagnostics_result_sha256": verify.sha256(root / "diagnostics-result.json")}
    write(root / "training_plan.json", training)
    training_hash = verify.sha256(root / "training_plan.json")
    with sqlite3.connect(root / "job_ledger.sqlite") as db:
        db.executescript("CREATE TABLE meta(key TEXT,value TEXT); CREATE TABLE jobs(job_id TEXT,status TEXT,exit_code INTEGER,started_at REAL,finished_at REAL,result_path TEXT,result_sha256 TEXT,command_json TEXT);")
        db.execute("INSERT INTO meta VALUES ('plan_sha256',?)", (plan_hash,))
        for index, stage in enumerate(("prepare", "diagnostics")):
            path = root / (stage + "-result.json")
            command = ["python", "analysis.py", "--plan", str(root / "segment_plan.json"),
                       "--expected-plan-sha256", plan_hash, "--stage", stage]
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)", (stage, "completed", 0, index * 2 + 1, index * 2 + 2,
                str(path), verify.sha256(path), json.dumps(command)))
        for job_id in verify.FIT_IDS:
            group, model, fold = job_id.split("-")
            folder = root / "jobs" / job_id
            columns = feature_spec["groups"][group]["columns"]
            train = frame.loc[frame.phase.isin(["meta_fit"] if fold == "development" else ["meta_fit", "development"])]
            view = frame.loc[frame.phase.eq(fold)]
            model_meta = {"model": model, "preprocessing": {"columns": columns, "fit_rows": len(train),
                "fit_date_from": train.race_date.min(), "fit_date_to": train.race_date.max()}}
            write(folder / "artifacts/model.json", model_meta)
            if model == "lightgbm":
                (folder / "artifacts/residual.txt").write_text("synthetic no-fit booster", encoding="utf-8")
            view[verify.KEYS].assign(probability=.5).to_parquet(folder / "predictions.parquet", index=False)
            result = {"status": "completed", "group": group, "model": model, "fold": fold,
                "provenance": {"plan_sha256": training_hash, "feature_spec_sha256": spec_hash, "dataset_sha256": dataset_hash},
                "evaluation_rows": len(view), "evaluation_races": view.race_id.nunique(), "train_rows": len(train),
                "train_races": train.race_id.nunique(), "train_date_from": train.race_date.min(), "train_date_to": train.race_date.max(),
                "evaluation_date_from": view.race_date.min(), "evaluation_date_to": view.race_date.max(),
                "train_cohort_sha256": verify._cohort_hash(train), "evaluation_cohort_sha256": verify._cohort_hash(view),
                "artifact_sha256": {str(p.relative_to(folder)).replace("\\", "/"): verify.sha256(p) for p in folder.rglob("*") if p.is_file()}}
            write(folder / "result.json", result)
            command = ["python", "worker.py", "--plan", str(root / "training_plan.json"), "--expected-plan-sha256", training_hash,
                "--expected-feature-spec-sha256", spec_hash, "--group", group, "--model", model, "--fold", fold]
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)", (job_id, "completed", 0, 5, 6, str(folder / "result.json"),
                verify.sha256(folder / "result.json"), json.dumps(command)))
        (root / "evaluation").mkdir()
        files = {}
        for name in verify.PRIMARY_TABLES:
            (root / "evaluation" / name).write_text("unchanged fixed primary output\n", encoding="utf-8")
            files[name] = verify.sha256(root / "evaluation" / name)
        conclusion = {"status": "completed", "new_model_fits": 8, "production_adoption": False, "files": files}
        write(root / "evaluation/conclusion.json", conclusion)
        write(root / "evaluate-result.json", conclusion)
        command = ["python", "analysis.py", "--plan", str(root / "segment_plan.json"), "--expected-plan-sha256", plan_hash, "--stage", "evaluate"]
        db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)", ("evaluate", "completed", 0, 7, 8, str(root / "evaluate-result.json"),
            verify.sha256(root / "evaluate-result.json"), json.dumps(command)))
    calls = []
    monkeypatch.setattr(verify, "_quality_check", lambda plan: None)
    monkeypatch.setattr(verify, "_protected_state", lambda repo: {"checked": True})

    def predict(view, artifacts):
        assert "finish" not in view and "h04_probability_fixed_reference" not in view
        assert "phase" not in view
        calls.append(str(artifacts))
        return np.full(len(view), .5)

    monkeypatch.setattr(verify, "_predict", predict)
    return repo, root, plan_hash, calls


def run(campaign):
    repo, root, plan_hash, _ = campaign
    return verify.verify_outputs(root, plan_hash, repo_root=repo)


def add_recovery(campaign):
    repo, root, plan_hash, _ = campaign
    (root / "evaluate-result.json").unlink()
    (root / "evaluation/conclusion.json").unlink()
    recovery = {"schema": "market-segments-evaluation-format-recovery-v1", "original_plan_sha256": plan_hash,
        "worker_sha256": verify.sha256(repo / verify.RECOVERY_WORKER), "preserve_failed_attempt": True,
        "original_partial_tables": {name: verify.sha256(root / "evaluation" / name) for name in verify.PRIMARY_TABLES},
        "output": str(root / "evaluation-recovery-v1")}
    write(root / "evaluation_recovery_plan.json", recovery)
    recovery_hash = verify.sha256(root / "evaluation_recovery_plan.json")
    out = root / "evaluation-recovery-v1"
    out.mkdir()
    for name in verify.PRIMARY_TABLES:
        shutil.copyfile(root / "evaluation" / name, out / name)
    for name in verify.RECOVERY_TABLES - verify.PRIMARY_TABLES:
        (out / name).write_text("fixed control schema adapted\n", encoding="utf-8")
    conclusion = {"status": "completed", "new_model_fits": 8, "production_adoption": False, "recovery_new_fits": 0,
        "recovery_plan_sha256": recovery_hash, "original_plan_sha256": plan_hash,
        "primary_evaluation_byte_identical": dict.fromkeys(verify.PRIMARY_TABLES, True),
        "files": {p.name: verify.sha256(p) for p in out.iterdir()}}
    write(out / "conclusion.json", conclusion)
    write(root / "evaluate-recovery-result.json", {**conclusion, "conclusion_sha256": verify.sha256(out / "conclusion.json"),
        "output_directory": str(out)})
    with sqlite3.connect(root / "job_ledger.sqlite") as db:
        db.execute("UPDATE jobs SET status='failed',exit_code=1,result_sha256=NULL WHERE job_id='evaluate'")
        command = ["python", str(repo / verify.RECOVERY_WORKER), "--execute", "--expected-recovery-sha256", recovery_hash]
        db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)", ("evaluate-recovery", "completed", 0, 9, 10,
            str(root / "evaluate-recovery-result.json"), verify.sha256(root / "evaluate-recovery-result.json"), json.dumps(command)))


def test_normal_complete_eight_model_replay_without_mutations(campaign):
    _, root, _, calls = campaign
    before = {p: verify.sha256(p) for p in root.rglob("*") if p.is_file()}
    result = run(campaign)
    assert result["verified"], result["issues"]
    assert result["completed_jobs"] == result["ledger_jobs"] == 11
    assert len(calls) == 8 and all(r["exactly_equal"] for r in result["replays"])
    assert "finish" not in result["decoded_data_columns"]
    assert before == {p: verify.sha256(p) for p in root.rglob("*") if p.is_file()}


def test_recovery_preserves_failure_and_primary_bytes(campaign):
    add_recovery(campaign)
    result = run(campaign)
    assert result["verified"], result["issues"]
    assert result["completed_jobs"] == 11 and result["ledger_jobs"] == 12
    assert result["preserved_failed_jobs"] == ["evaluate"]
    assert result["recovery_plan_sha256"] and len(result["replays"]) == 8


@pytest.mark.parametrize("target", ["frozen.py", "campaign/prepare-result.json", "campaign/diagnostics/fixed.csv",
    "campaign/jobs/B-logistic-development/artifacts/model.json", "campaign/segment_plan.json"])
def test_tampered_bound_source_or_artifact_fails(campaign, target):
    repo, _, _, calls = campaign
    with (repo / target).open("a", encoding="utf-8") as stream:
        stream.write(" ")
    assert not run(campaign)["verified"]
    assert not calls


@pytest.mark.parametrize("mutation", ["extra_job", "failed_fit", "skipped_fit", "invalid_order", "wrong_plan"])
def test_incomplete_or_expanded_ledger_fails(campaign, mutation):
    _, root, _, _ = campaign
    with sqlite3.connect(root / "job_ledger.sqlite") as db:
        if mutation == "extra_job":
            db.execute("INSERT INTO jobs(job_id) VALUES('extra-fit')")
        elif mutation == "invalid_order":
            db.execute("UPDATE jobs SET started_at=2 WHERE job_id='B-logistic-development'")
        elif mutation == "wrong_plan":
            db.execute("UPDATE meta SET value='wrong'")
        else:
            db.execute("UPDATE jobs SET status=? WHERE job_id='B-logistic-development'", (mutation.removesuffix("_fit"),))
    assert not run(campaign)["verified"]


@pytest.mark.parametrize("mutation", ["source", "primary", "new_fit", "success_old", "wrong_command", "unmanifested", "wrong_parent"])
def test_recovery_is_not_generic_failure_bypass(campaign, mutation):
    add_recovery(campaign)
    repo, root, _, _ = campaign
    if mutation == "source":
        (repo / verify.RECOVERY_WORKER).write_text("changed", encoding="utf-8")
    elif mutation == "primary":
        (root / "evaluation" / sorted(verify.PRIMARY_TABLES)[0]).write_text("changed", encoding="utf-8")
    elif mutation == "new_fit":
        value = read(root / "evaluate-recovery-result.json")
        value["recovery_new_fits"] = 1
        write(root / "evaluate-recovery-result.json", value)
        seal_result(root, "evaluate-recovery")
    elif mutation == "unmanifested":
        (root / "evaluation-recovery-v1/extra.csv").write_text("not declared", encoding="utf-8")
    elif mutation == "wrong_parent":
        value = read(root / "evaluation_recovery_plan.json")
        value["original_plan_sha256"] = "wrong"
        write(root / "evaluation_recovery_plan.json", value)
    else:
        with sqlite3.connect(root / "job_ledger.sqlite") as db:
            if mutation == "success_old":
                db.execute("UPDATE jobs SET status='completed',exit_code=0 WHERE job_id='evaluate'")
            else:
                db.execute("UPDATE jobs SET command_json='[]' WHERE job_id='evaluate-recovery'")
    assert not run(campaign)["verified"]


@pytest.mark.parametrize("mutation", ["missing", "extra", "wrong_probability", "future", "cohort_hash", "preprocess_date"])
def test_bound_but_inconsistent_model_output_fails(campaign, mutation):
    _, root, _, _ = campaign
    job_id = "B-logistic-development"
    folder = root / "jobs" / job_id
    result = read(folder / "result.json")
    if mutation in {"cohort_hash", "preprocess_date"}:
        if mutation == "cohort_hash":
            result["train_cohort_sha256"] = "wrong"
        else:
            model = read(folder / "artifacts/model.json")
            model["preprocessing"]["fit_date_to"] = "20220101"
            write(folder / "artifacts/model.json", model)
            result["artifact_sha256"]["artifacts/model.json"] = verify.sha256(folder / "artifacts/model.json")
    else:
        frame = pd.read_parquet(folder / "predictions.parquet")
        if mutation == "missing": frame = frame.iloc[:1]
        elif mutation == "extra": frame = pd.concat([frame, frame.iloc[:1]], ignore_index=True)
        elif mutation == "wrong_probability": frame.loc[0, "probability"] = .51
        elif mutation == "future": frame.loc[0, "race_date"] = "20230101"
        frame.to_parquet(folder / "predictions.parquet", index=False)
        result["artifact_sha256"]["predictions.parquet"] = verify.sha256(folder / "predictions.parquet")
    write(folder / "result.json", result)
    seal_result(root, job_id)
    assert not run(campaign)["verified"]


def test_tiny_float_difference_is_reported_not_claimed_exact(campaign, monkeypatch):
    monkeypatch.setattr(verify, "_predict", lambda frame, artifacts: np.full(len(frame), .5 + 1e-14))
    result = run(campaign)
    assert result["verified"], result["issues"]
    assert all(not item["exactly_equal"] and item["different_values"] == 2 and item["outside_tolerance"] == 0 for item in result["replays"])


@pytest.mark.parametrize("value", [1e-3, -1, float("nan"), float("inf")])
def test_no_permissive_tolerance(campaign, value):
    repo, root, plan_hash, _ = campaign
    assert not verify.verify_outputs(root, plan_hash, repo_root=repo, tolerance=value)["verified"]


def test_predictor_reader_rejects_post_2022_rows_and_wrong_phase(tmp_path):
    path = tmp_path / "input.parquet"
    frame = pd.DataFrame({"race_id": ["r1", "r1"], "horse_number": [1, 2], "race_date": ["20230101"] * 2,
                          "phase": ["validation"] * 2, "field_size": [2] * 2})
    frame.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="forbidden year"):
        verify._read_predictors(path, ["field_size"])
    frame["race_date"] = "20210101"
    frame.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="phase/date mismatch"):
        verify._read_predictors(path, ["field_size"])
    with pytest.raises(ValueError, match="outcome/reference"):
        verify._read_predictors(path, ["finish"])
