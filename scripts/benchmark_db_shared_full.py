"""Bounded full-period CPU/GPU speed study, never a production publication.

Uses the normal fixed47 fit function in isolated fresh processes. Cached raw
features retain point-in-time lineage; train-only encoders are fitted again.
The extra all-row fit has no calibration or independent performance claim.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "config/research/full-db-learning-benchmark-20261011.json"
OUTPUT_ROOT = ROOT / "reports/generated/full-db-learning-benchmarks"
WORKER = ROOT / "scripts/benchmark_db_shared_full_worker.py"
SNAPSHOT_SHA = "8880c290e31ae614f2c837dd00bc773f07e24ef6919e611a3f645927bc679a7e"
SNAPSHOT = ROOT / "keiba/data/model_retrain_snapshots" / (SNAPSHOT_SHA + ".db")
PROTOCOL_SHA = "7ad185abd66ee8b264ea8c4c4719e03f8259477dedba6c5d44c045de2cb0e614"


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def object_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_modules():
    for directory in (ROOT / "python-api", ROOT / "keiba"):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    from training import db_shared_fit
    return db_shared_fit


def runtime_receipt(executable):
    """Read package identities from each interpreter without changing it."""
    source = """import hashlib,json,platform,sys
from pathlib import Path
import lightgbm,numpy,pandas,scipy,pyarrow
from lightgbm.basic import _LIB
library=Path(_LIB._name).resolve()
print(json.dumps({'python':sys.executable,'python_version':platform.python_version(),
'lightgbm':lightgbm.__version__,'numpy':numpy.__version__,'pandas':pandas.__version__,
'scipy':scipy.__version__,'pyarrow':pyarrow.__version__,'lightgbm_library':str(library),
'lightgbm_library_sha256':hashlib.sha256(library.read_bytes()).hexdigest()}))
"""
    process = subprocess.run([str(executable), "-c", source], cwd=str(ROOT), text=True,
                             capture_output=True, check=True, timeout=30)
    return json.loads(process.stdout.strip())


def quality_check(reference, actual, protocol, *, vectors_reference, vectors_actual):
    """CPU exact repeatability or pre-fixed GPU non-deterioration, not profit."""
    import numpy as np
    ref, candidate = reference["fit_result"], actual["fit_result"]
    checks = {
        "partitions_match": ref["partitions"] == candidate["partitions"],
        "selected_preset_match": ref["selected_preset"] == candidate["selected_preset"],
        "source_hashes_match": ref["source_hashes"] == candidate["source_hashes"],
        "probability_gate_results_match": ref["probability_validation"]["gate_results"] == candidate["probability_validation"]["gate_results"],
        "input_identity_match": reference["input_identity"] == actual["input_identity"],
    }
    differences = {}
    expected_vectors = {"inner_lean", "inner_standard", "cal", "test"}
    if set(vectors_reference) != expected_vectors or set(vectors_actual) != expected_vectors:
        checks["vector_keys_match"] = False
    else:
        checks["vector_keys_match"] = True
        for key in vectors_reference:
            left, right = np.asarray(vectors_reference[key]), np.asarray(vectors_actual[key])
            ok = (left.ndim == right.ndim == 1 and left.size > 0 and left.shape == right.shape
                  and np.isfinite(left).all() and np.isfinite(right).all())
            checks["finite_and_aligned_" + key] = bool(ok)
            differences[key] = float(np.max(np.abs(left - right))) if ok and left.size else None
    exact = actual["runtime"] == "app" and actual["device"] == "cpu"
    if exact:
        checks["all_prediction_vectors_exact_within_tolerance"] = bool(
            checks["vector_keys_match"] and all(value is not None and value <= protocol["cpu_prediction_absolute_tolerance"] for value in differences.values()))
    metrics_ref = ref["evaluation"]["primary"]
    metrics_candidate = candidate["evaluation"]["primary"]
    deltas = {"rank_correlation": float(metrics_candidate["rank_correlation"] - metrics_ref["rank_correlation"]),
              "rmse": float(metrics_candidate["rmse"] - metrics_ref["rmse"])}
    for key in ("nll", "brier"):
        deltas[key] = float(candidate["probability_metrics"][key] - ref["probability_metrics"][key])
    checks["evaluation_metrics_finite"] = all(math.isfinite(value) for value in deltas.values())
    if not exact:
        bounds = protocol["gpu_quality_bounds"]
        checks["rank_correlation_bound"] = abs(deltas["rank_correlation"]) <= bounds["rank_correlation_absolute_difference"]
        checks["rmse_non_deterioration_bound"] = deltas["rmse"] <= abs(metrics_ref["rmse"]) * bounds["rmse_relative_deterioration"]
        for key in ("nll", "brier"):
            checks[key + "_non_deterioration_bound"] = deltas[key] <= abs(ref["probability_metrics"][key]) * bounds[key + "_relative_deterioration"]
    return {"passed": all(checks.values()), "checks": checks, "max_abs_score_differences": differences,
            "test_metric_differences": {key: value if math.isfinite(value) else None for key, value in deltas.items()},
            "equivalence_scope": "deterministic CPU prediction equality" if exact else "predefined non-deterioration bounds, not exact prediction equality"}


def schedule(protocol):
    # The initial recomputation also creates the cache; report it separately.
    rows = [{"mode": "A", "input_mode": "cold", "repeat": 0, "initialization": True, "full_row_timing": False}]
    for repeat in range(protocol["uncached_repetitions"]):
        order = list(protocol["uncached_modes"])
        if repeat % 2 == 0:
            order.reverse()
        rows += [{"mode": mode, "input_mode": "cold", "repeat": repeat + 1, "initialization": False, "full_row_timing": False} for mode in order]
    order = list(protocol["cached_modes"])
    for repeat in range(protocol["cached_repetitions"]):
        sequence = order if repeat == 0 else list(reversed(order)) if repeat == 1 else order[2:] + order[:2]
        rows += [{"mode": mode, "input_mode": "cache", "repeat": repeat + 1, "initialization": False,
                  "full_row_timing": mode in protocol["full_row_fit_timing_modes"] and repeat < protocol["full_row_fit_timing_repetitions"]} for mode in sequence]
    return rows


def summarize(runs, protocol):
    output = []
    for input_mode in ("cold", "cache"):
        for mode in protocol["modes"]:
            group = [row for row in runs if not row["initialization"] and row.get("status") == "completed"
                     and row["mode"] == mode and row["input_mode"] == input_mode]
            if not group:
                continue
            times = [row["run_processing_seconds"] for row in group]
            output.append({"mode": mode, "input_mode": input_mode, "repetitions": len(group),
                           "median_processing_seconds": statistics.median(times),
                           "min_processing_seconds": min(times), "max_processing_seconds": max(times),
                           "median_fit_pipeline_seconds": statistics.median(row["stage_seconds"]["fit_pipeline"] for row in group),
                           "all_quality_checks_passed": all(row["quality"]["passed"] for row in group)})
    by_key = {(row["input_mode"], row["mode"]): row for row in output}
    current_uncached = by_key.get(("cold", "C"))
    current_cached = by_key.get(("cache", "C"))
    for row in output:
        row["speedup_vs_current_uncached_C"] = (current_uncached["median_processing_seconds"] / row["median_processing_seconds"]
            if current_uncached and current_uncached["all_quality_checks_passed"] and row["all_quality_checks_passed"] else None)
        row["speedup_scope"] = "isolated fixed-snapshot pipeline; excludes live API queue/lease/new snapshot copy/production publication"
        row["fit_pipeline_speedup_vs_cached_C"] = (current_cached["median_fit_pipeline_seconds"] / row["median_fit_pipeline_seconds"]
            if current_cached and current_cached["all_quality_checks_passed"] and row["all_quality_checks_passed"] else None)
    return output


def summarize_full_rows(runs):
    """Speed only: an all-row fit cannot supply independent test performance."""
    output = []
    for mode in ("A", "GPU_BUILD_CPU", "GPU"):
        group = [row for row in runs if row.get("status") == "completed" and row["mode"] == mode
                 and isinstance(row.get("full_row_timing"), dict)]
        if not group:
            continue
        probes = [row["full_row_timing"] for row in group]
        output.append({"mode": mode, "repetitions": len(group),
                       "runner_count": probes[0]["runner_count"], "race_count": probes[0]["race_count"],
                       "median_total_seconds": statistics.median(row["total_seconds"] for row in probes),
                       "median_booster_seconds": statistics.median(row["booster_seconds"] for row in probes),
                       "min_total_seconds": min(row["total_seconds"] for row in probes),
                       "max_total_seconds": max(row["total_seconds"] for row in probes),
                       "related_heldout_pipeline_quality_passed": all(row["quality"]["passed"] for row in group),
                       "independent_test_evaluated": False, "calibrated": False, "model_saved": False})
    cpu = next((row for row in output if row["mode"] == "A"), None)
    for row in output:
        row["timing_ratio_vs_all_row_CPU_A"] = cpu["median_total_seconds"] / row["median_total_seconds"] if cpu else None
        row["accuracy_equivalence_claimed_for_all_row_fit"] = False
    return output


def merge_worker_output(worker, item, mode, result_path, subprocess_wall):
    """Keep optional measured full-row data separate from the request flag."""
    requested = item["full_row_timing"]
    if requested and not isinstance(worker.get("full_row_timing"), dict):
        raise ValueError("full-benchmark-full-row-measurement-required")
    merged = dict(worker)
    merged.update({key: value for key, value in item.items() if key != "full_row_timing"})
    merged.update(runtime=mode["runtime"], device=mode["device"], worker_json=str(result_path),
                  subprocess_wall_seconds=subprocess_wall, full_row_timing_requested=requested)
    merged["input_identity"] = {"cohort": worker["cohort"], "input_hashes": worker["input_hashes"],
                                "cache_key_sha256": worker["cache_key_sha256"]}
    merged["run_processing_seconds"] = worker["timings"]["run_processing_seconds"]
    merged["stage_seconds"] = {"fit_pipeline": worker["timings"]["fit_selection_calibration_evaluation_seconds"]}
    return merged


def run(execute=False):
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    if (object_hash(protocol) != PROTOCOL_SHA or protocol["snapshot_sha256"] != SNAPSHOT_SHA
            or protocol["period"] != ["2016-01", "2026-09"]):
        raise ValueError("full-benchmark-pinned-source-period-required")
    if not execute:
        print(json.dumps({"status": "protocol_only", "protocol": str(PROTOCOL), "scheduled_processes": len(schedule(protocol)), "no_learning_started": True}))
        return
    import psutil
    if (psutil.cpu_count(logical=False) or 1) < 8 or psutil.virtual_memory().available < 6 * 1024**3:
        raise RuntimeError("full-benchmark-needs-eight-cores-and-six-GiB-available")
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(SNAPSHOT) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError("full-benchmark-needs-checkpointed-snapshot")
    if file_hash(SNAPSHOT) != SNAPSHOT_SHA:
        raise ValueError("full-benchmark-snapshot-hash-mismatch")
    fit = source_modules()
    sources = fit.source_hashes()
    venue_helpers = load_module("venue_preservation_guard", ROOT / "scripts/evaluate_db_shared_venues.py")
    before = venue_helpers.preservation_receipt()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
    output = OUTPUT_ROOT / stamp
    output.mkdir(parents=True, exist_ok=False)
    (output / "requests").mkdir()
    pinned_scripts = {str(path.relative_to(ROOT)): file_hash(path) for path in (Path(__file__), WORKER, PROTOCOL)}
    write_json(output / "protocol.json", {"protocol": protocol, "protocol_sha256": object_hash(protocol), "source_hashes": sources, "script_hashes": pinned_scripts})
    report = {"schema": "full-db-learning-speed-result-v1", "status": "running", "protocol": protocol,
              "protocol_sha256": object_hash(protocol), "snapshot_sha256": SNAPSHOT_SHA,
              "source_hashes": sources, "script_hashes": pinned_scripts, "preservation_before": before,
              "output": str(output), "runs": [], "skipped": [], "models_published": False,
              "active_model_changed": False, "forward_collection_changed": False, "roi_evaluated": False}
    print(json.dumps({"output": str(output), "status": "started"}), flush=True)
    reference = reference_vectors = None
    unavailable = set()
    start = time.perf_counter()
    try:
        import numpy as np
        report["runtime_receipts"] = {}
        for runtime, executable in (("app", Path(sys.executable)), ("gpu", ROOT / ".local-runtime/gpu-validation/.venv/Scripts/python.exe")):
            try:
                report["runtime_receipts"][runtime] = runtime_receipt(executable)
            except Exception as error:
                report["runtime_receipts"][runtime] = {"status": "unavailable", "error": type(error).__name__}
                if runtime == "app":
                    raise
        for number, item in enumerate(schedule(protocol)):
            mode = protocol["modes"][item["mode"]]
            if item["mode"] in unavailable:
                report["skipped"].append({**item, "reason": "runtime_failed_earlier"})
                continue
            request = {"mode": item["mode"], "device": mode["device"], "workers": mode["workers"],
                       "threads": mode["threads"], "input_mode": item["input_mode"],
                       "full_row_timing": item["full_row_timing"],
                       "snapshot_path": str(SNAPSHOT), "snapshot_sha256": SNAPSHOT_SHA,
                       "period": protocol["period"], "cache_dir": str(output / "raw47-cache"), "expected_source_hashes": sources}
            request_path = output / "requests" / f"{number:02d}-{item['input_mode']}-{item['mode']}.json"
            result_path = output / (request_path.stem + ".json")
            write_json(request_path, request)
            executable = Path(sys.executable) if mode["runtime"] == "app" else ROOT / ".local-runtime/gpu-validation/.venv/Scripts/python.exe"
            if not executable.exists():
                unavailable.add(item["mode"])
                report["skipped"].append({**item, "reason": "runtime_missing"})
                continue
            print(f"START {number + 1}/{len(schedule(protocol))} {item['input_mode']} {item['mode']} repeat={item['repeat']}", flush=True)
            env = os.environ.copy()
            for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
                env[key] = str(mode["workers"] * mode["threads"])
            clock = time.perf_counter()
            process = subprocess.run([str(executable), str(WORKER), "--request", str(request_path), "--output", str(result_path)],
                                     cwd=str(ROOT), env=env, check=False)
            if process.returncode or not result_path.exists():
                failure = {**item, "status": "failed", "returncode": process.returncode, "output": str(result_path)}
                report["runs"].append(failure)
                if mode["runtime"] == "app":
                    raise RuntimeError("full-benchmark-app-worker-failed")
                unavailable.add(item["mode"])
                continue
            worker = merge_worker_output(json.loads(result_path.read_text(encoding="utf-8")), item, mode,
                                         result_path, time.perf_counter() - clock)
            if worker["status"] != "completed":
                raise RuntimeError("full-benchmark-worker-not-completed")
            vector_path = Path(worker["vectors"]["path"]).resolve(strict=True)
            if vector_path.parent != output.resolve() or vector_path != result_path.with_suffix(".vectors.npz").resolve():
                raise ValueError("full-benchmark-worker-vector-boundary-mismatch")
            if file_hash(vector_path) != worker["vectors"]["sha256"]:
                raise ValueError("full-benchmark-worker-vector-hash-mismatch")
            with np.load(vector_path, allow_pickle=False) as vector_file:
                vectors = dict(vector_file)
            if reference is None:
                reference, reference_vectors = worker, vectors
            worker["quality"] = quality_check(reference, worker, protocol, vectors_reference=reference_vectors, vectors_actual=vectors)
            report["runs"].append(worker)
            print(f"DONE {item['input_mode']} {item['mode']} seconds={worker['run_processing_seconds']:.3f} quality={worker['quality']['passed']}", flush=True)
        report["preservation_after"] = venue_helpers.preservation_receipt()
        report["preservation_unchanged"] = report["preservation_after"] == before
        report["snapshot_unchanged"] = file_hash(SNAPSHOT) == SNAPSHOT_SHA
        report["source_unchanged"] = fit.source_hashes() == sources
        report["scripts_unchanged"] = all(file_hash(ROOT / path) == digest for path, digest in pinned_scripts.items())
        if not all(report[key] for key in ("preservation_unchanged", "snapshot_unchanged", "source_unchanged", "scripts_unchanged")):
            raise ValueError("full-benchmark-preservation-guard-failed")
        report["summary"] = summarize(report["runs"], protocol)
        report["full_row_summary"] = summarize_full_rows(report["runs"])
        completed = [row for row in report["runs"] if row["status"] == "completed"]
        report["all_completed_quality_checks_passed"] = all(row["quality"]["passed"] for row in completed)
        report["status"] = "completed" if report["all_completed_quality_checks_passed"] and not report["skipped"] else "completed_with_quality_rejections_or_skips"
    except BaseException as error:
        report["status"] = "failed"
        report["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        if "preservation_after" not in report:
            try:
                report["preservation_after"] = venue_helpers.preservation_receipt()
                report["preservation_unchanged"] = report["preservation_after"] == before
                report["snapshot_unchanged"] = file_hash(SNAPSHOT) == SNAPSHOT_SHA
                report["source_unchanged"] = fit.source_hashes() == sources
                report["scripts_unchanged"] = all(file_hash(ROOT / path) == digest for path, digest in pinned_scripts.items())
            except Exception as guard_error:
                report["failure_preservation_audit_error"] = {"type": type(guard_error).__name__, "message": str(guard_error)}
        report["study_wall_seconds"] = time.perf_counter() - start
        write_json(output / "results.json", report)
        files = {str(path.relative_to(output)): file_hash(path) for path in output.rglob("*") if path.is_file()}
        write_json(output / "artifact_manifest.json", {"files": files, "protocol_sha256": report["protocol_sha256"], "snapshot_sha256": SNAPSHOT_SHA})
        print(json.dumps({"report": str(output / "results.json"), "status": report["status"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    run(execute=args.execute)
