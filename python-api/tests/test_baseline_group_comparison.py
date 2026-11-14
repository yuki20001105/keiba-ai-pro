import copy

import numpy as np
import pandas as pd
import pytest


def _speeds():
    rows = []
    for venue, offset in [("Tokyo", 0.5), ("Hanshin", -0.5)]:
        for going, penalty in [("良", 0), ("重", -0.3)]:
            for weather, weather_penalty in [("晴", 0), ("雨", -0.1)]:
                for index in range(40):
                    speed = 16 + offset + penalty + weather_penalty + (-0.2 if index % 2 else 0.2)
                    rows.append({"distance": 1600, "surface": "turf", "venue": venue,
                                 "field_condition": going, "weather": weather,
                                 "time_seconds": 1600 / speed})
    return pd.DataFrame(rows)


def test_course_grouping_separates_venue_without_changing_current_baseline():
    from keiba_ai.speed_deviation import apply_speed_deviation_baseline, fit_speed_deviation_baseline
    from training.grouped_speed_baseline import fit_group_baseline, normalization, teacher

    frame = _speeds()
    old = fit_speed_deviation_baseline(frame)
    current = fit_group_baseline(frame, "current_speed")
    np.testing.assert_allclose(teacher(frame, current), apply_speed_deviation_baseline(frame, old))
    mean, _ = normalization(frame, fit_group_baseline(frame, "course"))
    np.testing.assert_allclose(mean.iloc[:160], 16.3)
    np.testing.assert_allclose(mean.iloc[160:], 15.3)


def test_coarse_going_and_weather_preserve_distinct_conditional_speed_means():
    from training.grouped_speed_baseline import fit_group_baseline, normalization

    frame = _speeds()
    baseline = fit_group_baseline(frame, "course_going_weather")
    mean, std = normalization(frame, baseline)
    np.testing.assert_allclose(mean.iloc[:40], 16.5)
    np.testing.assert_allclose(mean.iloc[40:80], 16.4)
    np.testing.assert_allclose(mean.iloc[80:120], 16.2)
    np.testing.assert_allclose(std.iloc[:40], np.sqrt(40 * 0.2**2 / 39))
    later = frame.iloc[:6].copy()
    later["field_condition"] = ["稍重", "不良", "良", "良", "unknown", "良"]
    later["weather"] = ["小雨", "雪", "曇", "unknown", "晴", "小雪"]
    means, _ = normalization(later, baseline)
    np.testing.assert_allclose(means, [16.1, 16.1, 16.5, 16.45, 16.3, 16.4])


def test_sparse_or_unknown_children_use_nearest_parent_and_do_not_refit_on_evaluation():
    from training.grouped_speed_baseline import fit_group_baseline, normalization

    frame = _speeds().iloc[:50].copy()
    baseline = fit_group_baseline(frame, "course_going_weather")
    before = copy.deepcopy(baseline)
    later = frame.iloc[-1:].copy()
    later["time_seconds"] = 1
    mean, std = normalization(later, baseline)
    np.testing.assert_allclose(mean, [16.48])
    assert std.iloc[0] > 0
    later["venue"] = "unseen"
    mean, _ = normalization(later, baseline)
    np.testing.assert_allclose(mean, [16.48])
    assert baseline == before


def _races(rho=0.4, top1=0.25, nll=2.2):
    return pd.DataFrame({"race_id": [f"r{i}" for i in range(30)],
                         "race_date": pd.date_range("20240101", periods=30).strftime("%Y%m%d"),
                         "spearman": rho, "top1": top1, "nll": nll})


@pytest.mark.parametrize("rho,top1,nll,passes", [
    (0.41, 0.25, 2.2, True),
    (0.41, 0.244, 2.2, False),
    (0.41, 0.25, 2.211, False),
    (0.4005, 0.25, 2.2, False),
])
def test_rank_gate_requires_meaningful_rank_gain_and_preserves_win_metrics(rho, top1, nll, passes):
    from training.baseline_group_comparison import rank_comparison

    result = rank_comparison(_races(), _races(rho, top1, nll), development=True)
    assert result["passes"] is passes
    assert result["spearman_difference"] == pytest.approx(rho - 0.4)


def test_rank_comparison_rejects_changed_race_keys():
    from training.baseline_group_comparison import rank_comparison

    other = _races()
    other.loc[0, "race_id"] = "different"
    with pytest.raises(ValueError):
        rank_comparison(_races(), other, development=True)


@pytest.fixture(scope="module")
def completed_parent(tmp_path_factory):
    from experiments.common import json_sha256
    from experiments.search import freeze_json
    from test_target_comparison import _training_database
    from training import target_comparison as targets

    directory = tmp_path_factory.mktemp("grouping-parent")
    parent = directory / "target-original"
    task = targets.prepare(parent, _training_database(directory / "source.db"))
    freeze_json(parent / "approval.json", {"actor": "pytest", "task_sha256": json_sha256(task)})
    targets.run(parent)
    return parent


def test_real_grouping_run_reuses_exact_controls_and_rejects_hash_bound_forged_decision(tmp_path, completed_parent):
    from experiments.common import file_sha256, json_sha256, read_json, write_json
    from experiments.search import freeze_json
    from training import baseline_group_comparison as groups

    parent = completed_parent
    parent_before = {str(p.relative_to(parent)): file_sha256(p) for p in parent.rglob("*") if p.is_file()}
    root = tmp_path / "target-groups"
    task = groups.prepare(root, parent)
    with pytest.raises((ValueError, FileNotFoundError)):
        groups.run(root)
    freeze_json(root / "approval.json", {"actor": "pytest", "task_sha256": json_sha256(task)})
    result = groups.run(root)
    evidence = groups.verify(root)
    assert result["new_fits"] == 10
    assert evidence["models_reloaded"] == 14
    assert evidence["selected_grouping"] == "current_speed"
    assert evidence["promotion_eligible"] is False
    assert parent_before == {str(p.relative_to(parent)): file_sha256(p)
                             for p in parent.rglob("*") if p.is_file()}
    actual = read_json(root / "results.json")
    for field, value in [("selected_grouping", "course"), ("final", [{"name": "invented-model"}])]:
        forged = {**actual, field: value}
        write_json(root / "results.json", forged)
        manifest = read_json(root / "artifact-manifest.json")
        manifest["files"]["results.json"] = file_sha256(root / "results.json")
        write_json(root / "artifact-manifest.json", manifest)
        with pytest.raises(ValueError, match="selection|decision|final"):
            groups.verify(root)


def test_preparation_rejects_control_changed_during_input_reconstruction(tmp_path, completed_parent, monkeypatch):
    from experiments.common import read_json, write_json
    from training import baseline_group_comparison as groups

    path = completed_parent / "cases" / "current_speed-standard-2023" / "result.json"
    before = path.read_bytes()
    rebuild = groups.original.validate_prepared_inputs
    def changing_rebuild(parent, task):
        result = rebuild(parent, task)
        write_json(path, {**read_json(path), "seconds": -999})
        return result
    monkeypatch.setattr(groups.original, "validate_prepared_inputs", changing_rebuild)
    try:
        with pytest.raises(ValueError):
            groups.prepare(tmp_path / "target-changing-control", completed_parent)
    finally:
        path.write_bytes(before)


def test_preparation_rejects_protected_state_changed_during_reconstruction(tmp_path, completed_parent, monkeypatch):
    from training import baseline_group_comparison as groups

    changed = [False]
    protected, rebuild = groups.original._protected, groups.original.validate_prepared_inputs
    def changing_rebuild(parent, task):
        result = rebuild(parent, task)
        changed[0] = True
        return result
    def current_protected():
        result = protected()
        return {**result, "active_pointer": "changed"} if changed[0] else result
    monkeypatch.setattr(groups.original, "validate_prepared_inputs", changing_rebuild)
    monkeypatch.setattr(groups.original, "_protected", current_protected)
    with pytest.raises(ValueError):
        groups.prepare(tmp_path / "target-changing-protection", completed_parent)


def test_approval_diagnostics_cannot_change_under_the_same_task(tmp_path, completed_parent):
    from experiments.common import write_json
    from training import baseline_group_comparison as groups

    root = tmp_path / "target-changing-diagnostics"
    groups.prepare(root, completed_parent)
    write_json(root / "diagnostics.json", {"invented": "approval information"})
    with pytest.raises(ValueError):
        groups._assert_task(root)


def test_cached_module_is_rejected_when_disk_code_differs(tmp_path):
    import importlib.util
    from pathlib import Path
    from training import baseline_group_comparison as groups, grouped_speed_baseline

    path = tmp_path / "cached_grouping.py"
    source = Path(grouped_speed_baseline.__file__).read_text(encoding="utf-8")
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("cached_grouping", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path.write_text(source.replace('"良": "good"', '"良": "wet"'), encoding="utf-8")
    with pytest.raises(ValueError):
        groups._module_source_identity(module)


@pytest.mark.parametrize("corruption", ["encoder_order", "rounds", "case_year"])
def test_fitted_contract_rejects_noncanonical_order_rounds_or_case_identity(completed_parent, corruption):
    import joblib
    from experiments.common import read_json
    from training import baseline_group_comparison as groups

    directory = completed_parent / "cases" / "current_speed-standard-2023"
    bundle, row = joblib.load(directory / "model.research.joblib"), read_json(directory / "result.json")
    target = pd.read_parquet(completed_parent / "target.parquet")
    features = pd.read_parquet(completed_parent / "features.parquet")
    train, _, _ = groups.original.partition_masks(target, 2023)
    canonical, _, _ = groups.original.optimized._fitted_fixed_encoder(features.loc[train])
    if corruption == "encoder_order":
        bundle["encoder"].feature_columns = list(reversed(bundle["encoder"].feature_columns))
    elif corruption == "rounds":
        bundle["model"].params["num_iterations"] = 20
    else:
        row["evaluation_year"] = 2024
    with pytest.raises(ValueError):
        groups._validate_fitted_contract(bundle, row, canonical, "current_speed", 2023)


def test_final_control_is_not_materialized_before_development_selection(tmp_path, completed_parent):
    from training import baseline_group_comparison as groups

    target = pd.read_parquet(completed_parent / "target.parquet")
    root = tmp_path / "controls"
    root.mkdir()
    groups._control_metrics(root, {"parent_root": str(completed_parent)}, target)
    assert (root / "control-2023.parquet").is_file()
    assert not (root / "control-2026.parquet").exists()


def test_budget_expiring_during_final_comparison_cannot_publish_completion(tmp_path, completed_parent, monkeypatch):
    from experiments.common import json_sha256, read_json
    from experiments.search import freeze_json
    from training import baseline_group_comparison as groups

    root = tmp_path / "target-expired-final"
    task = groups.prepare(root, completed_parent)
    freeze_json(root / "approval.json", {"actor": "pytest", "task_sha256": json_sha256(task)})
    expired = [False]
    real_time, quantile = groups.time.time, np.quantile
    def comparison_interval(values, probabilities, *args, **kwargs):
        result = quantile(values, probabilities, *args, **kwargs)
        if probabilities == [.025, .975]:
            expired[0] = True
        return result
    monkeypatch.setattr(np, "quantile", comparison_interval)
    monkeypatch.setattr(groups.time, "time", lambda: real_time() + (4000 if expired[0] else 0))
    with pytest.raises(TimeoutError):
        groups.run(root)
    assert read_json(root / "run-state.json")["status"] == "failed"
