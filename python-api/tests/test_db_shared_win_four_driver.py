"""No training/network: fixed protocol, settlement sidecar and phase gate."""
from pathlib import Path
import json
import sqlite3
import sys
import types

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import evaluate_db_shared_win_four as driver


def sample():
    return pd.DataFrame({
        "race_id": ["202601010101"] * 2 + ["202601010102"] * 2,
        "race_date": ["20260101"] * 4, "horse_number": [1, 2, 1, 2],
        "finish": [1, 2, 2, 1], "odds": [2., 3., 3., 2.],
        "venue": ["東京"] * 4, "surface": ["芝"] * 4,
        "expected_runner_count": [2] * 4,
        "probability": [.6, .4, .4, .6], "raw_score": [.6, .4, .4, .6]})


def database(tmp_path, returns):
    path = tmp_path / "returns.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE return_tables_ultimate (race_id TEXT,bet_type TEXT,combinations TEXT,payout)")
        connection.executemany("INSERT INTO return_tables_ultimate VALUES (?,?,?,?)", returns)
    return path


def test_frozen_protocol_has_exact_four_and_independent_evaluation():
    value = driver.read(driver.CONFIG)
    driver.validate_protocol(value)
    assert len(value["candidates"]) == 4
    assert value["fold_years"] == list(range(2016, 2027))
    assert value["actual_purchase"] is False
    assert value["test_early_stopping"] is False


@pytest.mark.parametrize("key", ["activation", "forward_changes", "actual_purchase", "commit_push", "optuna"])
def test_prohibited_change_rejected(key):
    value = driver.read(driver.CONFIG)
    value[key] = True
    with pytest.raises(ValueError, match="frozen-protocol"):
        driver.validate_protocol(value)


def test_payout_missing_never_affects_training_rows_or_becomes_odds(tmp_path):
    path = database(tmp_path, [("202601010101", "単勝", "1", 210)])
    frame = sample()
    output, audit = driver.payout_attachment(frame, path)
    assert len(output) == len(frame)
    assert output.loc[0, "tansho_payout"] == 210
    assert output.loc[3, "race_payout_complete"] == False
    assert np.isnan(output.loc[3, "tansho_payout"])
    assert audit["fitting_rows_excluded_due_to_payout"] == 0
    assert audit["excluded_reasons"]["202601010102"] == "return_unavailable"
    assert "tansho_payout" not in frame


@pytest.mark.parametrize("records,reason", [
    ([("202601010101", "単勝", "2", 210)], "winner_return_key_conflict"),
    ([("202601010101", "単勝", "1", 205)], "invalid_return_key_or_amount"),
    ([("202601010101", "単勝", "1", 210)] * 2, "duplicate_or_multiple_winner_return"),
])
def test_invalid_return_excluded_only_from_roi(tmp_path, records, reason):
    output, audit = driver.payout_attachment(sample(), database(tmp_path, records))
    assert len(output) == 4
    assert output.race_payout_complete.eq(False).all()
    assert audit["excluded_reasons"]["202601010101"] == reason


def test_gate_rejects_unverified_chain_even_with_a_true_receipt(tmp_path, monkeypatch):
    (tmp_path / "proof.json").write_text(json.dumps({"phase1_pass": True}), encoding="utf-8")
    monkeypatch.setitem(sys.modules, "run_db_shared_win_pipeline", types.SimpleNamespace(
        verify=lambda directory: {"phase1_pass": False}))
    with pytest.raises(ValueError, match="real-complete-phase1"):
        driver.phase1_gate(tmp_path)


def test_gate_requires_exact_independent_readback(tmp_path, monkeypatch):
    (tmp_path / "proof.json").write_text(json.dumps({"phase1_pass": True, "reason": "forged"}), encoding="utf-8")
    monkeypatch.setitem(sys.modules, "run_db_shared_win_pipeline", types.SimpleNamespace(
        verify=lambda directory: {"phase1_pass": True, "reason": "real"}))
    with pytest.raises(ValueError, match="readback"):
        driver.phase1_gate(tmp_path)


def test_roi_uses_actual_payout_not_odds(tmp_path):
    import db_shared_blend46_betting as betting
    records = [("202601010101", "単勝", "1", 210), ("202601010102", "単勝", "2", 190)]
    output, _ = driver.payout_attachment(sample(), database(tmp_path, records))
    summary = betting.summarize_policy(output, betting.select_bets(output, "top1"))
    assert summary["stake"] == 200
    assert summary["payout"] == 400
    assert summary["roi_percent"] == 200


def test_prediction_attachment_preserves_chronology_not_venue_id_sort():
    reference = sample()
    reference.loc[:1, "race_id"] = "202610010101"
    reference.loc[2:, "race_id"] = "202601010101"
    reference.loc[2:, "race_date"] = "20260102"
    output = reference.sort_values(["race_id", "race_date", "horse_number"]).reset_index(drop=True)
    attached = driver.attach_predictions(reference, output)
    assert attached.race_date.tolist() == ["20260101", "20260101", "20260102", "20260102"]
    np.testing.assert_array_equal(attached.probability, reference.probability)


@pytest.mark.parametrize("column,value", [("finish", 2), ("odds", 9.), ("horse_number", 3)])
def test_prediction_outcomes_and_identities_cannot_drift(column, value):
    reference = sample()
    output = reference.copy()
    output.loc[0, column] = value
    with pytest.raises(ValueError, match="cohort|binding"):
        driver.attach_predictions(reference, output)


def test_full_three_part_evaluation_smoke_separates_probability_calibration_and_roi():
    rows = []
    for year in range(2016, 2027):
        for number in (1, 2):
            rows.append({"race_id": f"{year}01010101", "race_date": f"{year}0101", "horse_number": number,
                "finish": number, "odds": 2. if number == 1 else 3., "expected_runner_count": 2,
                "venue": "東京", "surface": "芝", "normalized_market_probability": .6 if number == 1 else .4,
                "probability": .6 if number == 1 else .4, "raw_score": .6 if number == 1 else .4,
                "race_payout_complete": year != 2026, "tansho_payout": 210. if number == 1 and year != 2026 else np.nan})
    frame = pd.DataFrame(rows)
    predictions = {candidate: frame.copy() for candidate in driver.CANDIDATES}
    protocol = driver.read(driver.CONFIG)
    protocol["probability_bootstrap_repetitions"] = 20
    protocol["roi_bootstrap_repetitions"] = 20
    result = driver.evaluate_predictions(predictions, frame, protocol)
    assert result["prediction_precision"]["SEEN_DIAGNOSTIC_2025_2026"]["market_reference"]["race_count"] == 2
    assert result["separate_fixed_rule_roi"]["SEEN_DIAGNOSTIC_2025_2026"]["roi_races"] == 1
    assert result["separate_fixed_rule_roi"]["SEEN_DIAGNOSTIC_2025_2026"]["policy_selection_performed"] is False
    assert result["prediction_precision"]["ALL_RETROSPECTIVE"]["market_reference"]["physical_speed_rmse"] is None
    assert result["win_probability_calibration"]["ALL_RETROSPECTIVE"]["market_reference"]["top1"]["selected_bet_count"] == 11


@pytest.mark.parametrize("name", ["job_id", "candidate_id", "fold_year", "frame_sha256", "phase1_receipt_sha256"])
def test_result_is_bound_to_requested_job_candidate_fold_and_evidence(name):
    import db_shared_win_four_worker as worker
    columns, categories = worker.feature_contract()
    request = {"job_id": "2026-expanding_binary", "candidate_id": "expanding_binary", "fold_year": 2026,
        "frame_sha256": "a" * 64, "protocol_sha256": "b" * 64, "phase1_receipt_sha256": "c" * 64,
        "feature_columns": columns, "categorical_columns": categories, "expected_source_hashes": {"example": "d" * 64}}
    periods = worker.fold_periods(2026, "expanding_binary")
    result = {"schema": worker.SCHEMA, "status": "completed", "request_sha256": worker.canonical_hash(request),
        **{key: value for key, value in request.items() if key != "expected_source_hashes"},
        "source_hashes": request["expected_source_hashes"], "feature_count": 50,
        "periods": periods, "runtime": {"device": "cpu", "native_threads": 2, "rounds": 150},
        "partitions": {key: {"date_from": bounds[0], "date_to": bounds[1]} for key, bounds in periods.items()}}
    driver.validate_worker_result(result, request, worker)
    result[name] = 2025 if name == "fold_year" else "wrong"
    with pytest.raises(ValueError, match="result-binding"):
        driver.validate_worker_result(result, request, worker)


def test_real_pipeline_gate_blocks_execute_before_preparation_or_fitting(monkeypatch):
    monkeypatch.setattr(driver, "phase1_gate", lambda plan: (_ for _ in ()).throw(ValueError("phase1 not complete")))
    monkeypatch.setattr(driver, "prepare_data", lambda protocol: pytest.fail("data prep must follow complete Phase1"))
    monkeypatch.setattr(driver, "run_subprocess", lambda *args: pytest.fail("fit must not start"))
    with pytest.raises(ValueError, match="phase1 not complete"):
        driver.execute()


@pytest.mark.parametrize("field", ["protocol_sha256", "source_hashes", "fits_started", "actual_purchase"])
def test_preregistered_protocol_and_source_cannot_drift(tmp_path, monkeypatch, field):
    protocol = driver.read(driver.CONFIG)
    pins = {"source": "a" * 64}
    locked = {"schema": "db-shared-win-four-preregistration-v1", "protocol": protocol,
        "protocol_sha256": driver.sha(driver.CONFIG), "source_hashes": pins,
        "fits_started": 0, "actual_purchase": False}
    path = tmp_path / "locked.json"
    monkeypatch.setattr(driver, "LOCK_PATH", path)
    driver.save(path, locked)
    assert driver.read_preregistration(protocol, pins) == locked
    path.unlink()
    locked[field] = True if field == "actual_purchase" else "wrong"
    driver.save(path, locked)
    with pytest.raises(ValueError, match="preregistered"):
        driver.read_preregistration(protocol, pins)
