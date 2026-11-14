from importlib import import_module
import copy
import time

import joblib
import numpy as np
import pandas as pd
import pytest

from test_pace_forecast import population

def api():
    return import_module("experiments.pace_research_loop")


def test_schedule_is_finite_and_covers_objective_and_context_ablations():
    stages = api().initial_stages()
    specifications = [item for stage in stages for item in stage]
    assert len(specifications) == 21
    assert len({item["id"] for item in specifications}) == len(specifications)
    assert any(item["objective"] == "win" and item["head"] == "none" for item in specifications)
    for group in ("condition_aptitude", "weight", "pedigree", "gate", "style_pace"):
        assert any(item["groups"] == ["baseline46", group] for item in specifications)


def test_winner_is_selected_without_final_year_and_annual_regressions_fail():
    baseline = {"id": "speed46", "folds": [
        {"year": year, "metrics": {"nll": 2.3, "brier": .88, "top1": .24}, "boundary": False}
        for year in (2022, 2023, 2024)]}
    good = copy.deepcopy(baseline)
    good["id"] = "better"
    for fold in good["folds"]:
        fold["metrics"].update(nll=2.2, brier=.85, top1=.28)
    bad = copy.deepcopy(good)
    bad["id"] = "fragile"
    bad["folds"][0]["metrics"]["nll"] = 2.32
    assert api().select_winner([baseline, good, bad])["id"] == "better"
    forbidden = copy.deepcopy(good)
    forbidden["folds"][0]["year"] = 2026
    with pytest.raises(ValueError, match="development"):
        api().select_winner([baseline, forbidden])


def test_bootstrap_pairs_dates_and_rejects_mismatched_cohorts():
    base = pd.DataFrame({"race_id": [f"r{i}" for i in range(40)],
                         "race_date": [f"202401{i // 2 + 1:02d}" for i in range(40)],
                         "nll": 2., "brier": .8, "top1": .2})
    candidate = base.assign(nll=1.9)
    report = api().paired_bootstrap(base, candidate)
    assert report["nll_improvement_ci95"] == pytest.approx([.1, .1])
    assert report["paired_dates"] == 20
    with pytest.raises(ValueError, match="cohort"):
        api().paired_bootstrap(base, candidate.iloc[:-1])


def test_approval_is_bound_to_exact_task_bytes(tmp_path):
    task = {"schema": "pace-research-v2", "purpose": "test"}
    api().freeze(tmp_path / "task-ledger.json", task)
    api().approve(tmp_path)
    api().assert_approval(tmp_path)
    (tmp_path / "task-ledger.json").write_text('{"schema":"changed"}', encoding="utf-8")
    with pytest.raises(ValueError, match="approval"):
        api().assert_approval(tmp_path)


def test_resume_reuses_only_identical_immutable_step_evidence(tmp_path):
    path = tmp_path / "attempts" / "00.json"
    api().reuse_immutable(path, {"prior_results_sha256": "a", "candidate": "x"})
    api().reuse_immutable(path, {"prior_results_sha256": "a", "candidate": "x"})
    with pytest.raises(ValueError, match="immutable"):
        api().reuse_immutable(path, {"prior_results_sha256": "b", "candidate": "x"})


def test_actual_worker_trains_serializes_and_replays_hierarchical_blend(tmp_path, monkeypatch):
    module = api()
    targets, frames, labels = [], [], []
    for year in range(2016, 2025):
        target, x = population(year, races=45)
        target["finish"] = target.horse_number
        target["horse_id"] = target.horse_number.map(str)
        target["time_seconds"] = 95. + target.horse_number
        target["distance"] = 1600.
        target["race_id"] = target.race_id.astype("string")
        x["ctx_style_count5"] = 3.
        x["ctx_front_rate5"] = 1. - (target.horse_number - 1) / 7.
        x["ctx_gate_fraction"] = (target.horse_number - 1) / 7.
        targets.append(target)
        frames.append(x)
        for i, race in enumerate(target.race_id.drop_duplicates()):
            labels.append({"race_id": str(race), "race_date": str(year) + "0101",
                           "validated": True, "source_sha256": "a" * 64,
                           "first600_seconds": 33. + i % 3 * 3})
    target, x = pd.concat(targets, ignore_index=True), pd.concat(frames, ignore_index=True)
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    packed = pd.concat([target[module.TARGET_COLUMNS], x.add_prefix("feature__")], axis=1)
    packed.to_parquet(dataset / "dataset.parquet", index=False)
    module.freeze(dataset / "lap-observations.json", {"observations": labels})
    module.freeze(dataset / "dataset_manifest.json", {"synthetic": True})
    module.freeze(tmp_path / "run-budget.json", {"deadline": time.time() + 120})
    monkeypatch.setattr(module, "assert_task", lambda root: {})
    specification = module.spec("smoke", objective="win", head="both", mode="hier_field",
                                fields=True, interactions=True, time_feature=True)
    specification.update(params={"num_boost_round": 12}, blend_weight=.1)
    attempt = tmp_path / "attempts" / "00.json"
    module.freeze(attempt, {"specifications": [specification]})
    module._event(tmp_path, "step_started", {"step": 0, "attempt_sha256": module.file_sha256(attempt)})
    result = module.run_trial(dataset, tmp_path / "artifacts",
                              {"id": "dev-smoke", "num_threads": 2, "device": "cpu", "phase": "development",
                               "specification": specification}, lambda *_args: None)
    assert len(result["folds"]) == 3
    bundle = joblib.load(tmp_path / "artifacts" / "2024" / "research.joblib")
    loaded, features, _ = module.load_data(dataset)
    selected = loaded.race_date.str.startswith("2024")
    before = module.predict_bundle(bundle, loaded.loc[selected], features.loc[selected])
    poisoned = loaded.loc[selected].copy()
    poisoned["finish"] = 1
    poisoned["time_seconds"] = .01
    poisoned["_ctx_history_corners"] = [(1,) for _ in range(len(poisoned))]
    after = module.predict_bundle(bundle, poisoned, features.loc[selected])
    np.testing.assert_array_equal(before[0], after[0])
    np.testing.assert_array_equal(before[1], after[1])


def test_worker_rejects_unadmitted_and_changed_attempts(tmp_path):
    module = api()
    proposal = module.spec("x")
    with pytest.raises(ValueError, match="not-admitted"):
        module.assert_payload(tmp_path, module.payload(proposal))
    path = tmp_path / "attempts" / "00.json"
    module.freeze(path, {"specifications": [proposal]})
    module._event(tmp_path, "step_started", {"step": 0, "attempt_sha256": module.file_sha256(path)})
    module.assert_payload(tmp_path, module.payload(proposal))
    path.write_text('{"specifications":[]}', encoding="utf-8")
    with pytest.raises(ValueError, match="attempt-evidence-changed"):
        module.assert_payload(tmp_path, module.payload(proposal))
