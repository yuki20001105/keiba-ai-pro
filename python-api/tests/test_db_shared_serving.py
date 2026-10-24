"""New local inputs never grant legacy models or client cards EV authority."""
from datetime import datetime, timedelta, timezone
import asyncio
from copy import deepcopy
import hashlib
import json
import sqlite3

import pandas as pd
import pytest
from fastapi import HTTPException

import app_config
from services import db_shared_input as serving
from training.local_retrain import validate_local_training_parameters, LocalRetrainError
from training.db_shared_contract import bound_pipeline, request_payload, verify_pipeline
from training.db_shared_features import FEATURE_COLUMNS
from types import SimpleNamespace


def parameters(**updates):
    values = dict(force_sync=False, test_size=.2, cv_folds=5, use_sqlite=True,
                  ultimate_mode=True, use_optimizer=True, use_optuna=False,
                  optuna_trials=50, optuna_timeout=300,
                  training_date_from="2024-01", training_date_to="2025-12")
    return dict(values, **updates)


def test_old_parameter_contract_remains_identical():
    old = parameters()
    assert validate_local_training_parameters(old) == old
    assert "feature_pipeline" not in validate_local_training_parameters(old)


def test_client_complete_card_metadata_never_invents_missing_or_mixed_values():
    frame = pd.DataFrame([dict(race_id="202605040301", race_date="20261011", distance=1600,
                              venue="東京", num_horses=2, surface="芝")] * 2)
    assert serving.client_race_metadata(frame)["date"] == "20261011"
    frame.loc[1, "distance"] = 1800
    with pytest.raises(ValueError, match="consistent"):
        serving.client_race_metadata(frame)
    with pytest.raises(ValueError, match="metadata-required"):
        serving.client_race_metadata(frame.drop(columns="race_date"))


def test_new_pipeline_is_bound_by_ordered_features_not_mutable_request():
    contract = SimpleNamespace(selected_features=tuple(FEATURE_COLUMNS), removed_features=(),
        target="speed_deviation", model_type="lightgbm", training_parameters=parameters())
    bundle = SimpleNamespace(contract=contract, training_request_payload=lambda: parameters())
    assert request_payload(bundle)["feature_pipeline"] == serving.VERSION
    assert validate_local_training_parameters(parameters()) == parameters()
    verify_pipeline(contract, serving.VERSION)
    with pytest.raises(LocalRetrainError, match="binding-mismatch"):
        verify_pipeline(contract, "legacy")
    contract.selected_features = tuple(reversed(FEATURE_COLUMNS))
    assert bound_pipeline(contract) == "legacy"
    with pytest.raises(LocalRetrainError):
        verify_pipeline(contract, serving.VERSION)


@pytest.mark.parametrize("change", [{"use_optuna": True}, {"use_optimizer": False}])
def test_new_shared_contract_rejects_incompatible_modes(change):
    contract = SimpleNamespace(selected_features=tuple(FEATURE_COLUMNS), removed_features=(),
        target="speed_deviation", model_type="lightgbm", training_parameters=parameters(**change))
    with pytest.raises(LocalRetrainError, match="shared-contract-invalid"):
        bound_pipeline(contract)


@pytest.mark.parametrize("updates", [dict(feature_pipeline="unknown"),
    dict(feature_pipeline=serving.VERSION),
    dict(feature_pipeline=serving.VERSION, use_optimizer=False, use_optuna=True),
    dict(extra=True), dict(use_optimizer=False)])
def test_parameter_contract_fails_closed(updates):
    with pytest.raises(LocalRetrainError):
        validate_local_training_parameters(parameters(**updates))


@pytest.fixture
def published(tmp_path, monkeypatch):
    models = tmp_path / "models"
    models.mkdir()
    path = models / (serving.PREFIX + "fixture.joblib")
    path.write_bytes(b"AAAA")
    digest = hashlib.sha256(b"AAAA").hexdigest()
    ledger = tmp_path / "jobs.db"
    with sqlite3.connect(ledger) as db:
        db.executescript("CREATE TABLE local_retrain_jobs(job_id,state);"
                         "CREATE TABLE local_retrain_publications(job_id,artifact_sha256,published_path,state);")
        db.execute("INSERT INTO local_retrain_jobs VALUES ('job','completed')")
        db.execute("INSERT INTO local_retrain_publications VALUES ('job',?,?,'published')", (digest, str(path.resolve())))
    monkeypatch.setattr(app_config, "APP_ENV", "test")
    monkeypatch.setattr(app_config, "MODELS_DIR", models)
    return path, ledger, digest


def test_publication_digest_is_checked_even_when_file_identity_unchanged(published):
    import os
    path, ledger, digest = published
    before = path.stat()
    assert serving.validate_published_artifact(path, ledger_path=ledger) == digest
    path.write_bytes(b"BBBB")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(ValueError, match="artifact-hash-mismatch"):
        serving.validate_published_artifact(path, ledger_path=ledger)


def test_unpublished_or_nonlocal_artifact_cannot_serve(published, monkeypatch):
    path, ledger, _ = published
    with sqlite3.connect(ledger) as db:
        db.execute("UPDATE local_retrain_jobs SET state='running'")
    with pytest.raises(ValueError):
        serving.validate_published_artifact(path, ledger_path=ledger)
    monkeypatch.setattr(app_config, "APP_ENV", "production")
    with pytest.raises(ValueError, match="local-only"):
        serving.validate_published_artifact(path, ledger_path=ledger)


def test_probability_report_requires_the_loaded_artifact_hash(published, monkeypatch):
    path, ledger, digest = published
    monkeypatch.setattr(serving, "verify_bundle_sources", lambda _: None)
    monkeypatch.setattr(serving, "validate_published_artifact", lambda _: digest)
    bundle = {"calibrator": object(), "created_at": "2026-10-11T01:00:00+09:00",
        "probability_validation": {"schema": "local-db-probability-validation-v1",
            "status": "validated", "validated": True, "calibrated": True,
            "eligible_for_ev": True, "validation_period_end": "2025-12-31"}}
    assert serving.embedded_probability_report(bundle, path)["validated"] is False
    bundle["_shared_artifact_sha256"] = digest
    verified = serving.embedded_probability_report(bundle, path)
    assert verified["validated"] is True
    assert verified["eligible_bet_types"] == ["単勝"]
    bundle["probability_validation"]["validated"] = False
    assert serving.embedded_probability_report(bundle, path)["validated"] is False


def receipt_args(tmp_path, now=None):
    now = now or datetime(2026, 10, 11, 1, 0, tzinfo=timezone.utc)
    return dict(info={"race_id": "202605040301", "date": "20261011", "post_time": "10:30",
                      "data_observed_at": (now - timedelta(minutes=1)).isoformat()},
                horses=pd.DataFrame([{"horse_number": 1}, {"horse_number": 2}]),
                features=pd.DataFrame({"age": [2., 2.], "previous": [float("nan"), 1.]}),
                diagnostics={"issue_codes": []}, model_path=tmp_path / "model.joblib",
                trusted_context={"trusted_db_card": True, "race_has_result": False},
                receipt_path=tmp_path / "receipts.db", now=now)


@pytest.mark.parametrize("problem", ["client", "result", "post", "stale", "issues"])
def test_no_backdating_or_client_authority(tmp_path, problem):
    args = receipt_args(tmp_path)
    if problem == "client": args["trusted_context"] = {"verified": True}
    if problem == "result": args["trusted_context"]["race_has_result"] = True
    if problem == "post": args["info"]["post_time"] = "09:30"
    if problem == "stale": args["info"]["data_observed_at"] = (args["now"] - timedelta(hours=1)).isoformat()
    if problem == "issues": args["diagnostics"]["issue_codes"] = ["source_mismatch"]
    assert serving.record_pre_race_input(**args)["verified"] is False
    assert not args["receipt_path"].exists()


def test_valid_server_input_is_bound_and_immutable(tmp_path, monkeypatch):
    args = receipt_args(tmp_path)
    monkeypatch.setattr(serving, "validate_published_artifact", lambda _: "a" * 64)
    result = serving.record_pre_race_input(**args)
    assert result["verified"] is True
    with sqlite3.connect(args["receipt_path"]) as db:
        row = db.execute("SELECT receipt_sha256,payload_json FROM input_receipts").fetchone()
        assert row[0] == hashlib.sha256(row[1].encode()).hexdigest()
        assert json.loads(row[1])["values"][0][1] is None
        for action in ("UPDATE input_receipts SET race_id='other'", "DELETE FROM input_receipts"):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                db.execute(action)


def test_legacy_bundle_has_no_embedded_probability_authority(tmp_path, monkeypatch):
    monkeypatch.setattr(serving, "verify_bundle_sources", lambda _: (_ for _ in ()).throw(ValueError("legacy")))
    assert serving.embedded_probability_report({"target": "speed_deviation"}, tmp_path / "legacy.joblib")["validated"] is False


@pytest.fixture
def refresh_card(monkeypatch):
    now = datetime(2026, 10, 11, 1, 0, tzinfo=timezone.utc)
    observed = (now - timedelta(minutes=1)).isoformat()
    race_id = "202605040411"
    info = dict(race_id=race_id, date="20261011", post_time="15:45", distance=1600,
                venue="東京", track_type="芝", num_horses=2, data_observed_at=observed,
                odds_status="middle")
    horses = [dict(race_id=race_id, horse_number=number, horse_id=str(2023000000 + number),
                   horse_name=f"fixture-{number}", distance=1600, odds=2.0 + number,
                   popularity=number, finish_position=None, finish_time=None,
                   data_observed_at=observed, odds_status="middle") for number in (1, 2)]
    verified = []
    monkeypatch.setattr(serving, "verify_bundle_sources", lambda bundle: verified.append(bundle))
    return SimpleNamespace(now=now, info=info, horses=horses, verified=verified,
                           bundle={"feature_pipeline": serving.VERSION, "model": object()})


def needs_refresh(card, **overrides):
    args = dict(bundle=card.bundle, race_id=card.info["race_id"], info=card.info,
                horse_rows=[(json.dumps(row),) for row in card.horses],
                trusted_db_card=True, now=card.now)
    return serving.shared_pre_race_input_needs_refresh(**dict(args, **overrides))


@pytest.mark.parametrize("where", ["metadata", "runner"])
@pytest.mark.parametrize("timestamp", [None, "bad", "2026-10-11T10:00:00", "stale", "future"])
def test_shared_refreshes_missing_invalid_stale_or_future_time(refresh_card, where, timestamp):
    card = refresh_card
    if timestamp == "stale": timestamp = (card.now - timedelta(minutes=5, seconds=1)).isoformat()
    if timestamp == "future": timestamp = (card.now + timedelta(seconds=1)).isoformat()
    target = card.info if where == "metadata" else card.horses[1]
    target["data_observed_at"] = timestamp
    assert needs_refresh(card) is True
    assert card.verified == [card.bundle]
    assert target["data_observed_at"] == timestamp  # Never repair timestamps without fetching.


def test_shared_refresh_repeats_after_next_five_minute_window(refresh_card):
    card = refresh_card
    assert needs_refresh(card) is False
    assert needs_refresh(card, now=card.now + timedelta(minutes=4)) is False  # Exactly five minutes.
    assert needs_refresh(card, now=card.now + timedelta(minutes=4, seconds=1)) is True
    refreshed = (card.now + timedelta(minutes=4, seconds=2)).isoformat()
    card.info["data_observed_at"] = refreshed
    for horse in card.horses: horse["data_observed_at"] = refreshed
    assert needs_refresh(card, now=card.now + timedelta(minutes=4, seconds=2)) is False
    assert needs_refresh(card, now=card.now + timedelta(minutes=10)) is True


@pytest.mark.parametrize("problem", ["client", "legacy", "m02", "past", "future_date",
    "post", "post_unknown", "date_unknown", "date_conflict", "fake_date", "race_mismatch", "naive_now"])
def test_new_refresh_reason_is_scoped_to_verified_today_pre_race(refresh_card, problem):
    card = refresh_card
    card.info["data_observed_at"] = (card.now - timedelta(hours=1)).isoformat()
    overrides = {}
    if problem == "client": overrides["trusted_db_card"] = False
    if problem == "legacy": overrides["bundle"] = {"model": object()}
    if problem == "m02": overrides["bundle"] = {"_local_candidate": {"model_id": "local_safe_m02_2025"}}
    if problem == "past": card.info["date"] = "20261010"
    if problem == "future_date": card.info["date"] = "20261012"
    if problem == "post": card.info["post_time"] = "10:00"
    if problem == "post_unknown": card.info["post_time"] = "unknown"
    if problem == "date_unknown": card.info.pop("date")
    if problem == "date_conflict": card.info["race_date"] = "20261010"
    if problem == "fake_date": card.info["date"] = card.info["race_id"][:8]
    if problem == "race_mismatch": card.horses[0]["race_id"] = "202605040401"
    if problem == "naive_now": overrides["now"] = card.now.replace(tzinfo=None)
    assert needs_refresh(card, **overrides) is False
    if problem in {"client", "legacy", "m02"}: assert card.verified == []


@pytest.mark.parametrize("where", ["metadata", "runner"])
@pytest.mark.parametrize("outcome", [{"finish_position": 1}, {"finish_position": None, "finish": 1},
    {"finish_position": 0, "finish": "取消"}, {"finish_time": "1:34.0"}, {"time": "1:34.0"},
    {"time_seconds": 94.0}, {"odds_status": "result"}, {"odds_status": "settled"}])
def test_result_aliases_never_add_shared_refresh(refresh_card, where, outcome):
    card = refresh_card
    card.info["data_observed_at"] = None
    (card.info if where == "metadata" else card.horses[0]).update(outcome)
    assert needs_refresh(card) is False


def test_refresh_checks_formula_sources_before_admitting_fetch(refresh_card, monkeypatch):
    refresh_card.info["data_observed_at"] = None
    def changed(_bundle): raise ValueError("shared-db-feature-contract-source-mismatch")
    monkeypatch.setattr(serving, "verify_bundle_sources", changed)
    with pytest.raises(ValueError, match="source-mismatch"):
        needs_refresh(refresh_card)


@pytest.mark.parametrize("scenario", ["stale", "metadata_missing", "invalid", "future",
    "fresh", "past", "started", "result_alias", "legacy", "m02", "write_failed"])
def test_analyze_shared_refresh_uses_atomic_whole_snapshot_only(refresh_card, tmp_path, monkeypatch, scenario):
    """Fake fetch and compute only; no HTTP, real models, receipts or purchases."""
    import aiohttp
    from models import AnalyzeRaceRequest
    from routers import predict
    from services import prediction_compute

    card = refresh_card
    stale = (card.now - timedelta(minutes=6)).isoformat()
    for row in [card.info, *card.horses]: row["data_observed_at"] = stale
    if scenario == "metadata_missing": card.info["data_observed_at"] = None
    if scenario == "invalid": card.horses[1]["data_observed_at"] = "bad"
    if scenario == "future": card.info["data_observed_at"] = (card.now + timedelta(minutes=1)).isoformat()
    if scenario == "fresh":
        for row in [card.info, *card.horses]: row["data_observed_at"] = card.now.isoformat()
    if scenario == "past": card.info["date"] = "20261010"
    if scenario == "started": card.info["post_time"] = "10:00"
    if scenario == "result_alias": card.horses[0]["finish"] = 1
    model_name = ("legacy_active.joblib" if scenario == "legacy" else
                  "local_safe_m02_2025.joblib" if scenario == "m02" else serving.PREFIX + "fixture.joblib")
    bundle = card.bundle if scenario not in {"legacy", "m02"} else {"model": object(), "target": "speed_deviation"}
    path = tmp_path / model_name
    path.write_bytes(b"test-only-no-deserialization")
    database = tmp_path / "ultimate.db"
    with sqlite3.connect(database) as db:
        db.executescript("CREATE TABLE races_ultimate(race_id TEXT, data TEXT);"
                         "CREATE TABLE race_results_ultimate(race_id TEXT, data TEXT);")
        db.execute("INSERT INTO races_ultimate VALUES (?,?)", (card.info["race_id"], json.dumps(card.info)))
        db.executemany("INSERT INTO race_results_ultimate VALUES (?,?)",
                       [(card.info["race_id"], json.dumps(row)) for row in card.horses])
    monkeypatch.setattr(predict, "ULTIMATE_DB", database)
    monkeypatch.setattr(predict, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(predict, "_ANALYZE_CACHE", {})
    monkeypatch.setattr(predict, "_resolve_model_path", lambda _model_id: path)
    monkeypatch.setattr(predict, "_local_candidate_spec", lambda **_kwargs: None)
    monkeypatch.setattr(predict, "_load_served_model_bundle", lambda _path: bundle)
    predicate = serving.shared_pre_race_input_needs_refresh
    monkeypatch.setattr(serving, "shared_pre_race_input_needs_refresh",
                        lambda *args, **kwargs: predicate(*args, **kwargs, now=card.now))
    events, computed = [], []
    fresh = {"race_info": deepcopy(card.info), "horses": deepcopy(card.horses)}
    fresh["race_info"].update(distance=1800, data_observed_at=card.now.isoformat())
    for horse in fresh["horses"]:
        horse.update(distance=1800, odds=9.0, data_observed_at=card.now.isoformat())
    fresh["horses"][1]["horse_number"] = 3  # The entire cohort, not an odds-only merge.
    class FakeSession:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return False
    async def fetch(_session, race_id, date=""):
        events.append(("fetch", race_id, date))
        return deepcopy(fresh)
    def save(snapshot, db_path, race_id):
        assert db_path == database and race_id == card.info["race_id"]
        events.append(("atomic_save", deepcopy(snapshot)))
        if scenario == "write_failed": raise RuntimeError("fixture atomic write rejected")
        return True
    async def compute(**kwargs):
        computed.append(kwargs)
        events.append(("compute",))
        raise HTTPException(status_code=418, detail="fixture stops before prediction or logging")
    monkeypatch.setattr(aiohttp, "ClientSession", FakeSession)
    monkeypatch.setattr(predict, "fetch_fresh_race_snapshot", fetch)
    monkeypatch.setattr(predict, "save_valid_race_snapshot", save)
    monkeypatch.setattr(prediction_compute, "run_prediction_compute", compute)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(predict._analyze_race_impl(AnalyzeRaceRequest(race_id=card.info["race_id"], model_id=path.stem)))
    assert exc.value.status_code == 418
    refreshing = scenario in {"stale", "metadata_missing", "invalid", "future", "write_failed"}
    assert [event[0] for event in events] == (["fetch", "atomic_save", "compute"] if refreshing else ["compute"])
    result = computed[0]
    if refreshing and scenario != "write_failed":
        assert result["race_info"]["distance"] == 1800
        assert result["race_info"]["data_observed_at"] == card.now.isoformat()
        assert result["frame"]["horse_number"].tolist() == [1, 3]
        assert result["frame"]["odds"].tolist() == [9.0, 9.0]
        assert result["frame"]["data_observed_at"].tolist() == [card.now.isoformat()] * 2
    else:
        assert {key: result["race_info"][key] for key in card.info} == card.info
        assert result["frame"]["horse_number"].tolist() == [1, 2]
        assert result["frame"]["odds"].tolist() == [3.0, 4.0]
    # Fake save does not mutate even the temporary source, much less live DB.
    with sqlite3.connect(database) as db:
        assert json.loads(db.execute("SELECT data FROM races_ultimate").fetchone()[0]) == card.info
