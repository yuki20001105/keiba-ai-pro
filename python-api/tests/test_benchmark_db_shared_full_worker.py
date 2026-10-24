"""Full-period worker safety/instrumentation tests, fake boosters; no real fit."""
from datetime import date, timedelta
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from training import db_shared_fit as fit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/benchmark_db_shared_full_worker.py"
spec = importlib.util.spec_from_file_location("full_benchmark_fixture", SCRIPT)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class FixtureBooster:
    def predict(self, matrix, *, num_threads):
        return -matrix.safe_horse_number.to_numpy()


def inputs():
    target, rows = [], []
    for day in range(60):
        current = (date(2016, 1, 1) + timedelta(days=day)).strftime("%Y%m%d")
        for horse in range(1, 4):
            target.append({"race_id": f"{current}0001", "race_date": current,
                           "horse_id": f"horse{horse}", "horse_number": horse, "finish": horse,
                           "time_seconds": 95.0 + horse + (day % 4) * .1,
                           "distance": 1600, "surface": "turf", "venue": "Tokyo", "num_horses": 3})
            row = {name: float(horse + day % 4) for name in fit.FEATURE_COLUMNS}
            row.update({name: "sunny" for name in fit.CATEGORICAL_COLUMNS})
            row["safe_horse_number"], row["safe_weight_change"] = float(horse), np.nan
            rows.append(row)
    features = pd.DataFrame(rows, columns=fit.FEATURE_COLUMNS)
    features.attrs.update(current_outcomes_consumed=False, same_day_or_future_history_used=False,
                          point_in_time_audit={"fixture": True})
    return pd.DataFrame(target), features


@pytest.fixture
def setup(tmp_path, monkeypatch):
    generated = tmp_path / "reports/generated"
    run = generated / "full-benchmark/fresh-run"
    run.mkdir(parents=True)
    snapshot = tmp_path / "snapshot.db"
    snapshot.write_bytes(b"immutable fixture")
    target, features = inputs()
    calls = []
    def load(path, lower, upper):
        calls.append(("load", str(path), lower, upper))
        return target.copy(), target.copy(), {"fixture": True}
    def build(current, history):
        calls.append(("build", len(current), len(history)))
        return features.copy()
    def train(matrix, labels, preset, *, n_threads, progress=None, progress_start=0):
        calls.append(("train", len(matrix), preset["id"], n_threads))
        return FixtureBooster()
    monkeypatch.setattr(worker, "OUTPUT_ROOT", generated)
    monkeypatch.setattr(worker, "_modules", lambda: (SimpleNamespace(load_db_frames=load, build_training_features=build), fit))
    monkeypatch.setattr(worker, "resource_gate", lambda *_args: {"fixture": True})
    monkeypatch.setattr(fit, "source_hashes", lambda: {"fixture": "a" * 64})
    monkeypatch.setattr(fit, "_train_booster", train)
    request = {"mode": "A", "device": "cpu", "workers": 1, "threads": 4,
               "input_mode": "cold", "snapshot_path": str(snapshot), "snapshot_sha256": worker.file_hash(snapshot),
               "period": ["2016-01", "2026-09"], "cache_dir": str(run / "cache"),
               "expected_source_hashes": {"fixture": "a" * 64}, "full_row_timing": False}
    return request, run, calls, target, features


def test_complete_cold_worker_records_all_vectors_and_exclusive_isolated_model(setup):
    request, run, calls, _, _ = setup
    original = fit._train_booster
    report = worker.run_worker(request, run / "A.json")
    assert report["status"] == "completed"
    assert report["cache_created"] is True
    assert report["cohort"]["runner_count"] == 180
    assert report["fit_result"]["selected_preset"] in {"lean", "standard"}
    assert report["fit_result"]["test_used_for_selection_or_fit"] is False
    assert fit._train_booster is original
    with np.load(report["vectors"]["path"], allow_pickle=False) as arrays:
        assert set(arrays.files) == {"inner_lean", "inner_standard", "cal", "test"}
        assert len(arrays["test"]) == 36
    assert report["artifact"]["hash_unchanged_after_reload"] is True
    assert report["artifact"]["max_abs_reload_prediction_difference"] == 0
    assert report["artifact_published"] is report["active_model_changed"] is False
    expected = sum(report["timings"][name] for name in ("db_read_seconds", "feature_generation_seconds",
        "prepare_validation_seconds", "fit_selection_calibration_evaluation_seconds", "model_serialize_and_hash_seconds",
        "model_reload_and_verify_seconds"))
    assert report["timings"]["run_processing_seconds"] == pytest.approx(expected)
    assert len([call for call in calls if call[0] == "train"]) == 3
    assert sorted(row["role"] for row in report["fit_stages"] if row["kind"] == "booster") == ["final", "inner_lean", "inner_standard"]


def test_cache_mode_skips_db_features_preserves_nan_and_digests(setup):
    request, run, calls, _, _ = setup
    cold = worker.run_worker(request, run / "A.json")
    before = list(calls)
    cached = worker.run_worker({**request, "mode": "B", "workers": 2, "threads": 2, "input_mode": "cache"}, run / "B.json")
    assert cached["cache_created"] is False
    assert cached["input_hashes"] == cold["input_hashes"]
    assert cached["cache_manifest_sha256"] == cold["cache_manifest_sha256"]
    assert not any(call[0] in {"load", "build"} for call in calls[len(before):])
    assert cached["fit_result"]["training_parallel_workers"] == 2
    assert cached["fit_result"]["final_fit_native_threads"] == 4
    _, features, _ = worker.read_cache(Path(request["cache_dir"]), request, fit)
    assert features.safe_weight_change.isna().all()


def test_repeated_cold_rebuilds_but_never_overwrites_cache(setup):
    request, run, calls, _, _ = setup
    first = worker.run_worker(request, run / "A.json")
    second = worker.run_worker({**request, "mode": "A2"}, run / "A2.json")
    assert second["cache_created"] is False
    assert second["cache_manifest_sha256"] == first["cache_manifest_sha256"]
    assert len([call for call in calls if call[0] == "load"]) == 2
    assert len([call for call in calls if call[0] == "build"]) == 2


def test_all_row_timing_has_no_test_or_calibration_claim(setup):
    request, run, calls, _, _ = setup
    report = worker.run_worker({**request, "full_row_timing": True}, run / "all.json")
    probe = report["full_row_timing"]
    assert probe["runner_count"] == 180
    assert probe["preset"]["id"] == "standard"
    assert probe["finite_sample_count"] == 32
    assert probe["independent_test_evaluated"] is probe["calibrated"] is probe["model_saved"] is False
    assert probe["in_sample_metrics_reported"] is False
    assert calls[-1][0:3] == ("train", 180, "standard")


@pytest.mark.parametrize("field,value", [("period", ["2024-01", "2025-12"]), ("workers", True),
    ("threads", 8), ("device", "cuda"), ("input_mode", "live"), ("snapshot_sha256", "not-a-hash")])
def test_fixed_conditions_rejected_before_load(setup, field, value):
    request, run, calls, _, _ = setup
    with pytest.raises(ValueError):
        worker.run_worker({**request, field: value}, run / "invalid.json")
    assert calls == []
    assert not (run / "invalid.json").exists()


def test_gpu_two_workers_forbidden_before_execution(setup):
    request, run, calls, _, _ = setup
    with pytest.raises(ValueError, match="fixed-conditions"):
        worker.run_worker({**request, "device": "gpu", "workers": 2}, run / "invalid.json")
    assert not calls


@pytest.mark.parametrize("damage", ["source", "period", "pit", "order", "file"])
def test_cache_damage_is_not_silently_rebuilt(setup, damage):
    request, run, _, _, _ = setup
    worker.run_worker(request, run / "A.json")
    cache = Path(request["cache_dir"])
    manifest = json.loads((cache / "manifest.json").read_text(encoding="utf-8"))
    if damage == "source": manifest["source_hashes"] = {"fixture": "b" * 64}
    if damage == "period": manifest["period"] = ["2020-01", "2026-09"]
    if damage == "pit": manifest["point_in_time_audit"]["same_day_or_future_history_used"] = True
    if damage == "order": manifest["feature_columns"].reverse()
    if damage == "file":
        with (cache / "features.parquet").open("ab") as stream: stream.write(b"corruption")
    else:
        (cache / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="cache"):
        worker.run_worker({**request, "input_mode": "cache"}, run / "damaged.json")
    report = json.loads((run / "damaged.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed"


def test_exception_restores_all_in_memory_overrides(setup, monkeypatch):
    request, run, _, _, _ = setup
    originals = {name: getattr(fit, name) for name in ("_resource_limits", "_train_booster", "_finite_predictions", "fit_temperature", "probability_metrics", "accuracy_metrics")}
    def fail(*_args, **_kwargs): raise RuntimeError("fixture interrupted")
    monkeypatch.setattr(fit, "fit_shared_model", fail)
    with pytest.raises(RuntimeError, match="interrupted"):
        worker.run_worker(request, run / "failed.json")
    assert all(getattr(fit, name) is value for name, value in originals.items())
    assert json.loads((run / "failed.json").read_text(encoding="utf-8"))["status"] == "failed"


def test_existing_outputs_and_outside_run_boundary_fail_closed(setup):
    request, run, calls, _, _ = setup
    (run / "existing.json").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="boundary"):
        worker.run_worker(request, run / "existing.json")
    with pytest.raises(ValueError, match="boundary"):
        worker.run_worker(request, run.parent / "outside.json")
    assert (run / "existing.json").read_text() == "keep"
    assert calls == []


def test_snapshot_mutation_yields_failed_evidence(setup, monkeypatch):
    request, run, _, _, _ = setup
    original = fit.fit_shared_model
    def mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        Path(request["snapshot_path"]).write_bytes(b"mutated snapshot")
        return result
    monkeypatch.setattr(fit, "fit_shared_model", mutate)
    with pytest.raises(ValueError, match="mutated"):
        worker.run_worker(request, run / "mutated.json")
    assert json.loads((run / "mutated.json").read_text(encoding="utf-8"))["status"] == "failed"


def test_gpu_wrapper_preserves_fixed_params_and_records_real_fit_log(setup, monkeypatch):
    request, run, _, _, _ = setup
    captured, owner = [], {}
    def register(logger): owner["logger"] = logger
    def dataset(matrix, **kwargs):
        assert kwargs["categorical_feature"] == list(fit.CATEGORICAL_COLUMNS)
        return matrix
    def train(params, data, *, num_boost_round):
        captured.append((params, len(data), num_boost_round))
        owner["logger"].info("[LightGBM] [Info] Using GPU Device: fixture GPU")
        return FixtureBooster()
    fake_lgb = SimpleNamespace(register_logger=register, Dataset=dataset, train=train)
    monkeypatch.setitem(sys.modules, "lightgbm", fake_lgb)
    report = worker.run_worker({**request, "device": "gpu"}, run / "gpu.json")
    assert report["gpu_learner_confirmed"] is True
    assert len(captured) == 3
    assert [rounds for _, _, rounds in captured] == [120, 200, 120]
    assert all(params["device_type"] == "gpu" and params["max_bin"] == 63
               and params["objective"] == "regression" and params["learning_rate"] == .05
               and params["seed"] == params["data_random_seed"] == 42
               and params["num_threads"] == 4 for params, _, _ in captured)
    assert report["artifact"]["hash_unchanged_after_reload"]


def test_gpu_missing_device_evidence_fails_without_speedup_claim(setup, monkeypatch):
    request, run, _, _, _ = setup
    monkeypatch.setitem(sys.modules, "lightgbm", SimpleNamespace(
        register_logger=lambda _logger: None, Dataset=lambda matrix, **_kwargs: matrix,
        train=lambda *_args, **_kwargs: FixtureBooster()))
    with pytest.raises(ValueError, match="gpu-learner-not-confirmed"):
        worker.run_worker({**request, "device": "gpu"}, run / "gpu-unverified.json")
    report = json.loads((run / "gpu-unverified.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed" and report["gpu_learner_confirmed"] is False
    assert not (run / "gpu-unverified.model.joblib").exists()


@pytest.mark.parametrize("suffix", ["-wal", "-journal"])
def test_pending_snapshot_transaction_rejected_before_load(setup, suffix):
    request, run, calls, _, _ = setup
    Path(request["snapshot_path"] + suffix).write_bytes(b"pending")
    with pytest.raises(ValueError, match="checkpointed"):
        worker.run_worker(request, run / "pending.json")
    assert not calls


def test_manifest_key_has_ordered_columns_and_full_source_lineage(setup):
    request, _, _, _, _ = setup
    key = worker.cache_key(request, fit)
    assert key["schema"] == "full-db-shared-raw47-cache-v1"
    assert key["snapshot_sha256"] == request["snapshot_sha256"]
    assert key["source_hashes"] == request["expected_source_hashes"]
    assert key["feature_columns"] == list(fit.FEATURE_COLUMNS)
    assert key["history_policy"] == worker.HISTORY_POLICY
    reversed_key = {**key, "feature_columns": list(reversed(key["feature_columns"]))}
    assert worker.canonical_hash(reversed_key) != worker.canonical_hash(key)
