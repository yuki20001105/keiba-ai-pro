"""Bounded, read-only process execution of the unchanged V3 cache parser.

Only cached_candidate is dispatched. Journal writes, fresh DB reads and the
second source parse before updates stay in the V3 driver's single parent.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
import hashlib
import importlib.util
import inspect
import multiprocessing
import os
from pathlib import Path
import sys
from threading import Lock
from types import CodeType

ROOT = Path(__file__).resolve().parents[1]
V3_PATH = ROOT / "scripts/repair_last3f_gaps_v3.py"
MAX_WORKERS = 3
MIN_AVAILABLE_BYTES = 4 * 1024 ** 3
PINNED_SOURCES = {
    "scripts/repair_last3f_gaps_v3.py": "09839fb7e7de22aa01759656ad90fa84307a1965fa0639e7bd4d54aaff7d3b2d",
    "scripts/last3f_evidence_v3.py": "d4bdb7d2146974e73276f8015ee110007147ad9698fa6a26a445b77bd77c54cc",
    "python-api/scraping/last3f_evidence.py": "f1f1963baae04c95a214392f73d106b0935c4038bf29ed495cd6703608ac03aa",
}
THREAD_ENV = (
    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS",
)
_WORKER_PARSER = None
_THREAD_LIMITER = None


def _check_memory_reserve():
    import psutil
    if psutil.virtual_memory().available < MIN_AVAILABLE_BYTES:
        raise MemoryError("last3f-cache-processes-require-four-gib-reserve")


def _check_source_pins():
    for relative, expected in PINNED_SOURCES.items():
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != expected:
            raise ValueError("last3f-cache-process-source-pin-changed:" + relative)


def _expected_candidate_code():
    compiled = compile(V3_PATH.read_bytes(), str(V3_PATH), "exec")
    return next(value for value in compiled.co_consts
                if isinstance(value, CodeType) and value.co_name == "cached_candidate")


def _initialize_worker():
    """Runs in spawned children only; parent environment/priority is untouched."""
    global _WORKER_PARSER, _THREAD_LIMITER
    sys.dont_write_bytecode = True
    for name in THREAD_ENV:
        os.environ[name] = "1"
    _check_memory_reserve()
    _check_source_pins()
    import psutil
    psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 10)
    # Spawn may import the parent's entry point before this initializer, so
    # constrain already-loaded native libraries as well as subsequent imports.
    from threadpoolctl import threadpool_limits
    _THREAD_LIMITER = threadpool_limits(limits=1)
    for directory in (ROOT / "scripts", ROOT / "python-api"):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    spec = importlib.util.spec_from_file_location("_last3f_cache_worker_v3", V3_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _WORKER_PARSER = module.cached_candidate
    _THREAD_LIMITER = threadpool_limits(limits=1)


def _parse_cached_target(target, cache_path):
    """Picklable entry point, no callable or mutable V3 module crosses processes."""
    _check_memory_reserve()
    _check_source_pins()
    if _WORKER_PARSER is None:
        raise RuntimeError("last3f-cache-worker-not-initialized")
    return _WORKER_PARSER(target, cache_path)


class ProcessCacheExecutorV4:
    """V3 ThreadPoolExecutor replacement with at most three pending tasks.

    Caller must retain the V3 bounded producer and single writer. Results are
    the complete V3 objects, including original bytes/HTML/evidence/receipts.
    This class intentionally has no network, write or generic map interface.
    """

    def __init__(self, max_workers=MAX_WORKERS):
        if type(max_workers) is not int or not 1 <= max_workers <= MAX_WORKERS:
            raise ValueError("last3f-cache-workers-must-be-one-through-three")
        _check_memory_reserve()
        _check_source_pins()
        self._candidate_code = _expected_candidate_code()
        self._max_workers = max_workers
        self._pending = set()
        self._state_lock = Lock()
        self._closed = False
        self._pool = ProcessPoolExecutor(
            max_workers=max_workers,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=_initialize_worker,
        )

    def submit(self, fn, target, cache_path):
        if (not inspect.isfunction(fn) or fn.__name__ != "cached_candidate"
                or Path(fn.__code__.co_filename).resolve() != V3_PATH.resolve()
                or fn.__code__ != self._candidate_code):
            raise ValueError("last3f-cache-only-pinned-v3-cached-candidate-allowed")
        _check_source_pins()
        _check_memory_reserve()
        path = Path(cache_path).resolve(strict=True)
        if not path.is_file():
            raise ValueError("last3f-cache-existing-database-required")
        with self._state_lock:
            if self._closed:
                raise RuntimeError("last3f-cache-process-executor-shut-down")
            # result() can return before a Future's done callbacks run. Prune
            # completed futures directly so the V3 three-item producer cannot
            # hit a false queue-full error at that boundary.
            self._pending = {future for future in self._pending if not future.done()}
            if len(self._pending) >= self._max_workers:
                raise RuntimeError("last3f-cache-bounded-queue-full")
            future = self._pool.submit(_parse_cached_target, target, str(path))
            self._pending.add(future)
        return future

    def shutdown(self, wait=True, *, cancel_futures=False):
        with self._state_lock:
            self._closed = True
        self._pool.shutdown(wait=wait, cancel_futures=cancel_futures)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.shutdown(wait=True, cancel_futures=exc_type is not None)
        return False
