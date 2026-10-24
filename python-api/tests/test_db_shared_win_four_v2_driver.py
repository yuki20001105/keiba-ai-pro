"""Offline regression: only JSON period representation may differ from v1."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import db_shared_win_four_worker as worker
import evaluate_db_shared_win_four as original
import evaluate_db_shared_win_four_v2 as driver


def result_fixture():
    columns, categories = worker.feature_contract()
    request = {
        "job_id": "2026-expanding_binary", "candidate_id": "expanding_binary", "fold_year": 2026,
        "frame_sha256": "a" * 64, "protocol_sha256": "b" * 64,
        "phase1_receipt_sha256": "c" * 64, "feature_columns": columns,
        "categorical_columns": categories, "expected_source_hashes": {"example": "d" * 64},
    }
    periods = worker.fold_periods(2026, "expanding_binary")
    result = {
        "schema": worker.SCHEMA, "status": "completed", "request_sha256": worker.canonical_hash(request),
        **{key: value for key, value in request.items() if key != "expected_source_hashes"},
        "source_hashes": request["expected_source_hashes"], "feature_count": 50,
        "periods": periods, "runtime": {"device": "cpu", "native_threads": 2, "rounds": 150},
        "partitions": {key: {"date_from": bounds[0], "date_to": bounds[1]}
                       for key, bounds in periods.items()},
    }
    return request, result


def round_trip(value):
    return json.loads(json.dumps(value))


def test_v2_accepts_real_json_roundtrip_without_mutating_result():
    request, result = result_fixture()
    loaded = round_trip(result)
    before = copy.deepcopy(loaded)
    assert all(type(bounds) is list for bounds in loaded["periods"].values())
    driver.validate_worker_result(loaded, request, worker)
    assert loaded == before
    # This driver consumes JSON files, so the corrected expected contract is
    # precisely JSON arrays rather than a general coercion of arbitrary values.
    with pytest.raises(ValueError, match="period-binding"):
        driver.validate_worker_result(result, request, worker)


def test_old_v1_still_reproduces_the_same_json_type_error():
    request, result = result_fixture()
    original.validate_worker_result(result, request, worker)
    with pytest.raises(ValueError, match="four-worker-result-period-binding"):
        original.validate_worker_result(round_trip(result), request, worker)


@pytest.mark.parametrize("change", [
    "wrong_date", "missing_period", "extra_period", "scalar_period", "dict_period",
    "null_period", "number_date", "bool_date", "wrong_number_of_bounds", "wrong_period_type",
])
def test_period_value_key_and_type_errors_are_not_normalized_away(change):
    request, result = result_fixture()
    loaded = round_trip(result)
    periods = loaded["periods"]
    if change == "wrong_date":
        periods["TRAIN"][0] = "20140101"
    elif change == "missing_period":
        periods.pop("CAL")
    elif change == "extra_period":
        periods["SELECT"] = ["20200101", "20201231"]
    elif change == "scalar_period":
        periods["CAL"] = "20250101"
    elif change == "dict_period":
        periods["CAL"] = {"0": "20250101", "1": "20251231"}
    elif change == "null_period":
        periods["CAL"] = None
    elif change == "number_date":
        periods["CAL"][0] = 20250101
    elif change == "bool_date":
        periods["CAL"][0] = True
    elif change == "wrong_number_of_bounds":
        periods["CAL"].append("20260101")
    else:
        loaded["periods"] = list(periods.values())
    with pytest.raises(ValueError, match="period-binding"):
        driver.validate_worker_result(loaded, request, worker)


@pytest.mark.parametrize("key", [
    "schema", "status", "request_sha256", "job_id", "candidate_id", "fold_year",
    "frame_sha256", "protocol_sha256", "phase1_receipt_sha256", "source_hashes",
    "feature_count", "feature_columns", "categorical_columns",
])
def test_old_result_bindings_are_still_enforced(key):
    request, result = result_fixture()
    loaded = round_trip(result)
    loaded[key] = 2025 if key == "fold_year" else "wrong"
    with pytest.raises(ValueError, match="result-binding"):
        driver.validate_worker_result(loaded, request, worker)


@pytest.mark.parametrize("key,value", [("device", "gpu"), ("native_threads", 4), ("rounds", 151)])
def test_execution_runtime_remains_frozen(key, value):
    request, result = result_fixture()
    loaded = round_trip(result)
    loaded["runtime"][key] = value
    with pytest.raises(ValueError, match="runtime-binding"):
        driver.validate_worker_result(loaded, request, worker)


@pytest.mark.parametrize("change", ["missing", "extra", "start_before", "end_after", "reversed"])
def test_partition_keys_and_actual_date_bounds_remain_frozen(change):
    request, result = result_fixture()
    loaded = round_trip(result)
    parts = loaded["partitions"]
    if change == "missing":
        parts.pop("TEST")
    elif change == "extra":
        parts["SELECT"] = dict(parts["CAL"])
    elif change == "start_before":
        parts["CAL"]["date_from"] = "20241231"
    elif change == "end_after":
        parts["CAL"]["date_to"] = "20260101"
    else:
        parts["CAL"]["date_from"], parts["CAL"]["date_to"] = "20251231", "20250101"
    with pytest.raises(ValueError, match="partition"):
        driver.validate_worker_result(loaded, request, worker)


def function_nodes(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_all_numeric_preparation_fitting_evaluation_and_gate_helpers_are_ast_identical():
    old = function_nodes(ROOT / "scripts/evaluate_db_shared_win_four.py")
    new = function_nodes(ROOT / "scripts/evaluate_db_shared_win_four_v2.py")
    names = ("validate_protocol", "phase1_gate", "payout_attachment", "prepare_data",
             "run_subprocess", "attach_predictions", "evaluate_predictions")
    for name in names:
        assert ast.dump(old[name], include_attributes=False) == ast.dump(new[name], include_attributes=False), name
    driver.verify_semantic_copy()
    assert tuple(driver.CANDIDATES) == tuple(original.CANDIDATES)
    assert driver.CONFIG == original.CONFIG
    assert tuple(driver.ORIGINAL_SOURCES) == tuple(original.SOURCES)
    assert set(driver.SOURCES) == set(original.SOURCES) | {"scripts/evaluate_db_shared_win_four_v2.py"}


def test_phase1_false_blocks_v2_before_preparation_or_fit(monkeypatch):
    monkeypatch.setattr(driver, "phase1_gate", lambda plan: (_ for _ in ()).throw(ValueError("phase1 not complete")))
    monkeypatch.setattr(driver, "prepare_data", lambda protocol: pytest.fail("preparation reached before gate"))
    monkeypatch.setattr(driver, "run_subprocess", lambda *args: pytest.fail("model fitting must not start"))
    with pytest.raises(ValueError, match="phase1 not complete"):
        driver.execute()


def test_true_flag_cannot_bypass_independent_phase1_readback(tmp_path, monkeypatch):
    original.save(tmp_path / "proof.json", {"phase1_pass": True, "reason": "forged"})
    monkeypatch.setitem(sys.modules, "run_db_shared_win_pipeline", types.SimpleNamespace(
        verify=lambda directory: {"phase1_pass": False},
    ))
    with pytest.raises(ValueError, match="real-complete-phase1"):
        driver.phase1_gate(tmp_path)


@pytest.mark.parametrize("field", ["protocol_sha256", "source_hashes", "fits_started", "actual_purchase"])
def test_original_preregistration_drift_is_still_rejected(tmp_path, monkeypatch, field):
    protocol = original.read(original.CONFIG)
    pins = {name: "a" * 64 for name in driver.SOURCES}
    locked = {
        "schema": "db-shared-win-four-preregistration-v1", "protocol": protocol,
        "protocol_sha256": driver.sha(driver.CONFIG),
        "source_hashes": {name: pins[name] for name in driver.ORIGINAL_SOURCES},
        "fits_started": 0, "actual_purchase": False,
    }
    path = tmp_path / "original-preregistration.json"
    monkeypatch.setattr(driver, "LOCK_PATH", path)
    driver.save(path, locked)
    assert driver.read_preregistration(protocol, pins) == locked
    changed = copy.deepcopy(locked)
    changed[field] = True if field == "actual_purchase" else "wrong"
    bad = tmp_path / "original-preregistration-drift.json"
    driver.save(bad, changed)
    monkeypatch.setattr(driver, "LOCK_PATH", bad)
    with pytest.raises(ValueError, match="preregistered"):
        driver.read_preregistration(protocol, pins)


@pytest.mark.parametrize("stage", ["original_preregistration", "technical_amendment"])
def test_amendment_and_preregistration_rejection_precedes_any_new_fit(tmp_path, monkeypatch, stage):
    import evaluate_db_shared_venues as preservation_module

    proof = {"plan_sha256": "a" * 64, "post_time": "2026-10-12T06:45:00+00:00"}
    preservation = {"test": "unchanged-protected-state"}
    locked = {
        "preservation_before": preservation, "phase1_plan_sha256": proof["plan_sha256"],
        "registered_at": "2026-10-12T04:53:20+00:00",
    }
    calls = []

    def prereg(*args):
        calls.append("original_preregistration")
        if stage == "original_preregistration":
            raise ValueError("original preregistration drift")
        return locked

    def amendment(*args):
        calls.append("technical_amendment")
        raise ValueError("technical amendment drift")

    monkeypatch.setattr(driver, "phase1_gate", lambda plan: (tmp_path / "proof.json", "b" * 64, proof))
    monkeypatch.setattr(preservation_module, "preservation_receipt", lambda: preservation)
    monkeypatch.setattr(driver, "read_preregistration", prereg)
    monkeypatch.setattr(driver, "read_amendment", amendment)
    monkeypatch.setattr(driver, "prepare_data", lambda protocol: pytest.fail("data preparation reached after drift"))
    monkeypatch.setattr(driver, "run_subprocess", lambda *args: pytest.fail("model fit reached after drift"))
    with pytest.raises(ValueError, match="drift"):
        driver.execute()
    assert calls == (["original_preregistration"] if stage == "original_preregistration"
                     else ["original_preregistration", "technical_amendment"])


@pytest.fixture
def sealed_amendment(tmp_path, monkeypatch):
    """Actual amendment reader/manifest, with only external evidence mocked."""
    protocol = original.read(original.CONFIG)
    pins = {name: driver.sha(ROOT / name) for name in driver.SOURCES}
    preservation = {"protected_state": "unchanged"}
    proof = {"post_time": "2026-10-12T06:45:00+00:00", "phase1_pass": True}
    proof_path = tmp_path / protocol["phase1_plan"] / "proof.json"
    proof_path.parent.mkdir(parents=True)
    driver.save(proof_path, proof)
    lock = tmp_path / "original-lock.json"
    driver.save(lock, {"fixture": "original immutable preregistration"})
    amendment_path = tmp_path / "technical-amendment.json"
    manifest_path = tmp_path / "technical-amendment-manifest.json"
    monkeypatch.setattr(driver, "ROOT", tmp_path)
    monkeypatch.setattr(driver, "LOCK_PATH", lock)
    monkeypatch.setattr(driver, "AMENDMENT_PATH", amendment_path)
    monkeypatch.setattr(driver, "AMENDMENT_MANIFEST_PATH", manifest_path)
    audit = {"completed_v1_workers": ["fixture"] * 4, "request_count": 44, "reuse": False}
    semantic = {"numeric_functions_ast_exact": True, "only_expected_period_container_json_roundtrip": True}
    monkeypatch.setattr(driver, "read_preregistration", lambda *args: {"preservation_before": preservation})
    monkeypatch.setattr(driver, "failure_audit_snapshot", lambda: audit)
    monkeypatch.setattr(driver, "verify_semantic_copy", lambda: semantic)
    audit_sha = hashlib.sha256(json.dumps(
        audit, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()
    amended = {
        "schema": "db-shared-win-four-technical-amendment-v2",
        "registered_at": "2026-10-12T08:00:00+00:00", "post_outcome_technical_amendment": True,
        "new_preregistration_claim": False, "human_authorized": True,
        "only_expected_json_period_container_normalized": True,
        "new_v2_fits_before_amendment": 0, "all44_fits_new": True, "failed_run_artifacts_reused": False,
        "source_hashes": pins, "original_source_hashes": driver.original_source_pins(pins),
        "v1_protocol_sha256": driver.sha(driver.CONFIG), "v1_preregistration_sha256": driver.sha(lock),
        "v1_driver_sha256": driver.sha(driver.V1_DRIVER), "v2_driver_sha256": driver.sha(driver.V2_DRIVER),
        "failed_v1_external_audit_path": str(driver.FAILED_V1_AUDIT_PATH.resolve()),
        "failed_v1_external_audit_sha256": driver.FAILED_V1_AUDIT_SHA256,
        "failed_run_audit": audit, "failed_run_audit_sha256": audit_sha,
        "semantic_copy_proof": semantic, "phase1_proof_sha256": driver.sha(proof_path),
        "preservation_before": preservation,
    }
    driver.save(amendment_path, amended)
    driver.save(manifest_path, driver.amendment_manifest(amended))
    return protocol, pins, proof, preservation, amended


def test_actual_amendment_reader_accepts_sealed_unchanged_evidence(sealed_amendment):
    protocol, pins, proof, preservation, amended = sealed_amendment
    assert driver.read_amendment(protocol, pins, proof, preservation) == amended


@pytest.mark.parametrize("field", [
    "schema", "post_outcome_technical_amendment", "new_preregistration_claim", "human_authorized",
    "only_expected_json_period_container_normalized", "new_v2_fits_before_amendment",
    "all44_fits_new", "failed_run_artifacts_reused", "source_hashes", "original_source_hashes",
    "v1_protocol_sha256", "v1_preregistration_sha256", "v1_driver_sha256", "v2_driver_sha256",
    "failed_v1_external_audit_path", "failed_v1_external_audit_sha256", "failed_run_audit",
    "failed_run_audit_sha256", "semantic_copy_proof", "phase1_proof_sha256", "preservation_before",
    "registered_at",
])
def test_actual_amendment_reader_rejects_semantic_drift_even_with_matching_new_manifest(
    sealed_amendment, tmp_path, monkeypatch, field,
):
    protocol, pins, proof, preservation, amended = sealed_amendment
    changed = copy.deepcopy(amended)
    if isinstance(changed[field], bool):
        changed[field] = not changed[field]
    elif field == "new_v2_fits_before_amendment":
        changed[field] = 1
    elif field == "registered_at":
        changed[field] = "2026-10-12T06:44:00+00:00"
    else:
        changed[field] = "wrong"
    path = tmp_path / "changed-amendment.json"
    manifest = tmp_path / "changed-amendment-manifest.json"
    monkeypatch.setattr(driver, "AMENDMENT_PATH", path)
    monkeypatch.setattr(driver, "AMENDMENT_MANIFEST_PATH", manifest)
    driver.save(path, changed)
    # This is not merely a content-hash mismatch test: even a freshly matching
    # manifest cannot relax bound source, Phase1, failed audit or provenance.
    driver.save(manifest, driver.amendment_manifest(changed))
    with pytest.raises(ValueError, match="amendment-or-protected-state-drift"):
        driver.read_amendment(protocol, pins, proof, preservation)


def test_actual_amendment_reader_rejects_manifest_only_drift(sealed_amendment, tmp_path, monkeypatch):
    protocol, pins, proof, preservation, amended = sealed_amendment
    changed = driver.amendment_manifest(amended)
    changed["amendment_sha256"] = "f" * 64
    path = tmp_path / "changed-manifest-only.json"
    driver.save(path, changed)
    monkeypatch.setattr(driver, "AMENDMENT_MANIFEST_PATH", path)
    with pytest.raises(ValueError, match="amendment-or-protected-state-drift"):
        driver.read_amendment(protocol, pins, proof, preservation)


def test_actual_amendment_reader_rejects_old_preregistration_bytes_drift(sealed_amendment, tmp_path, monkeypatch):
    protocol, pins, proof, preservation, amended = sealed_amendment
    path = tmp_path / "old-preregistration-changed.json"
    driver.save(path, {"fixture": "changed original preregistration bytes"})
    monkeypatch.setattr(driver, "LOCK_PATH", path)
    with pytest.raises(ValueError, match="amendment-or-protected-state-drift"):
        driver.read_amendment(protocol, pins, proof, preservation)


@pytest.mark.parametrize("missing", ["amendment", "manifest"])
def test_actual_amendment_missing_evidence_is_rejected_before_fit(sealed_amendment, tmp_path, monkeypatch, missing):
    protocol, pins, proof, preservation, amended = sealed_amendment
    monkeypatch.setattr(driver, "AMENDMENT_PATH" if missing == "amendment" else "AMENDMENT_MANIFEST_PATH",
                        tmp_path / "missing.json")
    with pytest.raises(ValueError, match="required-before-fit"):
        driver.read_amendment(protocol, pins, proof, preservation)
