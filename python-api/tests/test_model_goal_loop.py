from __future__ import annotations

import copy
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from experiments import cli, goal_loop as goal
from experiments.common import file_sha256, read_json, write_json
from experiments.goal_evidence import compare_records, verify_trial


def tiny_source(path: Path) -> Path:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE races_ultimate(race_id TEXT PRIMARY KEY, data TEXT)")
        connection.execute("CREATE TABLE race_results_ultimate(race_id TEXT, data TEXT)")
        import json
        for year in (2020, 2021, 2022, 2023, 2024, 2025, 2026):
            for day in range(1, 5):
                race_id = f"{year}0501{day:02d}01"
                meta = {
                    "date": f"{year}01{day:02d}", "distance": 1200, "track_type": "芝",
                    "venue": "東京", "num_horses": 4, "race_class": "未勝利",
                }
                connection.execute("INSERT INTO races_ultimate VALUES (?,?)",
                                   (race_id, json.dumps(meta)))
                for runner in range(1, 5):
                    row = {
                        "horse_id": str(100 + runner), "horse_number": runner, "finish": runner,
                        "time_seconds": 70 + runner * 2 + day / 10, "age": 3 + runner % 2,
                        "odds": 2.0 * runner, "popularity": runner,
                        "burden_weight": 55 + runner % 2, "horse_weight": 450 + runner * 10,
                    }
                    connection.execute("INSERT INTO race_results_ultimate VALUES (?,?)",
                                       (race_id, json.dumps(row)))
    return path


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    base = tmp_path / "campaigns"
    root = base / "tiny"
    monkeypatch.setattr(goal, "BASE", base)
    config = read_json(goal.DEFAULT_CONFIG)
    config.update({
        "max_proposals": 1, "max_trials_per_proposal": 1, "num_boost_round": 3,
        "bootstrap_samples": 5, "minimum_paired_dates": 2,
        "max_seconds": 120, "estimated_worker_memory_mb": 256, "reserve_ram_mb": 256,
    })
    source = tiny_source(tmp_path / "source.db")
    goal.prepare(root, source, config)
    return root, config, source


def inline_batch(root, payloads, **_kwargs):
    from experiments.goal_training import run_goal_trial
    store = cli.store_for(root)
    existing = {row["trial_id"] for row in store.snapshot(root.name)["trials"]}
    for payload in payloads:
        if payload["id"] not in existing:
            store.enqueue(root.name, payload["id"], {
                **payload, "experiment_contract_sha256": store.snapshot(root.name)["contract_sha256"],
            }, "cpu", memory_mb=256)
    if any(row["status"] == "queued" for row in store.snapshot(root.name)["trials"]):
        assert cli.worker(root, "cpu", trial_runner=run_goal_trial) == 0
    return {row["trial_id"]: row["result"] for row in store.snapshot(root.name)["trials"]}


@pytest.mark.parametrize("change", [
    {"max_proposals": 4}, {"max_total_trials": True}, {"minimum_improvement": 0},
    {"development_years": [2025, 2023]}, {"target": "catboost"},
    {"maximum_top1_degradation": float("nan")}, {"unknown": 1},
])
def test_fixed_goal_config_rejects_unsafe_or_unbounded_settings(change):
    config = {**read_json(goal.DEFAULT_CONFIG), **change}
    with pytest.raises(ValueError):
        goal.validate_config(config)


def test_goal_requires_bound_approval_and_never_changes_source(campaign, monkeypatch):
    root, _, source = campaign
    before = file_sha256(source)
    monkeypatch.setattr(cli, "batch", inline_batch)
    verifications = []
    def track_verification(directory, row, task):
        verifications.append(row["trial_id"])
        return verify_trial(directory, row, task)
    monkeypatch.setattr(goal, "verify_trial", track_verification)
    with pytest.raises(FileNotFoundError):
        goal.run(root)
    assert not (root / "experiment.sqlite3").exists()
    goal.approve(root, "test-operator")
    result = goal.run(root)
    assert result["verified"] is True
    assert result["trial_count"] == 2
    assert result["promotion_eligible"] is False
    assert result["unseen_validation"] is False
    assert result["acquisition_requests_performed"] == 0
    assert result["model_improved"] is False
    assert (root / "dashboard.html").exists()
    assert file_sha256(source) == before
    assert cli.store_for(root).snapshot(root.name)["status"] == "completed"
    assert verifications.count("baseline") == 2
    assert verifications.count("p00-t00") == 2
    monkeypatch.setattr(cli, "batch", lambda *_a, **_k: pytest.fail("must reuse verified results"))
    assert goal.run(root)["verified"] is True


def test_reusing_verified_records_still_rechecks_artifacts_before_completion(campaign, monkeypatch):
    root, _, _ = campaign
    goal.approve(root, "test-operator")
    monkeypatch.setattr(cli, "batch", inline_batch)
    original_compare = goal.compare_records
    def tamper_after_comparison(*args):
        result = original_compare(*args)
        artifact = root / "trials" / "baseline" / "artifacts" / "predictions.parquet"
        artifact.write_bytes(artifact.read_bytes() + b"changed")
        return result
    monkeypatch.setattr(goal, "compare_records", tamper_after_comparison)
    with pytest.raises(ValueError, match="hash"):
        goal.run(root)
    assert not (root / "verification.json").exists()
    assert cli.store_for(root).snapshot(root.name)["status"] != "completed"


def test_approval_is_invalidated_by_changed_task(campaign):
    root, _, _ = campaign
    goal.approve(root, "test-operator")
    task = read_json(root / "task-ledger.json")
    task["config"]["max_total_trials"] -= 1
    write_json(root / "task-ledger.json", task)
    with pytest.raises(ValueError, match="approval"):
        goal.run(root)


def test_contract_data_drift_blocks_resume(campaign):
    root, _, _ = campaign
    goal.approve(root, "test-operator")
    path = root / "dataset" / "dataset.parquet"
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(RuntimeError, match="dataset-hash"):
        goal.run(root)


def test_progress_chain_tamper_is_not_accepted(campaign):
    root, _, _ = campaign
    event = read_json(root / "progress" / "000000.json")
    event["sequence"] = 4
    write_json(root / "progress" / "000000.json", event)
    with pytest.raises(ValueError, match="progress-ledger"):
        goal.approve(root, "test-operator")


def test_time_budget_exit_is_not_goal_success(campaign, monkeypatch):
    root, _, _ = campaign
    goal.approve(root, "test-operator")
    monkeypatch.setattr(cli, "batch", lambda *_a, **_k: (_ for _ in ()).throw(
        TimeoutError("goal-loop-time-budget-exhausted")))
    result = goal.run(root)
    assert result["status"] == "budget_exhausted"
    assert result["verified"] is False
    assert result["promotion_eligible"] is False


def test_resume_never_resets_the_absolute_time_budget(campaign, monkeypatch):
    root, _, _ = campaign
    goal.approve(root, "test-operator")
    deadlines = []
    def timeout(_root, _payloads, **kwargs):
        deadlines.append(kwargs["deadline_at"])
        raise TimeoutError("goal-loop-time-budget-exhausted")
    monkeypatch.setattr(cli, "batch", timeout)
    goal.run(root)
    first_budget = read_json(root / "run-budget.json")
    goal.run(root)
    assert deadlines[0] == deadlines[1] == first_budget["deadline_at"]


def test_total_trial_budget_includes_control_and_does_not_claim_completion(campaign, monkeypatch):
    root, config, source = campaign
    config.update(max_total_trials=2, max_trials_per_proposal=3)
    new_root = root.parent / "bounded"
    goal.prepare(new_root, source, config)
    goal.approve(new_root, "test-operator")
    monkeypatch.setattr(cli, "batch", inline_batch)
    result = goal.run(new_root)
    assert result["status"] == "budget_exhausted"
    assert result["verified"] is False
    assert len(cli.store_for(new_root).snapshot(new_root.name)["trials"]) == 2
    assert goal.verify(new_root)["verified"] is False


def test_unknown_worker_is_not_automatically_requeued(campaign, monkeypatch):
    root, _, _ = campaign
    goal.approve(root, "test-operator")
    task = goal._approved_task(root)
    store = cli.store_for(root)
    store.create_experiment(root.name, task["execution_contract"])
    store.enqueue(root.name, "baseline", {"id": "baseline"}, "cpu")
    store.claim("test-worker", "cpu")
    monkeypatch.setattr(cli, "batch", lambda *_a, **_k: pytest.fail("worker is unresolved"))
    with pytest.raises(RuntimeError, match="investigation"):
        goal.run(root)


def test_new_campaign_cannot_bypass_an_unresolved_older_worker(campaign):
    root, config, source = campaign
    goal.approve(root, "test-operator")
    task = goal._approved_task(root)
    store = cli.store_for(root)
    store.create_experiment(root.name, task["execution_contract"])
    with pytest.raises(RuntimeError, match="another-experiment-requires-attention"):
        goal.prepare(root.parent / "different", source, config)


def test_comparison_requires_paired_improvement_and_fails_closed_on_small_cohort():
    baseline = pd.DataFrame({
        "race_id": [str(i) for i in range(30)],
        "race_date": [f"202301{i + 1:02d}" for i in range(30)],
        "year": ["2023"] * 30, "rank_correlation": [0.2] * 30, "top1_win": [0.0] * 30,
    })
    candidate = baseline.copy()
    candidate["rank_correlation"] = 0.3
    config = {**read_json(goal.DEFAULT_CONFIG), "development_years": [2023]}
    assert compare_records(baseline, candidate, config)["verdict"] == "candidate"
    config["minimum_paired_dates"] = 31
    comparison = compare_records(baseline, candidate, config)
    assert comparison["verdict"] == "inconclusive"
    assert comparison["paired_bootstrap_ci"]["lower"] is None
    changed = candidate.copy()
    changed.loc[0, "race_id"] = "other"
    with pytest.raises(ValueError, match="identical-cohort"):
        compare_records(baseline, changed, config)


def test_verifier_rejects_self_reported_metrics_without_prediction_evidence(campaign, monkeypatch):
    root, _, _ = campaign
    goal.approve(root, "test-operator")
    monkeypatch.setattr(cli, "batch", inline_batch)
    goal.run(root)
    task = goal._approved_task(root)
    row = copy.deepcopy(cli.store_for(root).snapshot(root.name)["trials"][0])
    row["result"]["evaluation"]["overall"]["rank_correlation"] = 0.99
    with pytest.raises(ValueError, match="result-ledger"):
        verify_trial(root, row, task)
    artifact = root / "trials" / "baseline" / "artifacts" / "predictions.parquet"
    artifact.write_bytes(artifact.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="hash"):
        goal.verify(root)


def test_duplicate_json_keys_are_not_valid_approval_input(tmp_path):
    value = tmp_path / "duplicate.json"
    value.write_text('{"goal": 1, "goal": 2}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        goal.strict_json(value)


def test_verification_cannot_complete_while_exploration_is_in_progress(campaign, monkeypatch):
    root, _, _ = campaign
    goal.approve(root, "test-operator")
    monkeypatch.setattr(cli, "batch", inline_batch)
    goal.run(root)
    events = sorted((root / "progress").glob("*.json"))
    # Simulate a driver interrupted before it published the stopped event.
    for path in events[2:]:
        path.unlink()
    report = goal.verify(root)
    assert report["verified"] is False
    assert report["stop_reason"] == "exploration_incomplete"


def test_win_control_has_independently_recomputed_probability_metrics(campaign, monkeypatch):
    root, config, source = campaign
    config["target"] = "win"
    config["allowed_targets"] = ["win"]
    new_root = root.parent / "probability"
    goal.prepare(new_root, source, config)
    goal.approve(new_root, "test-operator")
    monkeypatch.setattr(cli, "batch", inline_batch)
    assert goal.run(new_root)["verified"] is True
    task = goal._approved_task(new_root)
    row = next(r for r in cli.store_for(new_root).snapshot(new_root.name)["trials"]
               if r["trial_id"] == "baseline")
    evaluation, _ = verify_trial(new_root, row, task)
    assert evaluation["probability_metrics"]["brier"] >= 0
    assert evaluation["probability_metrics"]["calibration_status"] == "not_evaluated"
