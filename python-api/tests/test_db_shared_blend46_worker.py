"""Offline fixed46 blend tests; never use live DB/server or model registry."""
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
worker = importlib.import_module("evaluate_db_shared_blend46_worker")
helper = worker._helper()
fit, acceleration, cache, benchmark, venue = helper._modules()


class FixtureBooster:
    def __init__(self, slope=1.):
        self.slope = slope

    def predict(self, encoded, *, num_threads):
        return -encoded["safe_horse_number"].to_numpy(dtype=float) * self.slope

    def model_to_string(self):
        return "fixture-booster-" + str(self.slope)


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    generated = tmp_path / "reports/generated"
    output = generated / "new-blend-study"
    output.mkdir(parents=True)
    raw = generated / "immutable-raw47-cache"
    monkeypatch.setattr(worker, "OUTPUT_ROOT", generated)
    monkeypatch.setattr(fit, "source_hashes", lambda: {"fixture": "a" * 64})
    targets, values = [], []
    for year, weather in ((2013, "sunny"), (2015, "newcal"), (2016, "newtest")):
        for day in range(6):
            current = (date(year, 1, 1) + timedelta(days=day)).strftime("%Y%m%d")
            for horse in range(1, 4):
                targets.append({"race_id": current + "0001", "race_date": current,
                    "horse_id": "horse" + str(horse), "horse_number": horse, "finish": horse,
                    "time_seconds": 95. + horse + (day % 3) * .1, "distance": 1600,
                    "surface": "turf", "venue": "東京" if day < 4 else "unknown", "num_horses": 3})
                row = {name: float(horse + day % 4) for name in fit.FEATURE_COLUMNS}
                for name in fit.CATEGORICAL_COLUMNS:
                    row[name] = "known"
                row["safe_weather"] = weather
                row["safe_weight_change"] = np.nan
                row["safe_horse_number"] = float(horse)
                values.append(row)
    target = pd.DataFrame(targets)
    features = pd.DataFrame(values, columns=fit.FEATURE_COLUMNS)
    for name in features:
        features[name] = features[name].astype("string" if name in fit.CATEGORICAL_COLUMNS else "float32")
    features.attrs.update(current_outcomes_consumed=False, same_day_or_future_history_used=False,
        point_in_time_audit={"strict_predicate": "source_race_date < target_race_date", "future_leakage_rows": 0,
            "same_day_future_references": 0, "same_day_results_excluded": True})
    request = {"schema": worker.SCHEMA, "job_id": "cpu_Y2016", "cache_dir": str(raw),
        "cache_manifest_sha256": None, "expected_source_hashes": fit.source_hashes(),
        "expected_worker_source_hashes": {name: worker.file_hash(ROOT / name) for name in worker.REQUIRED_HELPERS},
        "protocol_sha256": "b" * 64,
        "feature_columns": [name for name in fit.FEATURE_COLUMNS if name != worker.OMITTED_FEATURE],
        "device": "cpu", "n_threads": 2, "execution_receipt": None, "save_models": False,
        "fold": {"fold_id": "Y2016", "test_year": 2016, "train_date_from": "20130101",
            "train_date_to": "20141231", "cal_date_from": "20150101", "cal_date_to": "20151231",
            "test_date_from": "20160101", "test_date_to": "20161231"}}
    benchmark.create_cache(raw, {"snapshot_sha256": "c" * 64, "period": ["2013-01", "2026-09"],
        "expected_source_hashes": fit.source_hashes()}, target, features, {"fixture": True}, fit)
    request["cache_manifest_sha256"] = worker.file_hash(raw / "manifest.json")
    return request, output / "cpu_Y2016.json", target, features


@pytest.mark.parametrize("updates", [
    {"schema": "other"}, {"n_threads": 8}, {"n_threads": True}, {"save_models": "yes"},
    {"job_id": "../outside"}, {"device": "cuda"}, {"device": "gpu"},
    {"execution_receipt": {"gpu": True}}, {"protocol_sha256": "x"},
    {"feature_columns": list(fit.FEATURE_COLUMNS)}, {"feature_columns": []},
    {"feature_columns": list(reversed([name for name in fit.FEATURE_COLUMNS if name != worker.OMITTED_FEATURE]))},
])
def test_fixed_options_and_exact46_reject_invalid(inputs, updates):
    request, output, *_ = inputs
    request.update(updates)
    with pytest.raises(ValueError):
        worker.validate_request(request, output, fit, cache)


@pytest.mark.parametrize("updates", [
    {"train_date_to": "20151231"}, {"cal_date_from": "20141231"}, {"test_date_to": "20161230"},
    {"test_year": 2015}, {"test_year": True}, {"train_date_from": "20160101"}, {"fold_id": "../bad"},
])
def test_chronological_predefined_fold_cannot_be_tuned(inputs, updates):
    request, output, *_ = inputs
    request["fold"].update(updates)
    with pytest.raises(ValueError):
        worker.validate_request(request, output, fit, cache)


def test_output_boundary_and_existing_files_are_protected(inputs):
    request, output, *_ = inputs
    with pytest.raises(ValueError, match="separation"):
        worker.validate_request(request, Path(request["cache_dir"]) / "new.json", fit, cache)
    with pytest.raises(ValueError, match="new-research"):
        worker.validate_request(request, ROOT / "python-api/models/new.json", fit, cache)
    output.parent.joinpath(request["job_id"] + "-races.parquet").touch()
    with pytest.raises(ValueError, match="already-exists"):
        worker.validate_request(request, output, fit, cache)


@pytest.mark.parametrize("kind", ["missing", "changed", "traversal", "absolute"])
def test_required_helper_pins_strict(inputs, kind):
    request, *_ = inputs
    pins = request["expected_worker_source_hashes"]
    if kind == "missing":
        pins.pop(next(iter(pins)))
    elif kind == "changed":
        pins[next(iter(pins))] = "d" * 64
    elif kind == "traversal":
        pins["../other.py"] = "d" * 64
    else:
        pins[str(ROOT / "scripts/evaluate_db_shared_blend46_worker.py")] = "d" * 64
    with pytest.raises(ValueError):
        worker.validate_helper_pins(request)


def test_split_uses_whole_races_and_dates(inputs):
    request, _, target, _ = inputs
    positions = worker.fold_positions(target, request["fold"])
    assert all(len(value) == 18 for value in positions.values())
    assert max(target.iloc[positions["TRAIN"]].race_date) < min(target.iloc[positions["CAL"]].race_date)
    assert max(target.iloc[positions["CAL"]].race_date) < min(target.iloc[positions["TEST"]].race_date)
    bad = target.copy()
    bad.loc[positions["TEST"][0], "race_id"] = bad.iloc[positions["TRAIN"][0]].race_id
    with pytest.raises(ValueError, match="overlap"):
        worker.fold_positions(bad, request["fold"])


def test_half_mix_is_raw_and_fallback_exact():
    values = worker.blend_scores([1., 3., 5.], [3., 1., 99.], np.array([True, True, False]))
    np.testing.assert_array_equal(values["global"], [1., 3., 5.])
    np.testing.assert_array_equal(values["venue_only"], [3., 1., 5.])
    np.testing.assert_array_equal(values["blend50"], [2., 2., 5.])
    with pytest.raises(ValueError):
        worker.blend_scores([1., 3.], [np.nan, 1.], np.array([True, False]))


def test_probability_additive_metrics_match_official_and_exclude_deadheat(inputs):
    _, _, target, _ = inputs
    target = target.iloc[:6].copy().reset_index(drop=True)
    probabilities = np.array([.5, .3, .2, .5, .3, .2])
    rows = worker.probability_records(target, probabilities)
    metrics = fit.probability_metrics(target, probabilities)
    assert rows.nll.mean() == pytest.approx(metrics["nll"])
    assert rows.brier.mean() == pytest.approx(metrics["brier"])
    assert abs((rows.top1_confidence - rows.probability_top1_correct).mean()) == pytest.approx(metrics["selection_absolute_bias"])
    target.loc[1, "finish"] = 1
    rows = worker.probability_records(target, probabilities)
    assert rows.probability_eligible.tolist() == [False, True]
    assert np.isnan(rows.iloc[0].nll)
    assert rows.iloc[0].selection_bin == -1
    with pytest.raises(ValueError, match="normalized"):
        worker.probability_records(target, np.ones(6) * .2)


def test_full_worker_fit_and_calibration_are_train_and_cal_only(inputs, monkeypatch):
    request, output, target, _ = inputs
    monkeypatch.setattr(helper, "_runtime", lambda *_: {"device": "fixture_cpu"})
    monkeypatch.setattr(sqlite3, "connect", lambda *_, **__: pytest.fail("DB connection attempted"))
    before = {path.name: worker.file_hash(path) for path in Path(request["cache_dir"]).iterdir()}
    train_calls, calibration_calls = [], []
    def train(matrix, labels, preset, categorical, threads):
        train_calls.append((matrix.copy(), labels.copy(), preset.copy(), threads))
        return FixtureBooster(float(len(train_calls)))
    monkeypatch.setattr(helper, "_cpu_train", train)
    original_temperature = fit.fit_temperature
    def temperature(cal, scores):
        calibration_calls.append((cal.copy(), scores.copy()))
        return original_temperature(cal, scores)
    monkeypatch.setattr(fit, "fit_temperature", temperature)
    result = worker.run_worker(request, output)
    assert len(train_calls) == 1  # too few TRAIN rows for all specialists
    assert train_calls[0][3] == 2
    assert train_calls[0][2]["num_boost_round"] == 200
    assert len(calibration_calls) == 3
    assert all(set(frame.race_date.str[:4]) == {"2015"} for frame, _ in calibration_calls)
    assert all(record["fallback_to_global"] for record in result["specialist_coverage"])
    assert result["categories"]["safe_weather"] == ["sunny"]
    assert train_calls[0][0].safe_weight_change.isna().all()
    assert worker.OMITTED_FEATURE not in train_calls[0][0]
    assert before == {path.name: worker.file_hash(path) for path in Path(request["cache_dir"]).iterdir()}
    assert all(result["proofs"][field] is False for field in ("zero_imputation", "roi_evaluated", "active_model_changed",
        "production_changed", "forward_collector_accessed", "eligible_for_ev", "artifact_published"))
    races = pd.read_parquet(result["artifacts"]["races"]["path"])
    assert len(races) == 36 and set(races.strategy) == set(worker.STRATEGIES)
    for strategy in worker.STRATEGIES:
        rows = races.query("strategy == @strategy and split == 'TEST'")
        assert rows.runner_count.sum() == 18
        expected = result["strategies"][strategy]["accuracy_metrics"]["TEST"]
        assert rows.rank_correlation.mean() == pytest.approx(expected["rank_correlation"])
        assert np.sqrt(rows.squared_speed_error_sum.sum() / rows.runner_count.sum()) == pytest.approx(expected["rmse_speed_mps"])
        assert result["strategies"][strategy]["probability_validation"]["eligible_for_ev"] is False


def test_specialist_and_global_use_same_encoder_baseline_and_labels(inputs, monkeypatch):
    import joblib
    request, output, *_ = inputs
    request["save_models"] = True
    monkeypatch.setattr(helper, "_runtime", lambda *_: {"device": "fixture_cpu"})
    calls = []
    monkeypatch.setattr(helper, "_cpu_train", lambda *args: (calls.append(args) or FixtureBooster(float(len(calls)))))
    official_eligibility = venue.specialist_eligibility
    def eligibility(frame, races, dates, runners):
        assert (races, dates, runners) == (500, 40, 2500)
        receipt = official_eligibility(frame, races, dates, runners)
        receipt["available"] = len(frame) > 0  # toy test only; production thresholds remain fixed
        return receipt
    monkeypatch.setattr(venue, "specialist_eligibility", eligibility)
    result = worker.run_worker(request, output)
    assert len(calls) == 2
    np.testing.assert_array_equal(calls[0][1][:12], calls[1][1])
    pd.testing.assert_frame_equal(calls[0][0].iloc[:12], calls[1][0])
    runners = pd.read_parquet(result["artifacts"]["runners"]["path"])
    test = runners.query("split == 'TEST'")
    pivot = test.pivot(index=["race_id", "horse_number"], columns="strategy", values="raw_score")
    np.testing.assert_allclose(pivot.blend50, (pivot["global"] + pivot.venue_only) * .5, rtol=0, atol=0)
    unknown = test.query("venue == 'unknown'")
    assert unknown.specialist_available.eq(False).all()
    artifact = joblib.load(result["artifacts"]["model"]["path"])
    assert artifact["schema"] == worker.MODEL_SCHEMA
    assert artifact["eligible_for_ev"] is False and artifact["artifact_published"] is False
    assert set(artifact["calibrators"]) == set(worker.STRATEGIES)
    assert set(artifact["models"]["specialists"]) == {"東京"}
    assert result["artifacts"]["model"]["max_abs_reload_difference"] == 0.


def test_gpu_receipt_validated_before_heavy_pipeline(inputs, monkeypatch):
    request, output, *_ = inputs
    request.update(device="gpu", execution_receipt={"fixture": True})
    events = []
    monkeypatch.setattr(helper, "_bootstrap_gpu", lambda value: events.append("bootstrap"))
    def modules():
        assert events == ["bootstrap"]
        raise RuntimeError("after-bootstrap")
    monkeypatch.setattr(helper, "_modules", modules)
    with pytest.raises(RuntimeError, match="after-bootstrap"):
        worker.run_worker(request, output)


def test_real_cpu_booster_retains46_schema_and_serializes(inputs, monkeypatch):
    request, output, *_ = inputs
    request["save_models"] = True
    monkeypatch.setattr(helper, "_runtime", lambda *_: {"device": "fixture_cpu"})
    result = worker.run_worker(request, output)
    assert result["status"] == "completed"
    assert result["fit_receipts"][0]["native_threads"] == 2
    assert len(result["feature_columns"]) == 46
    assert len(result["artifacts"]["model"]["reload_parity"]) == 1
    assert result["artifacts"]["model"]["max_abs_reload_difference"] == 0.
