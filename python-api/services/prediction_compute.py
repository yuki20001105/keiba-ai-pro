"""Bounded local CPU workers; no network, writes, or model training here.

Each task owns its history frame. Pandas feature helpers mutate that frame, so
sharing it between concurrent requests would change the prediction contract.
Windows uses spawn explicitly; the API's scraper/lease state stays in one API
process and is never duplicated into multiple uvicorn workers.
"""
from __future__ import annotations

import asyncio
import copy
import gc
import multiprocessing
import os
import time
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

from fastapi import HTTPException
from training.db_context_registry import SHARED_PIPELINES

_POOL: ProcessPoolExecutor | ThreadPoolExecutor | None = None
_POOL_WORKERS = 0
_LOOP: asyncio.AbstractEventLoop | None = None
_SLOTS: asyncio.Semaphore | None = None
_ACTIVE = 0
_BUNDLES: OrderedDict[tuple, dict] = OrderedDict()


def _configure_worker_logging() -> None:
    # app_config's shared rotating file is safe in one API process, not in
    # concurrent Windows children. Retain its redaction filters/formatter.
    import app_config  # noqa: F401
    import logging
    from logging.handlers import RotatingFileHandler
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, RotatingFileHandler):
            original = Path(handler.baseFilename)
            separate = original.with_name(f"prediction-worker-{os.getpid()}.log")
            replacement = RotatingFileHandler(
                separate, maxBytes=handler.maxBytes, backupCount=1, encoding="utf-8",
            )
            replacement.setFormatter(handler.formatter)
            replacement.setLevel(handler.level)
            for filter_ in handler.filters:
                replacement.addFilter(filter_)
            root.removeHandler(handler)
            handler.close()
            root.addHandler(replacement)


def ensure_model_identity(path: str | Path, expected: tuple[int, int] | None) -> None:
    if expected is None:
        return
    try:
        stat = Path(path).stat()
    except OSError as exc:
        raise HTTPException(503, "予測中にモデルが変更されました。再実行してください。") from exc
    if tuple(expected) != (stat.st_mtime_ns, stat.st_size):
        raise HTTPException(503, "予測中にモデルが変更されました。再実行してください。")


def worker_count() -> int:
    """Cap CPU and memory pressure, and leave deployed runtimes single-slot."""
    from app_config import APP_ENV
    if APP_ENV != "development":
        return 1
    import psutil
    physical = psutil.cpu_count(logical=False) or 2
    # Reserve 2 GiB for other apps, then allow 2 GiB per inference process.
    memory_slots = max(1, int((psutil.virtual_memory().available / 1024**3 - 2) // 2))
    try:
        requested = int(os.environ.get("PREDICTION_CPU_WORKERS", "4"))
    except ValueError:
        requested = 4
    return max(1, min(4, requested, max(1, physical // 2), memory_slots))


def native_threads(workers: int) -> int:
    import psutil
    physical = psutil.cpu_count(logical=False) or 2
    per_worker = max(1, physical // max(1, workers))
    try:
        requested = int(os.environ.get("PREDICTION_NATIVE_THREADS", "2"))
    except ValueError:
        requested = 2
    return max(1, min(requested, per_worker))


def runtime_status() -> dict:
    from app_config import APP_ENV
    return {
        "version": "bounded-cpu-v1",
        "max_workers": _POOL_WORKERS or worker_count(),
        "active_workers": _ACTIVE,
        "backend": "process" if APP_ENV == "development" else "thread",
    }


def _load_bundle(path: str) -> dict:
    """Cache artifacts by file identity, never retain request-specific state."""
    from app_config import load_model_bundle
    from routers.predict import _local_candidate_spec
    resolved = Path(path).resolve()
    if resolved.name.startswith("model_win_reference_"):
        # This special package is hash-checked before unpickling on every
        # worker admission, including cached predictions. It cannot fall
        # through to an unsigned legacy loader or change purchase authority.
        from services.win_nll_reference_input import load_published_bundle
        return dict(load_published_bundle(resolved), model_id=resolved.stem)
    from services.db_shared_input import PREFIX, validate_published_artifact
    shared_sha = validate_published_artifact(resolved) if resolved.name.startswith(PREFIX) else None
    candidate = _local_candidate_spec(path=resolved)
    if candidate is not None:
        # mtime/size cannot cryptographically bind cached model contents.
        from services.local_candidate_input import validate_candidate_sources
        validated = validate_candidate_sources(resolved)
        if validated["model_id"] != candidate["model_id"]:
            raise ValueError("local-candidate-selected-model-id-mismatch")
    stat = resolved.stat()
    identity = (str(resolved), stat.st_mtime_ns, stat.st_size)
    key = (*identity, shared_sha) if shared_sha is not None else identity
    if key not in _BUNDLES:
        if candidate is not None:
            from services.local_candidate_input import load_candidate_bundle
            bundle = load_candidate_bundle(resolved, model_id=candidate["model_id"])
        else:
            bundle = load_model_bundle(resolved)
        if shared_sha is not None:
            if validate_published_artifact(resolved) != shared_sha:
                raise ValueError("shared-artifact-changed-during-load")
            bundle["_shared_artifact_sha256"] = shared_sha
        # A same-path model replacement must evict the old in-memory artifact.
        for existing in list(_BUNDLES):
            if existing[0] == key[0]:
                del _BUNDLES[existing]
        _BUNDLES[key] = bundle
    if candidate is not None and (
        _BUNDLES[key].get("_local_candidate", {}).get("model_id") != candidate["model_id"]
    ):
        raise ValueError("local-candidate-cached-model-id-mismatch")
    _BUNDLES.move_to_end(key)
    while len(_BUNDLES) > 2:
        _BUNDLES.popitem(last=False)
    bundle = _BUNDLES[key].copy()
    # Legacy optimizers can store working state during transform. Copy only
    # that small preprocessing object, not the fitted LightGBM trees.
    bundle["optimizer"] = copy.deepcopy(bundle.get("optimizer"))
    return bundle


def compute_prediction(payload: dict) -> dict:
    """Picklable CPU entry point; identical feature/scoring/explanation path."""
    from app_config import logger
    from routers.predict import ModelPredictor
    from keiba_ai.db_ultimate_loader import load_prediction_history_frame
    import pandas as pd
    from threadpoolctl import threadpool_limits

    started = time.perf_counter()
    timings: dict[str, float] = {}
    frame = payload["frame"]
    try:
        with threadpool_limits(limits=payload["native_threads"]):
            stage = time.perf_counter()
            ensure_model_identity(payload["model_path"], payload.get("expected_model_identity"))
            predictor = ModelPredictor(
                _load_bundle(payload["model_path"]), payload["model_path"],
                inference_threads=payload["native_threads"],
                db_path=payload["db_path"], race_info=payload.get("race_info"),
                trusted_input_context=payload.get("trusted_input_context"),
            )
            timings["model_load_seconds"] = time.perf_counter() - stage
            stage = time.perf_counter()
            history_available = True
            candidate = bool(getattr(predictor, "bundle", {}).get("_local_candidate"))
            reference = getattr(predictor, "bundle", {}).get("_win_nll_reference") is True
            shared = getattr(predictor, "bundle", {}).get("feature_pipeline") in SHARED_PIPELINES
            if candidate or shared or reference:
                # Candidate input owns a strict canonical RO history loader.
                # Never mix legacy profile fields or optional place3 models.
                history = pd.DataFrame()
            else:
                try:
                    history = load_prediction_history_frame(
                        Path(payload["db_path"]), frame,
                        excluded_race_ids=set(payload["excluded_race_ids"]),
                    )
                except Exception as exc:
                    # Preserve the existing history-load fallback, with visibility.
                    logger.warning("prediction history scoped load failed: %s", exc)
                    history = pd.DataFrame()
                    history_available = False
            history_rows = len(history)
            full_history = pd.concat([history, frame], ignore_index=True)
            timings["history_seconds"] = time.perf_counter() - stage
            stage = time.perf_counter()
            features = predictor.build_features(frame, full_history)
            input_diagnostics = getattr(predictor, "input_diagnostics", None)
            if input_diagnostics is not None:
                input_diagnostics = dict(input_diagnostics, history_available=history_available)
                if candidate or shared or reference:
                    history_rows = int(input_diagnostics.get("history_audit", {}).get("eligible_rows", 0))
                if not history_available:
                    input_diagnostics["status"] = "degraded"
                    input_diagnostics["issue_codes"] = [*input_diagnostics["issue_codes"], "history_load_failed"]
            timings["features_seconds"] = time.perf_counter() - stage
            stage = time.perf_counter()
            raw, probabilities = predictor.predict_scores(features)
            timings["inference_seconds"] = time.perf_counter() - stage
            explanations = None
            stage = time.perf_counter()
            if payload.get("include_explanation"):
                try:
                    explanations = predictor.explain_scores(features, 6)
                except Exception as exc:
                    logger.warning("[explain] feature contributions unavailable: %s", exc)
            timings["explanation_seconds"] = time.perf_counter() - stage
            place3 = None
            stage = time.perf_counter()
            if payload.get("place3_model_path") and not candidate and not shared and not reference:
                try:
                    sub = ModelPredictor(
                        _load_bundle(payload["place3_model_path"]),
                        payload["place3_model_path"],
                        inference_threads=payload["native_threads"],
                    )
                    place3 = sub.predict_scores(sub.build_features(frame, full_history))
                except Exception as exc:
                    # Same optional-submodel fallback as the existing endpoint.
                    logger.warning("[analyze] place3 model failed: %s", exc)
            timings["place3_seconds"] = time.perf_counter() - stage
            result = {
                "features": features, "raw": raw, "probabilities": probabilities,
                "explanations": explanations, "place3": place3,
                "calibrator_applied": predictor.calibrator_applied,
                "input_diagnostics": input_diagnostics,
                "history_rows": history_rows,
            }
        del history, full_history
        gc.collect()
        ensure_model_identity(payload["model_path"], payload.get("expected_model_identity"))
        timings["worker_seconds"] = time.perf_counter() - started
        result["timings"] = timings
        return result
    except HTTPException as exc:
        # HTTPException itself is not reliably pickleable on Windows spawn.
        return {"error": {"status_code": exc.status_code, "detail": exc.detail}}
    except Exception as exc:
        logger.exception("prediction CPU task failed")
        return {"error": {"status_code": 500, "detail": str(exc)}}


def _runtime() -> tuple[ProcessPoolExecutor | ThreadPoolExecutor, asyncio.Semaphore]:
    global _POOL, _POOL_WORKERS, _LOOP, _SLOTS
    loop = asyncio.get_running_loop()
    if _POOL is None:
        from app_config import APP_ENV
        _POOL_WORKERS = worker_count()
        if APP_ENV == "development":
            _POOL = ProcessPoolExecutor(
                max_workers=_POOL_WORKERS,
                mp_context=multiprocessing.get_context("spawn"),
                initializer=_configure_worker_logging,
            )
        else:
            # No change to deployed-process topology or observation ownership.
            _POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="prediction-cpu")
    if _LOOP is not loop:
        if _ACTIVE:
            raise RuntimeError("prediction worker event loop changed while active")
        _LOOP = loop
        _SLOTS = asyncio.Semaphore(_POOL_WORKERS)
    assert _SLOTS is not None
    return _POOL, _SLOTS


async def run_prediction_compute(**payload) -> dict:
    """Admit at most N jobs, holding slots until CPU work actually completes."""
    global _ACTIVE
    pool, slots = _runtime()
    queued = time.perf_counter()
    await slots.acquire()
    queue_seconds = time.perf_counter() - queued
    _ACTIVE += 1
    payload["native_threads"] = native_threads(_POOL_WORKERS)

    def release(_future=None):
        global _ACTIVE
        if _future is not None and not _future.cancelled():
            _future.exception()  # Consume a detached waiter's eventual error.
        _ACTIVE -= 1
        slots.release()

    try:
        future = asyncio.get_running_loop().run_in_executor(pool, compute_prediction, payload)
    except Exception:
        release()
        raise
    try:
        # Cancelling an HTTP waiter must not free a slot for a still-running
        # native/Pandas task; it is not possible to safely terminate that task.
        result = await asyncio.shield(future)
    except asyncio.CancelledError:
        future.add_done_callback(release)
        raise
    except BrokenProcessPool as exc:
        release()
        raise HTTPException(503, "予測処理が停止しました。サーバーの状態を確認してください。") from exc
    except Exception:
        release()
        raise
    else:
        release()
    if "error" in result:
        raise HTTPException(**result["error"])
    result["timings"]["queue_seconds"] = queue_seconds
    result["timings"]["workers"] = _POOL_WORKERS
    return result


def shutdown_prediction_compute() -> None:
    global _POOL, _POOL_WORKERS, _LOOP, _SLOTS
    if _POOL is not None:
        _POOL.shutdown(wait=True, cancel_futures=False)
    _POOL = None
    _POOL_WORKERS = 0
    _LOOP = None
    _SLOTS = None
