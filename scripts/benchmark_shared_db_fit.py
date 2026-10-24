"""Same-quality CPU benchmark of the new fixed DB candidate comparison.

No publication, activation, API, forward collection, purchases or network.
Only a supplied immutable SQLite snapshot is read; reports get a fresh folder.
CAL/TEST outcomes are never scored or used to alter presets in this benchmark.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "reports/generated/shared-db-fit-benchmarks"
DATE_FROM, DATE_TO, TEST_SIZE = "2024-01", "2025-12", 0.20
TOLERANCE = 1e-10
MODES = {
    "A": {"label": "sequential_4_threads", "workers": 1, "threads_per_fit": 4, "total_native_threads": 4},
    "B": {"label": "parallel_2_fits_2_threads_each", "workers": 2, "threads_per_fit": 2, "total_native_threads": 4},
    "C": {"label": "parallel_2_fits_4_threads_each", "workers": 2, "threads_per_fit": 4, "total_native_threads": 8},
}


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _modules():
    # A fresh CLI process owns its imports and the fit contract's source pins.
    for directory in (ROOT / "python-api", ROOT / "keiba"):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    from training import db_shared_features, db_shared_fit
    return db_shared_features, db_shared_fit


def resource_probe():
    try:
        import psutil
        return {"probe": "psutil", "physical_cores": psutil.cpu_count(logical=False),
                "logical_cores": psutil.cpu_count(), "available_memory_bytes": int(psutil.virtual_memory().available)}
    except (ImportError, OSError, AttributeError):
        return {"probe": "unavailable", "physical_cores": None,
                "logical_cores": os.cpu_count(), "available_memory_bytes": None}


def mode_permission(mode, encoded_bytes, resources):
    config = MODES[mode]
    per_fit = max(512 * 1024**2, int(encoded_bytes) * 6)
    needed = config["workers"] * per_fit + 2 * 1024**3
    physical, available = resources.get("physical_cores"), resources.get("available_memory_bytes")
    if config["workers"] == 2 and (physical is None or available is None):
        return False, "parallel_resource_probe_unavailable"
    cores = physical if physical is not None else resources.get("logical_cores")
    if cores is not None and cores < config["total_native_threads"]:
        return False, "fixed_thread_budget_exceeds_available_cores"
    if available is not None and available < needed:
        return False, "insufficient_available_memory"
    return True, None


class MemorySampler:
    """Optional peak-process RSS; CPU seconds use the stdlib independently."""
    def __init__(self):
        self.peak_rss_bytes = None
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        try:
            import psutil
            process = psutil.Process()
            self.peak_rss_bytes = int(process.memory_info().rss)
            def sample():
                while not self._stop.wait(0.05):
                    try:
                        self.peak_rss_bytes = max(self.peak_rss_bytes, int(process.memory_info().rss))
                    except (OSError, psutil.Error):
                        break
            self._thread = threading.Thread(target=sample, name="fit-benchmark-rss", daemon=True)
            self._thread.start()
        except (ImportError, OSError, AttributeError):
            pass
        return self

    def __exit__(self, *_exc):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)


def prepare_inputs(snapshot, api_features, fit):
    import numpy as np
    started = time.perf_counter()
    clock = started
    target, history, coverage = api_features.load_db_frames(snapshot, DATE_FROM, DATE_TO)
    load_seconds = time.perf_counter() - clock
    clock = time.perf_counter()
    raw = api_features.build_training_features(target, history)
    del history
    features_seconds = time.perf_counter() - clock
    clock = time.perf_counter()
    target, inputs = fit._prepare(target, raw)
    splits = fit.date_partitions(target, test_size=TEST_SIZE)
    training = target.iloc[splits["TRAIN"]].reset_index(drop=True)
    training_inputs = inputs.iloc[splits["TRAIN"]].reset_index(drop=True)
    train_dates = np.sort(training.race_date.astype(str).unique())
    boundary = max(2, min(len(train_dates) - 1, int(math.floor(len(train_dates) * 0.8))))
    inner_mask = training.race_date.astype(str).isin(train_dates[:boundary]).to_numpy()
    inner_train = training.loc[inner_mask].reset_index(drop=True)
    inner_validation = training.loc[~inner_mask].reset_index(drop=True)
    encoder, encoded, encoder_audit = fit._fitted_fixed_encoder(training_inputs.loc[inner_mask].reset_index(drop=True))
    validation_encoded = encoder.transform(training_inputs.loc[~inner_mask].reset_index(drop=True))
    baseline = fit.fit_speed_deviation_baseline(inner_train)
    labels = fit.apply_speed_deviation_baseline(inner_train, baseline).to_numpy()
    preparation = {
        "load_seconds": load_seconds, "feature_generation_seconds": features_seconds,
        "partition_encoding_baseline_seconds": time.perf_counter() - clock,
        "total_seconds": time.perf_counter() - started,
        "period": [DATE_FROM, DATE_TO], "test_size": TEST_SIZE,
        "selection": "TRAIN-only inner 80% chronological train / remaining validation; same as fit_shared_model",
        "inner_train": fit._partition_receipt(inner_train), "inner_validation": fit._partition_receipt(inner_validation),
        "raw47_sha256": fit._frame_digest(raw), "encoded_train_sha256": fit._frame_digest(encoded),
        "encoded_validation_sha256": fit._frame_digest(validation_encoded),
        "labels_sha256": hashlib.sha256(labels.tobytes()).hexdigest(),
        "baseline_sha256": hashlib.sha256(json.dumps(baseline, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
        "encoder_audit": encoder_audit, "coverage": coverage,
        "data_prepared_once": True, "cal_test_scored": False, "roi_evaluated": False,
    }
    del target, inputs, raw, training, training_inputs
    gc.collect()
    return {"train": encoded, "validation": validation_encoded,
            "validation_target": inner_validation, "labels": labels, "baseline": baseline}, preparation


def prepared_hashes(prepared, fit):
    return {"train": fit._frame_digest(prepared["train"]),
            "validation": fit._frame_digest(prepared["validation"]),
            "labels": hashlib.sha256(prepared["labels"].tobytes()).hexdigest(),
            "baseline": hashlib.sha256(json.dumps(prepared["baseline"], sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()}


def selected_preset(trials):
    best_rho = max(row["metrics"]["rank_correlation"] for row in trials)
    shortlist = [row for row in trials if best_rho - row["metrics"]["rank_correlation"] <= 0.002]
    return min(shortlist, key=lambda row: (row["metrics"]["rmse_speed_mps"],
                                          row["preset"]["num_boost_round"], row["preset"]["id"]))["preset"]["id"]


def execute_mode(mode, prepared, fit):
    from threadpoolctl import threadpool_limits
    config = MODES[mode]
    # Keep process-wide BLAS/OMP limits outside parallel Python tasks. The
    # booster still receives its per-fit native thread budget explicitly.
    def trial(preset):
        clock = time.perf_counter()
        model = fit._train_booster(prepared["train"], prepared["labels"], preset,
                                  n_threads=config["threads_per_fit"])
        fit_seconds = time.perf_counter() - clock
        clock = time.perf_counter()
        prediction = fit._finite_predictions(model, prepared["validation"], config["threads_per_fit"])
        predict_seconds = time.perf_counter() - clock
        clock = time.perf_counter()
        metrics = fit.accuracy_metrics(prepared["validation_target"], prediction, prepared["baseline"])
        evaluation_seconds = time.perf_counter() - clock
        return {"preset": dict(preset), "metrics": metrics, "fit_seconds": fit_seconds,
                "inference_seconds": predict_seconds, "evaluation_seconds": evaluation_seconds,
                "predictions": prediction}
    start, cpu_start = time.perf_counter(), time.process_time()
    with MemorySampler() as memory, threadpool_limits(limits=config["total_native_threads"]):
        if config["workers"] == 1:
            trials = [trial(preset) for preset in fit.PRESETS]
        else:
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="shared-fit-benchmark") as pool:
                trials = list(pool.map(trial, fit.PRESETS))
    elapsed, cpu_seconds = time.perf_counter() - start, time.process_time() - cpu_start
    return {"mode": mode, **config, "candidate_comparison_seconds": elapsed,
            "cpu_seconds": cpu_seconds, "average_cpu_core_equivalents": cpu_seconds / elapsed if elapsed else None,
            "peak_process_rss_bytes": memory.peak_rss_bytes,
            "selected_preset": selected_preset(trials), "trials": trials}


def verify_quality(reference, actual):
    import numpy as np
    expected = {trial["preset"]["id"]: trial for trial in reference["trials"]}
    differences = []
    for trial in actual["trials"]:
        base = expected.get(trial["preset"]["id"])
        if base is None:
            return {"passed": False, "reason": "preset_mismatch"}
        left, right = base["predictions"], trial["predictions"]
        if left.shape != right.shape or not np.isfinite(left).all() or not np.isfinite(right).all():
            return {"passed": False, "reason": "prediction_shape_or_finiteness_mismatch"}
        maximum = float(np.max(np.abs(left - right))) if left.size else 0.0
        rho = abs(base["metrics"]["rank_correlation"] - trial["metrics"]["rank_correlation"])
        rmse = abs(base["metrics"]["rmse_speed_mps"] - trial["metrics"]["rmse_speed_mps"])
        differences.append({"preset": trial["preset"]["id"], "max_abs_prediction_difference": maximum,
                            "rank_correlation_difference": rho, "rmse_speed_mps_difference": rmse,
                            "passed": maximum <= TOLERANCE and rho <= TOLERANCE and rmse <= TOLERANCE})
    passed = len(differences) == len(expected) and all(row["passed"] for row in differences)
    same_selection = actual["selected_preset"] == reference["selected_preset"]
    return {"passed": passed and same_selection, "same_selected_preset": same_selection,
            "tolerance": TOLERANCE, "preset_differences": differences}


def summary_results(runs, preparation_seconds):
    import statistics
    groups = {mode: [row for row in runs if row["mode"] == mode] for mode in MODES}
    reference = groups["A"]
    baseline = statistics.median(row["candidate_comparison_seconds"] for row in reference) if reference else None
    quality_reference = bool(reference and all(row["quality"]["passed"] for row in reference))
    summaries = {}
    for mode, values in groups.items():
        if not values:
            continue
        elapsed = statistics.median(row["candidate_comparison_seconds"] for row in values)
        valid = quality_reference and all(row["quality"]["passed"] for row in values)
        summaries[mode] = {
            **MODES[mode], "repetitions": len(values), "median_candidate_comparison_seconds": elapsed,
            "quality_equivalence_passed": bool(valid), "speedup_valid": bool(valid),
            "candidate_comparison_speedup_vs_A": baseline / elapsed if valid and elapsed and baseline is not None else None,
            "prepared_plus_candidate_seconds": preparation_seconds + elapsed,
            "prepared_plus_candidate_speedup_vs_A": ((preparation_seconds + baseline) / (preparation_seconds + elapsed)
                                                     if valid and baseline is not None else None),
            "comparison_scope": "same total native budget" if mode in {"A", "B"} else "additional CPU utilization, not pure parallel effect",
            "full_training_pipeline_measured": False,
        }
    return summaries


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "item"):
        value = value.item()
    return None if isinstance(value, float) and not math.isfinite(value) else value


def run_benchmark(snapshot, *, repeats=2, modes=("A", "B", "C"), expected_sha256=None, output_root=None):
    if repeats not in (1, 2) or not modes or "A" not in modes or len(set(modes)) != len(modes) or any(mode not in MODES for mode in modes):
        raise ValueError("fixed-benchmark-modes-or-repeats-invalid")
    snapshot = Path(snapshot).resolve(strict=True)
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(snapshot) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError("benchmark-needs-immutable-checkpointed-snapshot")
    digest = file_hash(snapshot)
    if expected_sha256 is not None and digest != expected_sha256.lower():
        raise ValueError("benchmark-snapshot-sha256-mismatch")
    api_features, fit = _modules()
    sources = fit.source_hashes()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
    output = Path(output_root or OUTPUT_ROOT).resolve() / stamp
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema": "shared-db-fixed-candidate-speed-benchmark-v1", "snapshot": str(snapshot),
              "snapshot_sha256": digest, "source_hashes": sources, "script_sha256": file_hash(__file__),
              "period": [DATE_FROM, DATE_TO], "repeats": repeats, "presets": [dict(preset) for preset in fit.PRESETS],
              "tolerance": TOLERANCE, "runs": [], "skipped": [], "status": "running",
              "full_training_pipeline_measured": False, "profitability_validated": False,
              "cal_test_scored": False, "roi_evaluated": False, "artifact_published": False,
              "active_model_changed": False, "forward_collector_accessed": False,
              "scope": "candidate comparison only; preparation shown separately; no final fit/CAL/TEST/ROI timing"}
    reference = None
    clock = time.perf_counter()
    try:
        prepared, preparation = prepare_inputs(snapshot, api_features, fit)
        report["preparation"] = preparation
        shared_inputs_digest = prepared_hashes(prepared, fit)
        report["shared_prepared_inputs_sha256"] = shared_inputs_digest
        encoded_bytes = int(prepared["train"].memory_usage(deep=True).sum())
        for repeat in range(repeats):
            # First A establishes quality, then reversed order counters warm-
            # cache/thermal bias. Report exact order; two repetitions are not
            # a statistically robust latency distribution.
            order = list(modes) if repeat == 0 else list(reversed(modes))
            for mode in order:
                resources = resource_probe()
                permitted, reason = mode_permission(mode, encoded_bytes, resources)
                if not permitted:
                    report["skipped"].append({"repeat": repeat + 1, "mode": mode, "reason": reason, "resources": resources})
                    continue
                if reference is None and mode != "A":
                    report["skipped"].append({"repeat": repeat + 1, "mode": mode, "reason": "sequential_reference_unavailable"})
                    continue
                run = execute_mode(mode, prepared, fit)
                if prepared_hashes(prepared, fit) != shared_inputs_digest:
                    raise ValueError("benchmark-shared-prepared-input-mutated")
                if reference is None:
                    reference = run
                run["quality"] = verify_quality(reference, run)
                run.update(repeat=repeat + 1, resources=resources)
                # Keep only the reference vectors in memory for parity, never
                # serialize model objects or outcome-bearing prediction tables.
                serializable = {**run, "trials": [{key: value for key, value in trial.items() if key != "predictions"} for trial in run["trials"]]}
                report["runs"].append(serializable)
                gc.collect()
        if fit.source_hashes() != sources or file_hash(snapshot) != digest:
            raise ValueError("benchmark-source-or-snapshot-changed-during-run")
        report["summary"] = summary_results(report["runs"], preparation["total_seconds"])
        report["shared_prepared_inputs_unchanged"] = True
        report["all_executed_quality_checks_passed"] = bool(report["runs"]) and all(run["quality"]["passed"] for run in report["runs"])
        report["snapshot_unchanged"] = True
        report["source_unchanged"] = True
        report["status"] = "completed" if reference is not None else "resource_gate_blocked"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = {"type": type(error).__name__, "message": str(error)}
        report["summary"] = {}
        raise
    finally:
        report["wall_seconds"] = time.perf_counter() - clock
        with (output / "benchmark.json").open("x", encoding="utf-8") as stream:
            json.dump(_json_safe(report), stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        print(json.dumps({"report": str(output / "benchmark.json"), "status": report["status"]}, ensure_ascii=False))
    return report, output / "benchmark.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--repeats", type=int, choices=(1, 2), default=2)
    parser.add_argument("--modes", nargs="+", choices=tuple(MODES), default=list(MODES))
    args = parser.parse_args()
    run_benchmark(args.snapshot, repeats=args.repeats, modes=tuple(args.modes), expected_sha256=args.expected_sha256)


if __name__ == "__main__":
    main()
