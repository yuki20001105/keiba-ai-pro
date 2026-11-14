"""Offline v3 proposal contracts and small real learning/replay tests."""
from importlib import import_module
import copy
import json
from pathlib import Path
import time

import pytest


def api():
    return import_module("experiments.agent_pace_loop")


def specification(identifier="new", **changes):
    value = {"id": identifier, "groups": ["baseline46"], "objective": "win",
             "head": "none", "mode": "legacy", "fields": False,
             "interactions": False, "time_feature": False, "params": {}}
    value.update(changes)
    return value


def result(identifier, nll=2., features=80, **metrics):
    return {"id": identifier, "specification": specification(identifier), "folds": [
        {"year": year, "metrics": {"nll": nll, "brier": .8, "top1": .3, "ece": .02,
                                   **metrics}, "features": features, "boundary": False}
        for year in (2022, 2023, 2024)]}


def test_finite_proposals_canonicalize_defaults_and_irrelevant_head_mode():
    m = api()
    a = specification(groups=["style_pace", "baseline46"], mode="condition")
    b = specification("other", groups=["baseline46", "style_pace"],
                      params={"num_boost_round": 200, "num_leaves": 31,
                              "min_data_in_leaf": 30, "lambda_l2": 1.})
    assert m.canonical_key(a) == m.canonical_key(b)
    assert m.validate_spec(specification(fields=True))["fields"] is True
    for changed in (
        {"objective": "speed"}, {"params": {"num_leaves": True}},
        {"params": {"num_boost_round": 201}}, {"params": {"lambda_l2": float("nan")}},
        {"groups": ["baseline46", "finish"]}, {"blend_weight": .1},
        {"head": "pace", "interactions": True}, {"time_feature": True},
        {"groups": ["style_pace"]}, {"id": "..\\escape"},
        {"params": {"num_leaves": 31.}}, {"fields": 1},
    ):
        with pytest.raises(ValueError):
            m.validate_spec(specification(**changed))


def test_selection_guards_are_relative_to_win80_and_parsimony_has_a_band():
    m = api()
    baseline = result("win80")
    best = result("numerical", 1.990, 92)
    small = result("parsimonious", 1.9909, 46, brier=.8005)
    fragile = result("fragile", 1.98, 30)
    fragile["folds"][0]["metrics"]["brier"] = .80101
    selected = m.select_winner([baseline, best, small, fragile])
    assert selected["winner"] == "parsimonious"
    assert selected["numerical_minimum"] == "numerical"
    assert m.select_winner([baseline, result("insufficient", 1.9991, 46)])["winner"] == "win80"
    assert m.select_winner([baseline, result("edge", 1.999, 100)])["winner"] == "win80"
    assert m.select_winner([baseline, result("outside", 1.9989, 100)])["winner"] == "outside"
    bad = result("boundary", 1.9)
    bad["folds"][0]["boundary"] = True
    assert m.select_winner([baseline, bad])["winner"] == "win80"
    future = result("forbidden")
    future["folds"][0]["year"] = 2026
    with pytest.raises(ValueError, match="development"):
        m.select_winner([baseline, future])


@pytest.mark.parametrize("historical_id", ["group-condition_aptitude", "group-style_pace"])
def test_historical_identity_ignores_legacy_names_without_relaxing_new_ids(historical_id):
    m = api()
    old = specification(historical_id, objective="speed")
    renamed = specification("old", objective="speed")
    assert m.canonical_key(old, historical=True) == m.canonical_key(renamed, historical=True)
    with pytest.raises(ValueError, match="safe-candidate-id"):
        m.validate_spec(specification(historical_id))


def test_selection_checks_annual_nll_and_top1_even_if_mean_is_better():
    m = api()
    base = result("win80")
    for name, value in (("nll", 2.00501), ("top1", .29499)):
        bad = result("unsafe", 1.95)
        bad["folds"][0]["metrics"][name] = value
        assert m.select_winner([base, bad])["winner"] == "win80"


def test_parsimony_then_brier_then_nll_then_id_within_eligible_band():
    m = api()
    base = result("win80")
    large = result("large", 1.99, 90, brier=.79)
    small = result("small", 1.9905, 46, brier=.8)
    lower_brier = result("lower-brier", 1.9909, 46, brier=.799)
    assert m.select_winner([base, large, small, lower_brier])["winner"] == "lower-brier"
    tie = copy.deepcopy(lower_brier)
    tie["id"] = "a"
    tie["specification"]["id"] = "a"
    assert m.select_winner([base, tie, lower_brier])["winner"] == "a"


def test_strict_json_rejects_duplicate_keys_and_nonfinite(tmp_path):
    path = tmp_path / "proposal.json"
    for text in ('{"cycle":1,"cycle":2}', '{"cycle":NaN}'):
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError):
            api().strict_json(path)


def test_budget_start_includes_decisions_and_cannot_be_extended_or_reset(tmp_path):
    m = api()
    timing = m.start_budget(tmp_path)
    original = (tmp_path / "run-budget.json").read_bytes()
    assert m.start_budget(tmp_path) == timing
    assert (tmp_path / "run-budget.json").read_bytes() == original
    changed = {**timing, "started": timing["started"] + 30, "deadline": timing["deadline"] + 30}
    (tmp_path / "run-budget.json").write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="budget"):
        m.assert_budget(tmp_path)
    (tmp_path / "run-budget.json").write_bytes(original)
    fit = m.strict_json(tmp_path / "fit-budget.json")
    fit["maximum"] = 241
    (tmp_path / "fit-budget.json").write_text(json.dumps(fit))
    with pytest.raises(ValueError, match="budget"):
        m.assert_budget(tmp_path)


def test_fit_reservations_cannot_disappear_or_change_deadline(tmp_path):
    m = api()
    timing = m.start_budget(tmp_path)
    budget = m.AgentFitBudget(240, timing["deadline"], path=tmp_path / "fit-budget.json")
    budget.reserve("temperature-calibration")
    assert len(m.assert_budget(tmp_path)["fit_records"]) == 1
    saved = m.strict_json(tmp_path / "fit-budget.json")
    saved["records"] = []
    (tmp_path / "fit-budget.json").write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="budget"):
        m.assert_budget(tmp_path)


def test_context_admission_is_immutable_bound_and_canonical_duplicates_fail(tmp_path, monkeypatch):
    m = api()
    baseline = result("win80")
    task = {"reference_development": [baseline], "historical_specifications": [baseline["specification"]]}
    m.freeze(tmp_path / "task-ledger.json", task)
    monkeypatch.setattr(m, "assert_task", lambda _root: task)
    monkeypatch.setattr(m, "_other_experiments_settled", lambda _root: None)
    monkeypatch.setattr(m, "BASE", tmp_path / "driver")
    monkeypatch.setattr(m, "queue_state", lambda _root: {"trials": [], "status": "active"})
    context = m.context(tmp_path)
    assert m.context(tmp_path) == context
    document = m.strict_json(Path(context["path"]))
    assert document["development_years"] == [2022, 2023, 2024]
    assert document["prior_cycles"] == []
    candidates = [specification("new-" + str(i), params={"num_leaves": leaves})
                  for i, leaves in enumerate((7, 15, 63))]
    proposal = {"cycle": 1, "context_sha256": context["sha256"],
                "hypothesis": "Smaller trees generalize", "reasoning": "Development only",
                "expected_outcome": "Lower race NLL", "candidates": candidates}
    path = tmp_path / "proposal.json"
    m.freeze(path, proposal)
    admitted = m.admit(tmp_path, path)
    assert admitted["cycle"] == 1
    path.write_text("{}")
    assert m.admissions(tmp_path)[0]["proposal"] == proposal
    with pytest.raises(ValueError, match="pending|admitted"):
        m.context(tmp_path, cycle=2)
    with pytest.raises(ValueError, match="admitted|sequence"):
        m.admit(tmp_path, path)
    frozen = tmp_path / "admissions" / "01.json"
    frozen.write_text("{}")
    with pytest.raises(ValueError, match="evidence|immutable"):
        m.admissions(tmp_path)


def test_rejects_stale_context_duplicate_defaults_and_refuses_early_finalize(tmp_path, monkeypatch):
    m = api()
    base = result("win80")
    task = {"reference_development": [base], "historical_specifications": [base["specification"]]}
    m.freeze(tmp_path / "task-ledger.json", task)
    monkeypatch.setattr(m, "assert_task", lambda _root: task)
    monkeypatch.setattr(m, "_other_experiments_settled", lambda _root: None)
    monkeypatch.setattr(m, "BASE", tmp_path / "driver")
    monkeypatch.setattr(m, "queue_state", lambda _root: {"trials": [], "status": "active"})
    ctx = m.context(tmp_path)
    proposal = {"cycle": 1, "context_sha256": "0" * 64, "hypothesis": "h", "reasoning": "r",
                "expected_outcome": "e", "candidates": [specification(str_id, params={"num_leaves": n})
                                                        for str_id, n in (("x", 7), ("y", 15), ("z", 63))]}
    m.freeze(tmp_path / "stale.json", proposal)
    with pytest.raises(ValueError, match="context"):
        m.admit(tmp_path, tmp_path / "stale.json")
    proposal["context_sha256"] = ctx["sha256"]
    proposal["candidates"][0]["params"] = dict(m.DEFAULT_PARAMS)
    m.freeze(tmp_path / "duplicate.json", proposal)
    with pytest.raises(ValueError, match="duplicate"):
        m.admit(tmp_path, tmp_path / "duplicate.json")
    with pytest.raises(ValueError, match="ten|10"):
        m.finalize(tmp_path)


def test_worker_adapter_uses_private_namespace_and_original_v2_head_cache():
    m = api()
    from experiments import pace_research_loop as old
    before = old.run_trial.__globals__["assert_task"]
    adapted = m.worker_api()
    assert adapted.run_trial.__globals__["assert_task"] is m.assert_task
    assert adapted.run_trial.__globals__["cached_heads"] is old.cached_heads
    assert old.run_trial.__globals__["assert_task"] is before
    assert adapted.run_trial.__globals__["own_sources"] is m.own_sources


def test_failed_or_unknown_trials_never_resubmit(tmp_path, monkeypatch):
    m = api()
    monkeypatch.setattr(m, "assert_task", lambda _root: {})
    m.start_budget(tmp_path)
    for status in ("failed", "unknown", "running", "pending"):
        monkeypatch.setattr(m, "queue_state", lambda _root: {"trials": [{"status": status}]})
        with pytest.raises(ValueError, match="no-retry"):
            m.execute(tmp_path, [specification()], m.strict_json(tmp_path / "run-budget.json"))


def test_ten_stagnant_cycles_still_require_all_three_evaluations_and_refuse_eleven(tmp_path, monkeypatch):
    m = api()
    base = result("win80")
    task = {"reference_development": [base], "historical_specifications": [base["specification"]]}
    m.freeze(tmp_path / "task-ledger.json", task)
    monkeypatch.setattr(m, "assert_task", lambda _root: task)
    monkeypatch.setattr(m, "BASE", tmp_path / "driver")
    monkeypatch.setattr(m, "_other_experiments_settled", lambda _root: None)
    monkeypatch.setattr(m, "queue_state", lambda _root: {"trials": [], "status": "active"})
    # Learning is the expensive boundary; orchestration, hashing, admission,
    # scoring, cycle completion and context reflection below remain real.
    def learning_boundary(_root, specs, _timing, phase="development"):
        return [result(s["id"], 2.1) | {"specification": s} for s in specs]
    monkeypatch.setattr(m, "execute", learning_boundary)
    monkeypatch.setattr(m, "completed_evidence", lambda *_args: {})
    for cycle in range(1, 11):
        ctx = m.context(tmp_path)
        candidates = [specification(f"cycle-{cycle}-{j}", params={
            "num_leaves": [7, 15, 63][j], "num_boost_round": [120, 200, 300, 450, 600][(cycle - 1) % 5],
            "lambda_l2": 1. if cycle <= 5 else 5.}) for j in range(3)]
        proposal = {"cycle": cycle, "context_sha256": ctx["sha256"], "hypothesis": "h",
                    "reasoning": f"Reflection after {cycle-1} cycles", "expected_outcome": "e",
                    "candidates": candidates}
        path = tmp_path / f"proposal-{cycle}.json"
        m.freeze(path, proposal)
        m.admit(tmp_path, path)
        decision = m.cycle(tmp_path, cycle)
        assert decision["cycle"] == cycle
        assert decision["interim_selection"]["winner"] == "win80"
        assert len(decision["results"]) == 3
    assert len(m.decisions(tmp_path)) == 10
    with pytest.raises(ValueError, match="11|sequence"):
        m.context(tmp_path, cycle=11)
    with pytest.raises(ValueError, match="11|sequence"):
        m.cycle(tmp_path, 11)


@pytest.fixture
def synthetic_reference(tmp_path, monkeypatch):
    """A tiny genuine v2 worker ledger, not the user's frozen campaign."""
    import shutil
    import pandas as pd
    from experiments import cli, pace_research_loop as old
    from experiments.common import file_sha256, source_manifest, active_pointer_hash
    from test_pace_forecast import population
    m = api()
    root = tmp_path / "synthetic-v2"
    dataset = root / "dataset"
    dataset.mkdir(parents=True)
    target_parts, feature_parts, labels = [], [], []
    for year in range(2016, 2027):
        target, x = population(year, races=8)
        target["finish"] = (target.horse_number + (int(year) % 3) - 1) % 8 + 1
        target["horse_id"] = target.horse_number.map(str)
        target["time_seconds"] = 95. + target.horse_number
        target["race_id"] = target.race_id.astype("string")
        x["ctx_style_count5"] = 3.
        x["ctx_front_rate5"] = 1. - (target.horse_number - 1) / 7.
        x["ctx_gate_fraction"] = (target.horse_number - 1) / 7.
        target_parts.append(target)
        feature_parts.append(x)
        for i, row in target.drop_duplicates("race_id").iterrows():
            labels.append({"race_id": str(row.race_id), "race_date": str(row.race_date),
                           "validated": True, "source_sha256": "a" * 64,
                           "first600_seconds": 33. + i % 3 * 3})
    target, x = pd.concat(target_parts, ignore_index=True), pd.concat(feature_parts, ignore_index=True)
    packed = pd.concat([target[old.TARGET_COLUMNS], x.add_prefix("feature__")], axis=1)
    dev = packed.race_date.le("20241231")
    packed.loc[dev].to_parquet(dataset / "dataset.parquet", index=False)
    packed.loc[~dev].to_parquet(dataset / "confirmation.parquet", index=False)
    m.freeze(dataset / "lap-observations.json", {"observations": labels})
    snapshot = root / "snapshot.sqlite"
    snapshot.write_bytes(b"synthetic immutable snapshot identity")
    m.freeze(dataset / "dataset_manifest.json", {
        "schema": old.research.SCHEMA, "development_years": [2022, 2023, 2024],
        "confirmation_years": [2025, 2026], "confirmation_is_unseen": False,
        "snapshot": {"path": str(snapshot), "sha256": file_sha256(snapshot)},
        "files": {name: file_sha256(dataset / name) for name in
                  ("dataset.parquet", "confirmation.parquet", "lap-observations.json")}})
    contract = {"capacity": {"cpu": 1, "gpu": 0}, "reserve_ram_mb": 0, "estimated_worker_memory_mb": 64,
                "source_manifest": source_manifest(), "active_pointer_sha256": active_pointer_hash(),
                "dataset_manifest_sha256": file_sha256(dataset / "dataset_manifest.json"),
                "dataset_sha256": file_sha256(dataset / "dataset.parquet")}
    task = {"schema": old.research.SCHEMA, "sources": old.own_sources(), "execution_contract": contract}
    m.freeze(root / "task-ledger.json", task)
    old.approve(root)
    old._event(root, "prepared", {"task_sha256": file_sha256(root / "task-ledger.json")})
    archive_paths = set(contract["source_manifest"])
    for name in task["sources"]:
        prefix = "keiba" if name.startswith("keiba_ai.") else "python-api"
        path = (Path("scripts") / (name.split(".", 1)[1] + ".py") if name.startswith("scripts.")
                else Path(prefix).joinpath(*name.split(".")).with_suffix(".py"))
        archive_paths.add(str(path))
    for relative in archive_paths:
        destination = root / "sources" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(m.REPO_ROOT / relative, destination)
    specifications = [s for stage in old.initial_stages() for s in stage if s["id"] in ("win80", "field-win80")]
    m.freeze(root / "attempts" / "00.json", {"specifications": specifications})
    old._event(root, "step_started", {"step": 0, "attempt_sha256": file_sha256(root / "attempts" / "00.json")})
    m.freeze(root / "run-budget.json", {"started": time.time(), "deadline": time.time() + 240})
    store = cli.store_for(root)
    store.create_experiment(root.name, contract)
    with monkeypatch.context() as scoped:
        scoped.setattr(old, "assert_task", lambda _root: task)
        for s in specifications:
            p = {**old.payload(s), "device": "cpu", "num_threads": 2,
                 "experiment_contract_sha256": store.snapshot(root.name)["contract_sha256"]}
            store.enqueue(root.name, p["id"], p, "cpu")
            assert cli.worker(root, "cpu", trial_runner=old.run_trial) == 0
    results = [t["result"] for t in store.snapshot(root.name)["trials"]]
    m.freeze(root / "development-results.json", results)
    m.freeze(root / "diagnostics.json", {"development_only": True, "date_to": "20240108"})
    store.finish_experiment(root.name)
    m.freeze(root / "verification.json", {"verified": True})
    return root


def test_real_synthetic_reference_reuse_new_owned_worker_and_independent_replay(synthetic_reference, monkeypatch):
    import joblib
    import numpy as np
    from experiments import cli
    m = api()
    parent = synthetic_reference.parent
    root = parent / "synthetic-v3"
    monkeypatch.setattr(m, "BASE", parent)
    monkeypatch.setattr(m, "_other_experiments_settled", lambda _root: None)
    before = m.file_sha256(synthetic_reference / "experiment.sqlite3")
    m.prepare(root, synthetic_reference)
    assert m.file_sha256(synthetic_reference / "experiment.sqlite3") == before
    assert m.file_sha256(root / "dataset" / "dataset_manifest.json") == m.file_sha256(
        synthetic_reference / "dataset" / "dataset_manifest.json")
    m.approve(root)
    ctx = m.context(root)
    text = Path(ctx["path"]).read_text(encoding="utf-8")
    assert '"2025"' not in text and '"2026"' not in text and "confirmation-results" not in text
    candidates = [specification("smoke-" + str(i), groups=list(m.context_features.FEATURE_GROUPS),
                                params={"num_leaves": leaves, "num_boost_round": 120},
                                head="both" if i == 2 else "none", mode="hier_field" if i == 2 else "legacy",
                                fields=i == 2, time_feature=i == 2)
                  for i, leaves in enumerate((7, 15, 63))]
    proposal = {"cycle": 1, "context_sha256": ctx["sha256"], "hypothesis": "h", "reasoning": "r",
                "expected_outcome": "e", "candidates": candidates}
    path = root / "proposals" / "01.json"
    m.freeze(path, proposal)
    m.admit(root, path)
    # Actual CLI batch launches the NEW wrapper, obtains a lease, fits real
    # LightGBM models, serializes and checks prediction parity in a subprocess.
    decision = m.cycle(root, 1)
    assert len(decision["results"]) == 3
    assert len(m.assert_budget(root)["fit_records"]) == 18  # 9 final fits + 9 temperatures; all heads reused
    state = cli.store_for(root).snapshot(root.name)
    target, x, _ = m.load_data(root / "dataset")
    for trial in state["trials"]:
        records = m.audit_predictions(root, trial, target, x, m.own_sources())
        assert sorted(records) == [2022, 2023, 2024]
        assert trial["result"]["resource_usage"]["num_threads"] == 2
    assert m.file_sha256(synthetic_reference / "experiment.sqlite3") == before
    bundle = joblib.load(root / "trials" / "dev-smoke-2" / "artifacts" / "2024" / "research.joblib")
    t = target.loc[target.race_date.str.startswith("2024")].copy()
    original = m.predict_bundle(bundle, t, x.loc[t.index])
    t["finish"], t["time_seconds"] = 1, .01
    poisoned = m.predict_bundle(bundle, t, x.loc[t.index])
    np.testing.assert_array_equal(original[1], poisoned[1])
    artifact = root / "trials" / "dev-smoke-0" / "artifacts" / "2024" / "research.joblib"
    artifact.write_bytes(artifact.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="artifact"):
        m.audit_predictions(root, state["trials"][0], target, x, m.own_sources())


def test_verify_refuses_incomplete_campaign_without_writing_success(tmp_path, monkeypatch):
    m = api()
    monkeypatch.setattr(m, "assert_task", lambda _root: {})
    monkeypatch.setattr(m, "BASE", tmp_path / "driver")
    with pytest.raises(ValueError, match="ten|10|incomplete"):
        m.verify(tmp_path)
    assert not (tmp_path / "verification.json").exists()


def test_complete_synthetic_ten_cycle_campaign_freezes_selection_then_verifies_all_folds(
        synthetic_reference, monkeypatch):
    from experiments import cli
    m = api()
    root = synthetic_reference.parent / "complete-synthetic-v3"
    monkeypatch.setattr(m, "BASE", synthetic_reference.parent)
    monkeypatch.setattr(m, "_other_experiments_settled", lambda _root: None)
    # This exhaustive local replay does not need to rehash the multi-GB live
    # database hundreds of times; the separate subprocess test uses real guards.
    monkeypatch.setattr(m, "guards", lambda: {"isolated_fixture": True})
    # Subprocess/ownership is exercised in the smoke test above. Here replace
    # only process scheduling: keep leases, real fits, manifests and replay.
    def in_process_batch(root, payloads, **kwargs):
        assert kwargs["native_threads"] == 2 and kwargs["worker_script"] == m.WORKER
        store = cli.store_for(root)
        contract_sha = store.snapshot(root.name)["contract_sha256"]
        for p in payloads:
            p = {**p, "device": "cpu", "num_threads": 2, "experiment_contract_sha256": contract_sha}
            store.enqueue(root.name, p["id"], p, "cpu")
            assert cli.worker(root, "cpu", trial_runner=m.run_trial) == 0
        return {t["trial_id"]: t["result"] for t in store.snapshot(root.name)["trials"]}
    monkeypatch.setattr(cli, "batch", in_process_batch)
    m.prepare(root, synthetic_reference)
    m.approve(root)
    for cycle in range(1, 11):
        ctx = m.context(root)
        candidates = [specification(f"constant-{cycle}-{j}", params={
            "num_leaves": [7, 15, 63][j], "min_data_in_leaf": 120,
            "num_boost_round": [120, 200, 300, 450, 600][(cycle - 1) % 5],
            "lambda_l2": 1. if cycle <= 5 else 5.}) for j in range(3)]
        path = root / "proposals" / f"{cycle:02d}.json"
        m.freeze(path, {"cycle": cycle, "context_sha256": ctx["sha256"], "hypothesis": "h",
                        "reasoning": f"reflect on cycle {cycle-1}", "expected_outcome": "e",
                        "candidates": candidates})
        m.admit(root, path)
        m.cycle(root, cycle)
    summary = m.finalize(root)
    assert summary["selected"] == "win80"
    assert Path(summary["selected_research_bundle"]).is_relative_to(root)
    assert (root / "verification.json").exists() is False
    verification = m.verify(root)
    assert verification["verified"] is True
    assert verification["model_improved"] is False
    assert verification["production_eligible"] is False
    assert verification["known_retrospective_only"] is True
    assert verification["new_development_folds"] == 90
    assert len(verification["checked_folds"]) == 97  # six reused + ninety new + final baseline
    assert m.verify(root) == verification
    # A self-consistent replacement manifest must not bypass the cycle's
    # hash-pinned evidence, even when the queue result itself was not changed.
    directory = root / "trials" / "dev-constant-1-0"
    manifest_path = directory / "artifact_manifest.json"
    manifest = m.strict_json(manifest_path)
    added = directory / "unexpected.json"
    added.write_text("{}")
    manifest[str(added.relative_to(directory))] = m.file_sha256(added)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="pinned|evidence"):
        m.verify(root)
