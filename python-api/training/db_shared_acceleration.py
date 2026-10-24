"""Bounded local GPU acceleration without changing fixed feature/fit contracts.

The parent selects a pre-installed runtime before starting the durable worker.
The child verifies the immutable receipt before preparation or a lease. GPU
failure after that point is a job failure, never a silent CPU retry. All fit
overrides are process-local and restored. This module does not publish models,
change pointers, open a DB, install packages or touch forward collection.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "local-db-shared-execution-v1"
APP_PYTHON = ROOT / "python-api/.venv/Scripts/python.exe"
GPU_PYTHON = ROOT / ".local-runtime/gpu-validation/.venv/Scripts/python.exe"
APP_PACKAGES = ROOT / "python-api/.venv/Lib/site-packages"
DLL_PINS = {
    "cpu": "7e366d2e49cd061aac3ab21676b2f99b0c7a758dc3e888d4b23812af1b7d301c",
    "gpu": "e6c3260aa7bcd084b351a13bcd38dce77c1f10b637363d06a3f21554b7ddfefa",
}
MAX_CPU_NATIVE_THREADS = 8
MAX_GPU_FITS = 1
_RECEIPT = ContextVar("local_db_shared_execution", default=None)
_PROBE_CACHE = {}
_PROBE_LOCK = threading.RLock()
_FIT_LOCK = threading.RLock()


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _runtime_paths(device):
    interpreter = GPU_PYTHON if device == "gpu" else APP_PYTHON
    dll = interpreter.parent.parent / "Lib/site-packages/lightgbm/bin/lib_lightgbm.dll"
    return interpreter, dll


def get_compute_capability():
    """Cheap inventory only; resolve_runtime performs actual admission probe."""
    devices = {}
    for device in ("cpu", "gpu"):
        interpreter, dll = _runtime_paths(device)
        try:
            available = interpreter.is_file() and dll.is_file() and _sha(dll) == DLL_PINS[device]
        except OSError:
            available = False
        devices[device] = {"installed_and_pinned": available,
                           "actual_learner_probe_required": True}
    return {"schema": SCHEMA, "devices": devices, "default_device": "auto",
            "maximum_cpu_native_threads": MAX_CPU_NATIVE_THREADS,
            "maximum_concurrent_gpu_fits": MAX_GPU_FITS}


def _probe_runtime(device):
    interpreter, dll = _runtime_paths(device)
    if not interpreter.is_file() or not dll.is_file() or _sha(dll) != DLL_PINS[device]:
        raise ValueError(f"db-shared-{device}-runtime-unavailable-or-unpinned")
    key = (device, _sha(interpreter), _sha(dll), _sha(__file__))
    with _PROBE_LOCK:
        existing = _PROBE_CACHE.get(key)
        if existing and time.monotonic() - existing[0] < 600:
            return json.loads(json.dumps(existing[1]))
        creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
        try:
            completed = subprocess.run([str(interpreter), "-X", "utf8", str(Path(__file__)),
                "--probe", device], cwd=str(ROOT / "python-api"), text=True, encoding="utf-8",
                capture_output=True, timeout=45, creationflags=creationflags,
                env={**os.environ, "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2",
                     "MKL_NUM_THREADS": "2", "PYTHONUNBUFFERED": "1"})
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f"db-shared-{device}-runtime-probe-failed") from exc
        if completed.returncode != 0:
            # Never return arbitrary interpreter stdout/stderr or environment.
            raise ValueError(f"db-shared-{device}-runtime-probe-failed")
        try:
            record = json.loads(completed.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            raise ValueError("db-shared-runtime-probe-response-invalid") from exc
        if (record.get("device") != device or record.get("success") is not True
                or record.get("lightgbm_dll_sha256") != DLL_PINS[device]
                or record.get("python_sha256") != key[1]
                or record.get("acceleration_source_sha256") != key[3]
                or (device == "gpu" and not record.get("gpu_device_verified"))):
            raise ValueError("db-shared-runtime-probe-identity-invalid")
        _PROBE_CACHE[key] = (time.monotonic(), record)
        return json.loads(json.dumps(record))


def resolve_runtime(requested="auto", shared_pipeline=True):
    """Select before fit. Only auto may fall back, only before worker startup."""
    if requested not in {"auto", "cpu", "gpu"}:
        raise ValueError("db-shared-compute-device-invalid")
    if not shared_pipeline:
        if requested == "gpu":
            raise ValueError("db-shared-gpu-requires-shared-pipeline")
        return {"interpreter": str(APP_PYTHON), "execution_receipt": None}
    fallback = None
    device = "cpu" if requested == "cpu" else "gpu"
    try:
        probe = _probe_runtime(device)
    except ValueError as exc:
        if requested != "auto":
            raise
        fallback = str(exc)
        device, probe = "cpu", _probe_runtime("cpu")
    receipt = {"schema": SCHEMA, "requested_device": requested,
        "resolved_device": device, "fallback_reason": fallback,
        "fallback_before_fit_only": True, "maximum_cpu_native_threads": MAX_CPU_NATIVE_THREADS,
        "maximum_concurrent_gpu_fits": MAX_GPU_FITS, "probe": probe}
    receipt["receipt_sha256"] = _canonical_sha(receipt)
    return {"interpreter": probe["python"], "execution_receipt": receipt}


def configure_worker_imports(receipt):
    """GPU keeps its own libraries; missing API packages use fixed app venv.

    App site-packages are appended, never inserted before GPU libraries and
    never processed as executable .pth files. No machine/package mutation.
    """
    _validate_receipt_structure(receipt)
    if receipt["resolved_device"] == "gpu" and str(APP_PACKAGES) not in sys.path:
        sys.path.append(str(APP_PACKAGES))
    for directory in (ROOT / "keiba", ROOT / "python-api"):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))


def _validate_receipt_structure(receipt):
    if not isinstance(receipt, dict) or receipt.get("schema") != SCHEMA:
        raise ValueError("db-shared-execution-receipt-required")
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    if (receipt.get("receipt_sha256") != _canonical_sha(unsigned)
            or receipt.get("requested_device") not in {"auto", "cpu", "gpu"}
            or receipt.get("resolved_device") not in {"cpu", "gpu"}
            or receipt.get("maximum_cpu_native_threads") != MAX_CPU_NATIVE_THREADS
            or receipt.get("maximum_concurrent_gpu_fits") != MAX_GPU_FITS
            or receipt.get("fallback_before_fit_only") is not True
            or not isinstance(receipt.get("probe"), dict)):
        raise ValueError("db-shared-execution-receipt-invalid")
    requested, resolved = receipt["requested_device"], receipt["resolved_device"]
    fallback = receipt.get("fallback_reason")
    if (requested != "auto" and (requested != resolved or fallback is not None)
            or requested == "auto" and resolved == "cpu" and not isinstance(fallback, str)
            or resolved == "gpu" and fallback is not None):
        raise ValueError("db-shared-execution-fallback-policy-invalid")


def _current_identity(device):
    import lightgbm as lgb
    dll = Path(lgb.__file__).parent / "bin/lib_lightgbm.dll"
    packages = {name: importlib.metadata.version(name) for name in
                ("lightgbm", "pandas", "numpy", "scipy", "psutil", "joblib", "fastapi", "pydantic")}
    return {"device": device, "python": str(Path(sys.executable).resolve()),
        "python_sha256": _sha(sys.executable), "packages": packages,
        "lightgbm_dll": str(dll.resolve()), "lightgbm_dll_sha256": _sha(dll),
        "acceleration_source_sha256": _sha(__file__),
        "supplemental_app_packages": str(APP_PACKAGES) if device == "gpu" else None}


def validate_execution_receipt(receipt):
    """Child-side identity fence; must run before preparation/lease."""
    _validate_receipt_structure(receipt)
    device = receipt["resolved_device"]
    expected = receipt["probe"]
    actual = _current_identity(device)
    for key, value in actual.items():
        if expected.get(key) != value:
            raise ValueError(f"db-shared-worker-runtime-identity-mismatch:{key}")
    if actual["lightgbm_dll_sha256"] != DLL_PINS[device]:
        raise ValueError("db-shared-worker-runtime-unpinned")
    interpreter, dll = _runtime_paths(device)
    if (Path(actual["python"]).resolve() != interpreter.resolve()
            or Path(actual["lightgbm_dll"]).resolve() != dll.resolve()
            or expected.get("success") is not True
            or (device == "gpu" and expected.get("gpu_device_verified") is not True)):
        raise ValueError("db-shared-worker-runtime-admission-invalid")
    _RECEIPT.set(json.loads(json.dumps(receipt)))
    return json.loads(json.dumps(receipt))


def current_execution_receipt():
    value = _RECEIPT.get()
    return json.loads(json.dumps(value)) if value is not None else None


@contextmanager
def _fit_device(fit, device, records):
    """Only one fit context in this isolated process; bounded native work."""
    original_resource, original_train = fit._resource_limits, fit._train_booster
    import lightgbm as lgb
    import lightgbm.basic as basic
    logger_state = (basic._LOGGER, basic._INFO_METHOD_NAME, basic._WARNING_METHOD_NAME)
    messages = []
    class Logger:
        def info(self, message):
            if "GPU" in message or "kernel" in message:
                messages.append(str(message))
        def warning(self, message):
            if "GPU" in message:
                messages.append(str(message))
    def resources(threads, features):
        jobs, native = original_resource(threads, features)
        return (1, min(4, native * jobs)) if device == "gpu" else (jobs, native)
    def gpu_train(encoded, target, preset, *, n_threads, progress=None, progress_start=0):
        if not 1 <= n_threads <= 4:
            raise ValueError("db-shared-gpu-native-thread-budget-invalid")
        # LightGBM installs its existing ctypes log callback on the importing
        # thread. The normal worker fits inside asyncio.to_thread, while the
        # native logger is thread-local. Reuse the package-owned callback on
        # THIS fit thread before any Dataset/Booster native work; merely
        # register_logger() only changes the Python logger object.
        basic._safe_call(basic._LIB.LGBM_RegisterLogCallback(basic._LIB.callback))
        before = len(messages)
        clock = time.perf_counter()
        params = {"objective": "regression", "metric": "rmse", "learning_rate": .05,
            "num_leaves": preset["num_leaves"], "min_data_in_leaf": preset["min_data_in_leaf"],
            "lambda_l2": preset["lambda_l2"], "max_bin": 63, "feature_fraction": 1.0,
            "bagging_fraction": 1.0, "seed": 42, "data_random_seed": 42,
            "feature_fraction_seed": 42, "bagging_seed": 42, "deterministic": True,
            "force_col_wise": True, "device_type": "gpu", "num_threads": n_threads, "verbosity": 1}
        dataset = lgb.Dataset(encoded, label=target,
            categorical_feature=list(fit.CATEGORICAL_COLUMNS), free_raw_data=True)
        def callback(environment):
            if progress and environment.iteration % 20 == 0:
                progress(f"GPU {preset['id']}: 学習 {environment.iteration + 1}/{preset['num_boost_round']}",
                    min(progress_start + 15, progress_start + int(15 * (environment.iteration + 1) / preset['num_boost_round'])))
        model = lgb.train(params, dataset, num_boost_round=preset["num_boost_round"], callbacks=[callback])
        evidence = messages[before:]
        if not any("Using GPU Device" in message for message in evidence):
            raise ValueError("db-shared-gpu-actual-learner-not-verified")
        records.append({"preset": preset["id"], "stage": "candidate" if progress_start == 10 else "final",
            "seconds": time.perf_counter() - clock, "gpu_device_verified": True,
            "gpu_log_evidence": evidence, "native_threads": n_threads})
        return model
    try:
        fit._resource_limits = resources
        if device == "gpu":
            lgb.register_logger(Logger())
            fit._train_booster = gpu_train
        yield
    finally:
        fit._resource_limits, fit._train_booster = original_resource, original_train
        basic._LOGGER, basic._INFO_METHOD_NAME, basic._WARNING_METHOD_NAME = logger_state


def accelerated_fit_shared_model(target, features, *, compute_device="auto", execution_receipt=None,
                                 test_size=.2, n_threads=4, progress=None):
    """Same fixed 47 features, TRAIN selection/CAL/TEST metrics and source pins."""
    from training import db_shared_fit as fit
    if compute_device not in {"auto", "cpu", "gpu"}:
        raise ValueError("db-shared-compute-device-invalid")
    receipt = execution_receipt or current_execution_receipt()
    if receipt is None:
        raise ValueError("db-shared-bound-worker-execution-required")
    _validate_receipt_structure(receipt)
    # ContextVars are propagated by asyncio.to_thread. A caller may also pass
    # the already verified receipt explicitly; it is not a client request field.
    bound = current_execution_receipt()
    if bound is None or bound["receipt_sha256"] != receipt["receipt_sha256"]:
        raise ValueError("db-shared-unverified-worker-execution")
    device = receipt["resolved_device"]
    if compute_device != "auto" and compute_device != device:
        raise ValueError("db-shared-requested-bound-device-mismatch")
    before = fit.source_hashes()
    records = []
    clock = time.perf_counter()
    def notify(stage, percent):
        # The preserved fitter labels its scheduling message CPU. Translate
        # only that presentation message, not the fit or evaluation behavior.
        if device == "gpu" and stage.startswith("固定2候補を比較"):
            stage = "固定2候補を比較（GPU・順次実行）"
        if progress:
            progress(stage, percent)
    with _FIT_LOCK:
        with _fit_device(fit, device, records):
            bundle, result = fit.fit_shared_model(target, features, test_size=test_size,
                n_threads=n_threads, progress=notify)
    if fit.source_hashes() != before:
        raise ValueError("db-shared-acceleration-source-contract-changed")
    if device == "gpu" and (len(records) != 3 or not all(row["gpu_device_verified"] for row in records)):
        raise ValueError("db-shared-gpu-fit-evidence-incomplete")
    execution = {"schema": SCHEMA, "device": device, "requested_device": receipt["requested_device"],
        "fallback_reason": receipt["fallback_reason"], "receipt": receipt,
        "fit_pipeline_seconds": time.perf_counter() - clock,
        "gpu_fit_records": records, "gpu_device_verified": device == "gpu" and bool(records),
        "feature_formula_changed": False, "source_contract_changed": False,
        "partial_fit_cpu_fallback": False, "roi_evaluated": False}
    bundle["training_execution"] = execution
    result["training_execution"] = execution
    return bundle, result


def _probe_main(device):
    # Supplemental API dependencies are deliberately behind the isolated GPU
    # numerical libraries; runtime identities are reported and fenced later.
    if device == "gpu" and str(APP_PACKAGES) not in sys.path:
        sys.path.append(str(APP_PACKAGES))
    for directory in (ROOT / "keiba", ROOT / "python-api"):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    import numpy as np
    import lightgbm as lgb
    # Import the real entrypoint dependencies before declaring runtime ready.
    # Router import initializes no job/lease/model publication.
    from models import TrainRequest
    from routers import train
    messages = []
    if device == "gpu":
        class Logger:
            def info(self, message):
                if "GPU" in message:
                    messages.append(str(message))
            def warning(self, message):
                pass
        lgb.register_logger(Logger())
        random = np.random.default_rng(42)
        matrix = random.normal(size=(512, 8))
        target = matrix[:, 0] + .01 * random.normal(size=512)
        model = lgb.train({"objective": "regression", "device_type": "gpu", "max_bin": 63,
            "num_threads": 2, "verbosity": 1, "seed": 42}, lgb.Dataset(matrix, label=target), num_boost_round=2)
        if not np.isfinite(model.predict(matrix[:16], num_threads=2)).all():
            raise ValueError("db-shared-gpu-probe-finite-failed")
        if not any("Using GPU Device" in item for item in messages):
            raise ValueError("db-shared-gpu-probe-device-not-verified")
    identity = _current_identity(device)
    if identity["lightgbm_dll_sha256"] != DLL_PINS[device]:
        raise ValueError("db-shared-runtime-probe-unpinned")
    return {**identity, "success": True, "gpu_device_verified": device == "gpu",
            "gpu_log_evidence": messages, "normal_worker_imports_verified": True}


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--probe" or sys.argv[2] not in {"cpu", "gpu"}:
        raise SystemExit(2)
    print(json.dumps(_probe_main(sys.argv[2]), ensure_ascii=False, allow_nan=False), flush=True)
