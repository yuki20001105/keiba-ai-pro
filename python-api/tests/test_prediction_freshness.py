"""Synthetic local fixtures only; no downloads, acquisition or model changes."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from fastapi import HTTPException, Response
import numpy as np
import pandas as pd
import pytest

from models import AnalyzeRaceRequest, BatchAnalyzeRequest
from routers import predict
from services import prediction_compute
from services.prediction_freshness import data_revision, freshness_metadata, model_runtime_revision
from services.prediction_source_revision import initialize_source_revision


def forbidden(*_args, **_kwargs):
    raise AssertionError("saved DB prediction must not fetch or train")


def test_wal_commits_invalidate_before_database_checkpoint(tmp_path):
    path = tmp_path / "local.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA wal_autocheckpoint=0")
        db.execute("CREATE TABLE source (value TEXT)")
        db.commit()
        before = data_revision(path)
        main_stat = path.stat()
        db.execute("INSERT INTO source VALUES ('new result')")
        db.commit()
        assert path.stat().st_mtime_ns == main_stat.st_mtime_ns
        assert data_revision(path) != before
        before_checkpoint = data_revision(path)
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        assert data_revision(path) != before_checkpoint


def test_no_read_check_creates_missing_database(tmp_path):
    path = tmp_path / "missing.sqlite"
    assert data_revision(path) == data_revision(path)
    assert not path.exists()


def test_same_size_same_mtime_model_replacement_changes_revision(tmp_path):
    path = tmp_path / "model_fixture.joblib"
    path.write_bytes(b"original!")
    stat = path.stat()
    before = model_runtime_revision(path)
    path.write_bytes(b"modified!")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert path.stat().st_size == stat.st_size
    assert model_runtime_revision(path) != before


def test_validation_sidecar_change_invalidates_response(tmp_path):
    path = tmp_path / "model_fixture.joblib"
    path.write_bytes(b"isolated")
    before = model_runtime_revision(path)
    path.with_suffix(".betting-validation.json").write_text('{"validated":false}', encoding="utf-8")
    assert model_runtime_revision(path) != before


def test_metadata_never_claims_prestart_receipt(tmp_path):
    model = tmp_path / "model_fixture.joblib"
    model.write_bytes(b"isolated")
    database = tmp_path / "local.sqlite"
    database.write_bytes(b"old")
    before = data_revision(database)
    database.write_bytes(b"new history")
    result = freshness_metadata(db_path=database, model_path=model, source="saved_db", initial_revision=before)
    assert result["schema_version"] == "prediction-freshness-v1"
    assert result["data_changed_during_prediction"] is True
    assert result["cache_hit"] is False
    assert result["revision_kind"] == "sqlite-file-version"
    assert result["source_tracking"] is False
    assert not result.get("capture_time_verified")
    assert str(tmp_path) not in json.dumps(result)


@pytest.fixture
def saved_card(tmp_path, monkeypatch):
    database = tmp_path / "local.sqlite"
    model = tmp_path / "model_fixture.joblib"
    model.write_bytes(b"isolated-model-not-unpickled")
    race_id = "202606050101"
    race = {"race_id": race_id, "race_name": "fixture", "date": "20261201", "distance": 1600,
            "venue": "東京", "track_type": "芝", "num_horses": 2, "post_time": "15:45"}
    horses = [{"horse_id": str(2013100000 + n), "horse_number": n, "horse_name": f"fixture-{n}",
               "date": race["date"], "distance": 1600, "venue": "東京", "odds": odds,
               "popularity": n, "age": 3} for n, odds in [(1, 2.0), (2, 5.0)]]
    with sqlite3.connect(database) as db:
        db.executescript("CREATE TABLE races_ultimate(race_id TEXT, data TEXT, created_at TEXT);"
                         "CREATE TABLE race_results_ultimate(race_id TEXT, data TEXT, created_at TEXT);")
        db.execute("INSERT INTO races_ultimate(race_id,data) VALUES (?,?)", (race_id, json.dumps(race)))
        db.executemany("INSERT INTO race_results_ultimate(race_id,data) VALUES (?,?)",
                       [(race_id, json.dumps(horse)) for horse in horses])
    assert initialize_source_revision(database, local_enabled=True)
    monkeypatch.setattr(predict, "ULTIMATE_DB", database)
    monkeypatch.setattr(predict, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(predict, "_ANALYZE_CACHE", {})
    monkeypatch.setattr(predict, "_resolve_model_path", lambda _model_id: model)
    monkeypatch.setattr(predict, "_resolve_saved_model_path", lambda _model_id: model)
    monkeypatch.setattr(predict, "_local_candidate_spec", lambda **_kwargs: None)
    bundle = {"model_id": model.stem, "target": "win"}
    monkeypatch.setattr(predict, "_load_served_model_bundle", lambda _model: bundle)
    monkeypatch.setattr(predict, "ModelPredictor", lambda *_args: SimpleNamespace(
        target="win", bundle=bundle, input_diagnostics=None, calibrator_applied=False))
    monkeypatch.setattr(predict, "fetch_fresh_race_snapshot", forbidden)
    monkeypatch.setattr(predict, "stored_rows_need_refresh", lambda *_args: True)
    monkeypatch.setattr(predict, "_save_prediction_log", lambda *_args: None)
    calls = []
    async def compute(**payload):
        calls.append(payload)
        return {"features": pd.DataFrame({"safe": [1., 2.]}), "raw": np.array([.6, .4]),
                "probabilities": np.array([.6, .4]), "explanations": None, "place3": None,
                "calibrator_applied": False, "input_diagnostics": None, "history_rows": 2, "timings": {}}
    monkeypatch.setattr(prediction_compute, "run_prediction_compute", compute)
    return SimpleNamespace(database=database, model=model, race_id=race_id, calls=calls, horses=horses)


def run_saved(card, **kwargs):
    request = AnalyzeRaceRequest(race_id=card.race_id, data_refresh="saved_db", **kwargs)
    return asyncio.run(predict._analyze_race_impl(request))


def test_manual_recompute_never_serves_old_response(saved_card):
    # Deliberately exercise the ordinary endpoint twice, not a standalone hash.
    first = run_saved(saved_card, force_recompute=True)
    second = run_saved(saved_card, force_recompute=True)
    assert len(saved_card.calls) == 2
    assert first.freshness["cache_hit"] is False and second.freshness["cache_hit"] is False
    assert second.freshness["source"] == "saved_db"
    assert second.recommendation["decision"] == "skip"
    assert second.recommendation["tickets"] == []


def test_response_cache_uses_new_db_history_revision(saved_card):
    first = run_saved(saved_card)
    second = run_saved(saved_card)
    assert len(saved_card.calls) == 1 and second.freshness["cache_hit"] is True
    with sqlite3.connect(saved_card.database) as db:
        db.execute("INSERT INTO race_results_ultimate(race_id,data) VALUES (?,?)", ("202605010101", '{"new_history":true}'))
    third = run_saved(saved_card)
    assert len(saved_card.calls) == 2
    assert third.freshness["data_revision"] != first.freshness["data_revision"]
    assert third.freshness["cache_hit"] is False


def test_rerun_reads_repaired_odds_from_db(saved_card):
    first = run_saved(saved_card, force_recompute=True)
    with sqlite3.connect(saved_card.database) as db:
        horse = {**saved_card.horses[0], "odds": 3.2}
        db.execute("UPDATE race_results_ultimate SET data=? WHERE rowid=1", (json.dumps(horse),))
    second = run_saved(saved_card, force_recompute=True)
    assert next(p for p in first.predictions if p["horse_number"] == 1)["odds"] == 2.0
    assert next(p for p in second.predictions if p["horse_number"] == 1)["odds"] == 3.2
    assert saved_card.calls[-1]["frame"].iloc[0]["odds"] == 3.2
    assert all(p["expected_value"] is None for p in second.predictions)


def test_prediction_log_does_not_stale_inputs_or_parallel_cache(saved_card, monkeypatch):
    # Real legacy display log writes share the source DB; they are not raw input.
    before = data_revision(saved_card.database)
    writes = []
    def log(*_args):
        with sqlite3.connect(saved_card.database) as db:
            db.execute("CREATE TABLE IF NOT EXISTS prediction_log(id INTEGER PRIMARY KEY,data TEXT)")
            db.execute("INSERT INTO prediction_log(data) VALUES ('derived display prediction')")
        writes.append(True)
    monkeypatch.setattr(predict, "_save_prediction_log", log)
    first = run_saved(saved_card, force_recompute=True)
    second = run_saved(saved_card)
    assert writes == [True]
    assert len(saved_card.calls) == 1
    assert second.freshness["cache_hit"] is True
    assert first.freshness["source_tracking"] is True
    assert first.freshness["revision_kind"] == "sqlite-source-generation"
    assert first.freshness["data_revision"] == before == data_revision(saved_card.database)


def test_history_eligibility_update_invalidates_prediction_cache(saved_card):
    with sqlite3.connect(saved_card.database) as db:
        db.execute("CREATE TABLE scrape_race_acquisition_state(race_id TEXT,lifecycle TEXT,quality_status TEXT,excluded_from_standard INTEGER)")
        db.execute("INSERT INTO scrape_race_acquisition_state VALUES ('202605010101','completed','complete',0)")
    first = run_saved(saved_card)
    second = run_saved(saved_card)
    assert second.freshness["cache_hit"] is True
    with sqlite3.connect(saved_card.database) as db:
        db.execute("UPDATE scrape_race_acquisition_state SET excluded_from_standard=1")
    third = run_saved(saved_card)
    assert third.freshness["cache_hit"] is False
    assert third.freshness["data_revision"] != first.freshness["data_revision"]
    assert len(saved_card.calls) == 2


def test_unrelated_log_during_compute_does_not_reject_result(saved_card, monkeypatch):
    original = prediction_compute.run_prediction_compute
    async def changed(**payload):
        result = await original(**payload)
        with sqlite3.connect(saved_card.database) as db:
            db.execute("CREATE TABLE prediction_log(id INTEGER PRIMARY KEY,data TEXT)")
            db.execute("INSERT INTO prediction_log(data) VALUES ('another race prediction')")
        return result
    monkeypatch.setattr(prediction_compute, "run_prediction_compute", changed)
    result = run_saved(saved_card, force_recompute=True)
    assert result.freshness["data_changed_during_prediction"] is False


@pytest.mark.parametrize("missing", ["race", "runners"])
def test_saved_db_missing_data_never_scrapes(saved_card, missing):
    with sqlite3.connect(saved_card.database) as db:
        db.execute("DELETE FROM " + ("races_ultimate" if missing == "race" else "race_results_ultimate"))
    with pytest.raises(HTTPException) as caught:
        run_saved(saved_card, force_recompute=True)
    assert caught.value.status_code == 404
    assert "保存済みDB" in caught.value.detail
    assert saved_card.calls == []


def test_saved_db_missing_odds_does_not_use_playwright(saved_card, monkeypatch):
    from routers import realtime_odds
    monkeypatch.setattr(realtime_odds, "_fetch_tansho_odds_playwright", forbidden)
    with sqlite3.connect(saved_card.database) as db:
        db.execute("UPDATE race_results_ultimate SET data=json_set(data, '$.odds', NULL, '$.popularity', NULL)")
    result = run_saved(saved_card, force_recompute=True)
    assert len(saved_card.calls) == 1
    assert all(p["odds"] is None for p in result.predictions)


def test_concurrent_input_update_fails_before_snapshot_or_log(saved_card, monkeypatch):
    original = prediction_compute.run_prediction_compute
    async def changed(**payload):
        result = await original(**payload)
        with sqlite3.connect(saved_card.database) as db:
            db.execute("UPDATE race_results_ultimate SET data=json_set(data,'$.odds',3.1) WHERE rowid=1")
        return result
    monkeypatch.setattr(prediction_compute, "run_prediction_compute", changed)
    monkeypatch.setattr(predict, "_save_prediction_log", forbidden)
    with pytest.raises(HTTPException) as caught:
        run_saved(saved_card, force_recompute=True)
    assert caught.value.status_code == 409
    assert "最新データで再予測" in caught.value.detail
    assert predict._ANALYZE_CACHE == {}


def test_same_identity_model_change_during_prediction_rejected(saved_card, monkeypatch):
    original = prediction_compute.run_prediction_compute
    async def changed(**payload):
        result = await original(**payload)
        stat = saved_card.model.stat()
        contents = saved_card.model.read_bytes()
        saved_card.model.write_bytes(b"X" * len(contents))
        os.utime(saved_card.model, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        return result
    monkeypatch.setattr(prediction_compute, "run_prediction_compute", changed)
    with pytest.raises(HTTPException) as caught:
        run_saved(saved_card, force_recompute=True)
    assert caught.value.status_code == 503
    assert "モデルまたは入力処理" in caught.value.detail
    assert predict._ANALYZE_CACHE == {}


def test_freshness_endpoint_has_no_prediction_or_download(saved_card, monkeypatch):
    monkeypatch.setattr(predict, "_resolve_model_path", forbidden)
    monkeypatch.setattr(predict, "_ensure_model_local", forbidden)
    result = asyncio.run(predict.prediction_freshness(Response(), saved_card.model.stem))
    assert result["resolved_model_id"] == saved_card.model.stem
    assert result["data_revision"] == data_revision(saved_card.database)
    assert saved_card.calls == []


def test_unknown_freshness_model_does_not_download(tmp_path, monkeypatch):
    monkeypatch.setattr(predict, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(predict, "_local_candidate_spec", lambda **_kwargs: None)
    monkeypatch.setattr(predict, "_ensure_model_local", forbidden)
    with pytest.raises(HTTPException) as caught:
        predict._resolve_saved_model_path("missing_model")
    assert caught.value.status_code == 404


@pytest.mark.parametrize("invalid", ["../secrets", "*", "some[thing]", "nested/model", "C:\\secret"])
def test_saved_model_rejects_glob_and_path_input(invalid):
    with pytest.raises(HTTPException) as caught:
        predict._resolve_saved_model_path(invalid)
    assert caught.value.status_code == 400


def test_batch_passes_refresh_policy_to_every_unique_race(monkeypatch):
    seen = []
    async def analyze(request):
        seen.append(request)
        raise HTTPException(404, "isolated")
    async def no_quota(*_args, **_kwargs): pass
    monkeypatch.setattr(predict, "_analyze_race_impl", analyze)
    monkeypatch.setattr(predict, "check_and_consume_pred_count", no_quota)
    request = BatchAnalyzeRequest(race_ids=["first", "second", "first"], force_recompute=True, data_refresh="saved_db")
    result = asyncio.run(predict.analyze_races_batch(request, SimpleNamespace(state=SimpleNamespace(user_id=None))))
    assert result["total"] == 2
    assert len(seen) == 2
    assert all(r.force_recompute and r.data_refresh == "saved_db" for r in seen)
