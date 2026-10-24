"""Strict offline direct-win study tests: no live database or external access."""
from copy import deepcopy
from datetime import date, timedelta
import importlib
import json
from pathlib import Path
import sys

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "python-api"), str(ROOT / "keiba")]
worker = importlib.import_module("db_shared_win_direct_worker")


def fixture_frame(races_per_period=6):
    columns, categorical = worker.feature_contract("market46_extended")
    rows = []
    for year, weather in ((2016, "sunny"), (2021, "cal-only-weather"),
                          (2023, "select-only-weather"), (2025, "test-only-weather")):
        for day in range(races_per_period):
            current = (date(year, 1, 1) + timedelta(days=day)).strftime("%Y%m%d")
            for horse in range(1, 5):
                winner = (day % 4) + 1
                order = [winner] + [value for value in range(1, 5) if value != winner]
                row = {name: float(horse + day % 3) for name in columns if name not in worker.MARKET_COLUMNS}
                row.update({name: "known" for name in categorical})
                row.update(race_id=current + "0001", race_date=current, horse_number=horse,
                    finish=order.index(horse) + 1, odds=3. + horse,
                    tansho_payout=100 * (3 + horse) if horse == winner else 0,
                    expected_runner_count=4, raw_score=5. - horse, venue="東京", surface="turf",
                    safe_weather=weather, safe_weight_change=np.nan)
                rows.append(row)
    frame = pd.DataFrame(rows)
    groups = frame.groupby("race_id", sort=False)
    inverse = 1. / frame.odds
    frame["normalized_market_probability"] = inverse / inverse.groupby(frame.race_id).transform("sum")
    frame["log_odds"] = np.log(frame.odds)
    frame["market_rank"] = frame.horse_number.astype(float)
    frame["field_size"] = groups.race_id.transform("size").astype(float)
    return frame


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    root = tmp_path / "reports/generated"
    output_dir = root / "isolated"
    output_dir.mkdir(parents=True)
    frame_path = output_dir / "fixed-input.parquet"
    frame = fixture_frame()
    frame.to_parquet(frame_path, index=False)
    monkeypatch.setattr(worker, "OUTPUT_ROOT", root)
    columns, categorical = worker.feature_contract("market46_extended")
    request = {"schema": worker.SCHEMA, "job_id": "extended", "candidate_id": "market46_extended",
        "frame_path": str(frame_path), "frame_sha256": worker.file_hash(frame_path),
        "protocol_sha256": "b" * 64,
        "expected_source_hashes": {name: worker.file_hash(ROOT / name) for name in worker.REQUIRED_SOURCES},
        "feature_columns": columns, "categorical_columns": categorical, "device": "cpu", "n_threads": 2}
    return request, output_dir / "extended.json", frame


@pytest.mark.parametrize("candidate,count", [("market_only", 4), ("market46", 50), ("market46_extended", 92)])
def test_fixed_feature_contract(candidate, count):
    columns, categorical = worker.feature_contract(candidate)
    assert len(columns) == len(set(columns)) == count
    assert columns[:4] == list(worker.MARKET_COLUMNS)
    assert "safe_past5_top3_rate" not in columns
    assert not {"finish", "tansho_payout", "race_id", "raw_score"} & set(columns)
    assert set(categorical) <= set(columns)
    assert len(categorical) == (0 if candidate == "market_only" else 6)


@pytest.mark.parametrize("updates", [
    {"schema": "other"}, {"n_threads": 4}, {"n_threads": True}, {"device": "gpu"},
    {"candidate_id": "optimized-after-test"}, {"job_id": "../outside"},
    {"frame_sha256": "x"}, {"protocol_sha256": "x"}, {"feature_columns": []},
    {"categorical_columns": []}, {"unexpected_key": True}, {"frame_path": "missing.parquet"},
])
def test_request_rejects_changed_research_options(inputs, updates):
    request, output, _ = inputs
    request.update(updates)
    with pytest.raises(ValueError):
        worker.validate_request(request, output)


def test_ordered_columns_fixed_even_when_count_equal(inputs):
    request, output, _ = inputs
    request["feature_columns"] = list(reversed(request["feature_columns"]))
    with pytest.raises(ValueError, match="ordered"):
        worker.validate_request(request, output)


@pytest.mark.parametrize("kind", ["missing", "changed", "absolute", "traversal", "backslash"])
def test_source_pins_fail_closed(inputs, kind):
    request, _, _ = inputs
    pins = request["expected_source_hashes"]
    if kind == "missing":
        pins.pop(next(iter(pins)))
    elif kind == "changed":
        pins[next(iter(pins))] = "d" * 64
    elif kind == "absolute":
        pins[str(ROOT / "scripts/db_shared_win_direct_worker.py")] = "d" * 64
    elif kind == "traversal":
        pins["../other.py"] = "d" * 64
    else:
        pins["scripts\\other.py"] = "d" * 64
    with pytest.raises(ValueError):
        worker.validate_source_pins(request)


def test_output_existing_and_normal_models_protected(inputs):
    request, output, _ = inputs
    with pytest.raises(ValueError, match="nested-research"):
        worker.validate_request(request, ROOT / "python-api/models/model.json")
    (output.parent / (request["job_id"] + "-predictions.parquet")).touch()
    with pytest.raises(ValueError, match="already-exists"):
        worker.validate_request(request, output)


def test_input_hash_mismatch_rejected(inputs):
    request, output, _ = inputs
    request["frame_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="hash-mismatch"):
        worker.validate_request(request, output)


@pytest.mark.parametrize("kind", ["duplicate", "partial", "date", "metadata", "odds", "winner", "no-winner",
                                  "winner-payout", "loser-payout", "finish", "number"])
def test_whole_single_winner_cohort_validation(kind):
    frame = fixture_frame()
    if kind == "duplicate":
        frame = pd.concat([frame, frame.iloc[:1]], ignore_index=True)
    elif kind == "partial":
        frame = frame.iloc[1:].reset_index(drop=True)
    elif kind == "date":
        frame.loc[:3, "race_date"] = "20250231"
    elif kind == "metadata":
        frame.loc[1, "surface"] = "dirt"
    elif kind == "odds":
        frame.loc[1, "odds"] = np.nan
    elif kind == "winner":
        frame.loc[1, "finish"] = 1
    elif kind == "no-winner":
        frame.loc[:3, "finish"] = 2
    elif kind == "winner-payout":
        frame.loc[0, "tansho_payout"] = np.nan
    elif kind == "loser-payout":
        frame.loc[1, "tansho_payout"] = 400
    elif kind == "finish":
        frame.loc[1, "finish"] = 5
    else:
        frame["horse_number"] = frame.horse_number.astype(float)
        frame.loc[1, "horse_number"] = 1.5
    with pytest.raises(ValueError):
        worker.validate_frame(frame)


def test_fixed_whole_date_partitions_and_outside_rows_rejected():
    frame = fixture_frame()
    positions = worker.split_positions(frame)
    assert all(len(selected) == 24 for selected in positions.values())
    assert max(frame.iloc[positions["TRAIN"]].race_date) < min(frame.iloc[positions["CAL"]].race_date)
    bad = frame.copy()
    bad.loc[bad.race_date.str.startswith("2016"), "race_date"] = "20150101"
    with pytest.raises(ValueError):
        worker.split_positions(bad)


@pytest.mark.parametrize("column", worker.MARKET_COLUMNS)
def test_market_features_have_quote_derivation_not_arbitrary_values(column):
    frame = fixture_frame()
    frame.loc[0, column] += .01
    with pytest.raises(ValueError, match="derived-from-quotes"):
        worker.market_values(frame)


def test_market_ties_use_equal_minimum_rank_not_row_order():
    frame = fixture_frame().iloc[:4].copy()
    frame["odds"], frame["normalized_market_probability"] = 4., .25
    frame["log_odds"], frame["market_rank"] = np.log(4.), 1.
    result = worker.market_values(frame.sample(frac=1., random_state=2))
    np.testing.assert_array_equal(result, np.full(4, .25))


def test_encoder_train_only_keeps_fixed_constants_missing_and_unknown_categories():
    frame = fixture_frame()
    train, cal = frame.loc[frame.race_date.lt("20210101")], frame.loc[frame.race_date.ge("20210101")]
    columns, categorical = worker.feature_contract("market46")
    encoder = worker.TrainEncoder(columns, categorical).fit(train)
    result = encoder.transform(cal)
    assert list(result) == columns
    assert result.safe_weather.isna().all()
    assert result.safe_weight_change.isna().all()
    assert result.field_size.eq(0.).all()
    assert encoder.categories["safe_weather"] == ["sunny"]
    assert encoder.means["field_size"] == 4.
    assert encoder.standard_deviations["field_size"] == 1.
    assert encoder.diagnostics["safe_weight_change"]["all_missing"]
    assert encoder.diagnostics["field_size"]["constant_observed"]
    with pytest.raises(ValueError, match="new-fixed"):
        encoder.fit(cal)


@pytest.mark.parametrize("a,b", [(1., 0.), (2., -1.), (.5, 1.), (1.2, .3)])
def test_conditioned_calibration_formula_race_normalized(a, b):
    frame = fixture_frame().iloc[:8]
    original = np.tile([.4, .3, .2, .1], 2)
    market = worker.market_values(frame)
    result = worker.conditioned_probabilities(frame, original, market, a, b)
    expected = original ** a * market ** b
    expected /= pd.Series(expected).groupby(frame.race_id.reset_index(drop=True)).transform("sum").to_numpy()
    np.testing.assert_allclose(result, expected, rtol=1e-14)


@pytest.mark.parametrize("a,b", [(0., 0.), (2.1, 0.), (1., 1.1), (True, 0.), (1., np.nan)])
def test_conditioned_bounds_locked(a, b):
    frame = fixture_frame().iloc[:4]
    with pytest.raises(ValueError, match="bounds"):
        worker.conditioned_probabilities(frame, np.full(4, .25), worker.market_values(frame), a, b)


def test_calibration_rejects_outside_cal_dates_and_invalid_probabilities():
    frame = fixture_frame().iloc[:8]
    with pytest.raises(ValueError, match="cal-period"):
        worker.fit_conditioned_calibration(frame, np.tile([.4, .3, .2, .1], 2), worker.market_values(frame))
    with pytest.raises(ValueError, match="normalization"):
        worker.conditioned_probabilities(frame, np.full(8, .3), worker.market_values(frame), 1., 0.)


def test_conditioned_analytic_gradient_matches_central_difference(monkeypatch):
    frame = fixture_frame().loc[lambda value: value.race_date.str.startswith("2021")].reset_index(drop=True)
    original = np.tile([.4, .3, .2, .1], len(frame) // 4)
    market = worker.market_values(frame)
    from scipy.optimize import minimize
    def checked_minimize(fun, initial, **options):
        assert options["jac"] is True
        assert options["bounds"] == worker.CALIBRATION_BOUNDS
        assert list(initial) == [1., 0.]
        point = np.array([1.25, -.2])
        _, analytic = fun(point)
        numeric = []
        for dimension in range(2):
            delta = np.zeros(2)
            delta[dimension] = 1e-6
            numeric.append((fun(point + delta)[0] - fun(point - delta)[0]) / 2e-6)
        np.testing.assert_allclose(analytic, numeric, rtol=1e-6, atol=1e-8)
        return minimize(fun, initial, **options)
    monkeypatch.setattr("scipy.optimize.minimize", checked_minimize)
    fitted, audit = worker.fit_conditioned_calibration(frame, original, market)
    assert fitted["kind"] == worker.CALIBRATION_SCHEMA
    assert audit["scope"] == "CAL-only"
    assert not audit["select_outcomes_consumed"] and not audit["test_outcomes_consumed"]
    assert audit["bounds_are_warning_not_cohort_filter"]


@pytest.fixture(scope="module")
def fitted():
    frame = fixture_frame(races_per_period=8)
    train = frame.loc[frame.race_date.str.startswith("2016")].reset_index(drop=True)
    cal = frame.loc[frame.race_date.str.startswith("2021")].reset_index(drop=True)
    return worker.fit_model(train, cal, "market46"), frame


def test_direct_fit_cpu_offset_fixed_schema_and_calibration_receipts(fitted):
    bundle, _ = fitted
    assert bundle["rounds"] == 150
    assert bundle["parameters"] == worker.PARAMETERS
    assert bundle["fit_audit"]["actual_device"] == "cpu"
    assert bundle["fit_audit"]["actual_native_threads"] == 2
    assert bundle["fit_audit"]["offset_in_training"]
    assert bundle["fit_audit"]["offset_explicitly_added_in_prediction"]
    assert bundle["calibration_audit"]["scope"] == "CAL-only"
    assert bundle["temperature_calibration_audit"]["scope"] == "CAL-only"
    assert not bundle["eligible_for_ev"] and not bundle["artifact_published"]


def test_prediction_does_not_consume_labels_or_payout_and_reloads_both_calibrations(fitted, tmp_path):
    bundle, frame = fitted
    test = frame.loc[frame.race_date.str.startswith("2025")].reset_index(drop=True)
    inference = test.drop(columns=["finish", "tansho_payout"])
    expected = worker.predict_components(bundle, inference)
    poisoned = test.copy()
    poisoned["finish"], poisoned["tansho_payout"] = "future-label-must-not-be-read", object()
    actual = worker.predict_components(bundle, poisoned)
    path = tmp_path / "model.research.joblib"
    joblib.dump(bundle, path)
    replay = worker.predict_components(joblib.load(path), inference)
    for key in expected:
        np.testing.assert_array_equal(expected[key], actual[key])
        np.testing.assert_array_equal(expected[key], replay[key])
    np.testing.assert_allclose(pd.Series(expected["probability"]).groupby(test.race_id).sum(), 1., atol=1e-14)
    np.testing.assert_allclose(pd.Series(expected["temperature_probability"]).groupby(test.race_id).sum(), 1., atol=1e-14)


def test_offset_is_added_exactly_once_at_inference(fitted):
    bundle, frame = fitted
    test = frame.loc[frame.race_date.str.startswith("2025")].reset_index(drop=True)
    output = worker.predict_components(bundle, test)
    from scipy.special import expit
    market = output["market_probability"]
    expected_logits = np.log(market) - np.log1p(-market) + output["residual_score"]
    np.testing.assert_array_equal(output["direct_logit"], expected_logits)
    original = worker._normalize(test, np.clip(expit(expected_logits), *worker.SIGMOID_CLAMP))
    np.testing.assert_array_equal(output["p_original"], original)


@pytest.mark.parametrize("name,value", [("artifact_published", True), ("eligible_for_ev", True),
                                     ("actual_purchase_authorized", True), ("schema", "production-model"),
                                     ("temperature", 4.), ("conditioned_calibration", {"kind": "band-calibrator"})])
def test_research_artifact_cannot_gain_authority_or_changed_calibration(fitted, name, value):
    bundle, frame = fitted
    altered = deepcopy(bundle)
    altered[name] = value
    with pytest.raises(ValueError):
        worker.predict_model(altered, frame.iloc[-4:])


@pytest.mark.parametrize("candidate", worker.CANDIDATES)
def test_full_worker_atomic_unique_research_artifacts_and_equal_inference_cohort(inputs, candidate):
    request, output, frame = inputs
    request["candidate_id"], request["job_id"] = candidate, candidate
    request["feature_columns"], request["categorical_columns"] = worker.feature_contract(candidate)
    output = output.parent / (candidate + ".json")
    result = worker.run_worker(request, output)
    assert result["status"] == "completed"
    assert result["feature_count"] == {"market_only": 4, "market46": 50, "market46_extended": 92}[candidate]
    assert result["request_sha256"] == worker.canonical_hash(request)
    assert result["source_hashes"] == request["expected_source_hashes"]
    assert result["proofs"]["all_partitions_disjoint_whole_races"]
    assert not result["proofs"]["eligible_for_ev"]
    predictions = pd.read_parquet(result["artifacts"]["predictions"]["path"])
    assert len(predictions) == 48
    assert set(predictions.split) == {"SELECT", "TEST"}
    assert {"probability", "temperature_probability", "p_original", "market_probability", "direct_logit"} <= set(predictions)
    assert all(value == 0. for split in result["reload_parity"].values() for value in split.values())
    assert worker.file_hash(request["frame_path"]) == request["frame_sha256"]
    metadata = json.loads(output.read_text(encoding="utf-8"))
    assert metadata["runtime"]["device"] == "cpu"
    assert metadata["runtime"]["native_threads"] == 2
    model = joblib.load(result["artifacts"]["model"]["path"])
    original = frame.loc[frame.race_date.str.startswith("2025")].reset_index(drop=True)
    replay = worker.predict_model(model, original.drop(columns=["finish", "tansho_payout"]))
    np.testing.assert_array_equal(replay, predictions.loc[predictions.split.eq("TEST"), "probability"])
    with pytest.raises(ValueError, match="new-nested"):
        worker.run_worker(request, output)


def test_select_and_test_results_never_passed_to_fit(inputs, monkeypatch):
    request, output, _ = inputs
    fit_model = worker.fit_model
    receipts = []
    def spy(train, cal, candidate_id):
        receipts.append((train.race_date.max(), cal.race_date.max(), candidate_id))
        assert train.race_date.between(*worker.PERIODS["TRAIN"]).all()
        assert cal.race_date.between(*worker.PERIODS["CAL"]).all()
        return fit_model(train, cal, candidate_id)
    monkeypatch.setattr(worker, "fit_model", spy)
    result = worker.run_worker(request, output)
    assert len(receipts) == 1
    assert result["fit_audit"]["select_outcomes_consumed_in_fit"] is False
    assert result["fit_audit"]["test_outcomes_consumed_in_fit"] is False
