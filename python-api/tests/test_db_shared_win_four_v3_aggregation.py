"""Offline technical-only empty-selection fix and saved-artifact aggregation."""
from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import db_shared_win_direct_diagnostics as original_diagnostics
import db_shared_win_four_diagnostics_v3 as diagnostics
import db_shared_blend46_betting as betting
import evaluate_db_shared_win_four_v2 as original_driver
import aggregate_db_shared_win_four_v3 as aggregate


DTYPES = [object, "string[python]", "string[pyarrow]", pd.StringDtype(storage="pyarrow", na_value=np.nan)]


def cohort(dtype="string[pyarrow]", years=(2026,)):
    rows = []
    for year in years:
        for race, day in ((1, f"{year}0105"), (2, f"{year}0111")):
            for number in (1, 2, 3):
                winner = race
                finish = 1 if number == winner else 2 + [n for n in (1, 2, 3) if n != winner].index(number)
                odds = (2., 4., 7.)[number - 1]
                probability = (.4, .35, .25)[number - 1]
                rows.append({
                    "race_id": f"{year}050101{race:02d}", "race_date": day,
                    "horse_number": number, "expected_runner_count": 3,
                    "finish": finish, "odds": odds, "probability": probability,
                    "venue": "東京", "surface": "芝", "raw_score": np.log(probability),
                    "race_payout_complete": True,
                    "tansho_payout": int(100 * odds) if number == winner else np.nan,
                })
    frame = pd.DataFrame(rows)
    frame["race_id"] = frame.race_id.astype(dtype)
    inverse = 1 / frame.odds
    frame["normalized_market_probability"] = inverse / inverse.groupby(frame.race_id).transform("sum")
    return frame


@pytest.mark.parametrize("dtype", DTYPES)
@pytest.mark.parametrize("with_date", [False, True])
def test_empty_nullable_arrow_python_and_object_selection_is_not_imputed(dtype, with_date):
    frame = cohort(dtype)
    columns = ["race_id", "horse_number"] + (["race_date"] if with_date else [])
    keys = frame.iloc[:0][columns].copy()
    before = keys.copy(deep=True)
    result = diagnostics.selection_calibration(frame, keys, repetitions=20)
    pd.testing.assert_frame_equal(keys, before)
    assert result["selected_bet_count"] == result["selected_race_count"] == result["selected_date_count"] == 0
    assert result["date_count"] == 2
    assert result["selection_race_coverage"] == result["selection_date_coverage"] == 0.
    assert result["eligible_no_bet_dates_included"] is True
    for record in [result["overall"], *result["odds_band_calibration"]]:
        assert record["count"] == record["wins"] == record["bootstrap_samples_with_observations"] == 0
        for field in ("mean_probability", "observed_win_rate", "bias_predicted_minus_actual",
                      "mean_probability_ci95", "observed_win_rate_ci95", "bias_ci95"):
            assert record[field] is None
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("dtype", ["string[python]", "string[pyarrow]"])
def test_original_nullable_empty_reduction_failure_is_still_reproducible(dtype):
    frame = cohort(dtype)
    with pytest.raises(TypeError, match="reduction.*all.*string dtype"):
        original_diagnostics.selection_calibration(frame, frame.iloc[:0][["race_id", "horse_number"]], repetitions=20)


@pytest.mark.parametrize("dtype", DTYPES)
def test_nonempty_selection_and_probability_analysis_are_exactly_original(dtype):
    frame = cohort(dtype)
    selected = frame.loc[[0, 4], ["race_id", "horse_number", "race_date"]].copy()
    expected = original_diagnostics.selection_calibration(frame, selected, repetitions=40, seed=20261012)
    assert diagnostics.selection_calibration(frame, selected, repetitions=40, seed=20261012) == expected
    assert diagnostics.analyze_probability(frame, repetitions=40) == original_diagnostics.analyze_probability(frame, repetitions=40)


@pytest.mark.parametrize("value", [pd.NA, None, "", "  ", 1, False])
def test_invalid_or_missing_selected_race_identity_is_rejected(value):
    frame = cohort()
    selected = frame.iloc[:1][["race_id", "horse_number"]].astype({"race_id": object})
    selected.loc[0, "race_id"] = value
    with pytest.raises(ValueError, match="invalid-selected-race_id"):
        diagnostics.selection_calibration(frame, selected, repetitions=20)


@pytest.mark.parametrize("change", ["unknown_race", "unknown_horse", "date_mismatch", "duplicate", "horse_na"])
def test_invalid_selected_keys_cannot_be_treated_as_empty_or_valid(change):
    frame = cohort()
    selected = frame.iloc[:1][["race_id", "horse_number", "race_date"]].copy()
    if change == "unknown_race":
        selected["race_id"] = "202605019999"
    elif change == "unknown_horse":
        selected["horse_number"] = 18
    elif change == "date_mismatch":
        selected["race_date"] = "20260112"
    elif change == "duplicate":
        selected = pd.concat([selected, selected], ignore_index=True)
    else:
        selected["horse_number"] = pd.Series([pd.NA], dtype="Int64")
    with pytest.raises(ValueError):
        diagnostics.selection_calibration(frame, selected, repetitions=20)


def test_no_bet_roi_is_none_not_zero_or_one_hundred_percent():
    frame = cohort()
    keys = betting.select_bets(frame, "top1_ev105")
    assert keys.empty
    summary = betting.summarize_policy(frame, keys)
    assert summary["bets"] == 0 and summary["stake"] == 0 and summary["payout"] == 0
    assert summary["roi_percent"] is None
    bootstrap = betting.bootstrap_policy_comparisons(
        frame, {"no_bet": keys, "market_favorite": betting.select_bets(frame, "top1")},
        "market_favorite", repetitions=20, family_size=9,
    )
    no_bet = next(item for item in bootstrap["candidates"] if item["policy_id"] == "no_bet")
    assert no_bet["roi_percent"] is None and no_bet["roi_ci95"] is None
    assert no_bet["roi_ci_simultaneous"] is None and no_bet["bootstrap_samples_with_stake"] == 0


def function_nodes(path):
    return {node.name: node for node in ast.parse(path.read_text(encoding="utf-8")).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_diagnostics_ast_has_only_one_expected_boolean_container_change():
    old = function_nodes(ROOT / "scripts/db_shared_win_direct_diagnostics.py")
    new = function_nodes(ROOT / "scripts/db_shared_win_four_diagnostics_v3.py")
    assert set(old) <= set(new)
    for name in old:
        if name != "selection_calibration":
            assert ast.dump(old[name], include_attributes=False) == ast.dump(new[name], include_attributes=False), name
    revised = copy.deepcopy(new["selection_calibration"])
    changes = []
    for node in ast.walk(revised):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "all"
                and isinstance(node.func.value, ast.Call) and isinstance(node.func.value.func, ast.Attribute)
                and node.func.value.func.attr == "to_numpy"):
            wrapped = node.func.value
            assert wrapped.args == [] and len(wrapped.keywords) == 1
            assert wrapped.keywords[0].arg == "dtype"
            assert isinstance(wrapped.keywords[0].value, ast.Name) and wrapped.keywords[0].value.id == "bool"
            node.func.value = wrapped.func.value
            changes.append(node)
    assert len(changes) == 1
    assert ast.dump(revised, include_attributes=False) == ast.dump(old["selection_calibration"], include_attributes=False)


def test_aggregation_evaluation_ast_changes_only_diagnostics_import():
    old = function_nodes(ROOT / "scripts/evaluate_db_shared_win_four_v2.py")["evaluate_predictions"]
    revised = copy.deepcopy(function_nodes(ROOT / "scripts/aggregate_db_shared_win_four_v3.py")["evaluate_predictions"])
    changed = []
    for node in ast.walk(revised):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "db_shared_win_four_diagnostics_v3":
                    assert alias.asname == "diagnostics"
                    alias.name = "db_shared_win_direct_diagnostics"
                    changed.append(alias)
    assert len(changed) == 1
    assert ast.dump(old, include_attributes=False) == ast.dump(revised, include_attributes=False)


def test_all_year_evaluation_preserves_empty_strategy_counts_and_nulls():
    frame = cohort("string[pyarrow]", years=range(2016, 2027))
    protocol = original_driver.read(original_driver.CONFIG)
    protocol["probability_bootstrap_repetitions"] = protocol["roi_bootstrap_repetitions"] = 20
    predictions = {name: frame.copy() for name in original_driver.CANDIDATES}
    result = aggregate.evaluate_predictions(predictions, frame, protocol)
    for period in ("DEVELOPMENT_2016_2024", "SEEN_DIAGNOSTIC_2025_2026", "ALL_RETROSPECTIVE"):
        for candidate in original_driver.CANDIDATES:
            calibration = result["win_probability_calibration"][period][candidate]["top1_ev105"]
            assert calibration["selected_bet_count"] == 0
            assert calibration["overall"]["mean_probability"] is None
            roi = result["separate_fixed_rule_roi"][period]["summaries"][candidate + ":top1_ev105"]
            assert roi["bets"] == 0 and roi["roi_percent"] is None
    assert result["new_model_activation"] is False
    json.dumps(result, allow_nan=False)


def test_aggregation_source_has_no_fitter_preprocessing_or_subprocess_calls():
    tree = ast.parse((ROOT / "scripts/aggregate_db_shared_win_four_v3.py").read_text(encoding="utf-8"))
    forbidden = {"fit", "fit_model", "fit_candidate", "run_worker", "run_subprocess", "prepare_data", "subprocess"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
            assert name not in forbidden
            assert not (name == "execute" and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name) and node.func.value.id == "v2")
    assert aggregate.verify_semantic_copy()["fitter_subprocess_preprocessing_calls"] == 0


@pytest.fixture
def saved_study_fixture(tmp_path, monkeypatch):
    """44 receipts exercise the real verifier; physical hashes/evidence are mocked."""
    study = tmp_path / "saved44"
    study.mkdir()
    protocol = original_driver.read(original_driver.CONFIG)
    protocol_sha = aggregate.sha(aggregate.CONFIG)
    pins = {name: aggregate.sha(ROOT / name) for name in original_driver.SOURCES}
    preservation = {"protected_state": "unchanged"}
    proof_sha = "a" * 64
    frame_sha = "b" * 64
    artifact_sha = "c" * 64
    recorded = {
        "protocol": protocol, "protocol_sha256": protocol_sha, "source_hashes": pins,
        "preservation_before": preservation, "technical_amendment_sha256": "d" * 64,
        "technical_amendment_manifest_sha256": "e" * 64, "phase1_proof_sha256": proof_sha,
    }
    failure = {"status": "failed", "error_type": "TypeError",
               "reason": "Cannot perform reduction 'all' with string dtype", "preservation_after": preservation}
    documents = {"protocol.json": recorded, "failure.json": failure}
    hash_values = {str((ROOT / name).resolve()): value for name, value in pins.items()}
    hash_values.update({str((ROOT / name).resolve()): value for name, value in aggregate.REVIEW_PINS.items()})
    hash_values[str(original_driver.AMENDMENT_PATH.resolve())] = recorded["technical_amendment_sha256"]
    hash_values[str(original_driver.AMENDMENT_MANIFEST_PATH.resolve())] = recorded["technical_amendment_manifest_sha256"]
    hash_values[str(original_driver.LOCK_PATH.resolve())] = "f" * 64
    hash_values[str((study / "shared50.parquet").resolve())] = frame_sha
    columns, categories = aggregate.worker.feature_contract()
    for year in protocol["fold_years"]:
        for candidate in aggregate.CANDIDATES:
            job = f"{year}-{candidate}"
            request = {
                "job_id": job, "candidate_id": candidate, "fold_year": year,
                "frame_sha256": frame_sha, "protocol_sha256": protocol_sha,
                "phase1_receipt_sha256": proof_sha, "feature_columns": columns,
                "categorical_columns": categories, "expected_source_hashes": pins,
            }
            periods = aggregate.worker.fold_periods(year, candidate)
            result = {
                "schema": aggregate.worker.SCHEMA, "status": "completed",
                "request_sha256": aggregate.worker.canonical_hash(request),
                **{key: value for key, value in request.items() if key != "expected_source_hashes"},
                "source_hashes": pins, "feature_count": 50, "periods": json.loads(json.dumps(periods)),
                "runtime": {"device": "cpu", "native_threads": 2, "rounds": 150},
                "partitions": {key: {"date_from": bounds[0], "date_to": bounds[1]}
                               for key, bounds in periods.items()},
                "artifacts": {key: {"path": str(study / f"{job}-{key}.fixture"), "sha256": artifact_sha}
                              for key in ("model", "predictions", "calibration_predictions")},
            }
            documents[job + "-request.json"] = request
            documents[job + ".json"] = result
    inventory = {"file_count": aggregate.SAVED_STUDY_FILE_COUNT,
                 "fingerprint_sha256": aggregate.SAVED_STUDY_FINGERPRINT, "files_sha256": {}}

    def read_document(path):
        if Path(path).resolve() == aggregate.CONFIG.resolve():
            return protocol
        return documents[Path(path).name]

    def file_sha(path):
        return hash_values.get(str(Path(path).resolve()), artifact_sha)

    monkeypatch.setattr(aggregate, "SAVED_STUDY", study)
    monkeypatch.setattr(aggregate, "read", read_document)
    monkeypatch.setattr(aggregate, "sha", file_sha)
    monkeypatch.setattr(aggregate, "study_inventory", lambda: inventory)
    monkeypatch.setattr(aggregate, "preservation_receipt", lambda: preservation)
    monkeypatch.setattr(original_driver, "phase1_gate", lambda plan: (tmp_path / "proof.json", proof_sha, {"phase1_pass": True}))
    monkeypatch.setattr(original_driver, "read_amendment", lambda *args: {"validated": True})
    return documents, hash_values, inventory, preservation


def test_real_saved44_verifier_accepts_all_expected_receipts_without_any_fit(saved_study_fixture):
    metadata = aggregate.validate_saved_study()
    assert len(metadata["worker_results"]) == 44
    assert metadata["validated_worker_artifacts"] == 132
    assert metadata["phase1_pass"] is True


@pytest.mark.parametrize("change", [
    "source", "review", "phase1", "protected_state", "amendment",
    "fingerprint", "file_count", "request", "result", "artifact_hash",
    "artifact_outside", "artifact_count", "missing_worker",
])
def test_saved44_integrity_source_and_provenance_drift_fail_closed(saved_study_fixture, tmp_path, monkeypatch, change):
    documents, hashes, inventory, preservation = saved_study_fixture
    first = "2016-expanding_binary"
    if change == "source":
        path = next(iter(documents["protocol.json"]["source_hashes"]))
        hashes[str((ROOT / path).resolve())] = "0" * 64
    elif change == "review":
        path = next(iter(aggregate.REVIEW_PINS))
        hashes[str((ROOT / path).resolve())] = "0" * 64
    elif change == "phase1":
        documents["protocol.json"]["phase1_proof_sha256"] = "0" * 64
    elif change == "protected_state":
        monkeypatch.setattr(aggregate, "preservation_receipt", lambda: {"protected_state": "changed"})
    elif change == "amendment":
        documents["protocol.json"]["technical_amendment_sha256"] = "0" * 64
    elif change == "fingerprint":
        inventory["fingerprint_sha256"] = "0" * 64
    elif change == "file_count":
        inventory["file_count"] -= 1
    elif change == "request":
        documents[first + "-request.json"]["fold_year"] = 2017
    elif change == "result":
        documents[first + ".json"]["candidate_id"] = "wrong"
    elif change == "artifact_hash":
        documents[first + ".json"]["artifacts"]["model"]["sha256"] = "0" * 64
    elif change == "artifact_outside":
        documents[first + ".json"]["artifacts"]["model"]["path"] = str(tmp_path / "outside.fixture")
    elif change == "artifact_count":
        documents[first + ".json"]["artifacts"].pop("model")
    else:
        documents.pop(first + ".json")
    with pytest.raises((ValueError, KeyError, FileNotFoundError)):
        aggregate.validate_saved_study()


@pytest.mark.parametrize("stage", ["phase1", "old_amendment", "saved_integrity", "new_amendment"])
def test_rejected_provenance_never_reaches_saved_input_loading_or_evaluation(
    saved_study_fixture, monkeypatch, stage,
):
    def rejected(*args):
        raise ValueError(stage + " not verified")

    if stage == "phase1":
        monkeypatch.setattr(original_driver, "phase1_gate", rejected)
    elif stage == "old_amendment":
        monkeypatch.setattr(original_driver, "read_amendment", rejected)
    elif stage == "saved_integrity":
        monkeypatch.setattr(aggregate, "validate_saved_study", rejected)
    else:
        monkeypatch.setattr(aggregate, "read_amendment", rejected)
    monkeypatch.setattr(aggregate, "load_saved_predictions", lambda *args: pytest.fail("unverified saved inputs consumed"))
    monkeypatch.setattr(aggregate, "evaluate_predictions", lambda *args: pytest.fail("unverified probabilities evaluated"))
    with pytest.raises(ValueError, match="not verified"):
        aggregate.aggregate()


@pytest.fixture
def sealed_aggregation_amendment(tmp_path, monkeypatch):
    """Use the real v3 reader and manifest; do not modify the operational seal."""
    metadata = {
        "source_hashes": {"original nineteen sources": "a" * 64},
        "protocol_sha256": "b" * 64, "phase1_proof_sha256": "c" * 64,
        "v2_amendment_sha256": "d" * 64, "original_preregistration_sha256": "e" * 64,
        "preservation_before": {"protected_state": "unchanged"},
    }
    path, manifest = tmp_path / "aggregation-amendment.json", tmp_path / "aggregation-manifest.json"
    monkeypatch.setattr(aggregate, "AMENDMENT_PATH", path)
    monkeypatch.setattr(aggregate, "AMENDMENT_MANIFEST_PATH", manifest)
    receipt = {
        "schema": "db-shared-win-four-aggregation-amendment-v3", "human_authorized": True,
        "post_outcome_technical_amendment": True, "new_preregistration_claim": False,
        "new_fits_started": 0, "saved_v2_artifacts_reused": True, "old_v1_models_adopted": False,
        "saved_study_path": str(aggregate.SAVED_STUDY.resolve()),
        "saved_study_fingerprint_sha256": aggregate.SAVED_STUDY_FINGERPRINT,
        "original_source_hashes": metadata["source_hashes"],
        "aggregation_source_hashes": {name: aggregate.sha(ROOT / name) for name in aggregate.AGGREGATION_SOURCES},
        "protocol_sha256": metadata["protocol_sha256"], "phase1_proof_sha256": metadata["phase1_proof_sha256"],
        "v2_amendment_sha256": metadata["v2_amendment_sha256"],
        "original_preregistration_sha256": metadata["original_preregistration_sha256"],
        "review_receipt_hashes": aggregate.REVIEW_PINS, "semantic_copy_proof": aggregate.verify_semantic_copy(),
        "preservation_before": metadata["preservation_before"],
    }
    aggregate.save(path, receipt)
    aggregate.save(manifest, aggregate.manifest_for(receipt))
    return metadata, receipt


def test_actual_aggregation_amendment_reader_accepts_unchanged_matching_seal(sealed_aggregation_amendment):
    metadata, receipt = sealed_aggregation_amendment
    assert aggregate.read_amendment(metadata) == receipt


@pytest.mark.parametrize("field", [
    "schema", "human_authorized", "post_outcome_technical_amendment", "new_preregistration_claim",
    "new_fits_started", "saved_v2_artifacts_reused", "old_v1_models_adopted", "saved_study_path",
    "saved_study_fingerprint_sha256", "original_source_hashes", "aggregation_source_hashes",
    "protocol_sha256", "phase1_proof_sha256", "v2_amendment_sha256", "original_preregistration_sha256",
    "review_receipt_hashes", "semantic_copy_proof", "preservation_before",
])
def test_aggregation_amendment_semantic_drift_rejected_even_with_new_matching_manifest(
    sealed_aggregation_amendment, tmp_path, monkeypatch, field,
):
    metadata, receipt = sealed_aggregation_amendment
    changed = copy.deepcopy(receipt)
    changed[field] = (not changed[field]) if isinstance(changed[field], bool) else 1 if field == "new_fits_started" else "wrong"
    path, manifest = tmp_path / "changed-amendment.json", tmp_path / "changed-manifest.json"
    monkeypatch.setattr(aggregate, "AMENDMENT_PATH", path)
    monkeypatch.setattr(aggregate, "AMENDMENT_MANIFEST_PATH", manifest)
    aggregate.save(path, changed)
    aggregate.save(manifest, aggregate.manifest_for(changed))
    with pytest.raises(ValueError, match="amendment-source-provenance-drift"):
        aggregate.read_amendment(metadata)


def test_aggregation_amendment_manifest_only_drift_is_rejected(sealed_aggregation_amendment, tmp_path, monkeypatch):
    metadata, receipt = sealed_aggregation_amendment
    manifest = aggregate.manifest_for(receipt)
    manifest["amendment_sha256"] = "0" * 64
    path = tmp_path / "manifest-only-drift.json"
    aggregate.save(path, manifest)
    monkeypatch.setattr(aggregate, "AMENDMENT_MANIFEST_PATH", path)
    with pytest.raises(ValueError, match="amendment-source-provenance-drift"):
        aggregate.read_amendment(metadata)


@pytest.mark.parametrize("missing", ["amendment", "manifest"])
def test_missing_v3_seal_is_rejected_before_aggregation(sealed_aggregation_amendment, tmp_path, monkeypatch, missing):
    metadata, receipt = sealed_aggregation_amendment
    monkeypatch.setattr(aggregate, "AMENDMENT_PATH" if missing == "amendment" else "AMENDMENT_MANIFEST_PATH",
                        tmp_path / "missing-evidence.json")
    with pytest.raises(ValueError, match="required-before-aggregation"):
        aggregate.read_amendment(metadata)
