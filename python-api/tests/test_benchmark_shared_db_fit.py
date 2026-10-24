"""CPU benchmark orchestration only; fake boosters, no real fitting or DB I/O."""
from datetime import date, timedelta
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from training import db_shared_fit as fit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/benchmark_shared_db_fit.py"
spec = importlib.util.spec_from_file_location("fixture_shared_fit_benchmark", SCRIPT)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def fixture_inputs():
    targets, inputs = [], []
    for day in range(60):
        current = (date(2024, 1, 1) + timedelta(days=day)).strftime("%Y%m%d")
        for horse in range(1, 4):
            targets.append({"race_id": f"{current}0001", "race_date": current,
                            "horse_id": f"horse{horse}", "horse_number": horse, "finish": horse,
                            "time_seconds": 95.0 + horse + (day % 4) * .1,
                            "distance": 1600, "surface": "turf", "venue": "Tokyo", "num_horses": 3})
            row = {name: float(horse + day % 4) for name in fit.FEATURE_COLUMNS}
            for name in fit.CATEGORICAL_COLUMNS:
                row[name] = "sunny"
            row["safe_horse_number"] = float(horse)
            row["safe_weight_change"] = np.nan
            inputs.append(row)
    return pd.DataFrame(targets), pd.DataFrame(inputs, columns=fit.FEATURE_COLUMNS)


@pytest.fixture
def fake_pipeline(monkeypatch):
    target, raw = fixture_inputs()
    calls = []
    def load(snapshot, start, end):
        calls.append(("load", str(snapshot), start, end))
        return target.copy(), target.copy(), {"fixture": True}
    def features(current, history):
        calls.append(("features", len(current), len(history)))
        return raw.copy()
    class Booster:
        def predict(self, matrix, *, num_threads):
            return -matrix.safe_horse_number.to_numpy()
    def train(matrix, labels, preset, *, n_threads):
        calls.append(("train", preset["id"], n_threads, len(matrix)))
        return Booster()
    monkeypatch.setattr(fit, "_train_booster", train)
    monkeypatch.setattr(fit, "source_hashes", lambda: {"fixture": "a" * 64})
    fake_features = SimpleNamespace(load_db_frames=load, build_training_features=features)
    monkeypatch.setattr(benchmark, "_modules", lambda: (fake_features, fit))
    monkeypatch.setattr(benchmark, "resource_probe", lambda: {
        "probe": "fixture", "physical_cores": 8, "logical_cores": 16, "available_memory_bytes": 16 * 1024**3,
    })
    return target, raw, calls


def test_modes_separate_parallel_effect_and_extra_cpu_budget():
    assert benchmark.MODES["A"]["total_native_threads"] == benchmark.MODES["B"]["total_native_threads"] == 4
    assert benchmark.MODES["C"]["total_native_threads"] == 8
    assert benchmark.MODES["B"]["threads_per_fit"] == 2


@pytest.mark.parametrize("mode", ["B", "C"])
def test_missing_probe_never_starts_parallel(mode):
    permitted, reason = benchmark.mode_permission(mode, 100, {"physical_cores": None, "available_memory_bytes": None})
    assert permitted is False
    assert reason == "parallel_resource_probe_unavailable"


def test_fixed_thread_and_memory_conditions_are_not_silently_weakened():
    resources = {"physical_cores": 4, "available_memory_bytes": 16 * 1024**3}
    assert benchmark.mode_permission("A", 100, resources)[0]
    assert benchmark.mode_permission("B", 100, resources)[0]
    assert not benchmark.mode_permission("C", 100, resources)[0]
    assert not benchmark.mode_permission("A", 100, {**resources, "available_memory_bytes": 1})[0]


def test_prepare_exact_inner_train_only_partitions_no_test_metrics(tmp_path, fake_pipeline):
    target, _, calls = fake_pipeline
    api_features, api_fit = benchmark._modules()
    prepared, report = benchmark.prepare_inputs(tmp_path / "snapshot.db", api_features, api_fit)
    assert len(prepared["train"]) == 28 * 3
    assert len(prepared["validation"]) == 8 * 3
    assert report["inner_train"]["date_to"] < report["inner_validation"]["date_from"]
    assert report["cal_test_scored"] is False
    assert report["data_prepared_once"] is True
    assert calls[:2] == [("load", str(tmp_path / "snapshot.db"), "2024-01", "2025-12"), ("features", 180, 180)]
    assert len([call for call in calls if call[0] == "load"]) == 1
    assert len(prepared["train"].columns) == 47
    assert prepared["train"].safe_weight_change.isna().all()


def test_repeated_benchmark_same_quality_and_fresh_report_never_overwrites(tmp_path, fake_pipeline):
    _, _, calls = fake_pipeline
    snapshot = tmp_path / "snapshot.db"
    snapshot.write_bytes(b"immutable fixture snapshot")
    before = benchmark.file_hash(snapshot)
    first, path1 = benchmark.run_benchmark(snapshot, output_root=tmp_path / "reports", expected_sha256=before)
    assert first["status"] == "completed"
    assert [row["mode"] for row in first["runs"]] == ["A", "B", "C", "C", "B", "A"]
    assert first["all_executed_quality_checks_passed"] is True
    assert first["shared_prepared_inputs_unchanged"] is True
    assert first["full_training_pipeline_measured"] is False
    assert first["summary"]["B"]["comparison_scope"] == "same total native budget"
    assert first["summary"]["C"]["comparison_scope"].startswith("additional CPU")
    assert all(row["speedup_valid"] for row in first["summary"].values())
    assert len([call for call in calls if call[0] == "load"]) == 1
    assert len([call for call in calls if call[0] == "features"]) == 1
    assert len([call for call in calls if call[0] == "train"]) == 12
    assert not any("predictions" in trial for run in json.loads(path1.read_text())["runs"] for trial in run["trials"])
    first_bytes = path1.read_bytes()
    _, path2 = benchmark.run_benchmark(snapshot, repeats=1, modes=("A",), output_root=tmp_path / "reports")
    assert path1 != path2 and path1.read_bytes() == first_bytes
    assert benchmark.file_hash(snapshot) == before


def test_quality_difference_prevents_speedup_claim():
    base = {"selected_preset": "lean", "trials": [{"preset": {"id": "lean"},
            "predictions": np.array([1., 2.]), "metrics": {"rank_correlation": .5, "rmse_speed_mps": .4}}]}
    changed = {**base, "trials": [{**base["trials"][0], "predictions": np.array([1., 2.1])}]}
    check = benchmark.verify_quality(base, changed)
    assert check["passed"] is False
    results = benchmark.summary_results([
        {"mode": "A", "candidate_comparison_seconds": 4., "quality": {"passed": True}},
        {"mode": "B", "candidate_comparison_seconds": 1., "quality": check},
    ], 10.)
    assert results["B"]["speedup_valid"] is False
    assert results["B"]["candidate_comparison_speedup_vs_A"] is None
    assert results["B"]["prepared_plus_candidate_speedup_vs_A"] is None


def test_resource_skip_reports_no_parallel_fit(tmp_path, fake_pipeline, monkeypatch):
    _, _, calls = fake_pipeline
    monkeypatch.setattr(benchmark, "resource_probe", lambda: {
        "physical_cores": None, "logical_cores": 8, "available_memory_bytes": None,
    })
    snapshot = tmp_path / "snapshot.db"
    snapshot.write_bytes(b"immutable fixture snapshot")
    report, _ = benchmark.run_benchmark(snapshot, repeats=1, output_root=tmp_path / "reports")
    assert list(report["summary"]) == ["A"]
    assert [row["mode"] for row in report["skipped"]] == ["B", "C"]
    assert len([call for call in calls if call[0] == "train"]) == 2


@pytest.mark.parametrize("problem", ["digest", "wal", "journal", "repeats", "modes"])
def test_snapshot_and_conditions_fail_before_preparation(tmp_path, fake_pipeline, problem):
    _, _, calls = fake_pipeline
    snapshot = tmp_path / "snapshot.db"
    snapshot.write_bytes(b"immutable fixture snapshot")
    kwargs = {"output_root": tmp_path / "reports"}
    if problem == "digest": kwargs["expected_sha256"] = "0" * 64
    elif problem in {"wal", "journal"}: Path(str(snapshot) + "-" + problem).write_bytes(b"pending transaction")
    elif problem == "repeats": kwargs["repeats"] = 3
    else: kwargs["modes"] = ("B",)
    with pytest.raises(ValueError):
        benchmark.run_benchmark(snapshot, **kwargs)
    assert calls == []
    assert not (tmp_path / "reports").exists()


def test_snapshot_mutation_invalidates_benchmark_without_partial_speedup(tmp_path, fake_pipeline, monkeypatch):
    snapshot = tmp_path / "snapshot.db"
    snapshot.write_bytes(b"immutable fixture snapshot")
    original = benchmark.execute_mode
    def mutate(mode, prepared, api_fit):
        result = original(mode, prepared, api_fit)
        snapshot.write_bytes(b"mutated fixture snapshot")
        return result
    monkeypatch.setattr(benchmark, "execute_mode", mutate)
    with pytest.raises(ValueError, match="changed-during-run"):
        benchmark.run_benchmark(snapshot, repeats=1, modes=("A",), output_root=tmp_path / "reports")
    report = json.loads(next((tmp_path / "reports").glob("*/benchmark.json")).read_text())
    assert report["status"] == "failed"
    assert report["summary"] == {}
