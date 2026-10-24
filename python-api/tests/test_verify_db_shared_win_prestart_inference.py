"""Read-only sealed capture/model bridge tests; never fetch or read a DB."""
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "python-api"), str(ROOT / "keiba")]
bridge = importlib.import_module("verify_db_shared_win_prestart_inference")


def save(path, value):
    path.write_bytes(bridge.prestart._body(value))


@pytest.fixture
def capture(tmp_path, monkeypatch):
    boundary = tmp_path / "reports/generated"
    directory = boundary / "capture"
    directory.joinpath("sources").mkdir(parents=True)
    output_dir = boundary / "new-proof"
    output_dir.mkdir()
    monkeypatch.setattr(bridge, "OUTPUT_ROOT", boundary)
    current = pd.DataFrame([{
        "race_id": "202605040511", "race_date": "20261012", "horse_id": "h" + str(horse),
        "horse_number": horse, "distance": 1600, "surface": "turf", "venue": "東京", "num_horses": 3,
        "bracket_number": horse, "age": 3, "sex": "牡", "burden_weight": 56., "horse_weight": 480. + horse,
        "horse_weight_change": 2., "field_condition": "良", "weather": "晴", "race_class": "1勝",
        "jockey_id": "j1", "trainer_id": "t1", "sire": "s1", "damsire": "ds1",
        "finish": np.nan, "time_seconds": np.nan, "last_3f_time": np.nan, "odds": np.nan, "popularity": np.nan,
    } for horse in range(1, 4)]).reindex(columns=bridge.features.canonical.SOURCE_COLUMNS)
    history = current.iloc[:0].copy()
    full, receipt = bridge.features.build_current_features(current, history)
    raw47 = full.loc[:, list(bridge.features.BASE46_COLUMNS)].copy()
    raw47["safe_past5_top3_rate"] = np.nan
    raw47 = raw47.loc[:, list(bridge.features.RAW47_COLUMNS)]
    full, receipt = bridge.features.build_current_features(current, history, raw47=raw47)
    quoted = full.copy()
    quoted["odds"] = [4., 5., 6.]
    quoted["expected_runner_count"] = 3
    quoted = bridge.features.add_market_features(quoted)
    columns = bridge.worker.feature_contract("market46_extended")[0]
    values = bridge.prestart._matrix_values(quoted.loc[:, columns])
    source = {"target_race_id": "202605040511", "history_scope": "ALL complete eligible JRA flat rows since 2013; strictly prior target day",
              "current": bridge.prestart._records(current), "canonical_columns": list(current),
              "history_object": "sources/history.parquet"}
    history.to_parquet(directory / "sources/history.parquet", index=False)
    source["history_object_sha256"] = bridge.worker.file_hash(directory / "sources/history.parquet")
    save(directory / "sources/canonical_inputs.json", source)
    save(directory / "sources/odds.json", {"fixture": "actual quotes marker, not network"})
    odds_sha = bridge.worker.file_hash(directory / "sources/odds.json")
    bundle = {"schema": bridge.prestart.SCHEMA, "mode": "sealed_replay", "race_id": "202605040511",
        "captured_at": "2026-10-12T03:38:50Z", "post_time": "2026-10-12T06:40:00Z",
        "capture_validation": {"point_in_time_verified": False, "reason_codes": ["replay_is_not_live_capture", "outside_fixed_window"]},
        "fixed_window": {"opens_at": "2026-10-12T06:34:00Z", "deadline": "2026-10-12T06:36:00Z"},
        "card": {"horses": [{"horse_id": "h" + str(horse), "horse_number": horse} for horse in range(1, 4)]},
        "quotes": {str(horse): 3. + horse for horse in range(1, 4)}, "odds_status": "middle",
        "source_receipts": [{"kind": "odds", "body_file": "sources/odds.json", "body_sha256": odds_sha,
                             "body_size": (directory / "sources/odds.json").stat().st_size}],
        "research_only": True, "eligible_for_ev": False, "purchase_approved": False,
        "model_activated": False, "forward_modified": False,
        "features_extended": {"schema": "db-shared-win-direct-captured-input-v1", "feature_count": 92,
            "columns": columns, "values": values, "identities": full.loc[:, list(bridge.features.IDENTITY_COLUMNS)].to_dict("records"),
            "input_sha256": bridge.prestart.sha256(bridge.prestart._body({"columns": columns, "values": values})),
            "formula_receipt": receipt, "transform_file_sha256": bridge.worker.file_hash(bridge.features.__file__),
            "source_snapshot_sha256": bridge.worker.file_hash(directory / "sources/canonical_inputs.json"),
            "market_source_body_sha256": odds_sha, "eligible_for_ev": False},
        "extension_contract": {"canonical_source_file": "sources/canonical_inputs.json",
            "canonical_source_snapshot_sha256": bridge.worker.file_hash(directory / "sources/canonical_inputs.json")}}
    save(directory / "bundle.json", bundle)
    files = {path.relative_to(directory).as_posix(): {"sha256": bridge.worker.file_hash(path), "bytes": path.stat().st_size}
             for path in directory.rglob("*") if path.is_file()}
    save(directory / "artifact_manifest.json", {"schema": bridge.prestart.SCHEMA + "-manifest",
         "files": files, "capture_sha256": files["bundle.json"]["sha256"]})
    return directory, bundle, output_dir


def test_read_bundle_full_hash_readback_and_outcome_free_frame(capture):
    directory, bundle, _ = capture
    assert bridge.prestart.read_bundle(directory) == bundle
    frame, receipt = bridge.frame_from_capture(bundle, directory)
    assert len(frame) == 3
    assert set(bridge.features.FEATURE_COLUMNS) <= set(frame)
    assert not {"finish", "tansho_payout", "time_seconds", "last_3f_time"} & set(frame)
    assert receipt["current_outcomes_consumed"] is False
    assert receipt["actual_quote_key_join_verified"] is True
    np.testing.assert_array_equal(frame.odds, [4., 5., 6.])


@pytest.mark.parametrize("change", ["matrix", "feature-order", "ev", "cal-proof", "history-policy", "horse-id", "quote-key", "quote-status", "source-hash"])
def test_capsule_matrix_identity_formula_and_safety_binding_fail_closed(capture, change):
    directory, bundle, _ = capture
    changed = deepcopy(bundle)
    if change == "matrix":
        changed["features_extended"]["values"][0][0] = .99
    elif change == "feature-order":
        changed["features_extended"]["columns"].reverse()
    elif change == "ev":
        changed["eligible_for_ev"] = True
    elif change == "cal-proof":
        changed["features_extended"]["formula_receipt"]["base46_parity"]["verified"] = False
    elif change == "history-policy":
        changed["features_extended"]["formula_receipt"]["history_contract"]["current_horse_only_history_allowed"] = True
    elif change == "horse-id":
        changed["features_extended"]["identities"][0]["horse_id"] = "not-this-horse"
    elif change == "quote-key":
        changed["quotes"].pop("3")
    elif change == "quote-status":
        changed["odds_status"] = "result"
    else:
        changed["features_extended"]["transform_file_sha256"] = "d" * 64
    with pytest.raises(ValueError):
        bridge.frame_from_capture(changed, directory)


def test_poison_canonical_current_result_fields_are_not_inference_inputs(capture):
    directory, bundle, _ = capture
    source_path = directory / "sources/canonical_inputs.json"
    before, _ = bridge.frame_from_capture(bundle, directory)
    source = json.loads(source_path.read_bytes())
    for row in source["current"]:
        row["finish"], row["time_seconds"], row["last_3f_time"] = "future-result", "must-not-read", "must-not-read"
    save(source_path, source)
    changed = deepcopy(bundle)
    changed["features_extended"]["source_snapshot_sha256"] = bridge.worker.file_hash(source_path)
    after, _ = bridge.frame_from_capture(changed, directory)
    pd.testing.assert_frame_equal(before, after)


def test_current_formula_and_quotes_match_worker_market_contract(capture):
    directory, bundle, _ = capture
    frame, _ = bridge.frame_from_capture(bundle, directory)
    np.testing.assert_allclose(bridge.worker.market_values(frame), frame.normalized_market_probability)
    assert frame.safe_venue.eq(frame.venue).all()
    assert frame.safe_surface.eq(frame.surface).all()


@pytest.fixture
def study(capture):
    _, _, output = capture
    directory = output.parent / "study"
    directory.mkdir()
    sources = {name: bridge.worker.file_hash(ROOT / name) for name in bridge.worker.REQUIRED_SOURCES}
    protocol = {"source_hashes": sources, "protocol": {"fixture": True}}
    protocol_sha = bridge.worker.canonical_hash(protocol)
    save(directory / "protocol.json", protocol)
    save(directory / "protocol-hash.json", {"sha256": protocol_sha})
    (directory / "common-input.parquet").write_bytes(b"trusted fixture, not read as data")
    frame_sha = bridge.worker.file_hash(directory / "common-input.parquet")
    models, workers = {}, []
    for candidate in bridge.worker.CANDIDATES:
        path = directory / (candidate + ".research.joblib")
        path.write_bytes(b"fixture model unpickling is stubbed in run tests")
        receipt = {"schema": bridge.worker.SCHEMA, "status": "completed", "candidate_id": candidate,
            "protocol_sha256": protocol_sha, "source_hashes": sources, "frame_sha256": frame_sha,
            "artifacts": {"model": {"path": str(path), "schema": bridge.worker.MODEL_SCHEMA, "sha256": bridge.worker.file_hash(path)}}}
        workers.append(receipt)
        models[candidate] = {"feature_columns": bridge.worker.feature_contract(candidate)[0],
                            "calibration_audit": {"fixture": True}, "temperature_calibration_audit": {"fixture": True}}
    result = {"schema": bridge.RESULT_SCHEMA, "status": "completed", "protocol_sha256": protocol_sha,
        "models_research_only": True, "models_published": False, "models_activated": False,
        "forward_changed": False, "actual_bets": 0, "worker_results": workers}
    save(directory / "results.json", result)
    files = {path.name: {"sha256": bridge.worker.file_hash(path), "bytes": path.stat().st_size}
             for path in directory.iterdir() if path.is_file()}
    save(directory / "artifact-manifest.json", {"schema": bridge.MANIFEST_SCHEMA, "protocol_sha256": protocol_sha, "files": files})
    return directory, models


def test_study_manifest_verifies_all_objects_before_loading(study):
    directory, _ = study
    receipt = bridge.verify_manifest(directory)
    assert set(receipt["models"]) == set(bridge.worker.CANDIDATES)
    (directory / "common-input.parquet").write_bytes(b"tampered dataset")
    with pytest.raises(ValueError, match="hash-mismatch"):
        bridge.verify_manifest(directory)


@pytest.mark.parametrize("kind", ["unsealed", "traversal", "not-completed", "activated", "protocol"])
def test_study_gate_rejects_unsealed_or_production_authority(study, kind):
    directory, _ = study
    manifest_path = directory / "artifact-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    if kind == "unsealed":
        manifest["files"].pop("common-input.parquet")
    elif kind == "traversal":
        manifest["files"]["../outside"] = {"sha256": "a" * 64, "bytes": 1}
    elif kind in ("not-completed", "activated"):
        result = json.loads((directory / "results.json").read_bytes())
        result["status" if kind == "not-completed" else "models_activated"] = "running" if kind == "not-completed" else True
        save(directory / "results.json", result)
        manifest["files"]["results.json"] = {"sha256": bridge.worker.file_hash(directory / "results.json"),
                                              "bytes": (directory / "results.json").stat().st_size}
    else:
        manifest["protocol_sha256"] = "e" * 64
    save(manifest_path, manifest)
    with pytest.raises(ValueError):
        bridge.verify_manifest(directory)


def test_source_directories_and_existing_outputs_cannot_be_overwritten(capture, study):
    capture_dir, _, output_dir = capture
    study_dir, _ = study
    for path in (capture_dir / "new.json", study_dir / "new.json", output_dir.parent / "new.json"):
        with pytest.raises(ValueError, match="separated"):
            bridge.validate_paths(study_dir, capture_dir, path)
    destination = output_dir / "output.json"
    destination.touch()
    with pytest.raises(ValueError, match="separated"):
        bridge.validate_paths(study_dir, capture_dir, destination)


def fake_components(bundle, frame):
    market = frame.normalized_market_probability.to_numpy(float)
    return {"probability": market, "p_original": market, "market_probability": market,
            "temperature_probability": market, "residual_score": np.zeros(len(frame)),
            "direct_logit": np.log(market) - np.log1p(-market)}


def test_full_reference_replay_never_upgrades_window_or_ev_no_extra_source_access(capture, study, monkeypatch):
    capture_dir, bundle, output_dir = capture
    study_dir, models = study
    monkeypatch.setattr(bridge, "load_models", lambda _receipt: deepcopy(models))
    monkeypatch.setattr(bridge.worker, "predict_components", fake_components)
    before_capture = bridge.worker.file_hash(capture_dir / "artifact_manifest.json")
    before_study = bridge.worker.file_hash(study_dir / "artifact-manifest.json")
    destination = output_dir / "new.json"
    result = bridge.run(study_dir, capture_dir, destination)
    assert result["status"] == "completed_reference_only"
    assert result["capture_validation"] == bundle["capture_validation"]
    assert result["capture_validation"]["point_in_time_verified"] is False
    assert result["source_claims_upgraded"] is False
    assert result["model_inference_before_post_verified"] is False
    assert result["reference_only"] is True
    assert result["eligible_for_ev"] is False
    assert result["purchase_approved"] is False
    assert result["additional_http_requests"] == result["additional_database_reads"] == 0
    assert result["serialized_model_replay_equal"] is True
    assert result["actual_purchases"] == 0 and not result["stakes_assigned"]
    assert result["runner_count"] == 3
    assert all(len(value) == 3 for value in result["predictions"].values())
    assert bridge.worker.file_hash(capture_dir / "artifact_manifest.json") == before_capture
    assert bridge.worker.file_hash(study_dir / "artifact-manifest.json") == before_study
    loaded = json.loads(destination.read_bytes())
    assert bridge.worker.canonical_hash(loaded["predictions"]) == loaded["prediction_sha256"]


def test_serialized_model_second_replay_difference_fails_without_output(capture, study, monkeypatch):
    capture_dir, _, output_dir = capture
    study_dir, models = study
    monkeypatch.setattr(bridge, "load_models", lambda _receipt: deepcopy(models))
    calls = []
    def unstable(bundle, frame):
        output = fake_components(bundle, frame)
        calls.append(1)
        if len(calls) > 3:
            output["residual_score"] = np.full(len(frame), .01)
        return output
    monkeypatch.setattr(bridge.worker, "predict_components", unstable)
    destination = output_dir / "rejected.json"
    with pytest.raises(ValueError, match="replay-mismatch"):
        bridge.run(study_dir, capture_dir, destination)
    assert not destination.exists()
