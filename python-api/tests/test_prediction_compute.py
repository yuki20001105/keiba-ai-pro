from __future__ import annotations

import asyncio
import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException

from services import prediction_compute as compute


@pytest.fixture(autouse=True)
def isolated_runtime():
    compute.shutdown_prediction_compute()
    compute._BUNDLES.clear()
    yield
    compute.shutdown_prediction_compute()
    compute._BUNDLES.clear()


def test_workers_cpu_memory_and_deployed_caps(monkeypatch):
    import app_config
    import psutil
    monkeypatch.setattr(app_config, "APP_ENV", "development")
    monkeypatch.setattr(psutil, "cpu_count", lambda **kwargs: 8)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=12 * 1024**3))
    monkeypatch.setenv("PREDICTION_CPU_WORKERS", "24")
    assert compute.worker_count() == 4
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=5 * 1024**3))
    assert compute.worker_count() == 1
    monkeypatch.setenv("PREDICTION_CPU_WORKERS", "invalid")
    assert compute.worker_count() == 1
    monkeypatch.setattr(app_config, "APP_ENV", "production")
    assert compute.worker_count() == 1


def test_native_threads_avoid_oversubscription(monkeypatch):
    import psutil
    monkeypatch.setattr(psutil, "cpu_count", lambda **kwargs: 8)
    monkeypatch.setenv("PREDICTION_NATIVE_THREADS", "16")
    assert compute.native_threads(4) == 2
    monkeypatch.setenv("PREDICTION_NATIVE_THREADS", "0")
    assert compute.native_threads(4) == 1


def test_bundle_cache_refresh_and_optimizer_isolation(tmp_path, monkeypatch):
    import app_config
    model = tmp_path / "model.joblib"
    model.write_bytes(b"first")
    calls = []
    def load(path):
        calls.append(path)
        return {"model": object(), "optimizer": {"mutable": []}}
    monkeypatch.setattr(app_config, "load_model_bundle", load)
    first = compute._load_bundle(str(model))
    first["optimizer"]["mutable"].append("request state")
    second = compute._load_bundle(str(model))
    assert len(calls) == 1
    assert second["optimizer"]["mutable"] == []
    assert first["model"] is second["model"]
    model.write_bytes(b"replacement artifact")
    compute._load_bundle(str(model))
    assert len(calls) == 2
    assert len(compute._BUNDLES) == 1


@pytest.mark.parametrize('cache_hit', [False, True])
def test_bundle_rejects_other_trusted_candidate_at_same_path(tmp_path, monkeypatch, cache_hit):
    import app_config
    from services import local_candidate_input
    path = tmp_path / 'm01.joblib'
    path.write_bytes(b'AAAA')
    stat = path.stat()
    first = {'model_id': 'local_safe_m01_fixture', 'source_path': str(path),
             'sha256': hashlib.sha256(b'AAAA').hexdigest()}
    second = {'model_id': 'local_safe_m02_fixture', 'source_path': str(tmp_path / 'm02.joblib'),
              'sha256': hashlib.sha256(b'BBBB').hexdigest()}
    monkeypatch.setattr(app_config, 'APP_ENV', 'test')
    monkeypatch.setattr(local_candidate_input, '_SPECS', {spec['model_id']: spec for spec in [first, second]})
    monkeypatch.setattr(local_candidate_input, '_modules_verified', lambda: True)
    loads = []
    def load(actual_path, *, model_id=None):
        loads.append((actual_path, model_id))
        assert model_id == first['model_id']
        return {'model_id': model_id, '_local_candidate': first, 'optimizer': None}
    monkeypatch.setattr(local_candidate_input, 'load_candidate_bundle', load)
    if cache_hit:
        assert compute._load_bundle(str(path))['model_id'] == first['model_id']
    path.write_bytes(b'BBBB')
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert (path.stat().st_mtime_ns, path.stat().st_size) == (stat.st_mtime_ns, stat.st_size)
    assert local_candidate_input.validate_candidate_sources(path)['model_id'] == second['model_id']
    with pytest.raises(ValueError, match='selected-model-id-mismatch'):
        compute._load_bundle(str(path))
    assert len(loads) == int(cache_hit)


def test_bundle_cache_marker_must_match_selected_candidate(tmp_path, monkeypatch):
    import app_config
    from services import local_candidate_input
    path = tmp_path / 'm01.joblib'
    path.write_bytes(b'AAAA')
    stat = path.stat()
    spec = {'model_id': 'local_safe_m01_fixture', 'source_path': str(path),
            'sha256': hashlib.sha256(b'AAAA').hexdigest()}
    monkeypatch.setattr(app_config, 'APP_ENV', 'test')
    monkeypatch.setattr(local_candidate_input, '_SPECS', {spec['model_id']: spec})
    monkeypatch.setattr(local_candidate_input, '_modules_verified', lambda: True)
    key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    compute._BUNDLES[key] = {'_local_candidate': {'model_id': 'local_safe_m02_fixture'}, 'optimizer': None}
    with pytest.raises(ValueError, match='cached-model-id-mismatch'):
        compute._load_bundle(str(path))


def test_worker_reuses_exact_features_for_score_and_explanation(tmp_path, monkeypatch):
    from routers import predict
    from keiba_ai import db_ultimate_loader
    model = tmp_path / "model.joblib"
    model.write_bytes(b"fake")
    frame = pd.DataFrame({"horse_number": [1, 2], "score": [5., 2.]})
    seen = {}

    class Predictor:
        def __init__(self, bundle, path, **kwargs):
            seen["threads"] = kwargs["inference_threads"]
            self.calibrator_applied = False
        def build_features(self, actual, history):
            assert len(history) == 3
            seen["features"] = actual[["score"]].copy()
            return seen["features"]
        def predict_scores(self, features):
            assert features is seen["features"]
            self.calibrator_applied = True
            return features["score"].to_numpy(), np.array([.7, .3])
        def explain_scores(self, features, top_n):
            assert features is seen["features"]
            assert top_n == 6
            return [{"method": "tree_shap"}] * 2

    monkeypatch.setattr(predict, "ModelPredictor", Predictor)
    monkeypatch.setattr(compute, "_load_bundle", lambda path: {})
    monkeypatch.setattr(db_ultimate_loader, "load_prediction_history_frame", lambda *a, **k: pd.DataFrame({"score": [1.]}))
    result = compute.compute_prediction({
        "frame": frame, "model_path": str(model), "db_path": "unused",
        "excluded_race_ids": ["target"], "native_threads": 2,
        "include_explanation": True,
    })
    assert "error" not in result
    np.testing.assert_array_equal(result["raw"], [5., 2.])
    assert result["calibrator_applied"] is True
    assert len(result["explanations"]) == 2
    assert seen["threads"] == 2
    assert result["history_rows"] == 1
    assert set(result["timings"]) >= {"history_seconds", "features_seconds", "inference_seconds", "worker_seconds"}


def test_worker_rejects_changed_model_identity(tmp_path):
    model = tmp_path / "model.joblib"
    model.write_bytes(b"fake")
    result = compute.compute_prediction({
        "frame": pd.DataFrame(), "model_path": str(model), "db_path": "unused",
        "expected_model_identity": (-1, -1), "native_threads": 1,
    })
    assert result["error"]["status_code"] == 503


def test_worker_rejects_model_replacement_during_inference(tmp_path, monkeypatch):
    from routers import predict
    from keiba_ai import db_ultimate_loader
    model = tmp_path / "model.joblib"
    model.write_bytes(b"original")
    stat = model.stat()
    class Predictor:
        calibrator_applied = False
        def __init__(self, *a, **k): pass
        def build_features(self, actual, history): return actual
        def predict_scores(self, actual):
            model.write_bytes(b"replaced artifact")
            return np.array([1.]), np.array([1.])
    monkeypatch.setattr(predict, "ModelPredictor", Predictor)
    monkeypatch.setattr(compute, "_load_bundle", lambda path: {})
    monkeypatch.setattr(db_ultimate_loader, "load_prediction_history_frame", lambda *a, **k: pd.DataFrame())
    result = compute.compute_prediction({
        "frame": pd.DataFrame({"score": [1.]}), "model_path": str(model), "db_path": "unused",
        "excluded_race_ids": [], "native_threads": 1,
        "expected_model_identity": (stat.st_mtime_ns, stat.st_size),
    })
    assert result["error"]["status_code"] == 503


def test_lightgbm_only_native_thread_keyword():
    from routers.predict import ModelPredictor
    class Generic:
        def predict(self, frame): return np.array([.2, .8])
    generic = ModelPredictor({"model": Generic()}, inference_threads=2)
    generic.predict_scores(pd.DataFrame({"x": [1., 2.]}))
    seen = {}
    class Booster:
        def predict(self, frame, **kwargs):
            seen.update(kwargs)
            return np.array([.2, .8])
    Booster.__module__ = "lightgbm.basic"
    lightgbm = ModelPredictor({"model": Booster()}, inference_threads=2)
    lightgbm.predict_scores(pd.DataFrame({"x": [1., 2.]}))
    assert seen == {"num_threads": 2}


def test_batch_concurrency_is_bounded_deduplicated_and_owner_scoped(monkeypatch):
    from routers import predict, performance
    from models import AnalyzeRaceResponse, BatchAnalyzeRequest
    active = peak = 0
    charges = []
    owners = []
    async def quota(request, units): charges.append(units)
    async def analyze(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(.01)
            if request.race_id == "failed":
                raise HTTPException(400, "one failed race")
            return AnalyzeRaceResponse(success=True, race_info={}, pro_evaluation={},
                predictions=[], bet_types={}, best_bet_type="none", best_bet_info={}, race_level="", recommendation={})
        finally: active -= 1
    async def attach(response, owner, race_id):
        owners.append((owner, race_id))
        return response
    monkeypatch.setattr(predict, "check_and_consume_pred_count", quota)
    monkeypatch.setattr(predict, "_analyze_race_impl", analyze)
    monkeypatch.setattr(performance, "attach_performance_snapshot", attach)
    monkeypatch.setattr(compute, "worker_count", lambda: 2)
    req = SimpleNamespace(state=SimpleNamespace(user_id="owner-only"))
    result = asyncio.run(predict.analyze_races_batch(BatchAnalyzeRequest(race_ids=["a", "b", "a", "failed", "c"]), req))
    assert peak == 2
    assert charges == [4]
    assert list(result["results"]) == ["a", "b", "failed", "c"]
    assert result["success_count"] == 3
    assert result["results"]["failed"]["error"] == "one failed race"
    assert sorted(owners) == [("owner-only", "a"), ("owner-only", "b"), ("owner-only", "c")]


def test_runtime_bounded_and_event_loop_responsive(monkeypatch):
    compute._POOL = ThreadPoolExecutor(max_workers=2)
    compute._POOL_WORKERS = 2
    lock = threading.Lock()
    current = peak = 0
    def worker(payload):
        import time
        nonlocal current, peak
        with lock:
            current += 1
            peak = max(peak, current)
        time.sleep(.05)
        with lock:
            current -= 1
        return {"timings": {}}
    monkeypatch.setattr(compute, "compute_prediction", worker)
    async def scenario():
        jobs = asyncio.gather(*(compute.run_prediction_compute() for _ in range(7)))
        beats = 0
        while not jobs.done():
            beats += 1
            await asyncio.sleep(.005)
        result = await jobs
        assert beats >= 10
        assert result[-1]["timings"]["queue_seconds"] > .1
    asyncio.run(scenario())
    assert peak == 2
    assert compute._ACTIVE == 0


def test_cancellation_keeps_slot_until_native_work_finishes(monkeypatch):
    compute._POOL = ThreadPoolExecutor(max_workers=1)
    compute._POOL_WORKERS = 1
    started = threading.Event()
    finish = threading.Event()
    calls = 0
    def worker(payload):
        nonlocal calls
        calls += 1
        started.set()
        assert finish.wait(timeout=3)
        return {"timings": {}}
    monkeypatch.setattr(compute, "compute_prediction", worker)
    async def scenario():
        first = asyncio.create_task(compute.run_prediction_compute())
        await asyncio.to_thread(started.wait, 1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert compute._ACTIVE == 1
        second = asyncio.create_task(compute.run_prediction_compute())
        await asyncio.sleep(.02)
        assert calls == 1
        finish.set()
        await second
    asyncio.run(scenario())
    assert calls == 2
    assert compute._ACTIVE == 0


def test_error_preserves_http_status_and_releases_slot(monkeypatch):
    compute._POOL = ThreadPoolExecutor(max_workers=1)
    compute._POOL_WORKERS = 1
    monkeypatch.setattr(compute, "compute_prediction", lambda payload: {"error": {"status_code": 400, "detail": "bad input"}})
    with pytest.raises(HTTPException) as error:
        asyncio.run(compute.run_prediction_compute())
    assert error.value.status_code == 400
    assert compute._ACTIVE == 0


def test_real_windows_spawn_transports_worker_errors(tmp_path, monkeypatch):
    import app_config
    monkeypatch.setattr(app_config, "APP_ENV", "development")
    monkeypatch.setattr(compute, "worker_count", lambda: 1)
    # A nonexistent model exercises real spawn/import/pickle, with no DB writes
    # or network and no dependency on fitted production artifacts.
    with pytest.raises(HTTPException) as error:
        asyncio.run(compute.run_prediction_compute(
            frame=pd.DataFrame(), model_path=str(tmp_path / "absent.joblib"),
            db_path="unused", excluded_race_ids=[],
        ))
    assert error.value.status_code == 500
