"""Offline four-candidate guards; no database, network or real model fitting."""
from copy import deepcopy
from datetime import date, timedelta
import hashlib
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
worker = importlib.import_module("db_shared_win_four_worker")


def fixture_frame(years=(2013, 2014, 2015, 2016, 2023, 2024, 2025, 2026)):
    columns, categorical = worker.feature_contract("expanding_binary")
    rows = []
    for year in years:
        for day in range(3):
            current = (date(year, 1, 1) + timedelta(days=day)).strftime("%Y%m%d")
            for horse in range(1, 5):
                winner = day + 1
                order = [winner] + [value for value in range(1, 5) if value != winner]
                row = {name: float(horse + day) for name in columns if name not in worker.direct.MARKET_COLUMNS}
                row.update({name: "known" for name in categorical})
                row.update(race_id=current + "0001", race_date=current, horse_number=horse,
                    finish=order.index(horse) + 1, odds=3. + horse, expected_runner_count=4,
                    venue="東京", surface="turf", safe_weather="unseen" if year == 2015 else "known",
                    safe_weight_change=np.nan, tansho_payout=(3. + horse) * 100 if horse == winner else 0.)
                rows.append(row)
    frame = pd.DataFrame(rows)
    inverse = 1. / frame.odds
    frame["normalized_market_probability"] = inverse / inverse.groupby(frame.race_id).transform("sum")
    frame["log_odds"], frame["market_rank"], frame["field_size"] = np.log(frame.odds), frame.horse_number.astype(float), 4.
    return frame


@pytest.mark.parametrize("candidate,year,first", [
    ("expanding_binary", 2016, 2013), ("rolling5_binary", 2016, 2013),
    ("expanding_race_nll", 2026, 2013), ("rolling5_race_nll", 2026, 2020),
])
def test_fixed_candidate_fold_periods(candidate, year, first):
    assert worker.fold_periods(year, candidate) == {
        "TRAIN": (f"{first}0101", f"{year - 2}1231"),
        "CAL": (f"{year - 1}0101", f"{year - 1}1231"), "TEST": (f"{year}0101", f"{year}1231")}
    columns, categories = worker.feature_contract(candidate)
    assert len(columns) == 50 and len(categories) == 6
    assert "safe_past5_top3_rate" not in columns
    assert columns == worker.direct.feature_contract("market46")[0]


@pytest.mark.parametrize("year", [True, 2015, 2027, "2026", None])
def test_nonfixed_folds_forbidden(year):
    with pytest.raises(ValueError):
        worker.fold_periods(year, "expanding_binary")


def test_split_shared_frame_ignores_outside_windows_without_mixing_years():
    frame = fixture_frame()
    parts = worker.split_positions(frame, 2016, "rolling5_binary")
    assert len(parts["TRAIN"]) == 24
    assert len(parts["CAL"]) == len(parts["TEST"]) == 12
    assert frame.iloc[parts["TRAIN"]].race_date.max() < frame.iloc[parts["CAL"]].race_date.min()
    assert not set(frame.iloc[parts["TRAIN"]].race_id) & set(frame.iloc[parts["TEST"]].race_id)


def test_empty_fold_guard():
    with pytest.raises(ValueError, match="nonempty"):
        worker.split_positions(fixture_frame((2013, 2014, 2016)), 2016, "expanding_binary")


@pytest.mark.parametrize("kind", ["duplicate", "partial", "twowinners", "nowinner", "fractional", "date"])
def test_whole_race_label_contract(kind):
    frame = fixture_frame((2013,)).copy()
    if kind == "duplicate":
        frame = pd.concat([frame, frame.iloc[:1]], ignore_index=True)
    elif kind == "partial":
        frame = frame.iloc[1:]
    elif kind == "twowinners":
        frame.loc[1, "finish"] = 1
    elif kind == "nowinner":
        frame.loc[:3, "finish"] = 2
    elif kind == "fractional":
        frame["finish"] = frame.finish.astype(float)
        frame.loc[1, "finish"] = 1.5
    else:
        frame.loc[:3, "race_date"] = "20130230"
    with pytest.raises(ValueError):
        worker.validate_frame(frame)


def test_payout_never_conditions_training_or_calibration_cohort():
    frame = fixture_frame((2013,)).drop(columns="tansho_payout")
    assert worker.validate_frame(frame)["race_count"] == 3
    with pytest.raises(ValueError):
        worker.validate_frame(frame, require_payout=True)


def test_contiguous_whole_race_guard():
    frame = fixture_frame((2013,))
    np.testing.assert_array_equal(worker.contiguous_groups(frame, True), [4, 4, 4])
    scrambled = frame.iloc[[0, 4, 1, 2, 3, 5, 6, 7, 8, 9, 10, 11]]
    with pytest.raises(ValueError, match="contiguous"):
        worker.contiguous_groups(scrambled, True)


def test_race_softmax_shift_invariance_and_independent_groups():
    scores, sizes = np.array([.8, -.3, .1, -2., 4.]), np.array([3, 2])
    first = worker.race_softmax(scores, sizes)
    shifted = worker.race_softmax(scores + np.array([3., 3., 3., -8., -8.]), sizes)
    np.testing.assert_allclose(first, shifted, rtol=1e-14)
    np.testing.assert_allclose([first[:3].sum(), first[3:].sum()], 1., atol=1e-15)


def test_custom_nll_analytic_gradient_and_exact_diagonal_hessian():
    scores = np.array([.2, 1.5, -.4, -.2, .5])
    labels, sizes = np.array([0., 1., 0., 1., 0.]), np.array([3, 2])
    gradient, hessian = worker.race_nll_gradient_hessian(scores, labels, sizes)
    epsilon = 1e-5
    for index in range(len(scores)):
        shift = np.zeros(len(scores)); shift[index] = epsilon
        numeric_g = (worker.race_nll(scores + shift, labels, sizes) - worker.race_nll(scores - shift, labels, sizes)) / (2 * epsilon)
        numeric_h = (worker.race_nll_gradient_hessian(scores + shift, labels, sizes)[0][index]
                     - worker.race_nll_gradient_hessian(scores - shift, labels, sizes)[0][index]) / (2 * epsilon)
        assert gradient[index] == pytest.approx(numeric_g, abs=1e-9)
        assert hessian[index] == pytest.approx(numeric_h, abs=1e-10)
    # Nonzero cross-runner terms exist; the supplied Hessian omits them.
    shift = np.array([epsilon, 0., 0., 0., 0.])
    off_diagonal = (worker.race_nll_gradient_hessian(scores + shift, labels, sizes)[0][1]
                    - worker.race_nll_gradient_hessian(scores - shift, labels, sizes)[0][1]) / (2 * epsilon)
    assert off_diagonal < 0
    assert "diagonal Hessian approximation" in worker.objective_description("expanding_race_nll")


def test_extreme_scores_have_finite_nll_and_fixed_hessian_floor():
    scores = np.array([1000., -1000., 0.])
    labels = np.array([0., 1., 0.])
    assert worker.race_nll(scores, labels, [3]) == pytest.approx(2000.)
    gradient, hessian = worker.race_nll_gradient_hessian(scores, labels, [3])
    np.testing.assert_array_equal(hessian, np.full(3, 1e-6))
    assert np.isfinite(gradient).all()


@pytest.mark.parametrize("scores,labels,sizes", [
    ([0., 0.], [1., 0.], [1, 1]), ([0., 0.], [1., 0.], [3]),
    ([0., 0.], [1., 1.], [2]), ([0., 0.], [0., 0.], [2]),
    ([0., np.nan], [1., 0.], [2]), ([0., 0.], [1., 0.], [2.]),
])
def test_custom_objective_invalid_group_inputs_fail_closed(scores, labels, sizes):
    with pytest.raises(ValueError):
        worker.race_nll_gradient_hessian(scores, labels, sizes)


def test_objective_uses_supplied_offset_predictions_and_checks_label_order():
    labels, sizes = np.array([1., 0., 0.]), np.array([3])
    market = np.array([.6, .3, .1])
    objective = worker.make_race_objective(sizes, labels)
    class Dataset:
        def get_label(self):
            return labels
    gradient, _ = objective(np.log(market), Dataset())
    np.testing.assert_allclose(gradient, market - labels)
    labels[:] = [0., 1., 0.]
    with pytest.raises(ValueError, match="label-order"):
        objective(np.log(market), Dataset())


class FakeBooster:
    def __init__(self, model_str="offline-fake-model"):
        self.model_str = model_str
    def model_to_string(self):
        return self.model_str
    def num_feature(self):
        return 50
    def feature_name(self):
        return worker.feature_contract("expanding_binary")[0]
    def predict(self, frame, **kwargs):
        assert kwargs == {"raw_score": True, "num_threads": 2}
        return frame.iloc[:, 0].to_numpy(float) * .02


class FakeDataset:
    def __init__(self, matrix, **kwargs):
        self.matrix, self.kwargs = matrix, kwargs
    def get_label(self):
        return self.kwargs["label"]


@pytest.fixture
def fake_fitting(monkeypatch):
    calls = []
    def fake_train(settings, dataset, **kwargs):
        assert kwargs == {"num_boost_round": 150}
        assert settings["num_threads"] == 2 and settings["device_type"] == "cpu"
        calls.append((settings, dataset.kwargs.copy()))
        if callable(settings["objective"]):
            gradient, hessian = settings["objective"](dataset.kwargs["init_score"], dataset)
            assert np.isfinite(gradient).all() and (hessian >= worker.HESSIAN_FLOOR).all()
        return FakeBooster()
    monkeypatch.setattr(lgb, "Dataset", FakeDataset)
    monkeypatch.setattr(lgb, "train", fake_train)
    monkeypatch.setattr(lgb, "Booster", FakeBooster)
    return calls


@pytest.mark.parametrize("candidate", worker.CANDIDATES)
def test_fold_fit_offset_train_encoder_and_serialized_prediction_parity(candidate, fake_fitting, tmp_path):
    frame = fixture_frame()
    positions = worker.split_positions(frame, 2016, candidate)
    train, cal = (frame.iloc[positions[name]].reset_index(drop=True) for name in ("TRAIN", "CAL"))
    bundle = worker.fit_model(train, cal, candidate, 2016)
    calls = fake_fitting
    assert len(calls) == 1
    market = worker.market_values(train)
    expected_offset = np.log(market) if candidate.endswith("race_nll") else np.log(market) - np.log1p(-market)
    np.testing.assert_array_equal(calls[0][1]["init_score"], expected_offset)
    assert bundle["encoder"].categories["safe_weather"] == ["known"]
    assert bundle["encoder"].transform(cal).safe_weather.isna().all()
    assert bundle["encoder"].transform(cal).safe_weight_change.isna().all()
    assert bundle["calibration_audit"]["period"] == ["20150101", "20151231"]
    model_path = tmp_path / "offline.joblib"
    joblib.dump(bundle, model_path)
    inference = frame.iloc[positions["TEST"]].drop(columns=["finish", "tansho_payout"])
    output = worker.predict_components(bundle, inference)
    replay = worker.predict_components(joblib.load(model_path), inference)
    for name in output:
        np.testing.assert_array_equal(output[name], replay[name])
    market = output["market_probability"]
    expected_score = (np.log(market) if candidate.endswith("race_nll") else np.log(market) - np.log1p(-market)) + output["residual_score"]
    np.testing.assert_array_equal(output["direct_logit"], expected_score)
    assert bundle["fit_audit"]["test_outcomes_consumed_in_fit"] is False


def test_model_prediction_never_accesses_or_changes_with_poisoned_outcomes(fake_fitting):
    frame = fixture_frame()
    parts = worker.split_positions(frame, 2016, "expanding_race_nll")
    bundle = worker.fit_model(frame.iloc[parts["TRAIN"]], frame.iloc[parts["CAL"]], "expanding_race_nll", 2016)
    test = frame.iloc[parts["TEST"]].reset_index(drop=True)
    expected = worker.predict_model(bundle, test)
    test["finish"], test["tansho_payout"] = "untrusted-result", "future-payout"
    np.testing.assert_array_equal(expected, worker.predict_model(bundle, test))


def test_calibration_analytic_gradient_matches_finite_difference(monkeypatch):
    frame = fixture_frame((2015,))
    probabilities = np.tile([.4, .3, .2, .1], 3)
    market = worker.market_values(frame)
    from scipy.optimize import minimize
    def checked(fun, initial, **kwargs):
        point = np.array([1.15, -.2])
        value, gradient = fun(point)
        assert np.isfinite(value)
        for index in range(2):
            shift = np.eye(2)[index] * 1e-6
            numeric = (fun(point + shift)[0] - fun(point - shift)[0]) / 2e-6
            assert gradient[index] == pytest.approx(numeric, abs=1e-8)
        return minimize(fun, initial, **kwargs)
    monkeypatch.setattr("scipy.optimize.minimize", checked)
    settings, audit = worker.fit_conditioned_calibration(frame, probabilities, market, ("20150101", "20151231"))
    assert audit["converged"] and audit["scope"] == "CAL-only"
    assert .5 <= settings["a"] <= 2. and -1. <= settings["b"] <= 1.
    with pytest.raises(ValueError, match="cal-only"):
        worker.fit_conditioned_calibration(frame, probabilities, market, ("20160101", "20161231"))


@pytest.mark.parametrize("name,value", [("artifact_published", True), ("eligible_for_ev", True),
    ("actual_purchase_authorized", True), ("periods", {}), ("rounds", 200), ("model_text", "changed"),
    ("conditioned_calibration", {"kind": "band-ad-hoc", "a": 1., "b": 0.})])
def test_bundle_authority_and_contract_are_locked(fake_fitting, name, value):
    frame = fixture_frame()
    positions = worker.split_positions(frame, 2016, "expanding_binary")
    bundle = worker.fit_model(frame.iloc[positions["TRAIN"]], frame.iloc[positions["CAL"]], "expanding_binary", 2016)
    bundle[name] = value
    with pytest.raises(ValueError):
        worker.predict_model(bundle, frame.iloc[positions["TEST"]])


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    root = tmp_path / "reports/generated"
    directory = root / "isolated"
    directory.mkdir(parents=True)
    monkeypatch.setattr(worker, "OUTPUT_ROOT", root)
    frame_path = directory / "input.parquet"
    frame = fixture_frame()
    frame.to_parquet(frame_path, index=False)
    receipt_path = directory / "phase1-proof.json"
    receipt = {"schema": "db-shared-win-pipeline-proof-v1", "status": "completed", "race_id": "202608040511",
        "phase1_pass": True, "capture_before_post_verified": True, "prediction_before_post_verified": True,
        "decision_before_post_verified": True, "actual_settlement_verified": True,
        "research_only": True, "purchase_authorized": False, "actual_purchases": 0, "existing_forward_changed": False}
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    # No network/result collection in unit tests. Production calls the real
    # sealed-chain recomputation; these fixtures substitute its offline result.
    import run_db_shared_win_pipeline as pipeline
    monkeypatch.setattr(pipeline, "verify_gate_proof", lambda path: {
        "proof": json.loads(Path(path).read_text()), "proof_sha256": worker.file_hash(path)})
    columns, categorical = worker.feature_contract("expanding_binary")
    request = {"schema": worker.SCHEMA, "job_id": "expanding_binary_2016", "candidate_id": "expanding_binary",
        "fold_year": 2016, "frame_path": str(frame_path), "frame_sha256": worker.file_hash(frame_path),
        "protocol_sha256": worker.file_hash(ROOT / worker.PROTOCOL_SOURCE),
        "expected_source_hashes": {name: worker.file_hash(ROOT / name) for name in worker.REQUIRED_SOURCES},
        "feature_columns": columns, "categorical_columns": categorical, "device": "cpu", "n_threads": 2,
        "phase1_receipt_path": str(receipt_path), "phase1_receipt_sha256": worker.file_hash(receipt_path)}
    return request, directory / "output.json", frame


@pytest.mark.parametrize("updates", [{"device": "gpu"}, {"n_threads": 4}, {"fold_year": 2015},
    {"candidate_id": "posthoc"}, {"feature_columns": []}, {"phase1_receipt_sha256": "x"}, {"unexpected": True}])
def test_request_options_are_fixed(inputs, updates):
    request, output, _ = inputs
    request.update(updates)
    with pytest.raises(ValueError):
        worker.validate_request(request, output)


def test_unproven_phase1_blocks_before_any_model_fit(inputs, monkeypatch):
    request, output, _ = inputs
    path = Path(request["phase1_receipt_path"])
    receipt = json.loads(path.read_text())
    receipt["actual_settlement_verified"] = False
    path.write_text(json.dumps(receipt))
    request["phase1_receipt_sha256"] = worker.file_hash(path)
    monkeypatch.setattr(worker, "fit_model", lambda *args: pytest.fail("fit must not run before actual pipeline proof"))
    with pytest.raises(ValueError, match="actual-pipeline-pass"):
        worker.run_worker(request, output)


@pytest.mark.parametrize("candidate", worker.CANDIDATES)
def test_worker_exclusive_outputs_two_partitions_and_reloaded_parity(inputs, fake_fitting, candidate):
    request, output, frame = inputs
    request["candidate_id"] = candidate
    request["job_id"] = candidate + "_2016"
    result = worker.run_worker(request, output)
    assert result["status"] == "completed" and result["feature_count"] == 50
    assert result["rows_outside_fold"] == 48
    assert result["phase1_receipt_sha256"] == request["phase1_receipt_sha256"]
    assert all(value == 0. for split in result["reload_parity"].values() for value in split.values())
    test = pd.read_parquet(result["artifacts"]["predictions"]["path"])
    assert len(test) == 12 and set(test.split) == {"TEST"}
    assert test.race_date.str.startswith("2016").all()
    assert result["runtime"]["native_threads"] == 2
    assert len(fake_fitting) == 1
    with pytest.raises(ValueError, match="new-nested"):
        worker.run_worker(request, output)


def test_source_pins_and_phase_proof_changes_fail_closed(inputs):
    request, output, _ = inputs
    worker.validate_source_pins(request)
    altered = deepcopy(request)
    altered["expected_source_hashes"]["scripts/db_shared_win_four_worker.py"] = "f" * 64
    with pytest.raises(ValueError, match="source-pin-mismatch"):
        worker.validate_source_pins(altered)
    Path(request["phase1_receipt_path"]).write_text("{}")
    with pytest.raises(ValueError, match="phase1-proof-hash"):
        worker.run_worker(request, output)


def test_protocol_hash_is_bound_to_pinned_config(inputs):
    request, _, _ = inputs
    request["protocol_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="protocol-hash-not-bound"):
        worker.validate_source_pins(request)


def test_phase1_boolean_flags_without_chain_recomputation_are_insufficient(inputs, monkeypatch):
    request, output, _ = inputs
    import run_db_shared_win_pipeline as pipeline
    monkeypatch.setattr(pipeline, "verify_gate_proof", lambda path: {
        "proof": {"phase1_pass": False}, "proof_sha256": worker.file_hash(path)})
    with pytest.raises(ValueError, match="proof-readback"):
        worker.run_worker(request, output)
