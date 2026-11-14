"""Reference-only paired inference: no network, training or live-model writes."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import sqlite3
from types import FunctionType

import numpy as np
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "reports" / "generated" / "model-experiments" / "pace-agent-20261114-v3"


def service():
    assert importlib.util.find_spec("services.pace74_shadow"), "sealed shadow service is missing"
    return importlib.import_module("services.pace74_shadow")


def raw_observation():
    return {
        "status": "compared", "primary_model_id": "primary",
        "primary_artifact_sha256": "a" * 64, "candidate_artifact_sha256": "b" * 64,
        "captured_at": "2026-11-14T01:00:00+00:00",
        "input_observed_at": "2026-11-14T01:00:00+00:00",
        "inference_completed_at": "2026-11-14T01:00:01+00:00",
        "input_revision": "source-1", "input_aligned": True,
        "race_id": "202605010101", "card_fingerprint": "c" * 64,
        "expected_count": 2, "known_outcomes": False,
        "issue_codes": [], "publication_id": "d" * 64,
        "predictions": [
            {"horse_number": 1, "horse_id": "h1", "candidate_probability": .6,
             "candidate_rank": 1, "candidate_raw": .2},
            {"horse_number": 2, "horse_id": "h2", "candidate_probability": .4,
             "candidate_rank": 2, "candidate_raw": -.2},
        ],
    }


def primary():
    return [
        {"horse_number": 2, "horse_id": "h2", "horse_name": "second",
         "win_probability": .65, "p_norm": .65, "p_raw": 8., "predicted_rank": 1, "odds": 3.},
        {"horse_number": 1, "horse_id": "h1", "horse_name": "first",
         "win_probability": .35, "p_norm": .35, "p_raw": 4., "predicted_rank": 2, "odds": 5.},
    ]


def finalize(tmp_path, raw=None, predictions=None, **changes):
    kwargs = dict(
        raw=raw_observation() if raw is None else raw,
        predictions=primary() if predictions is None else predictions,
        race_info={"race_id": "202605010101", "date": "20261114", "num_horses": 2, "post_time": "15:00"},
        source_records=[], context={}, now=datetime(2026, 11, 14, 1, 0, 2, tzinfo=timezone.utc),
        store_path=tmp_path / "shadow.sqlite3",
    )
    kwargs.update(changes)
    return service().finalize_comparison(**kwargs)


def test_final_prices_pair_by_identity_without_mutating_primary(tmp_path):
    predictions = primary()
    before = deepcopy(predictions)
    result = finalize(tmp_path, predictions=predictions)
    assert predictions == before
    assert result["status"] == "compared"
    assert result["reference_only"] and not result["purchase_eligible"]
    assert result["input_aligned"] and result["recorded"]
    assert result["predictions"][0] == {
        "horse_number": 2, "horse_name": "second", "primary_probability": .65,
        "primary_rank": 1, "candidate_probability": .4, "candidate_rank": 2,
        "odds": 3., "expected_value": pytest.approx(1.2),
    }
    assert result["observation_kind"] == "unverified"
    assert not result["odds_verified"]
    assert result["promotion"]["eligible"] is False
    assert "prospective_evidence_missing" in result["promotion"]["reasons"]


@pytest.mark.parametrize("price", [0., float("nan"), float("inf"), 1., None, -3.])
def test_missing_or_invalid_final_price_has_no_ev(tmp_path, price):
    predictions = primary()
    predictions[0]["odds"] = price
    row = finalize(tmp_path, predictions=predictions)["predictions"][0]
    assert row["odds"] is None and row["expected_value"] is None


def test_comparison_uses_final_displayed_normalized_primary_probability(tmp_path):
    predictions = primary()
    predictions[0]["win_probability"] = .9
    result = finalize(tmp_path, predictions=predictions)
    assert result["predictions"][0]["primary_probability"] == .65


@pytest.mark.parametrize("change", ["duplicate", "changed_id", "missing_runner", "unknown_count", "race_id", "bad_probability"])
def test_unpaired_cohorts_never_enter_evidence(tmp_path, change):
    raw, predictions = raw_observation(), primary()
    if change == "duplicate":
        predictions[1] = predictions[0].copy()
    elif change == "changed_id":
        predictions[0]["horse_id"] = "replacement"
    elif change == "missing_runner":
        predictions.pop()
    elif change == "unknown_count":
        raw["expected_count"] = None
    elif change == "race_id":
        raw["race_id"] = "202605010102"
    else:
        predictions[0]["win_probability"] = float("nan")
        predictions[0]["p_norm"] = float("nan")
    result = finalize(tmp_path, raw, predictions)
    assert result["status"] == "skipped"
    assert not result["recorded"] and not result["input_aligned"]
    assert result["predictions"] == []


def test_append_only_idempotent_capture_and_distinct_replay(tmp_path):
    first = finalize(tmp_path)
    again = finalize(tmp_path)
    assert again["comparison_id"] == first["comparison_id"] and again["recorded"]
    replay = raw_observation()
    replay["captured_at"] = "2026-11-14T01:01:00+00:00"
    replay["input_observed_at"] = replay["captured_at"]
    replay["inference_completed_at"] = "2026-11-14T01:01:01+00:00"
    finalize(tmp_path, replay, now=datetime(2026, 11, 14, 1, 1, 2, tzinfo=timezone.utc))
    with sqlite3.connect(tmp_path / "shadow.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM paired_observations").fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("DELETE FROM paired_observations")
    status = service().evidence_status(tmp_path / "shadow.sqlite3")
    assert status["counts"]["unverified_observations"] == 2
    assert status["counts"]["pre_race_unique_races"] == 0
    assert status["eligible"] is False


def test_store_failure_is_public_sanitized_and_nonfatal(tmp_path):
    directory = tmp_path / "not-a-db"
    directory.mkdir()
    result = finalize(tmp_path, store_path=directory)
    assert result["status"] == "compared"
    assert not result["recorded"] and "evidence_write_failed" in result["issue_codes"]
    assert len(result["predictions"]) == 2
    assert str(tmp_path) not in json.dumps(result)


def test_evidence_query_failure_does_not_claim_recording_or_break_primary(tmp_path, monkeypatch):
    s = service()
    connect = sqlite3.connect

    def deny_readonly_queries(path, *args, **kwargs):
        connection = connect(path, *args, **kwargs)
        if "?mode=ro" in str(path):
            connection.set_authorizer(
                lambda operation, *details: sqlite3.SQLITE_DENY if operation == sqlite3.SQLITE_SELECT else sqlite3.SQLITE_OK
            )
        return connection

    monkeypatch.setattr(sqlite3, "connect", deny_readonly_queries)
    result = finalize(tmp_path)
    assert result["status"] == "compared" and len(result["predictions"]) == 2
    assert not result["recorded"]
    assert "evidence_query_failed" in result["issue_codes"]
    assert result["promotion"]["eligible"] is False


def test_historical_odds_do_not_become_pre_race_by_refresh_timestamp(tmp_path):
    result = finalize(
        tmp_path, race_info={"race_id": "202605010101", "date": "20261113", "num_horses": 2, "post_time": "15:00"},
        context={"sources": [{"horse_number": 1, "observed_at": "2026-11-14T01:00:00Z"}]},
    )
    assert result["observation_kind"] == "historical"
    assert not result["odds_verified"]


def odds_context(prices=None):
    evidence = {
        "schema": "netkeiba-win-odds-capture-v1", "race_id": "202605010101",
        "http_status": 200, "odds_status": "middle", "response_sha256": "e" * 64,
        "source_url": "https://race.netkeiba.com/api/api_get_jra_odds.html?race_id=202605010101&type=1",
        "observed_at": "2026-11-14T01:00:00Z",
        "odds_by_horse_number": {"1": 5., "2": 3.} if prices is None else prices,
    }
    return {"sources": [{"horse_number": n, "has_result": False, "odds_evidence": evidence,
                         "observed_at": evidence["observed_at"]} for n in (1, 2)]}


def test_actual_final_overlay_proof_has_timestamp_but_not_fabricated_card_provenance(tmp_path):
    result = finalize(tmp_path, context=odds_context())
    assert result["odds_verified"]
    assert result["odds_observed_at"] == "2026-11-14T01:00:00+00:00"
    assert result["observation_kind"] == "unverified"
    assert result["promotion"]["counts"]["pre_race_unique_races"] == 0
    with sqlite3.connect(tmp_path / "shadow.sqlite3") as db:
        payload = json.loads(db.execute("SELECT payload FROM paired_observations").fetchone()[0])
    assert payload["odds_evidence"][0]["response_sha256"] == "e" * 64


def test_overlay_different_from_verified_quote_is_not_odds_verified(tmp_path):
    predictions = primary()
    predictions[0]["odds"] = 4.
    result = finalize(tmp_path, predictions=predictions, context=odds_context())
    assert not result["odds_verified"]
    assert result["predictions"][0]["expected_value"] == pytest.approx(1.6)


@pytest.mark.parametrize("change", ["duplicate", "replacement", "incomplete"])
def test_post_compute_source_cohort_change_refuses_pairing(tmp_path, change):
    records = [{"horse_number": 1, "horse_id": "h1"}, {"horse_number": 2, "horse_id": "h2"}]
    if change == "duplicate":
        records[1] = records[0].copy()
    elif change == "replacement":
        records[1]["horse_id"] = "replacement"
    else:
        records.pop()
    result = finalize(tmp_path, source_records=records)
    assert result["status"] == "skipped" and not result["recorded"]
    assert result["predictions"] == []


def test_disabled_and_absent_packages_are_harmless(tmp_path, monkeypatch):
    s = service()
    monkeypatch.setattr(s, "PACKAGE_DIR", tmp_path / "missing")
    args = dict(db_path=tmp_path / "missing.sqlite3", race_info={}, horses=pd.DataFrame(),
                primary_model_id="primary", input_revision="r", captured_at="2026-11-14T01:00:00Z",
                primary_artifact_sha256="a" * 64)
    monkeypatch.setenv("PACE74_SHADOW_ENABLED", "false")
    assert s.run_shadow(**args)["status"] == "skipped"
    monkeypatch.delenv("PACE74_SHADOW_ENABLED")
    result = s.run_shadow(**args)
    assert result["status"] == "unavailable"
    assert "package_missing" in result["issue_codes"]


def test_reference_candidate_activation_is_blocked_even_local_opt_in(monkeypatch):
    service()
    from fastapi import HTTPException
    from routers import models_mgmt
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MODEL_ACTIVATION_LOCAL_ENABLED", "true")
    with pytest.raises(HTTPException) as error:
        asyncio.run(models_mgmt.activate_model("pace74-c09-minleaf240", {}))
    assert error.value.status_code == 409


@pytest.mark.parametrize("alias", ["pace74-shadow/research", "pace74-shadow\\research",
                                   ".\\pace74-shadow\\research", "PACE74-SHADOW\\research"])
def test_nested_shadow_path_cannot_activate_before_pointer_write(tmp_path, monkeypatch, alias):
    s = service()
    from fastapi import HTTPException
    from routers import models_mgmt
    models = tmp_path / "models"
    package = models / "pace74-shadow"
    package.mkdir(parents=True)
    (package / "research.joblib").write_bytes(b"not even a valid model")
    pointer = []
    monkeypatch.setattr(models_mgmt, "MODELS_DIR", models)
    monkeypatch.setattr(s, "PACKAGE_DIR", package)
    monkeypatch.setattr(models_mgmt, "set_active_model_id", pointer.append)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MODEL_ACTIVATION_LOCAL_ENABLED", "true")
    with pytest.raises(HTTPException) as error:
        asyncio.run(models_mgmt.activate_model(alias, {}))
    assert error.value.status_code == 409 and pointer == []


@pytest.mark.parametrize("repack", [False, True])
def test_renamed_shadow_bundle_never_acquires_local_activation_authority(sealed_package, tmp_path, monkeypatch, repack):
    import joblib
    from fastapi import HTTPException
    from routers import models_mgmt
    s = service()
    models = tmp_path / "models"
    models.mkdir()
    copied = models / "renamed_candidate.joblib"
    if repack:
        bundle, _ = s.load_package()
        joblib.dump(bundle, copied, compress=1)
    else:
        copied.write_bytes((sealed_package / "research.joblib").read_bytes())
    pointer = []
    monkeypatch.setattr(models_mgmt, "MODELS_DIR", models)
    monkeypatch.setattr(models_mgmt, "set_active_model_id", pointer.append)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MODEL_ACTIVATION_LOCAL_ENABLED", "true")
    with pytest.raises(HTTPException) as error:
        asyncio.run(models_mgmt.activate_model(copied.stem, {}))
    assert error.value.status_code == 409 and pointer == []


def test_response_type_keeps_comparison_field(tmp_path):
    service()
    from models import AnalyzeRaceResponse
    response = AnalyzeRaceResponse(
        success=True, race_info={}, pro_evaluation={}, predictions=[], bet_types={},
        best_bet_type="", best_bet_info={}, race_level="skip", recommendation={},
        model_comparison=finalize(tmp_path),
    )
    assert response.model_dump()["model_comparison"]["purchase_eligible"] is False


@pytest.fixture
def sealed_package(tmp_path, monkeypatch):
    s = service()
    if not (STUDY / "selection.json").exists():
        pytest.skip("approved immutable local research fixture unavailable")
    package = tmp_path / "pace74-shadow"
    monkeypatch.setattr(s, "PACKAGE_DIR", package)
    # Publication is tested against the real, fixed approved source; never live models.
    monkeypatch.setattr(s, "_verify_reference_receipt_files", lambda receipt: None)
    s.publish_package(destination=package)
    return package


def test_selected_bundle_parity_and_sealed_package_portability(sealed_package):
    import pyarrow.parquet as pq
    from experiments.pace_research_loop import predict_bundle
    s = service()
    bundle, manifest = s.load_package()
    frame = next(pq.ParquetFile(STUDY / "dataset" / "confirmation.parquet").iter_batches(batch_size=64)).to_pandas()
    frame = frame.loc[frame.race_id.eq(frame.race_id.iloc[0])].reset_index(drop=True)
    features = frame.filter(like="feature__").rename(columns=lambda name: name.removeprefix("feature__"))
    expected_raw, expected_probability = predict_bundle(bundle, frame, features)
    raw, probability = s.score_candidate(bundle, frame, features)
    np.testing.assert_array_equal(raw, expected_raw)
    np.testing.assert_array_equal(probability, expected_probability)
    assert manifest["candidate_model_id"] == "pace74-c09-minleaf240"
    assert manifest["feature_count"] == 74
    assert not any("snapshot.sqlite3" in p.name for p in sealed_package.rglob("*"))


def test_same_size_mtime_replacement_rejected_before_cache_reuse(sealed_package):
    s = service()
    s.load_package()
    artifact = sealed_package / "research.joblib"
    stat = artifact.stat()
    contents = bytearray(artifact.read_bytes())
    contents[-1] ^= 1
    artifact.write_bytes(contents)
    os.utime(artifact, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(ValueError, match="artifact_hash_mismatch"):
        s.load_package()


@pytest.mark.parametrize("tamper", ["temperature", "columns", "scope"])
def test_unexpected_bundle_contract_refused(sealed_package, tamper):
    s = service()
    bundle, _ = s.load_package()
    bundle = deepcopy(bundle)
    if tamper == "temperature":
        bundle["fit"]["calibration"]["temperature"] = 1.
    elif tamper == "columns":
        bundle["fit"]["feature_columns"] = list(reversed(bundle["fit"]["feature_columns"]))
    else:
        bundle["specification"]["head"] = "both"
    with pytest.raises(ValueError, match="bundle_contract_mismatch"):
        s.validate_bundle(bundle)


def test_source_pin_change_refused_and_invalidates_primary_cache(sealed_package, tmp_path, monkeypatch):
    s = service()
    from services.prediction_freshness import model_runtime_revision
    model = tmp_path / "primary.joblib"
    model.write_bytes(b"primary")
    before = model_runtime_revision(model)
    monkeypatch.setenv("PACE74_SHADOW_ENABLED", "false")
    assert model_runtime_revision(model) != before
    monkeypatch.delenv("PACE74_SHADOW_ENABLED")
    manifest_path = sealed_package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_pins"]["training.db_context_features"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        s.load_package()
    assert model_runtime_revision(model) != before


def test_actual_feature_source_change_is_refused_before_cached_model_use(sealed_package, tmp_path, monkeypatch):
    s = service()
    s.load_package()
    replacement = tmp_path / "changed_formula.py"
    replacement.write_bytes(b"changed formula")
    original = s._source_path
    monkeypatch.setattr(s, "_source_path", lambda name: replacement if name == "training.db_context_features" else original(name))
    with pytest.raises(ValueError, match="source_pin_mismatch"):
        s.load_package()


def test_publisher_does_not_bless_changed_original_history_cache_source(sealed_package, tmp_path, monkeypatch):
    s = service()
    original = s.file_hash
    cache = ROOT / "python-api" / "training" / "db_context_cache.py"
    monkeypatch.setattr(s, "file_hash", lambda path: "0" * 64 if Path(path) == cache else original(path))
    with pytest.raises(ValueError, match="source_pin_mismatch"):
        s.publish_package(destination=tmp_path / "different-publication")


@pytest.mark.parametrize("flavor,root", [
    (PurePosixPath, "/srv/keiba"),
    (PureWindowsPath, "C:\\keiba"),
])
def test_source_paths_and_runtime_revision_normalize_both_separator_styles(monkeypatch, flavor, root):
    s = service()
    root = flavor(root)
    monkeypatch.setattr(s, "ROOT", root)
    monkeypatch.setattr(s, "API_ROOT", root / "python-api")
    monkeypatch.setattr(s, "PACKAGE_DIR", root / "python-api" / "models" / "pace74-shadow")
    expected = root / "python-api" / "scraping" / "race_class.py"
    assert s._source_path("python-api/scraping/race_class.py") == expected
    assert s._source_path("python-api\\scraping\\race_class.py") == expected
    assert s._source_path("scraping.race_class") == expected
    reads = []
    monkeypatch.setattr(s, "file_hash", lambda path: reads.append(path) or "0" * 64)
    monkeypatch.setattr(s, "_historical_pins", lambda task: {})
    s._source_pins({})
    s.runtime_revision()
    assert expected in reads
    assert root / "python-api" / "training" / "db_context_cache.py" in reads
    assert all("\\" not in part and "/" not in part for path in reads for part in path.parts[1:])


def _compiled_function(body, original):
    code = compile(body, original.__code__.co_filename, "exec", dont_inherit=True)
    function_code = next(value for value in code.co_consts if isinstance(value, type(code)))
    return FunctionType(function_code, original.__globals__, original.__name__)


def test_modified_loaded_parser_fails_candidate_without_affecting_primary(sealed_package, tmp_path, monkeypatch):
    from scraping import race_class
    from experiments import dataset
    from test_db_context_features import _serving_fixture
    from routers import predict
    from services import prediction_compute as compute
    s = service()
    path, info, current = _serving_fixture(tmp_path)
    original = race_class.explicit_race_class
    source_path = Path(race_class.__file__)
    source_hash = s.file_hash(source_path)
    replacement = _compiled_function("def explicit_race_class(*sources):\n    return 'G1'\n", original)
    monkeypatch.setattr(race_class, "explicit_race_class", replacement)
    monkeypatch.setattr(dataset, "explicit_race_class", replacement)
    assert race_class.explicit_race_class("未勝利") == "G1"
    with pytest.raises(ValueError, match="loaded_source_mismatch"):
        s._verify_sources({"python-api/scraping/race_class.py": source_hash})
    model = tmp_path / "primary.joblib"
    model.write_bytes(b"primary fixture")

    class Predictor:
        bundle = {"_local_candidate": True}
        calibrator_applied = False

        def __init__(self, *args, **kwargs):
            pass

        def build_features(self, frame, history):
            return pd.DataFrame({"score": [4., 3., 2., 1.]}, index=frame.index)

        def predict_scores(self, features):
            return np.array([4., 3., 2., 1.]), np.array([.4, .3, .2, .1])

    monkeypatch.setattr(predict, "ModelPredictor", Predictor)
    monkeypatch.setattr(compute, "_load_bundle", lambda path: {})
    result = compute.compute_prediction({
        "frame": current, "model_path": str(model), "db_path": path, "race_info": info,
        "excluded_race_ids": [info["race_id"]], "native_threads": 2, "include_model_comparison": True,
    })
    np.testing.assert_array_equal(result["probabilities"], [.4, .3, .2, .1])
    assert result["model_comparison"]["status"] == "error"
    assert result["model_comparison"]["issue_codes"] == ["loaded_source_mismatch"]
    assert result["model_comparison"]["predictions"] == []
    assert s.file_hash(source_path) == source_hash


@pytest.mark.parametrize("change", ["constant", "class_method", "alias", "detached_globals"])
def test_loaded_constants_classes_aliases_and_detached_globals_cannot_claim_source_identity(monkeypatch, change):
    from experiments import dataset, training
    from training import db_shared_optimized
    s = service()
    if change == "constant":
        module = dataset
        monkeypatch.setattr(module, "JRA_VENUES", {**module.JRA_VENUES, "05": "changed"})
    elif change == "class_method":
        module = training
        replacement = _compiled_function("def transform(self, frame):\n    return frame\n", training.FoldEncoder.transform)
        monkeypatch.setattr(training.FoldEncoder, "transform", replacement)
    elif change == "alias":
        module = db_shared_optimized
        replacement = _compiled_function("def date_partitions(*args, **kwargs):\n    return None\n", module.date_partitions)
        monkeypatch.setattr(module, "date_partitions", replacement)
    else:
        module = dataset
        original = module._race_metadata
        detached = FunctionType(original.__code__, dict(original.__globals__), original.__name__,
                                original.__defaults__, original.__closure__)
        monkeypatch.setattr(module, "_race_metadata", detached)
    pins = {module.__name__: s.file_hash(Path(module.__file__))}
    with pytest.raises(ValueError, match="loaded_source"):
        s._verify_sources(pins)


def test_normal_loaded_helpers_keep_primary_classes_modules_and_cache_state(sealed_package, monkeypatch):
    from collections import OrderedDict
    from experiments import dataset, training
    from training import db_context_features
    import sys
    s = service()
    sentinel = object()
    cache = OrderedDict([("preserved-primary-state", sentinel)])
    monkeypatch.setattr(db_context_features, "_SERVING_STATES", cache)
    before = (sys.modules[dataset.__name__], dataset._race_metadata, training.FoldEncoder,
              training.FoldEncoder.transform, db_context_features._SERVING_LOCK)
    task = json.loads((sealed_package / "task-ledger.json").read_text(encoding="utf-8"))
    s._verify_sources(s._source_pins(task))
    after = (sys.modules[dataset.__name__], dataset._race_metadata, training.FoldEncoder,
             training.FoldEncoder.transform, db_context_features._SERVING_LOCK)
    assert all(left is right for left, right in zip(before, after))
    assert db_context_features._SERVING_STATES is cache
    assert list(cache.items()) == [("preserved-primary-state", sentinel)]


def test_full_reference_receipt_matches_unchanged_original_study():
    s = service()
    if not (STUDY / "reference-receipt.json").is_file():
        pytest.skip("approved local readonly study unavailable")
    receipt = json.loads((STUDY / "reference-receipt.json").read_text(encoding="utf-8"))
    s._verify_reference_receipt_files(receipt)


def test_current_outcome_injection_cannot_change_real_shadow_inference(sealed_package, tmp_path):
    from test_db_context_features import _serving_fixture
    from services.prediction_freshness import data_revision
    s = service()
    path, info, current = _serving_fixture(tmp_path)
    args = dict(db_path=path, race_info=info, primary_model_id="primary",
                input_revision=data_revision(path), captured_at=datetime.now(timezone.utc).isoformat(),
                primary_artifact_sha256="a" * 64)
    clean = current.drop(columns=["finish", "time_seconds", "last_3f_time"], errors="ignore")
    first = s.run_shadow(horses=clean, **args)
    poisoned = clean.assign(finish=999, time_seconds=999, win=1, corner_positions="4-1", odds=9999)
    second = s.run_shadow(horses=poisoned, **args)
    assert first["status"] == second["status"] == "compared"
    assert first["predictions"] == second["predictions"]
    assert first["card_fingerprint"] == second["card_fingerprint"]
    assert not first["known_outcomes"] and second["known_outcomes"]
    assert first["input_revision"] == data_revision(path)


def test_revision_drift_never_yields_paired_candidate(sealed_package, tmp_path, monkeypatch):
    from test_db_context_features import _serving_fixture
    from services import prediction_freshness
    s = service()
    path, info, current = _serving_fixture(tmp_path)
    revisions = iter(["initial", "changed"])
    monkeypatch.setattr(prediction_freshness, "data_revision", lambda path: next(revisions))
    result = s.run_shadow(
        db_path=path, race_info=info, horses=current, primary_model_id="primary", input_revision="initial",
        captured_at=datetime.now(timezone.utc).isoformat(), primary_artifact_sha256="a" * 64,
    )
    assert result["status"] == "skipped"
    assert result["issue_codes"] == ["input_revision_changed"]
    assert result["predictions"] == [] and not result["input_aligned"]


@pytest.mark.parametrize("shadow_fails", [False, True])
def test_cpu_worker_runs_shadow_serially_on_pristine_input_without_changing_primary(tmp_path, monkeypatch, shadow_fails):
    from services import prediction_compute as compute
    from services import prediction_freshness
    from routers import predict
    from keiba_ai import db_ultimate_loader
    import threading
    s = service()
    frame = pd.DataFrame({"horse_number": [1, 2], "score": [4., 7.]})
    model = tmp_path / "primary.joblib"
    model.write_bytes(b"fixture")
    events, threads = [], []

    class Predictor:
        calibrator_applied = False

        def __init__(self, *args, **kwargs):
            pass

        def build_features(self, actual, history):
            actual["finish"] = 999
            return actual[["score"]]

        def predict_scores(self, features):
            events.append("primary")
            threads.append(threading.get_ident())
            return np.array([4., 7.]), np.array([.3, .7])

    def shadow(**kwargs):
        events.append("shadow")
        threads.append(threading.get_ident())
        assert "finish" not in kwargs["horses"]
        assert kwargs["primary_model_id"] == "primary"
        assert kwargs["input_revision"] == "initial"
        if shadow_fails:
            raise RuntimeError("private path and sensitive exception")
        return raw_observation()

    monkeypatch.setattr(predict, "ModelPredictor", Predictor)
    monkeypatch.setattr(compute, "_load_bundle", lambda path: {})
    monkeypatch.setattr(db_ultimate_loader, "load_prediction_history_frame", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(prediction_freshness, "data_revision", lambda path: "initial")
    monkeypatch.setattr(s, "run_shadow", shadow)
    result = compute.compute_prediction({
        "frame": frame, "model_path": str(model), "db_path": "unused", "race_info": {},
        "excluded_race_ids": [], "native_threads": 2, "include_model_comparison": True,
    })
    np.testing.assert_array_equal(result["raw"], [4., 7.])
    np.testing.assert_array_equal(result["probabilities"], [.3, .7])
    assert events == ["primary", "shadow"]
    assert threads[0] == threads[1]
    assert result["model_comparison"]["status"] == ("error" if shadow_fails else "compared")
    assert "private path" not in json.dumps(result["model_comparison"])


def test_analyze_attaches_reference_after_betting_without_changing_primary(saved_card, monkeypatch, tmp_path):
    from services import prediction_compute as compute
    from test_prediction_freshness import run_saved
    s = service()
    monkeypatch.setattr(s, "STORE_PATH", tmp_path / "shadow.sqlite3")
    baseline = run_saved(saved_card, force_recompute=True).model_dump()
    original = compute.run_prediction_compute

    async def with_shadow(**payload):
        assert payload["include_model_comparison"] is True
        result = await original(**payload)
        raw = raw_observation()
        raw["race_id"] = saved_card.race_id
        raw["primary_model_id"] = saved_card.model.stem
        for row, horse in zip(raw["predictions"], saved_card.horses):
            row["horse_id"] = horse["horse_id"]
        result["model_comparison"] = raw
        return result

    monkeypatch.setattr(compute, "run_prediction_compute", with_shadow)
    response = run_saved(saved_card, force_recompute=True).model_dump()
    for data in (baseline, response):
        for key in ("evaluated_at", "valid_until"):
            data["recommendation"].pop(key, None)
    for field in ("predictions", "recommendation", "bet_types", "best_bet_type", "model_id"):
        assert response[field] == baseline[field]
    comparison = response["model_comparison"]
    assert comparison["recorded"] and comparison["status"] == "compared"
    assert comparison["predictions"][0]["odds"] == response["predictions"][0]["odds"]
    assert comparison["primary_model_id"] == saved_card.model.stem
    assert not comparison["purchase_eligible"]


def test_shadow_configuration_change_during_inference_preserves_primary(saved_card, monkeypatch):
    from services import prediction_compute as compute
    from test_prediction_freshness import run_saved
    original = compute.run_prediction_compute

    async def disable_shadow(**payload):
        result = await original(**payload)
        monkeypatch.setenv("PACE74_SHADOW_ENABLED", "false")
        return result

    monkeypatch.setattr(compute, "run_prediction_compute", disable_shadow)
    response = run_saved(saved_card, force_recompute=True)
    assert response.success
    assert [row["win_probability"] for row in response.predictions] == [.6, .4]
    assert response.model_comparison["status"] == "skipped"
    assert response.model_comparison["issue_codes"] == ["shadow_runtime_changed"]


from test_prediction_freshness import saved_card  # noqa: E402,F401
