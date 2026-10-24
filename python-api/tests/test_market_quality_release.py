from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3

import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/verify_market_quality_release.py"
spec = importlib.util.spec_from_file_location("quality_release_under_test", SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), "utf-8")


def _seal(repo, quality):
    _write(quality / "artifact_manifest.json", {
        "schema": "market-h04-quality-artifacts-v1",
        "files": {str(path.relative_to(quality)): release.sha256(path)
                  for path in quality.rglob("*") if path.is_file() and path.name != "artifact_manifest.json"},
        "code": {"audit_module.py": release.sha256(repo / "audit_module.py")},
    })


@pytest.fixture
def prepared(tmp_path):
    repo, quality, worker = tmp_path, tmp_path / "quality-v2", tmp_path / "worker"
    quality.mkdir(); worker.mkdir()
    (repo / "audit_module.py").write_text("# frozen test audit", "utf-8")
    original_path = repo / "reports/generated/model-experiments/market-edge-v1/development.parquet"
    original_path.parent.mkdir(parents=True)
    original = pd.DataFrame([
        {"race_id": rid, "horse_number": n, "race_date": day, "horse_id": f"h{n}", "finish": n,
         "odds": 3.0 if n == 1 else 5.0}
        for rid, day in (("202105010101", "20210105"), ("202205010101", "20220105")) for n in (1, 2)])
    original.to_parquet(original_path, index=False)
    original.to_parquet(quality / "clean_runners.parquet", index=False)
    plan = {"original_cohort_sha256": release.sha256(original_path), "snapshot_sha256": "b" * 64,
            "scope": {"original_races": 2, "original_runners": 4}, "prediction_replay_tolerance": 1e-10}
    _write(quality / "audit_plan.json", plan)
    (worker / "audit_plan.json").write_bytes((quality / "audit_plan.json").read_bytes())
    plan_sha = release.sha256(worker / "audit_plan.json")
    feature_names = [f"safe_{x}" for x in range(99)]
    lineage = {
        "audit_plan_sha256": plan_sha, "source_snapshot_sha256": "b" * 64,
        "feature_columns": feature_names, "feature_count": 99, "lineage_pass": True,
        "point_in_time_pass": True, "reinference_pass": True, "lineage_and_event_time_ready": True,
        "point_in_time": {"features_checked": 99, "actual_event_replay": True,
                          "mismatched_feature_rows": 0, "same_day_future_references": 0, "future_leakage_rows": 0,
                          "per_feature": [{"feature": n, "mismatched_rows": 0, "max_absolute_difference": 0} for n in feature_names]},
        "reinference": [{"year": year, "pass": True, "mismatched_scores": 0, "max_absolute_score_difference": 0} for year in (2021, 2022)],
    }
    _write(quality / "lineage.json", lineage)
    result_path = worker / "lineage-job-result.json"
    _write(result_path, {"status": "completed", "training_performed": False, "lineage_and_event_time_ready": True,
                        "lineage_path": str(quality / "lineage.json"), "lineage_sha256": release.sha256(quality / "lineage.json")})
    command = ["python", "worker.py", "--plan", str(worker / "audit_plan.json"), "--plan-sha256", plan_sha,
               "--result", str(result_path)]
    with sqlite3.connect(worker / "job_ledger.sqlite") as db:
        db.executescript("CREATE TABLE meta(key TEXT,value TEXT); CREATE TABLE jobs(job_id TEXT,status TEXT,exit_code INTEGER,result_path TEXT,result_sha256 TEXT,command_json TEXT);")
        db.execute("INSERT INTO meta VALUES('plan_sha256',?)", (plan_sha,))
        db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?)", ("lineage-replay", "completed", 0, str(result_path), release.sha256(result_path), json.dumps(command)))
    races = original[["race_id", "race_date"]].drop_duplicates().copy()
    for field in ("runner_pass", "payout_pass", "acquisition_pass", "included_in_clean"):
        races[field] = True
    races["excluded_from_standard"], races["quality_class"] = False, "A CLEAN"
    for name, frame in (("race_quality_audit.csv", races), ("clean_races.csv", races), ("excluded_races.csv", races.iloc[:0]),
                        ("runner_integrity.csv", races[["race_id", "runner_pass"]]),
                        ("payout_audit.csv", races[["race_id", "payout_pass"]])):
        frame.to_csv(quality / name, index=False)
    (quality / "acquisition").mkdir()
    races[["race_id", "acquisition_pass"]].to_csv(quality / "acquisition/acquisition_audit.csv", index=False)
    odds = original[["race_id", "horse_number"]].copy()
    odds["odds_quality"] = "C"
    for field in ("invalid_odds", "market_probability_mismatch", "raw_implied_mismatch", "normalization_unavailable"):
        odds[field] = False
    odds.to_csv(quality / "odds_quality.csv", index=False)
    _write(quality / "quality_summary.json", {
        "MODEL_RESEARCH_READY": True, "blockers": [], "gate_checks": {"all_pass": True},
        "audit_plan_sha256": plan_sha, "original_races": 2, "original_runners": 4,
        "clean_races": 2, "clean_runners": 4, "excluded_races": 0,
        "clean_runners_sha256": release.sha256(quality / "clean_runners.parquet"),
        "clean_races_sha256": release.sha256(quality / "clean_races.csv"),
    })
    _seal(repo, quality)
    return repo, quality, worker


def test_full_release_and_no_quality_artifact_mutation(prepared):
    repo, quality, worker = prepared
    before = {p: release.sha256(p) for p in quality.rglob("*") if p.is_file()}
    result = release.verify_release(quality, worker, repo)
    assert result["released"], result["issues"]
    assert len(result["checks"]) == 4
    assert result["cohort"] == {"races": 2, "runners": 4, "excluded_races": 0}
    release.save_release(result, worker)
    assert all(release.sha256(p) == digest for p, digest in before.items())
    assert (worker / "quality-release.json").exists()
    with pytest.raises(FileExistsError): release.save_release(result, worker)


@pytest.mark.parametrize("kind", ["artifact", "code", "unlisted", "missingmanifest"])
def test_unsealed_or_modified_outputs_cannot_release(prepared, kind):
    repo, quality, worker = prepared
    if kind == "artifact": (quality / "lineage.json").write_text("{}")
    if kind == "code": (repo / "audit_module.py").write_text("changed")
    if kind == "unlisted": (quality / "unexpected.txt").write_text("unknown")
    if kind == "missingmanifest": (quality / "artifact_manifest.json").unlink()
    assert not release.verify_release(quality, worker, repo)["released"]


@pytest.mark.parametrize("column,value", [("status", "running"), ("exit_code", 1), ("result_sha256", "0" * 64)])
def test_ledger_completion_and_hash_binding(prepared, column, value):
    repo, quality, worker = prepared
    with sqlite3.connect(worker / "job_ledger.sqlite") as db:
        db.execute(f"UPDATE jobs SET {column}=?", (value,))
    result = release.verify_release(quality, worker, repo)
    assert not result["released"] and "ledger completion" in result["issues"][0]


def test_worker_result_tamper_not_hidden_by_quality_manifest(prepared):
    repo, quality, worker = prepared
    result = json.loads((worker / "lineage-job-result.json").read_text())
    result["lineage_sha256"] = "0" * 64
    _write(worker / "lineage-job-result.json", result)
    assert not release.verify_release(quality, worker, repo)["released"]


@pytest.mark.parametrize("change", ["missing_feature", "wrong_feature", "leakage"])
def test_even_resealed_evidence_requires_exact99_and_zero_leakage(prepared, change):
    repo, quality, worker = prepared
    lineage = json.loads((quality / "lineage.json").read_text())
    if change == "missing_feature": lineage["point_in_time"]["per_feature"].pop()
    if change == "wrong_feature": lineage["point_in_time"]["per_feature"][0]["feature"] = "wrong"
    if change == "leakage": lineage["point_in_time"]["same_day_future_references"] = 1
    _write(quality / "lineage.json", lineage)
    result = json.loads((worker / "lineage-job-result.json").read_text())
    result["lineage_sha256"] = release.sha256(quality / "lineage.json")
    _write(worker / "lineage-job-result.json", result)
    with sqlite3.connect(worker / "job_ledger.sqlite") as db:
        db.execute("UPDATE jobs SET result_sha256=?", (release.sha256(worker / "lineage-job-result.json"),))
    _seal(repo, quality)
    assert not release.verify_release(quality, worker, repo)["released"]


def test_mandatory_race_failure_cannot_stay_in_clean_even_resealed(prepared):
    repo, quality, worker = prepared
    part = pd.read_csv(quality / "payout_audit.csv")
    part.loc[0, "payout_pass"] = False
    part.to_csv(quality / "payout_audit.csv", index=False)
    _seal(repo, quality)
    assert not release.verify_release(quality, worker, repo)["released"]


def test_resealed_clean_value_change_is_not_quality_repair(prepared):
    repo, quality, worker = prepared
    clean = pd.read_parquet(quality / "clean_runners.parquet")
    clean.loc[0, "odds"] += 1
    clean.to_parquet(quality / "clean_runners.parquet", index=False)
    summary = json.loads((quality / "quality_summary.json").read_text())
    summary["clean_runners_sha256"] = release.sha256(quality / "clean_runners.parquet")
    _write(quality / "quality_summary.json", summary)
    _seal(repo, quality)
    assert not release.verify_release(quality, worker, repo)["released"]


def test_release_destination_must_remain_outside_quality_root(prepared):
    repo, quality, worker = prepared
    result = release.verify_release(quality, worker, repo)
    with pytest.raises(ValueError, match="outside"):
        release.save_release(result, quality)
