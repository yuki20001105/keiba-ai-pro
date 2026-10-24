"""Offline shadow evidence and CAL/replay safety guards; no real fits or capture."""
from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "python-api"), str(ROOT / "keiba")]
adapter = importlib.import_module("win_shadow_adapter_v1")
runner = importlib.import_module("run_win_shadow_v1")
worker = adapter.worker


@pytest.fixture
def shadow_input():
    columns, categorical = worker.feature_contract(adapter.CANDIDATE)
    rows = []
    for number, odds in enumerate((2., 4., 7., 12.), 1):
        row = {name: np.nan for name in columns}
        row.update({name: "known" for name in categorical})
        row.update(race_id="202608040511", race_date="20261012", horse_number=number,
                   horse_id=f"horse-{number}", venue="京都", surface="turf", num_horses=4,
                   expected_runner_count=4, odds=odds, safe_venue="京都", safe_surface="turf",
                   safe_distance=1800., safe_num_horses=4., safe_horse_number=float(number),
                   safe_age=3., safe_burden_weight=56.)
        rows.append(row)
    frame = adapter.features.add_market_features(pd.DataFrame(rows))
    pins = adapter.features.source_hashes()
    receipt = {
        "schema": adapter.SCHEMA, "race_id": "202608040511", "race_date": "20261012",
        "columns": columns, "generator_version": adapter.GENERATOR_VERSION,
        "frame_sha256": worker.frame_hash(frame),
        "identities": frame[["race_id", "race_date", "horse_number", "horse_id"]].to_dict("records"),
        "formula_receipt": {"base46_parity": {"verified": True}, "source_hashes": pins,
            "history_contract": adapter.features.HISTORY_CONTRACT, "input_outcomes_consumed": False,
            "same_day_or_future_history_used": False, "dummy_current_results_used": False},
        "generator_source_hashes": pins, "capture_manifest_sha256": "a" * 64,
        "capture_bundle_sha256": "b" * 64, "history_sha256": "c" * 64, "canonical_sha256": "d" * 64,
        "history": {"rows": 1000, "min_date": "20130105", "max_date": "20260913",
            "strict_prior_day_verified": True, "target_or_future_rows": 0,
            "freshness_verified": False, "complete_career_coverage_verified": False},
        "capture": {"source_mode": "live", "point_in_time_verified": True,
            "odds_kind": "actual_market_single_win", "odds_status": "middle", "quote_key_join_verified": True,
            "post_time": "2026-10-12T15:45:00+09:00", "feature_generated_at": "2026-10-12T15:39:50+09:00",
            "captured_at": "2026-10-12T15:39:55+09:00", "window_opens_at": "2026-10-12T15:39:00+09:00",
            "window_deadline": "2026-10-12T15:41:00+09:00",
            "source_receipts": [
                {"kind": kind, "status": 200, "source": "network", "body_sha256": "e" * 64,
                 "request_started_at": "2026-10-12T15:39:01+09:00", "observed_at": "2026-10-12T15:39:10+09:00",
                 "url": url, "request_url": url} for kind, url in (
                    ("card", "https://race.netkeiba.com/race/shutuba.html?race_id=202608040511"),
                    ("odds", "https://race.netkeiba.com/api/api_get_jra_odds.html?race_id=202608040511&type=1&action=update"))]},
    }
    return frame, receipt


def rebind(frame, receipt):
    receipt["frame_sha256"] = worker.frame_hash(frame)
    return worker.canonical_hash(receipt)


def validate(frame, receipt, **kwargs):
    return adapter.validate_shadow_input(frame, receipt, expected_input_sha256=worker.canonical_hash(receipt), **kwargs)


def bundle_fixture():
    columns, categories = worker.feature_contract(adapter.CANDIDATE)
    encoder = worker.TrainEncoder(columns=columns, categorical_columns=categories, fitted=True,
        categories={name: ["known"] for name in categories},
        means={name: 0. for name in columns if name not in categories},
        standard_deviations={name: 1. for name in columns if name not in categories})
    model_text = "unit-test-only-not-a-real-model"
    return {"schema": worker.MODEL_SCHEMA, "research_only": True, "artifact_published": False,
        "eligible_for_ev": False, "actual_purchase_authorized": False, "candidate_id": adapter.CANDIDATE,
        "fold_year": 2026, "periods": worker.fold_periods(2026, adapter.CANDIDATE),
        "feature_columns": columns, "categorical_columns": categories, "encoder": encoder,
        "parameters": worker.parameters(adapter.CANDIDATE), "rounds": worker.BOOST_ROUNDS,
        "objective_description": worker.objective_description(adapter.CANDIDATE), "model_text": model_text,
        "model_text_sha256": hashlib.sha256(model_text.encode()).hexdigest(),
        "conditioned_calibration": {"kind": worker.direct.CALIBRATION_SCHEMA, "a": 1., "b": 0.}}


def test_strict_input_allows_historical_structural_nans_without_zero_fill(shadow_input):
    frame, receipt = shadow_input
    before = frame.copy(deep=True)
    result = validate(frame, receipt)
    assert result["runner_count"] == 4
    assert result["history_lag_days"] == 29 and result["history_freshness_verified"] is False
    assert result["complete_career_coverage_verified"] is False
    assert result["prospective_validation_pass"] is False and result["eligible_for_ev"] is False
    pd.testing.assert_frame_equal(frame, before)


@pytest.mark.parametrize("mode", ["live", "forward", "prospective", "outside_window_probe", None])
def test_no_unregistered_future_or_live_target_mode(shadow_input, mode):
    frame, receipt = shadow_input
    with pytest.raises(ValueError, match="future-target-not-yet-registered"):
        validate(frame, receipt, mode=mode)


def test_exact_registered_evidence_hash_is_required(shadow_input):
    frame, receipt = shadow_input
    with pytest.raises(ValueError, match="registered-input-evidence-binding"):
        adapter.validate_shadow_input(frame, receipt, expected_input_sha256="f" * 64)


@pytest.mark.parametrize("change", ["empty", "missing_column", "duplicate_column", "finish", "actual_return", "future_extra",
    "frame_hash", "partial_card", "duplicate_horse", "empty_horse", "market4", "current_missing", "current_metadata"])
def test_missing_partial_future_or_nonidentical_input_is_rejected(shadow_input, change):
    frame, receipt = shadow_input
    frame, receipt = frame.copy(deep=True), deepcopy(receipt)
    if change == "empty": frame = frame.iloc[:0].copy()
    elif change == "missing_column": frame = frame.drop(columns="safe_age")
    elif change == "duplicate_column": frame = pd.concat([frame, frame[["safe_age"]]], axis=1)
    elif change in {"finish", "actual_return", "future_extra"}: frame[change] = 1
    elif change == "partial_card": frame = frame.iloc[:-1].copy()
    elif change == "duplicate_horse": frame.loc[1, "horse_id"] = frame.loc[0, "horse_id"]
    elif change == "empty_horse": frame.loc[1, "horse_id"] = pd.NA
    elif change == "market4": frame.loc[1, "log_odds"] = 12.
    elif change == "current_missing": frame.loc[1, "safe_age"] = np.nan
    elif change == "current_metadata": frame.loc[1, "safe_num_horses"] = 5.
    if change != "frame_hash": rebind(frame, receipt)
    else: receipt["frame_sha256"] = "f" * 64
    with pytest.raises(ValueError): validate(frame, receipt)


@pytest.mark.parametrize("change", ["same_day_history", "future_history", "future_row_count", "prior_not_verified",
    "generator_source", "formula_version", "formula_not_verified", "formula_dummy_results", "input_outcome_consumed",
    "capture_future", "source_after_capture", "source_result_odds", "source_url_other_race", "source_skew",
    "snapshot_source", "source_hash_missing", "noncentral", "jump", "wrong_identities",
    "formula_wrong_type", "history_wrong_type", "capture_wrong_type", "source_element_wrong_type"])
def test_history_capture_formula_and_quote_evidence_constraints(shadow_input, change):
    frame, receipt = shadow_input
    frame, receipt = frame.copy(), deepcopy(receipt)
    if change == "same_day_history": receipt["history"]["max_date"] = "20261012"
    elif change == "future_history": receipt["history"]["max_date"] = "20261013"
    elif change == "future_row_count": receipt["history"]["target_or_future_rows"] = 1
    elif change == "prior_not_verified": receipt["history"]["strict_prior_day_verified"] = False
    elif change == "generator_source": receipt["generator_source_hashes"] = {}
    elif change == "formula_version": receipt["generator_version"] = "changed"
    elif change == "formula_not_verified": receipt["formula_receipt"]["base46_parity"]["verified"] = False
    elif change == "formula_dummy_results": receipt["formula_receipt"]["dummy_current_results_used"] = True
    elif change == "input_outcome_consumed": receipt["formula_receipt"]["input_outcomes_consumed"] = True
    elif change == "capture_future": receipt["capture"]["captured_at"] = "2026-10-12T15:46:00+09:00"
    elif change == "source_after_capture": receipt["capture"]["source_receipts"][0]["observed_at"] = "2026-10-12T15:40:00+09:00"
    elif change == "source_result_odds": receipt["capture"]["odds_status"] = "result"
    elif change == "source_url_other_race": receipt["capture"]["source_receipts"][0]["url"] = "https://race.netkeiba.com/race/shutuba.html?race_id=202608040512"
    elif change == "source_skew":
        receipt["capture"]["captured_at"] = "2026-10-12T15:40:55+09:00"
        receipt["capture"]["source_receipts"][1]["observed_at"] = "2026-10-12T15:40:20+09:00"
    elif change == "snapshot_source": receipt["capture"]["source_receipts"][0]["source"] = "cache"
    elif change == "source_hash_missing": receipt["capture"]["source_receipts"][0]["body_sha256"] = ""
    elif change == "noncentral": frame["race_id"] = "202645040511"
    elif change == "jump": frame["surface"] = frame["safe_surface"] = "jump"
    elif change == "wrong_identities": receipt["identities"] = []
    elif change == "formula_wrong_type": receipt["formula_receipt"] = []
    elif change == "history_wrong_type": receipt["history"] = []
    elif change == "capture_wrong_type": receipt["capture"] = []
    else: receipt["capture"]["source_receipts"][0] = "invalid"
    rebind(frame, receipt)
    with pytest.raises(ValueError): validate(frame, receipt)


def test_comparison_has_same_calibration_formula_no_ev_no_holdout_and_no_new_fit(shadow_input, monkeypatch):
    frame, receipt = shadow_input
    bundle = bundle_fixture()
    seen = []

    def inference(actual_bundle, actual_frame):
        assert actual_bundle is bundle
        pd.testing.assert_frame_equal(actual_frame, frame)
        seen.append(actual_frame)
        market = worker.market_values(actual_frame)
        return {"probability": worker.conditioned_probabilities(actual_frame, market, market, 1., 0.)}

    monkeypatch.setattr(worker, "predict_components", inference)
    calibration = {"kind": worker.direct.CALIBRATION_SCHEMA, "a": 1., "b": 0.}
    before = frame.copy(deep=True)
    result = adapter.compare_shadow(frame, receipt, bundle, calibration,
                                   expected_input_sha256=worker.canonical_hash(receipt))
    assert len(seen) == 1 and result["same_input_rows_for_both_models"] is True
    assert result["model_fits_started"] == result["actual_purchases"] == 0
    for field in ("prospective_validation_pass", "eligible_for_ev", "purchase_approved", "model_activated",
                  "forward_modified", "unknown_future_validation", "stakes_assigned", "model_inference_before_post_verified"):
        assert result[field] is False
    assert result["validation"]["history_freshness_verified"] is False
    assert result["validation"]["history_lag_days"] == 29
    market = [row["market_only_calibrated"] for row in result["predictions"]]
    candidate = [row[adapter.CANDIDATE] for row in result["predictions"]]
    np.testing.assert_array_equal(market, candidate)
    assert result["prediction_sha256"] == worker.canonical_hash(result["predictions"])
    pd.testing.assert_frame_equal(frame, before)


@pytest.mark.parametrize("change", ["wrong_columns", "unfitted_encoder", "published", "other_candidate", "other_year", "unsafe_calibration"])
def test_saved_encoder_and_candidate_contract_cannot_be_substituted(shadow_input, monkeypatch, change):
    frame, receipt = shadow_input
    bundle = bundle_fixture()
    if change == "wrong_columns": bundle["encoder"].columns = []
    elif change == "unfitted_encoder": bundle["encoder"].fitted = False
    elif change == "published": bundle["artifact_published"] = True
    elif change == "other_candidate": bundle["candidate_id"] = "rolling5_binary"
    elif change == "other_year": bundle["fold_year"] = 2025
    else: bundle["conditioned_calibration"]["a"] = 3.
    monkeypatch.setattr(worker, "predict_components", lambda *args: pytest.fail("invalid bundle reached inference"))
    with pytest.raises(ValueError):
        adapter.compare_shadow(frame, receipt, bundle, {"kind": worker.direct.CALIBRATION_SCHEMA, "a": 1., "b": 0.},
                               expected_input_sha256=worker.canonical_hash(receipt))


@pytest.mark.parametrize("settings", [None, {}, {"kind": "bad", "a": 1., "b": 0.},
    {"kind": worker.direct.CALIBRATION_SCHEMA, "a": True, "b": 0.},
    {"kind": worker.direct.CALIBRATION_SCHEMA, "a": 3., "b": 0.},
    {"kind": worker.direct.CALIBRATION_SCHEMA, "a": 1., "b": float("nan")}])
def test_market_calibration_is_only_the_original_bounded_formula(settings):
    with pytest.raises(ValueError): adapter.validate_market_calibration(settings)


def test_shadow_sources_have_no_network_db_purchase_model_fit_or_activation_calls():
    forbidden = {"fit_model", "fit_candidate", "run_worker", "record_selection", "save_snapshot",
                 "recommend", "requests", "urlopen", "executescript", "execute", "connect", "Popen", "run_subprocess"}
    for name in ("scripts/win_shadow_adapter_v1.py", "scripts/run_win_shadow_v1.py"):
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                called = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
                assert called not in forbidden, (name, called)


@pytest.fixture
def sealed_cli(tmp_path, monkeypatch, shadow_input):
    output = tmp_path / "shadow"
    monkeypatch.setattr(runner, "OUTPUT", output)
    monkeypatch.setattr(runner, "PLAN_PATH", output / "registered-plan.json")
    monkeypatch.setattr(runner, "CAL_PATH", output / "market-calibration.json")
    monkeypatch.setattr(runner, "CAL_ATTEMPT_PATH", output / "market-calibration-attempt.json")
    before = {"current_model_sha256": "unchanged", "forward_sources": "unchanged"}
    calibration = {"bounds": [list(value) for value in worker.direct.CALIBRATION_BOUNDS],
                   "ridge": worker.direct.CALIBRATION_RIDGE,
                   "formula": "softmax_within_race(a*log(p_uncalibrated)+b*log(p_market))"}
    frame, receipt = shadow_input
    plan = {"schema": runner.SCHEMA, "registered_at": "2026-10-12T00:00:00+00:00",
            "cal_partition_input_sha256": "a" * 64, "calibration": calibration,
            "preservation_before": before, "input_receipt": receipt,
            "input_receipt_sha256": worker.canonical_hash(receipt),
            "artifacts": {"model": {"sha256": "b" * 64}},
            "future_target_status": "awaiting_user_selected_unstarted_race"}
    runner.write_sealed(runner.PLAN_PATH, plan)
    monkeypatch.setattr(runner, "load_plan", lambda: deepcopy(plan))
    monkeypatch.setattr(runner, "preservation_receipt", lambda: deepcopy(before))
    # Explicit simulated CAL rows, not a real model or calibration fit.
    cal = frame.copy()
    cal["race_date"] = "20250105"
    cal["finish"] = [1, 2, 3, 4]
    market = worker.market_values(cal)
    saved = pd.DataFrame({"probability": market})
    monkeypatch.setattr(runner, "load_cal_input", lambda p: (cal, saved, market))
    monkeypatch.setattr(runner, "load_candidate", lambda p: bundle_fixture())
    monkeypatch.setattr(worker, "predict_components", lambda b, f: {"probability": market})
    calls = []

    def simulated_calibration(actual_frame, probabilities, actual_market, bounds):
        assert runner.CAL_ATTEMPT_PATH.exists()
        attempt = runner.read_sealed(runner.CAL_ATTEMPT_PATH)
        assert attempt["retry_authorized"] is False and attempt["market_calibrator_attempt"] == 1
        assert bounds == ("20250101", "20251231")
        assert actual_frame is cal and actual_frame.race_date.between(*bounds).all()
        np.testing.assert_array_equal(probabilities, actual_market)
        np.testing.assert_array_equal(actual_market, market)
        calls.append("simulated calibration only")
        audit = {"scope": "CAL-only", "period": list(bounds), "kind": worker.direct.CALIBRATION_SCHEMA,
            **calibration, "initial_parameters": [1., 0.], "converged": True,
            "test_outcomes_consumed": False, "feature_selection": False, "rule_selection": False,
            "odds_band_posthoc_multipliers": False, "a": 1., "b": 0.}
        return {"kind": worker.direct.CALIBRATION_SCHEMA, "a": 1., "b": 0.}, audit

    monkeypatch.setattr(worker, "fit_conditioned_calibration", simulated_calibration)
    return plan, calls, simulated_calibration


def test_preflight_simulated_calibration_once_same_cal_rows_and_formula_no_model_fit(sealed_cli):
    plan, calls, _fake = sealed_cli
    response = runner.preflight()
    assert calls == ["simulated calibration only"]
    assert response["calibrator_fits_started"] == 1 and response["model_fits_started"] == 0
    actual = runner.read_calibration(plan)
    assert actual["same_cal_rows_and_procedure"] is True and actual["test_outcomes_consumed"] is False
    assert actual["attempt_sha256"] == worker.file_hash(runner.CAL_ATTEMPT_PATH)
    with pytest.raises(ValueError, match="already-exists-no-refit"):
        runner.preflight()
    assert len(calls) == 1


def test_interrupted_market_calibration_cannot_silently_retry(sealed_cli, monkeypatch):
    _plan, calls, simulated = sealed_cli

    def interrupted(*args):
        simulated(*args)
        raise RuntimeError("simulated interruption after attempt seal")

    monkeypatch.setattr(worker, "fit_conditioned_calibration", interrupted)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        runner.preflight()
    assert runner.CAL_ATTEMPT_PATH.exists() and not runner.CAL_PATH.exists()
    with pytest.raises(ValueError, match="already-exists-no-refit"):
        runner.preflight()
    assert len(calls) == 1


@pytest.mark.parametrize("stage", ["plan", "cal_input", "candidate", "cal_replay"])
def test_invalid_provenance_and_cal_replay_are_rejected_before_optimization(sealed_cli, monkeypatch, stage):
    _plan, calls, _simulated = sealed_cli

    def reject(*args): raise ValueError("simulated " + stage + " not verified")

    if stage == "plan": monkeypatch.setattr(runner, "load_plan", reject)
    elif stage == "cal_input": monkeypatch.setattr(runner, "load_cal_input", reject)
    elif stage == "candidate": monkeypatch.setattr(runner, "load_candidate", reject)
    else: monkeypatch.setattr(worker, "predict_components", lambda *args: {"probability": np.zeros(4)})
    with pytest.raises(ValueError): runner.preflight()
    assert calls == [] and not runner.CAL_ATTEMPT_PATH.exists()


@pytest.mark.parametrize("field", ["plan_sha256", "same_cal_rows_and_procedure", "test_outcomes_consumed",
    "cal_partition_input_sha256", "candidate_cal_replay_equal", "calibrator_fits_started", "model_fits_started",
    "saved_candidate_retrained", "attempt_sha256", "preservation_after", "calibration_audit", "settings", "completed_at"])
def test_real_calibration_reader_rejects_bound_field_drift_even_resealed(sealed_cli, tmp_path, monkeypatch, field):
    plan, calls, _fake = sealed_cli
    runner.preflight()
    calibration = runner.read_sealed(runner.CAL_PATH)
    changed = deepcopy(calibration)
    if isinstance(changed[field], bool): changed[field] = not changed[field]
    elif isinstance(changed[field], int): changed[field] += 1
    elif field == "completed_at": changed[field] = "2026-10-11T00:00:00+00:00"
    else: changed[field] = "wrong"
    path = runner.OUTPUT / "changed-calibration.json"
    runner.write_sealed(path, changed)
    monkeypatch.setattr(runner, "CAL_PATH", path)
    with pytest.raises((ValueError, KeyError, TypeError)): runner.read_calibration(plan)
    assert len(calls) == 1


def test_real_readback_seal_detects_file_mutation_and_output_is_append_only(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "OUTPUT", tmp_path / "shadow")
    path = runner.OUTPUT / "one.json"
    runner.write_sealed(path, {"test": "first"})
    assert runner.read_sealed(path) == {"test": "first"}
    with pytest.raises(ValueError, match="new-isolated-output-required"):
        runner.write_sealed(path, {"test": "rewrite"})
    path.write_text(json.dumps({"test": "changed"}), encoding="utf-8")
    with pytest.raises(ValueError, match="output-seal-mismatch"): runner.read_sealed(path)
    with pytest.raises(ValueError, match="new-isolated-output-required"):
        runner.write_sealed(tmp_path / "outside.json", {"test": "outside"})


def test_replay_is_post_outcome_connection_only_with_no_calibration_or_model_fit(sealed_cli, monkeypatch, shadow_input):
    plan, calls, _simulated = sealed_cli
    runner.preflight()
    frame, receipt = shadow_input
    monkeypatch.setattr(runner, "capture_input", lambda: (frame, deepcopy(receipt)))
    monkeypatch.setattr(worker, "fit_conditioned_calibration", lambda *args: pytest.fail("replay attempted calibration"))
    market = worker.market_values(frame)
    monkeypatch.setattr(worker, "predict_components", lambda *args: {"probability": market})
    response = runner.replay()
    assert len(calls) == 1 and response["new_model_fits"] == 0
    saved = runner.read_sealed(Path(response["output"]))
    assert saved["shadow_prediction_generated_after_outcome"] is True
    assert saved["historical_ci_transferred_to_prediction"] is False
    assert saved["original_input_timestamp_preserved"] is True
    assert saved["serialized_replay_equal"] is True
    assert saved["validation"]["history_freshness_verified"] is False
    assert saved["actual_purchases"] == 0 and saved["eligible_for_ev"] is False
    assert saved["future_target_status"] == "awaiting_user_selected_unstarted_race"


def test_actual_plan_reader_detects_source_or_preservation_drift_before_execution(tmp_path, monkeypatch):
    output = tmp_path / "shadow"
    monkeypatch.setattr(runner, "OUTPUT", output)
    monkeypatch.setattr(runner, "PLAN_PATH", output / "registered-plan.json")
    preview = {"schema": runner.SCHEMA, "source_hashes": {"immutable": "a" * 64},
               "preservation_before": {"model": "unchanged", "forward": "unchanged"},
               "future_target": None}
    recorded = {**deepcopy(preview), "registered_at": "2026-10-12T00:00:00+00:00"}
    runner.write_sealed(runner.PLAN_PATH, recorded)
    monkeypatch.setattr(runner, "build_plan", lambda: deepcopy(preview))
    assert runner.load_plan() == recorded
    preview["preservation_before"]["model"] = "different"
    with pytest.raises(ValueError, match="plan-or-immutable-input-drift"): runner.load_plan()


@pytest.mark.parametrize("change", [None, "not_cal", "future_year", "different_odds", "different_winner",
                                   "different_market", "different_features", "wrong_partition"])
def test_actual_cal_loader_uses_exact_2025_full_rows_and_saved_market(shadow_input, tmp_path, monkeypatch, change):
    frame = shadow_input[0].copy()
    frame["race_date"] = "20250105"
    frame["finish"] = [1, 2, 3, 4]
    columns, _ = worker.feature_contract(adapter.CANDIDATE)
    saved = frame.copy()
    saved["split"], saved["candidate_id"], saved["fold_year"] = "CAL", adapter.CANDIDATE, 2026
    saved["market_probability"] = worker.market_values(frame)
    plan = {"cal_partition": worker.validate_frame(frame),
            "cal_partition_input_sha256": worker.frame_hash(frame[columns]),
            "artifacts": {"shared_input": {"path": "shared.parquet"}, "cal_predictions": {"path": "cal.parquet"}}}
    if change == "not_cal": saved["split"] = "TEST"
    elif change == "future_year": frame["race_date"] = "20260105"
    elif change == "different_odds": saved.loc[1, "odds"] = 99.
    elif change == "different_winner": saved["finish"] = [2, 1, 3, 4]
    elif change == "different_market": saved.loc[1, "market_probability"] = .99
    elif change == "different_features": frame["safe_age"] = 99.
    elif change == "wrong_partition": plan["cal_partition"]["runner_count"] = 9
    monkeypatch.setattr(runner, "verify_file_receipt", lambda item: tmp_path / item["path"])
    seen = []

    def read_parquet(path, **kwargs):
        seen.append(kwargs)
        if Path(path).name == "shared.parquet":
            assert kwargs["filters"] == [("race_date", ">=", "20250101"), ("race_date", "<=", "20251231")]
            assert "finish" in kwargs["columns"] and "tansho_payout" not in kwargs["columns"]
            return frame.copy()
        return saved.copy()

    monkeypatch.setattr(pd, "read_parquet", read_parquet)
    if change is None:
        loaded, actual_saved, market = runner.load_cal_input(plan)
        pd.testing.assert_frame_equal(loaded, frame)
        pd.testing.assert_frame_equal(actual_saved, saved)
        np.testing.assert_array_equal(market, saved.market_probability)
    else:
        with pytest.raises(ValueError): runner.load_cal_input(plan)
    assert seen


@pytest.mark.parametrize("change", [None, "sources", "encoder_sha", "shared_input_sha", "text_sha", "cal_audit", "artifact_sha"])
def test_actual_candidate_loader_binds_model_encoder_calibration_and_sources(tmp_path, monkeypatch, change):
    import joblib
    bundle = bundle_fixture()
    bundle.update(source_hashes={"immutable": "a" * 64}, frame_sha256="b" * 64,
                  encoder_sha256="c" * 64, calibration_audit={"CAL": "2025-only"})
    result = {"model_text_sha256": bundle["model_text_sha256"], "calibration_audit": bundle["calibration_audit"]}
    plan = {"source_hashes": deepcopy(bundle["source_hashes"]), "candidate_encoder_sha256": bundle["encoder_sha256"],
        "artifacts": {"model": {"path": "model.joblib", "sha256": "d" * 64},
                      "worker_result": {"path": "worker.json"}, "shared_input": {"sha256": bundle["frame_sha256"]}}}
    if change == "sources": bundle["source_hashes"] = {}
    elif change == "encoder_sha": bundle["encoder_sha256"] = "0" * 64
    elif change == "shared_input_sha": bundle["frame_sha256"] = "0" * 64
    elif change == "text_sha": result["model_text_sha256"] = "0" * 64
    elif change == "cal_audit": result["calibration_audit"] = {}
    monkeypatch.setattr(runner, "verify_file_receipt", lambda item: tmp_path / item["path"])
    monkeypatch.setattr(joblib, "load", lambda path: bundle)
    monkeypatch.setattr(runner, "read", lambda path: result)
    monkeypatch.setattr(worker, "file_hash", lambda path: "0" * 64 if change == "artifact_sha" else "d" * 64)
    if change is None:
        assert runner.load_candidate(plan) is bundle
    else:
        with pytest.raises(ValueError, match="model-encoder-and-calibration-binding"):
            runner.load_candidate(plan)
