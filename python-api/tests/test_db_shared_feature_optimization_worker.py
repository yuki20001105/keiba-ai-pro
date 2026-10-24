"""Offline worker fixtures only; no real DB, API, publication or active model."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import importlib
from pathlib import Path
import sqlite3
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "python-api"), str(ROOT / "keiba")]
worker = importlib.import_module("optimize_db_shared_features_worker")
fit, acceleration, cache, benchmark, venue = worker._modules()


class FixtureBooster:
    def predict(self, encoded, *, num_threads):
        return -encoded["safe_horse_number"].to_numpy(dtype=float)


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    generated = tmp_path / "reports/generated"
    output = generated / "new-study"
    output.mkdir(parents=True)
    raw = generated / "immutable-raw47-cache"
    monkeypatch.setattr(worker, "OUTPUT_ROOT", generated)
    monkeypatch.setattr(fit, "source_hashes", lambda: {"fixture": "a" * 64})
    targets, values = [], []
    for day in range(12):
        current = (date(2019, 1, 1) + timedelta(days=day)).strftime("%Y%m%d")
        for horse in range(1, 4):
            targets.append({"race_id": current + "0001", "race_date": current,
                "horse_id": "horse" + str(horse), "horse_number": horse, "finish": horse,
                "time_seconds": 95. + horse + (day % 3) * .1, "distance": 1600,
                "surface": "turf", "venue": "Tokyo", "num_horses": 3})
            row = {name: float(horse + day % 4) for name in fit.FEATURE_COLUMNS}
            for name in fit.CATEGORICAL_COLUMNS:
                row[name] = "known"
            row["safe_weather"] = "sunny" if day < 6 else "new-eval-weather"
            row["safe_weight_change"] = np.nan
            row["safe_horse_number"] = float(horse)
            values.append(row)
    target = pd.DataFrame(targets)
    features = pd.DataFrame(values, columns=fit.FEATURE_COLUMNS)
    for name in features:
        features[name] = features[name].astype("string" if name in fit.CATEGORICAL_COLUMNS else "float32")
    features.attrs.update(current_outcomes_consumed=False, same_day_or_future_history_used=False,
        point_in_time_audit={"strict_predicate": "source_race_date < target_race_date",
            "future_leakage_rows": 0, "same_day_future_references": 0, "same_day_results_excluded": True})
    request = {"schema": worker.SCHEMA, "candidate_id": "baseline47", "phase": "development",
        "cache_dir": str(raw), "expected_source_hashes": fit.source_hashes(), "protocol_sha256": "b" * 64,
        "feature_columns": list(fit.FEATURE_COLUMNS), "device": "cpu", "n_threads": 2,
        "execution_receipt": None, "save_models": False,
        "splits": [{"split_id": "fold1", "train_date_from": "20190101", "train_date_to": "20190106",
                    "eval_date_from": "20190107", "eval_date_to": "20190112"}]}
    adapted = {"snapshot_sha256": "c" * 64, "period": ["2016-01", "2026-09"],
               "expected_source_hashes": fit.source_hashes()}
    benchmark.create_cache(raw, adapted, target, features, {"fixture": True}, fit)
    request["cache_manifest_sha256"] = worker.file_hash(raw / "manifest.json")
    return request, output / "baseline47.json", target, features


def test_cache_is_readonly_and_identical_under_existing_verifier(inputs):
    request, output, target, features = inputs
    before = {path.name: worker.file_hash(path) for path in Path(request["cache_dir"]).iterdir()}
    loaded_target, loaded_features, manifest = worker.load_prepared(request, fit, cache, benchmark)
    pd.testing.assert_frame_equal(loaded_target, fit._prepare(target, features)[0])
    pd.testing.assert_frame_equal(loaded_features, features)
    assert manifest["race_count"] == 12
    assert before == {path.name: worker.file_hash(path) for path in Path(request["cache_dir"]).iterdir()}


@pytest.mark.parametrize("changes", [
    {"phase": "unknown"}, {"device": "cuda"}, {"n_threads": 8}, {"n_threads": True},
    {"save_models": "true"}, {"feature_columns": []}, {"feature_columns": ["odds"]},
    {"feature_columns": ["safe_horse_number", "safe_horse_number"]},
    {"feature_columns": list(reversed(fit.FEATURE_COLUMNS))},
    {"protocol_sha256": "not-a-hash"}, {"device": "gpu", "execution_receipt": None},
    {"device": "cpu", "execution_receipt": {"gpu": True}},
])
def test_invalid_execution_or_subset_is_rejected(inputs, changes):
    request, output, *_ = inputs
    request.update(changes)
    with pytest.raises(ValueError):
        worker.validate_request(request, output, fit, cache)


@pytest.mark.parametrize("changes", [
    {"eval_date_from": "20190106"}, {"train_date_to": "20190113"},
    {"train_date_from": "20191301"}, {"eval_date_to": "20190106"},
    {"eval_date_to": "20220522"},
])
def test_split_dates_and_development_boundary_are_strict(inputs, changes):
    request, output, *_ = inputs
    request["splits"][0].update(changes)
    with pytest.raises(ValueError):
        worker.validate_request(request, output, fit, cache)


def test_unknown_request_fields_and_existing_outputs_are_rejected(inputs):
    request, output, *_ = inputs
    with pytest.raises(ValueError, match="schema-invalid"):
        worker.validate_request({**request, "optuna": True}, output, fit, cache)
    output.touch()
    with pytest.raises(ValueError, match="new-research-output"):
        worker.validate_request(request, output, fit, cache)


def test_output_cannot_be_written_into_cache_or_production(inputs):
    request, output, *_ = inputs
    with pytest.raises(ValueError, match="separation"):
        worker.validate_request(request, Path(request["cache_dir"]) / "candidate.json", fit, cache)
    with pytest.raises(ValueError, match="new-research-output"):
        worker.validate_request(request, ROOT / "python-api/models/candidate.json", fit, cache)


def test_manifest_or_source_change_is_rejected(inputs):
    request, *_ = inputs
    request["cache_manifest_sha256"] = "d" * 64
    with pytest.raises(ValueError, match="manifest-hash"):
        worker.load_prepared(request, fit, cache, benchmark)
    request["cache_manifest_sha256"] = worker.file_hash(Path(request["cache_dir"]) / "manifest.json")
    request["expected_source_hashes"] = {"fixture": "e" * 64}
    with pytest.raises(ValueError, match="frozen-source"):
        worker.load_prepared(request, fit, cache, benchmark)


def test_encoder_is_train_only_and_retains_missing_without_zero_fill(inputs):
    _, _, target, features = inputs
    columns = [name for name in fit.FEATURE_COLUMNS if name in {"safe_horse_number", "safe_weather", "safe_weight_change"}]
    encoder, train, evaluate, diagnostics = worker.encode_subset(features.iloc[:18], features.iloc[18:], columns, fit)
    assert encoder.categories["safe_weather"] == ["sunny"]
    assert evaluate.safe_weather.isna().all()
    assert train.safe_weight_change.isna().all()
    assert evaluate.safe_weight_change.isna().all()
    assert list(train.columns) == columns
    assert diagnostics["safe_weight_change"] == "all_missing_in_training"


def test_race_metrics_pool_to_same_physical_rmse(inputs):
    _, _, target, _ = inputs
    baseline = fit.fit_speed_deviation_baseline(target.iloc[:18])
    evaluation = target.iloc[18:].reset_index(drop=True)
    scores = -evaluation.horse_number.to_numpy(dtype=float)
    records = venue.race_metric_records(evaluation, scores, baseline)
    metrics = fit.accuracy_metrics(evaluation, scores, baseline)
    assert records.rank_correlation.mean() == pytest.approx(metrics["rank_correlation"])
    assert np.sqrt(records.squared_error_sum.sum() / records.runner_count.sum()) == pytest.approx(metrics["rmse_speed_mps"])


def test_offline_run_cannot_touch_db_publish_or_activate(inputs, monkeypatch):
    request, output, *_ = inputs
    calls = []
    monkeypatch.setattr(worker, "_runtime", lambda *_: {"device": "fixture-cpu"})
    monkeypatch.setattr(sqlite3, "connect", lambda *_, **__: pytest.fail("DB connection attempted"))
    def train(encoded, labels, preset, categorical, threads):
        calls.append((encoded.copy(), labels.copy(), preset.copy(), threads))
        return FixtureBooster()
    monkeypatch.setattr(worker, "_cpu_train", train)
    report = worker.run_worker(request, output)
    assert report["status"] == "completed"
    assert len(calls) == 1
    assert calls[0][3] == 2
    assert calls[0][2]["num_boost_round"] == 200
    assert report["proofs"]["artifact_published"] is False
    assert report["proofs"]["active_model_changed"] is False
    assert report["proofs"]["production_changed"] is False
    assert report["proofs"]["forward_collector_accessed"] is False
    assert report["proofs"]["roi_evaluated"] is False
    assert report["splits"][0]["categories"]["safe_weather"] == ["sunny"]
    records = pd.read_parquet(output.parent / "baseline47-races.parquet")
    assert len(records) == 6
    assert records.runner_count.sum() == 18
    with np.load(output.parent / "baseline47-predictions.npz", allow_pickle=False) as predictions:
        assert len(predictions["fold1__raw_scores"]) == 18
        assert np.isfinite(predictions["fold1__predicted_speed_mps"]).all()


def test_same_train_reused_without_evaluation_refit(inputs, monkeypatch):
    request, output, *_ = inputs
    request["phase"] = "retrospective"
    request["splits"] = [
        {"split_id": "CAL", "train_date_from": "20190101", "train_date_to": "20190106",
         "eval_date_from": "20190107", "eval_date_to": "20190109"},
        {"split_id": "TEST", "train_date_from": "20190101", "train_date_to": "20190106",
         "eval_date_from": "20190110", "eval_date_to": "20190112"}]
    monkeypatch.setattr(worker, "_runtime", lambda *_: {"device": "fixture-cpu"})
    calls = []
    def train(*_):
        calls.append(True)
        return FixtureBooster()
    monkeypatch.setattr(worker, "_cpu_train", train)
    result = worker.run_worker(request, output)
    assert len(calls) == 1
    assert result["splits"][0]["training_fit_reused"] is False
    assert result["splits"][1]["training_fit_reused"] is True
    assert result["splits"][1]["timings"]["fit"] == 0
    assert result["splits"][1]["fit_origin_split_id"] == "CAL"
    assert result["splits"][0]["baseline_sha256"] == result["splits"][1]["baseline_sha256"]


def test_full_fit_has_no_in_sample_metrics_calibration_or_probability_claim(inputs, monkeypatch):
    import joblib
    request, output, *_ = inputs
    request.update(phase="full_fit", save_models=True)
    request["splits"][0].update(train_date_to="20190112", eval_date_from=None, eval_date_to=None)
    monkeypatch.setattr(worker, "_runtime", lambda *_: {"device": "fixture-cpu"})
    monkeypatch.setattr(worker, "_cpu_train", lambda *_: FixtureBooster())
    result = worker.run_worker(request, output)
    assert result["splits"][0]["evaluation"] is None
    assert result["splits"][0]["metrics"] is None
    assert result["proofs"]["calibration_claimed"] is False
    assert result["proofs"]["win_probability_claimed"] is False
    model = joblib.load(output.parent / "baseline47-fold1.research.joblib")
    assert model["schema"] == worker.MODEL_SCHEMA
    assert model["artifact_published"] is False
    assert model["eligible_for_ev"] is False
    assert not (output.parent / "baseline47-races.parquet").exists()


def test_full_fit_cannot_supply_eval_dates(inputs):
    request, output, *_ = inputs
    request["phase"] = "full_fit"
    with pytest.raises(ValueError, match="must-not-claim-evaluation"):
        worker.validate_request(request, output, fit, cache)


def test_gpu_bootstrap_configures_and_validates_before_pipeline_imports(monkeypatch):
    calls = []
    dummy = {"gpu-receipt": True}
    monkeypatch.setattr(acceleration, "configure_worker_imports", lambda receipt: calls.append(("configure", receipt)))
    monkeypatch.setattr(acceleration, "validate_execution_receipt", lambda receipt: calls.append(("validate", receipt)))
    def modules():
        assert calls == [("configure", dummy), ("validate", dummy)]
        raise RuntimeError("pipeline-import-reached-after-bootstrap")
    monkeypatch.setattr(worker, "_modules", modules)
    with pytest.raises(RuntimeError, match="after-bootstrap"):
        worker.run_worker({"device": "gpu", "execution_receipt": dummy}, "not-written.json")


def test_invalid_gpu_receipt_fails_before_heavy_imports(monkeypatch):
    import builtins
    original_import = builtins.__import__
    def imports(name, *args, **kwargs):
        if name in {"numpy", "pandas", "joblib"}:
            pytest.fail("Heavy dependency imported before rejecting invalid GPU receipt")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", imports)
    monkeypatch.setattr(worker, "_modules", lambda: pytest.fail("Experiment pipeline imported with invalid receipt"))
    with pytest.raises(ValueError, match="receipt-required"):
        worker.run_worker({"device": "gpu", "execution_receipt": None}, "not-written.json")
