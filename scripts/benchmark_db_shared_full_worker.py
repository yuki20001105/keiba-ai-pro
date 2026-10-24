"""Isolated full-period timing worker; never publish, activate or access a server.

Fresh-process CLI: --request request.json --output new-run/mode.json. Only the
immutable supplied snapshot is read. CPU scheduling is overridden in memory,
then restored; the shared formulas and their source contracts stay untouched.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import gc
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "reports/generated"
CACHE_SCHEMA = "full-db-shared-raw47-cache-v1"
HISTORY_POLICY = "source_race_date < target_race_date; same-day outcomes excluded"
REQUEST_FIELDS = {"mode", "device", "workers", "threads", "input_mode", "snapshot_path",
                  "snapshot_sha256", "period", "cache_dir", "expected_source_hashes", "full_row_timing"}


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _modules():
    for directory in (ROOT / "python-api", ROOT / "keiba"):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    from training import db_shared_features, db_shared_fit
    return db_shared_features, db_shared_fit


def _safe_json(value):
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    if hasattr(value, "item"):
        value = value.item()
    return None if isinstance(value, float) and not math.isfinite(value) else value


def _write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(_safe_json(value), stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)


def validate_request(request, output):
    if not isinstance(request, dict) or set(request) != REQUEST_FIELDS:
        raise ValueError("full-benchmark-request-schema-invalid")
    if (request["device"] not in {"cpu", "gpu"} or request["input_mode"] not in {"cold", "cache"}
            or type(request["workers"]) is not int or request["workers"] not in (1, 2)
            or type(request["threads"]) is not int or request["threads"] not in (2, 4)
            or type(request["full_row_timing"]) is not bool
            or request["period"] != ["2016-01", "2026-09"]
            or not isinstance(request["mode"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request["mode"])
            or (request["device"] == "gpu" and request["workers"] != 1)):
        raise ValueError("full-benchmark-fixed-conditions-invalid")
    hashes = request["expected_source_hashes"]
    if (not isinstance(hashes, dict) or not hashes or any(not isinstance(key, str)
            or not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for key, value in hashes.items())
            or not isinstance(request["snapshot_sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", request["snapshot_sha256"])):
        raise ValueError("full-benchmark-required-hashes-invalid")
    snapshot = Path(request["snapshot_path"]).resolve(strict=True)
    cache = Path(request["cache_dir"]).resolve()
    destination = Path(output).resolve()
    boundary = OUTPUT_ROOT.resolve()
    if (not destination.is_relative_to(boundary) or not cache.is_relative_to(boundary)
            or cache == boundary or cache.parent == boundary or destination.parent != cache.parent
            or destination.suffix != ".json" or destination.exists()
            or not destination.parent.is_dir() or snapshot.is_relative_to(cache)):
        raise ValueError("full-benchmark-new-run-output-boundary-invalid")
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(snapshot) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError("full-benchmark-immutable-checkpointed-snapshot-required")
    for suffix in (".vectors.npz", ".model.joblib"):
        if destination.with_suffix(suffix).exists():
            raise ValueError("full-benchmark-artifact-already-exists")
    return snapshot, cache, destination


def cache_key(request, fit):
    return {"schema": CACHE_SCHEMA, "snapshot_sha256": request["snapshot_sha256"],
            "period": request["period"], "source_hashes": request["expected_source_hashes"],
            "feature_columns": list(fit.FEATURE_COLUMNS),
            "categorical_columns": list(fit.CATEGORICAL_COLUMNS), "history_policy": HISTORY_POLICY}


def _frame_receipt(frame, path, fit):
    return {"path": path.name, "sha256": file_hash(path), "frame_digest": fit._frame_digest(frame),
            "rows": len(frame), "columns": list(frame.columns),
            "dtypes": [str(dtype) for dtype in frame.dtypes]}


def read_cache(cache, request, fit):
    import pandas as pd
    manifest_path = cache / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    expected = cache_key(request, fit)
    if (manifest.get("schema") != CACHE_SCHEMA or manifest.get("key_payload") != expected
            or manifest.get("key_sha256") != canonical_hash(expected)
            or manifest.get("snapshot_sha256") != request["snapshot_sha256"]
            or manifest.get("source_hashes") != request["expected_source_hashes"]
            or manifest.get("period") != request["period"]
            or manifest.get("history_policy") != HISTORY_POLICY
            or manifest.get("feature_columns") != list(fit.FEATURE_COLUMNS)
            or manifest.get("categorical_columns") != list(fit.CATEGORICAL_COLUMNS)
            or manifest.get("point_in_time_audit", {}).get("same_day_or_future_history_used") is not False
            or manifest.get("point_in_time_audit", {}).get("current_outcomes_consumed") is not False):
        raise ValueError("full-benchmark-cache-lineage-mismatch")
    frames = {}
    for name in ("target", "features"):
        receipt = manifest["files"][name]
        if receipt.get("path") != f"{name}.parquet":
            raise ValueError("full-benchmark-cache-path-invalid")
        path = cache / receipt["path"]
        if file_hash(path) != receipt["sha256"]:
            raise ValueError("full-benchmark-cache-file-hash-mismatch")
        frame = pd.read_parquet(path)
        if _frame_receipt(frame, path, fit) != receipt:
            raise ValueError("full-benchmark-cache-frame-mismatch")
        frames[name] = frame
    if list(frames["features"].columns) != list(fit.FEATURE_COLUMNS):
        raise ValueError("full-benchmark-cache-feature-order-mismatch")
    target, features = fit._prepare(frames["target"], frames["features"])
    if (fit._frame_digest(target) != manifest["files"]["target"]["frame_digest"]
            or fit._frame_digest(features) != manifest["files"]["features"]["frame_digest"]):
        raise ValueError("full-benchmark-cache-order-mismatch")
    if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != hashlib.sha256(manifest_bytes).hexdigest():
        raise ValueError("full-benchmark-cache-manifest-mutated")
    features.attrs.update(manifest["point_in_time_audit"])
    return target, features, manifest


def create_cache(cache, request, target, features, coverage, fit):
    if (cache / "manifest.json").exists():
        old_target, old_features, manifest = read_cache(cache, request, fit)
        if (fit._frame_digest(old_target) != fit._frame_digest(target)
                or fit._frame_digest(old_features) != fit._frame_digest(features)):
            raise ValueError("full-benchmark-regenerated-cache-disagrees")
        return manifest, False
    cache.mkdir(parents=True, exist_ok=True)
    if any(cache.iterdir()):
        raise ValueError("full-benchmark-cache-incomplete-or-nonempty")
    audit = {"current_outcomes_consumed": features.attrs.get("current_outcomes_consumed"),
             "same_day_or_future_history_used": features.attrs.get("same_day_or_future_history_used"),
             "point_in_time_audit": features.attrs.get("point_in_time_audit")}
    if audit["current_outcomes_consumed"] is not False or audit["same_day_or_future_history_used"] is not False:
        raise ValueError("full-benchmark-feature-pit-audit-required")
    files = {}
    for name, frame in (("target", target), ("features", features)):
        path = cache / f"{name}.parquet"
        # Lineage lives in the checked manifest, not permissive DataFrame
        # attrs JSON; do not duplicate arbitrary attrs in parquet metadata.
        stored = frame.copy()
        stored.attrs = {}
        with path.open("xb") as stream:
            stored.to_parquet(stream, index=False)
        files[name] = _frame_receipt(frame, path, fit)
    key = cache_key(request, fit)
    manifest = {**key, "key_payload": key, "key_sha256": canonical_hash(key), "files": files,
                "coverage": coverage, "point_in_time_audit": audit,
                "actual_date_from": str(target.race_date.min()), "actual_date_to": str(target.race_date.max()),
                "race_count": int(target.race_id.nunique()), "runner_count": len(target)}
    _write_json(cache / "manifest.json", manifest)
    return manifest, True


def resource_gate(request, features):
    import psutil
    physical = psutil.cpu_count(logical=False)
    available = int(psutil.virtual_memory().available)
    needed = request["workers"] * max(512 * 1024**2, int(features.memory_usage(deep=True).sum()) * 6) + 2 * 1024**3
    if physical is None or physical < request["workers"] * request["threads"] or available < needed:
        raise ValueError("full-benchmark-fixed-resource-budget-unavailable")
    return {"physical_cores": physical, "logical_cores": psutil.cpu_count(),
            "available_memory_bytes": available, "estimated_required_memory_bytes": needed}


class FitInstrumentation:
    def __init__(self, request, fit):
        self.request, self.fit = request, fit
        self.records, self.vectors, self.progress = [], {}, []
        self._lock = threading.Lock()
        self._models = {}
        self.started = time.perf_counter()
        self.gpu_messages = []

    def notify(self, message, percent):
        row = {"message": message, "percent": percent, "at_seconds": time.perf_counter() - self.started}
        with self._lock:
            self.progress.append(row)
        if percent in (5, 10, 30, 55, 70, 95):
            print(json.dumps({"mode": self.request["mode"], "stage": message, "percent": percent}, ensure_ascii=False), flush=True)

    @contextmanager
    def install(self):
        fit = self.fit
        names = ("_resource_limits", "_train_booster", "_finite_predictions", "fit_temperature", "probability_metrics", "accuracy_metrics")
        originals = {name: getattr(fit, name) for name in names}
        def timed(name, callable_, *args, **kwargs):
            start = time.perf_counter()
            value = callable_(*args, **kwargs)
            with self._lock:
                self.records.append({"kind": name, "start_seconds": start - self.started,
                                     "seconds": time.perf_counter() - start})
            return value
        def train(matrix, labels, preset, *, n_threads, progress=None, progress_start=0):
            stage = "candidate" if progress_start == 10 else "final"
            role = f"inner_{preset['id']}" if stage == "candidate" else "final"
            start = time.perf_counter()
            if self.request["device"] == "cpu":
                model = originals["_train_booster"](matrix, labels, preset, n_threads=n_threads,
                                                    progress=progress, progress_start=progress_start)
            else:
                import lightgbm as lgb
                params = {"objective": "regression", "metric": "rmse", "learning_rate": .05,
                          "num_leaves": preset["num_leaves"], "min_data_in_leaf": preset["min_data_in_leaf"],
                          "lambda_l2": preset["lambda_l2"], "max_bin": 63, "feature_fraction": 1.0,
                          "bagging_fraction": 1.0, "seed": 42, "data_random_seed": 42,
                          "feature_fraction_seed": 42, "bagging_seed": 42, "deterministic": True,
                          "force_col_wise": True, "device_type": "gpu", "num_threads": n_threads, "verbosity": 1}
                data = lgb.Dataset(matrix, label=labels, categorical_feature=list(fit.CATEGORICAL_COLUMNS), free_raw_data=True)
                model = lgb.train(params, data, num_boost_round=preset["num_boost_round"])
            with self._lock:
                self._models[id(model)] = {"role": role, "predictions": 0}
                self.records.append({"kind": "booster", "role": role, "preset": preset["id"],
                                     "native_threads": n_threads, "start_seconds": start - self.started,
                                     "seconds": time.perf_counter() - start})
            return model
        def predict(model, matrix, n_threads):
            start = time.perf_counter()
            values = originals["_finite_predictions"](model, matrix, n_threads)
            with self._lock:
                model_info = self._models[id(model)]
                model_info["predictions"] += 1
                role = model_info["role"]
                if role == "final":
                    role = "cal" if model_info["predictions"] == 1 else "test"
                if role in self.vectors:
                    raise ValueError("full-benchmark-duplicate-prediction-vector")
                self.vectors[role] = values.copy()
                self.records.append({"kind": "inference", "role": role, "rows": len(matrix),
                                     "start_seconds": start - self.started, "seconds": time.perf_counter() - start})
            return values
        fit._resource_limits = lambda _threads, _features: (self.request["workers"], self.request["threads"])
        fit._train_booster, fit._finite_predictions = train, predict
        for name in ("fit_temperature", "probability_metrics", "accuracy_metrics"):
            original = originals[name]
            setattr(fit, name, lambda *args, _name=name, _original=original, **kwargs: timed(_name, _original, *args, **kwargs))
        try:
            if self.request["device"] == "gpu":
                import lightgbm as lgb
                owner = self
                class Logger:
                    def info(self, message):
                        if any(word in message for word in ("GPU", "kernel")):
                            owner.gpu_messages.append(message)
                    def warning(self, message):
                        owner.gpu_messages.append(message)
                lgb.register_logger(Logger())
            yield
        finally:
            for name, original in originals.items():
                setattr(fit, name, original)


def full_row_probe(target, features, fit, request):
    import numpy as np
    start = time.perf_counter()
    encoder, encoded, audit = fit._fitted_fixed_encoder(features)
    baseline = fit.fit_speed_deviation_baseline(target)
    labels = fit.apply_speed_deviation_baseline(target, baseline).to_numpy()
    prepared = time.perf_counter()
    instrument = FitInstrumentation(request, fit)
    with instrument.install():
        model = fit._train_booster(encoded, labels, dict(fit.PRESETS[1]), n_threads=request["threads"])
    fitted = time.perf_counter()
    predicted = np.asarray(model.predict(encoded.iloc[:32], num_threads=request["threads"]), dtype=float)
    if predicted.shape != (min(32, len(encoded)),) or not np.isfinite(predicted).all():
        raise ValueError("full-benchmark-all-row-finite-probe-failed")
    return {"runner_count": len(target), "race_count": int(target.race_id.nunique()),
            "period": fit._partition_receipt(target), "preset": dict(fit.PRESETS[1]),
            "prepare_seconds": prepared - start, "booster_seconds": fitted - prepared,
            "finite_probe_seconds": time.perf_counter() - fitted, "total_seconds": time.perf_counter() - start,
            "finite_sample_count": len(predicted), "finite_predictions": True,
            "independent_test_evaluated": False, "calibrated": False, "in_sample_metrics_reported": False,
            "model_saved": False, "encoder_audit": audit, "gpu_log_evidence": instrument.gpu_messages}


def run_worker(request, output):
    import numpy as np
    import joblib
    started = time.perf_counter()
    snapshot, cache, destination = validate_request(request, output)
    api_features, fit = _modules()
    if fit.source_hashes() != request["expected_source_hashes"]:
        raise ValueError("full-benchmark-source-hash-mismatch")
    report = {"schema": "full-db-shared-benchmark-worker-v1", "request": request, "status": "running",
              "python": sys.executable, "script_sha256": file_hash(__file__), "timings": {},
              "artifact_published": False, "active_model_changed": False, "forward_collector_accessed": False,
              "roi_evaluated": False, "live_or_production_accessed": False}
    clock = time.perf_counter()
    try:
        if file_hash(snapshot) != request["snapshot_sha256"]:
            raise ValueError("full-benchmark-snapshot-hash-mismatch")
        report["timings"]["initial_snapshot_hash_seconds"] = time.perf_counter() - clock
        clock = time.perf_counter()
        if request["input_mode"] == "cold":
            target, history, coverage = api_features.load_db_frames(snapshot, *request["period"])
            report["timings"]["db_read_seconds"] = time.perf_counter() - clock
            clock = time.perf_counter()
            features = api_features.build_training_features(target, history)
            del history
            report["timings"]["feature_generation_seconds"] = time.perf_counter() - clock
            clock = time.perf_counter()
            target, features = fit._prepare(target, features)
            report["timings"]["prepare_validation_seconds"] = time.perf_counter() - clock
            clock = time.perf_counter()
            manifest, created = create_cache(cache, request, target, features, coverage, fit)
            report["timings"]["cache_create_or_compare_seconds"] = time.perf_counter() - clock
            report["cache_created"] = created
        else:
            target, features, manifest = read_cache(cache, request, fit)
            report["timings"]["cache_validate_and_read_seconds"] = time.perf_counter() - clock
            report["cache_created"] = False
        report["cache_manifest_sha256"] = file_hash(cache / "manifest.json")
        report["cache_key_sha256"] = manifest["key_sha256"]
        report["cohort"] = {"runner_count": len(target), "race_count": int(target.race_id.nunique()),
                            "date_from": str(target.race_date.min()), "date_to": str(target.race_date.max())}
        report["input_hashes"] = {"target": fit._frame_digest(target), "raw47": fit._frame_digest(features)}
        report["resources"] = resource_gate(request, features)
        instrument = FitInstrumentation(request, fit)
        clock, cpu_clock = time.perf_counter(), time.process_time()
        with instrument.install():
            # Both four-thread-budget modes use four threads for the single
            # final fit. Only their two-candidate scheduling differs.
            final_budget = min(4, request["threads"] * request["workers"])
            bundle, result = fit.fit_shared_model(target, features, test_size=.2,
                                                 n_threads=final_budget, progress=instrument.notify)
        report["timings"]["fit_selection_calibration_evaluation_seconds"] = time.perf_counter() - clock
        report["fit_cpu_seconds"] = time.process_time() - cpu_clock
        report["fit_result"], report["fit_stages"], report["progress"] = result, instrument.records, instrument.progress
        report["gpu_log_evidence"] = instrument.gpu_messages
        report["gpu_learner_confirmed"] = any("Using GPU Device" in message for message in instrument.gpu_messages)
        if request["device"] == "gpu" and not report["gpu_learner_confirmed"]:
            raise ValueError("full-benchmark-gpu-learner-not-confirmed")
        if set(instrument.vectors) != {"inner_lean", "inner_standard", "cal", "test"}:
            raise ValueError("full-benchmark-complete-prediction-vectors-required")
        clock = time.perf_counter()
        vector_path = destination.with_suffix(".vectors.npz")
        with vector_path.open("xb") as stream:
            np.savez_compressed(stream, **instrument.vectors)
        report["vectors"] = {"path": str(vector_path), "sha256": file_hash(vector_path),
                             "lengths": {name: len(values) for name, values in instrument.vectors.items()}}
        report["timings"]["vector_evidence_save_seconds"] = time.perf_counter() - clock
        clock = time.perf_counter()
        artifact = destination.with_suffix(".model.joblib")
        with artifact.open("xb") as stream:
            joblib.dump(bundle, stream)
        digest = file_hash(artifact)
        report["timings"]["model_serialize_and_hash_seconds"] = time.perf_counter() - clock
        clock = time.perf_counter()
        restored = joblib.load(artifact)
        encoded = restored["encoder"].transform(features.iloc[:32])
        left = fit._finite_predictions(bundle["model"], encoded, request["threads"])
        right = fit._finite_predictions(restored["model"], encoded, request["threads"])
        if not np.allclose(left, right, rtol=0, atol=1e-10) or file_hash(artifact) != digest:
            raise ValueError("full-benchmark-model-save-reload-parity-failed")
        report["artifact"] = {"path": str(artifact), "sha256": digest, "hash_unchanged_after_reload": True,
                              "finite_reload_sample": len(right), "max_abs_reload_prediction_difference": float(np.max(np.abs(left - right)))}
        report["timings"]["model_reload_and_verify_seconds"] = time.perf_counter() - clock
        preparation_names = (("db_read_seconds", "feature_generation_seconds", "prepare_validation_seconds")
                             if request["input_mode"] == "cold" else ("cache_validate_and_read_seconds",))
        report["timings"]["run_processing_seconds"] = sum(report["timings"].get(name, 0.0) for name in
            (*preparation_names, "fit_selection_calibration_evaluation_seconds",
             "model_serialize_and_hash_seconds", "model_reload_and_verify_seconds"))
        report["processing_scope"] = "input prep + TRAIN selection/final fit + CAL + TEST + isolated model serialize/reload; excludes imports/guards/raw47 cache creation/NPZ evidence"
        del bundle, restored
        gc.collect()
        report["timings"]["pipeline_elapsed_before_optional_all_rows_seconds"] = time.perf_counter() - started
        if request["full_row_timing"]:
            report["full_row_timing"] = full_row_probe(target, features, fit, request)
        clock = time.perf_counter()
        if (fit.source_hashes() != request["expected_source_hashes"]
                or file_hash(snapshot) != request["snapshot_sha256"]
                or file_hash(cache / "manifest.json") != report["cache_manifest_sha256"]
                or fit._frame_digest(target) != report["input_hashes"]["target"]
                or fit._frame_digest(features) != report["input_hashes"]["raw47"]):
            raise ValueError("full-benchmark-source-snapshot-cache-or-input-mutated")
        report["timings"]["final_integrity_check_seconds"] = time.perf_counter() - clock
        report.update(status="completed", source_unchanged=True, snapshot_unchanged=True,
                      cache_manifest_unchanged=True, prepared_inputs_unchanged=True)
    except Exception as error:
        report.update(status="failed", error={"type": type(error).__name__, "message": str(error)})
        raise
    finally:
        report["timings"]["worker_wall_seconds"] = time.perf_counter() - started
        _write_json(destination, report)
        print(json.dumps({"mode": request["mode"], "status": report["status"], "report": str(destination)}, ensure_ascii=False), flush=True)
    return report


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    run_worker(request, args.output)


if __name__ == "__main__":
    main()
