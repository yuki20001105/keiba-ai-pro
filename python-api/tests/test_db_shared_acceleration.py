from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import threading

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python-api"))
sys.path.insert(0, str(ROOT / "keiba"))
from training import db_shared_acceleration as acceleration
import local_retrain_worker_main as worker


def receipt(requested="gpu", resolved="gpu", fallback=None):
    record = {"schema": acceleration.SCHEMA, "requested_device": requested,
        "resolved_device": resolved, "fallback_reason": fallback,
        "fallback_before_fit_only": True, "maximum_cpu_native_threads": 8,
        "maximum_concurrent_gpu_fits": 1,
        "probe": {"python": str(acceleration.GPU_PYTHON), "success": True,
                  "gpu_device_verified": resolved == "gpu"}}
    record["receipt_sha256"] = acceleration._canonical_sha(record)
    return record


def test_runtime_auto_fallback_occurs_only_before_fit(monkeypatch):
    calls = []
    def probe(device):
        calls.append(device)
        if device == "gpu":
            raise ValueError("gpu-runtime-not-ready")
        return {"python": "cpu/python.exe"}
    monkeypatch.setattr(acceleration, "_probe_runtime", probe)
    value = acceleration.resolve_runtime()
    assert calls == ["gpu", "cpu"]
    assert value["interpreter"] == "cpu/python.exe"
    assert value["execution_receipt"]["resolved_device"] == "cpu"
    assert value["execution_receipt"]["fallback_reason"] == "gpu-runtime-not-ready"
    acceleration._validate_receipt_structure(value["execution_receipt"])


def test_explicit_gpu_failure_is_not_silently_retried(monkeypatch):
    calls = []
    def probe(device):
        calls.append(device)
        raise ValueError("gpu-failed")
    monkeypatch.setattr(acceleration, "_probe_runtime", probe)
    with pytest.raises(ValueError, match="gpu-failed"):
        acceleration.resolve_runtime("gpu")
    assert calls == ["gpu"]


def test_legacy_pipeline_never_selects_gpu(monkeypatch):
    monkeypatch.setattr(acceleration, "_probe_runtime", lambda *_: pytest.fail("probe called"))
    assert acceleration.resolve_runtime("auto", False)["execution_receipt"] is None
    with pytest.raises(ValueError, match="requires-shared"):
        acceleration.resolve_runtime("gpu", False)


def test_receipt_hash_and_fallback_are_enforced():
    value = receipt()
    acceleration._validate_receipt_structure(value)
    value["resolved_device"] = "cpu"
    with pytest.raises(ValueError, match="receipt-invalid"):
        acceleration._validate_receipt_structure(value)
    value = receipt("gpu", "cpu", "fallback")
    with pytest.raises(ValueError, match="fallback-policy-invalid"):
        acceleration._validate_receipt_structure(value)


def test_receipt_identity_mismatch_fails_before_binding(monkeypatch):
    value = receipt()
    monkeypatch.setattr(acceleration, "_current_identity", lambda *_: {"python_sha256": "changed"})
    token = acceleration._RECEIPT.set(None)
    try:
        with pytest.raises(ValueError, match="identity-mismatch"):
            acceleration.validate_execution_receipt(value)
        assert acceleration.current_execution_receipt() is None
    finally:
        acceleration._RECEIPT.reset(token)


def test_gpu_resource_limit_and_original_functions_restore_on_error():
    original_resources = lambda *_: (2, 4)
    original_train = lambda *_: "cpu"
    fake = SimpleNamespace(_resource_limits=original_resources, _train_booster=original_train)
    import lightgbm.basic as basic
    logger = basic._LOGGER
    with pytest.raises(RuntimeError):
        with acceleration._fit_device(fake, "gpu", []):
            assert fake._resource_limits(4, None) == (1, 4)
            assert fake._train_booster is not original_train
            raise RuntimeError("fit failed")
    assert fake._resource_limits is original_resources
    assert fake._train_booster is original_train
    assert basic._LOGGER is logger


def test_gpu_booster_requires_actual_gpu_device_log(monkeypatch):
    import lightgbm as lgb
    fake = SimpleNamespace(_resource_limits=lambda *_: (2, 4), _train_booster=lambda *_: None,
                           CATEGORICAL_COLUMNS=[])
    monkeypatch.setattr(lgb, "Dataset", lambda *_, **__: object())
    monkeypatch.setattr(lgb, "train", lambda *_, **__: object())
    with acceleration._fit_device(fake, "gpu", []):
        with pytest.raises(ValueError, match="actual-learner-not-verified"):
            fake._train_booster([], [], {"id": "lean", "num_leaves": 15,
                "min_data_in_leaf": 60, "lambda_l2": 1., "num_boost_round": 120}, n_threads=4)


def test_gpu_parameters_and_evidence_are_fixed(monkeypatch):
    import lightgbm as lgb
    import lightgbm.basic as basic
    fake = SimpleNamespace(_resource_limits=lambda *_: (2, 4), _train_booster=lambda *_: None,
                           CATEGORICAL_COLUMNS=["venue"])
    captured = {}
    monkeypatch.setattr(lgb, "Dataset", lambda *_, **__: object())
    def train(params, data, num_boost_round, callbacks):
        captured.update(params)
        captured["rounds"] = num_boost_round
        basic._LOGGER.info("[LightGBM] [Info] Using GPU Device: NVIDIA GeForce RTX 4070 Ti SUPER")
        return "gpu-booster"
    monkeypatch.setattr(lgb, "train", train)
    rows = []
    with acceleration._fit_device(fake, "gpu", rows):
        assert fake._train_booster([], [], {"id": "lean", "num_leaves": 15,
            "min_data_in_leaf": 60, "lambda_l2": 1., "num_boost_round": 120}, n_threads=4) == "gpu-booster"
    assert captured["device_type"] == "gpu"
    assert captured["max_bin"] == 63
    assert captured["rounds"] == 120
    assert captured["seed"] == 42
    assert captured["num_threads"] == 4
    assert rows[0]["gpu_device_verified"] is True


def test_native_callback_is_registered_on_actual_training_thread(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import lightgbm as lgb
    import lightgbm.basic as basic
    fake = SimpleNamespace(_resource_limits=lambda *_: (2, 4), _train_booster=lambda *_: None,
                           CATEGORICAL_COLUMNS=[])
    callback = object()
    calls = []
    def register(value):
        assert value is callback
        calls.append(("registered", threading.get_ident()))
        return 0
    monkeypatch.setattr(basic, "_LIB", SimpleNamespace(callback=callback, LGBM_RegisterLogCallback=register))
    monkeypatch.setattr(lgb, "Dataset", lambda *_, **__: object())
    def native_train(*_, **__):
        calls.append(("trained", threading.get_ident()))
        assert calls[-2] == ("registered", threading.get_ident())
        basic._LOGGER.info("Using GPU Device: threaded test")
        return "gpu-model"
    monkeypatch.setattr(lgb, "train", native_train)
    main_thread = threading.get_ident()
    records = []
    with acceleration._fit_device(fake, "gpu", records):
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(fake._train_booster, [], [],
                {"id": "lean", "num_leaves": 15, "min_data_in_leaf": 60,
                 "lambda_l2": 1., "num_boost_round": 120}, n_threads=4).result()
    assert result == "gpu-model"
    assert calls[0][1] != main_thread
    assert calls[0][1] == calls[1][1]
    assert records[0]["gpu_device_verified"] is True


def test_native_callback_registration_failure_restores_and_never_fits(monkeypatch):
    import lightgbm as lgb
    import lightgbm.basic as basic
    original_train = lambda *_: "original"
    fake = SimpleNamespace(_resource_limits=lambda *_: (2, 4), _train_booster=original_train,
                           CATEGORICAL_COLUMNS=[])
    original_logger = basic._LOGGER
    monkeypatch.setattr(basic, "_LIB", SimpleNamespace(callback=object(), LGBM_RegisterLogCallback=lambda *_: -1))
    def fail(_):
        raise ValueError("native-log-registration-failed")
    monkeypatch.setattr(basic, "_safe_call", fail)
    monkeypatch.setattr(lgb, "Dataset", lambda *_, **__: pytest.fail("Dataset constructed after callback failure"))
    with pytest.raises(ValueError, match="registration-failed"):
        with acceleration._fit_device(fake, "gpu", []):
            fake._train_booster([], [], {"id": "lean", "num_leaves": 15,
                "min_data_in_leaf": 60, "lambda_l2": 1., "num_boost_round": 120}, n_threads=4)
    assert fake._train_booster is original_train
    assert basic._LOGGER is original_logger


@pytest.mark.skipif(os.environ.get("RUN_LOCAL_GPU_SMOKE") != "1", reason="Opt-in installed GPU runtime only")
def test_real_gpu_log_evidence_in_asyncio_worker_thread():
    """Synthetic 512x8/2-round fit only; no DB, lease, model save or publication."""
    import numpy as np
    import lightgbm as lgb
    import lightgbm.basic as basic
    assert acceleration._sha(Path(lgb.__file__).parent / "bin/lib_lightgbm.dll") == acceleration.DLL_PINS["gpu"]
    random = np.random.default_rng(42)
    matrix = random.normal(size=(512, 8))
    labels = matrix[:, 0] + .01 * random.normal(size=512)
    fake = SimpleNamespace(_resource_limits=lambda *_: (2, 4), _train_booster=lambda *_: None,
                           CATEGORICAL_COLUMNS=[])
    main_thread = threading.get_ident()
    records, thread_ids = [], []
    def fit_in_thread():
        thread_ids.append(threading.get_ident())
        with acceleration._fit_device(fake, "gpu", records):
            model = fake._train_booster(matrix, labels,
                {"id": "lean", "num_leaves": 15, "min_data_in_leaf": 20,
                 "lambda_l2": 1., "num_boost_round": 2}, n_threads=2)
        assert np.isfinite(model.predict(matrix[:16], num_threads=2)).all()
    asyncio.run(asyncio.to_thread(fit_in_thread))
    assert thread_ids[0] != main_thread
    assert len(records) == 1
    assert records[0]["gpu_device_verified"] is True
    assert any("Using GPU Device" in line for line in records[0]["gpu_log_evidence"])


def test_fit_requires_verified_context_even_if_receipt_is_well_formed():
    token = acceleration._RECEIPT.set(None)
    try:
        with pytest.raises(ValueError, match="unverified-worker-execution"):
            acceleration.accelerated_fit_shared_model(None, None, execution_receipt=receipt())
    finally:
        acceleration._RECEIPT.reset(token)


def test_v2_envelope_accepts_and_binds_receipt():
    value = {"schema_version": 2, "mode": "prepare",
        "job_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "preparation_token": "b" * 64,
        "request": {"force_sync": False, "feature_pipeline": "local-db-shared-v1", "compute_device": "gpu"},
        "execution_receipt": receipt()}
    parsed = worker.parse_envelope(json.dumps(value).encode())
    assert parsed.execution_receipt == value["execution_receipt"]
    value["execution_receipt"]["receipt_sha256"] = "tampered"
    with pytest.raises(worker.WorkerEnvelopeError, match="receipt-invalid"):
        worker.parse_envelope(json.dumps(value).encode())


def test_worker_runtime_verification_precedes_any_job_dispatch(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr(acceleration, "configure_worker_imports", lambda *_: None)
    def fail(_):
        raise ValueError("runtime changed")
    monkeypatch.setattr(acceleration, "validate_execution_receipt", fail)
    envelope = worker.WorkerEnvelope("queued", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", None, None, receipt())
    with pytest.raises(ValueError, match="runtime changed"):
        asyncio.run(worker.run_worker(envelope))


def test_gpu_worker_without_receipt_is_forbidden(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr(sys, "executable", str(acceleration.GPU_PYTHON))
    envelope = worker.WorkerEnvelope("queued", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", None, None)
    with pytest.raises(worker.WorkerEnvelopeError, match="gpu-execution-receipt-required"):
        asyncio.run(worker.run_worker(envelope))
