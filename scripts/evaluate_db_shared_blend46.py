"""Finite offline annual 46-feature specialist/blend accuracy and calibration.

Every TEST year has disjoint preceding TRAIN and CAL dates. Mix scores at the
fixed 50% rate before fitting each strategy's own CAL temperature. All primary
comparisons use the same CPU learner, cohorts and common TRAIN encoder/baseline.
GPU is an isolated equivalence/timing control. Never publish or switch models.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/research/db-shared-blend46-20261012.json"
OUTPUT_ROOT = ROOT / "reports/generated/db-shared-blend46"
WORKER = ROOT / "scripts/evaluate_db_shared_blend46_worker.py"
APP_PYTHON = ROOT / "python-api/.venv/Scripts/python.exe"
SNAPSHOT_SHA = "8880c290e31ae614f2c837dd00bc773f07e24ef6919e611a3f645927bc679a7e"


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def object_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def save(path, value, *, replace=False):
    path = Path(path)
    if replace:
        temp = path.with_suffix(path.suffix + ".tmp")
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        os.replace(temp, path)
    else:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)


def modules():
    for directory in (ROOT / "python-api", ROOT / "keiba", ROOT / "scripts"):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    from training import db_shared_features as shared, db_shared_fit as fit
    from training import db_shared_optimized as optimized, db_shared_acceleration as acceleration
    import db_shared_blend46_design as design
    import benchmark_db_shared_full_worker as cache
    from evaluate_db_shared_venues import preservation_receipt
    if tuple(design.CANONICAL46) != tuple(optimized.FEATURE_COLUMNS):
        raise ValueError("blend46-design-does-not-match-normal-profile")
    return shared, fit, optimized, acceleration, design, cache, preservation_receipt


def study_source_manifest():
    return {path.relative_to(ROOT).as_posix(): file_hash(path) for path in (
        Path(__file__), CONFIG, WORKER, ROOT / "scripts/db_shared_blend46_design.py",
        ROOT / "scripts/optimize_db_shared_features_worker.py", ROOT / "scripts/benchmark_db_shared_full_worker.py",
        ROOT / "scripts/evaluate_db_shared_venues.py", ROOT / "python-api/training/db_shared_acceleration.py")}


def reusable_cache_path(path):
    candidate = Path(path)
    boundary = (ROOT / "reports/generated").resolve()
    resolved = candidate.resolve(strict=True)
    if (candidate.is_symlink() or not resolved.is_dir() or not resolved.is_relative_to(boundary)
            or resolved == boundary or resolved.parent == boundary):
        raise ValueError("blend46-prepared-cache-boundary-invalid")
    for parent in [candidate, *candidate.parents]:
        if parent.is_symlink():
            raise ValueError("blend46-prepared-cache-symlink-forbidden")
    return resolved


def validate_protocol(value):
    if not isinstance(value, dict) or value.get("schema") != "db-shared-blend46-protocol-v1":
        raise ValueError("blend46-protocol-schema-invalid")
    fixed = {
        "requested_period": ["2016-01", "2026-09"], "cache_period": ["2013-01", "2026-09"],
        "snapshot_sha256": SNAPSHOT_SHA, "feature_pipeline": "local-db-shared-optimized-v1",
        "feature_count": 46, "excluded_features": ["safe_past5_top3_rate"],
        "strategies": ["global", "venue_only", "blend50"], "specialist_weight": .5,
        "minimum_specialist_races": 500, "minimum_specialist_runners": 2500,
        "minimum_specialist_dates": 40, "preset": "standard", "num_boost_round": 200,
        "maximum_cpu_workers": 3, "native_threads_per_worker": 2, "reserved_physical_cores": 2,
        "reserved_memory_gib": 4, "minimum_memory_per_worker_gib": 1.5, "maximum_worker_seconds": 900,
        "gpu_control_fold": "Y2026", "gpu_used_for_primary_comparison": False,
        "gpu_control_absolute_rank_difference": .001,
        "gpu_control_relative_rmse_nll_brier_difference": .001,
        "gpu_control_absolute_ece_difference": .001, "bootstrap_repetitions": 5000,
        "bootstrap_seed": 20261012, "primary_comparison_family_size": 4,
        "primary_simultaneous_confidence": .9875, "secondary_confidence": .95,
        "shared_train_only_encoder": True, "shared_train_only_speed_baseline": True,
        "history_all_venues": True, "same_day_and_future_history_excluded": True,
        "historical_periods_and_feature_selection_already_seen": True,
        "independent_future_validation": False, "roi_evaluated": False,
        "publish_models": False, "activate_models": False, "modify_forward_collection": False,
        "commit_push": False, "real_purchase": False,
        "primary_metrics": ["race_macro_spearman", "runner_pooled_rmse_speed_mps"],
        "calibration": "each strategy independently; previous calendar year only; one race-softmax temperature",
        "folds": "TEST 2016..2026 partial to 20260913; TRAIN 2013..year-2; CAL year-1",
        "bootstrap_unit": "paired whole race dates stratified by evaluation year",
    }
    if set(value) != set(fixed) | {"schema"}:
        raise ValueError("blend46-exact-protocol-keys-required")
    for key, expected in fixed.items():
        if type(value[key]) is not type(expected) or object_hash(value[key]) != object_hash(expected):
            raise ValueError("blend46-fixed-protocol-condition-invalid:" + key)
    return value


def resource_allocation(protocol, frame_bytes):
    import psutil
    validate_protocol(protocol)
    if type(frame_bytes) is not int or frame_bytes < 1:
        raise ValueError("blend46-resource-frame-size-required")
    physical = psutil.cpu_count(logical=False) or 1
    available = int(psutil.virtual_memory().available)
    reserve = int(protocol["reserved_memory_gib"] * 1024**3)
    per_worker = max(int(protocol["minimum_memory_per_worker_gib"] * 1024**3), frame_bytes * 6)
    workers = min(3, max(0, (physical - 2) // 2), max(0, (available - reserve) // per_worker))
    if workers < 1:
        raise ValueError("blend46-no-safe-compute-slot")
    return {"cpu_workers": int(workers), "native_threads_per_worker": 2,
        "physical_cores": physical, "logical_cores": psutil.cpu_count(),
        "available_memory_bytes": available, "estimated_bytes_per_worker": per_worker,
        "reserved_memory_bytes": reserve, "reserved_physical_cores": 2,
        "gpu_control_concurrency": 1, "reservation_is_scheduling_budget_not_os_allocation": True}


def preflight():
    if os.environ.get("BLEND46_RESEARCH_ENV", "local") not in {"local", "development", "test"}:
        raise ValueError("blend46-local-offline-only")
    shared, fit, optimized, acceleration, design, cache, preserve = modules()
    protocol = validate_protocol(json.loads(CONFIG.read_text(encoding="utf-8")))
    snapshot = ROOT / "keiba/data/model_retrain_snapshots" / (SNAPSHOT_SHA + ".db")
    if snapshot.is_symlink() or file_hash(snapshot) != SNAPSHOT_SHA:
        raise ValueError("blend46-pinned-snapshot-required")
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(snapshot) + suffix)
        if sidecar.is_file() and sidecar.stat().st_size:
            raise ValueError("blend46-checkpointed-snapshot-required")
    return {"protocol": protocol, "snapshot": str(snapshot), "preservation_before": preserve(),
        "raw_source_hashes": fit.source_hashes(), "optimized_source_hashes": optimized.source_hashes(),
        "folds": design.build_folds()}


def read_artifact(result, kind, directory):
    key = {"race_metrics": "races", "runner_predictions": "runners", "research_bundle": "model"}.get(kind)
    matches = [result["artifacts"][key]] if key in result["artifacts"] else []
    if len(matches) != 1:
        raise ValueError("blend46-single-artifact-kind-required:" + kind)
    item = matches[0]
    path = Path(item["path"]).resolve(strict=True)
    if (not path.is_relative_to(Path(directory).resolve()) or path.is_symlink()
            or file_hash(path) != item["sha256"]):
        raise ValueError("blend46-output-artifact-boundary-or-hash-invalid")
    import pandas as pd
    return pd.read_parquet(path)


def compare_gpu(cpu_records, gpu_records, design, protocol):
    cpu_records = design.validate_metric_cohort(cpu_records.loc[cpu_records.split.eq("TEST")], split="TEST")
    gpu_records = design.validate_metric_cohort(gpu_records.loc[gpu_records.split.eq("TEST")], split="TEST")
    comparisons = []
    for strategy in protocol["strategies"]:
        cpu = cpu_records.loc[(cpu_records.split == "TEST") & (cpu_records.strategy == strategy)]
        gpu = gpu_records.loc[(gpu_records.split == "TEST") & (gpu_records.strategy == strategy)]
        keys = ["fold_id", "split", "strategy", "race_id", "race_date", "venue", "runner_count"]
        if "surface" in cpu.columns or "surface" in gpu.columns:
            if "surface" not in cpu.columns or "surface" not in gpu.columns:
                raise ValueError("blend46-gpu-control-surface-schema-mismatch")
            keys.append("surface")
        if set(map(tuple, cpu[keys].to_numpy())) != set(map(tuple, gpu[keys].to_numpy())):
            raise ValueError("blend46-gpu-control-cohort-mismatch")
        c, g = design.accuracy_summary(cpu), design.accuracy_summary(gpu)
        cp, gp = design.probability_summary(cpu), design.probability_summary(gpu)
        if cp["eligible_cohort_sha256"] != gp["eligible_cohort_sha256"]:
            raise ValueError("blend46-gpu-control-probability-cohort-mismatch")
        values = [c["rank_correlation"], g["rank_correlation"], c["rmse_speed_mps"], g["rmse_speed_mps"],
                  cp["selection_ece"], gp["selection_ece"], cp["nll"], gp["nll"], cp["brier"], gp["brier"]]
        if (not all(type(value) in (int, float) and math.isfinite(value) for value in values)
                or c["rmse_speed_mps"] <= 0 or cp["nll"] <= 0 or cp["brier"] <= 0):
            raise ValueError("blend46-gpu-control-finite-positive-probability-metrics-required")
        rho = g["rank_correlation"] - c["rank_correlation"]
        rmse = (g["rmse_speed_mps"] - c["rmse_speed_mps"]) / c["rmse_speed_mps"]
        probability = {key: (gp[key] - cp[key]) / cp[key] for key in ("nll", "brier")}
        ece = gp["selection_ece"] - cp["selection_ece"]
        ok = (abs(rho) <= protocol["gpu_control_absolute_rank_difference"]
              and abs(rmse) <= protocol["gpu_control_relative_rmse_nll_brier_difference"]
              and all(abs(delta) <= protocol["gpu_control_relative_rmse_nll_brier_difference"] for delta in probability.values())
              and abs(ece) <= protocol["gpu_control_absolute_ece_difference"])
        comparisons.append({"strategy": strategy, "rank_delta": rho, "rmse_relative_difference": rmse,
            "probability_relative_difference": probability, "selection_ece_difference": ece, "quality_pass": ok})
    return {"comparisons": comparisons, "quality_pass": all(row["quality_pass"] for row in comparisons),
            "used_for_primary_comparison": False, "single_run_timing_not_repeated_benchmark": True}


def execute(prepared_cache=None):
    started = time.perf_counter()
    checked = preflight()
    shared, fit, optimized, acceleration, design, cache, preserve = modules()
    protocol = checked["protocol"]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
    output = OUTPUT_ROOT / run_id
    output.mkdir(parents=True, exist_ok=False)
    pins = study_source_manifest()
    existing_cache = reusable_cache_path(prepared_cache) if prepared_cache is not None else None
    locked = {**checked, "run_id": run_id, "source_manifest": pins,
        "runtime_scope": "all primary strategy comparisons use CPU; GPU independent control only",
        "historical_label": "reused retrospective diagnostics; 46 selection previously saw 2020..2022",
        "prepared_cache": str(existing_cache) if existing_cache is not None else None,
        "prepared_cache_manifest_sha256": file_hash(existing_cache / "manifest.json") if existing_cache is not None else None}
    locked_sha = object_hash(locked)
    save(output / "protocol.json", locked)
    save(output / "protocol-hash.json", {"sha256": locked_sha})
    completed, active, peak = [], 0, 0
    mutex = threading.RLock()

    def progress(stage, **extra):
        save(output / "progress.json", {"status": "running", "stage": stage,
            "completed_jobs": completed, "active_workers": active, "peak_workers": peak,
            "elapsed_seconds": time.perf_counter() - started, **extra}, replace=True)
        print(json.dumps({"stage": stage, **extra}, ensure_ascii=False), flush=True)

    try:
        progress("preparing_common_raw_features")
        prep_started = time.perf_counter()
        cache_request = {"snapshot_sha256": SNAPSHOT_SHA, "period": protocol["cache_period"],
                         "expected_source_hashes": checked["raw_source_hashes"]}
        if existing_cache is None:
            target, history, coverage = shared.load_db_frames(Path(checked["snapshot"]), "20130101", "20260913")
            loaded = time.perf_counter()
            raw = shared.build_training_features(target, history)
            del history
            target, raw = fit._prepare(target, raw)
        else:
            target, raw, reused_manifest = cache.read_cache(existing_cache, cache_request, fit)
            if file_hash(existing_cache / "manifest.json") != locked["prepared_cache_manifest_sha256"]:
                raise ValueError("blend46-prepared-cache-manifest-changed")
            from training.db_shared_cache import _validate_audit
            _validate_audit(reused_manifest["point_in_time_audit"])
            coverage = reused_manifest["coverage"]
            loaded = time.perf_counter()
        prepared = time.perf_counter()
        resources = resource_allocation(protocol, int(target.memory_usage(deep=True).sum() + raw.memory_usage(deep=True).sum()))
        cache_dir = existing_cache or output / "raw47-cache"
        manifest = reused_manifest if existing_cache is not None else cache.create_cache(cache_dir, cache_request, target, raw, coverage, fit)[0]
        cache_sha = file_hash(cache_dir / "manifest.json")
        quality = {"target": fit._partition_receipt(target), "raw47_sha256": fit._frame_digest(raw),
            "target_sha256": fit._frame_digest(target), "duplicate_runners": int(target.duplicated(["race_id", "horse_number"]).sum()),
            "selected_feature_count": 46, "selected_feature_missing_rate": float(optimized.select_features(raw).isna().to_numpy().mean()),
            "point_in_time_audit": manifest["point_in_time_audit"],
            "load_seconds": loaded - prep_started, "generation_and_prepare_seconds": prepared - loaded,
            "cache_save_seconds": time.perf_counter() - prepared, "features_generated_once": True,
            "prepared_cache_reused": existing_cache is not None, "cache_directory": str(cache_dir),
            "feature_generation_this_run": existing_cache is None}
        fold_receipts = [{"fold": fold, "partitions": {name: fit._partition_receipt(target.iloc[positions])
            for name, positions in design.validate_fold_target(target, fold).items()}} for fold in checked["folds"]]
        save(output / "data-preparation.json", {"quality": quality, "resources": resources,
            "folds": fold_receipts, "cache_manifest_sha256": cache_sha})
        del target, raw
        gc.collect()
        cpu_runtime = acceleration.resolve_runtime("cpu", True)
        gpu_runtime, gpu_failure = None, None
        if resources["cpu_workers"] >= 2:
            try:
                gpu_runtime = acceleration.resolve_runtime("gpu", True)
            except (ValueError, OSError) as exc:
                gpu_failure = type(exc).__name__
        else:
            gpu_failure = "insufficient_parallel_control_budget"
        save(output / "runtimes.json", {"cpu": cpu_runtime, "gpu": gpu_runtime, "gpu_failure": gpu_failure})

        def worker(fold, device="cpu", phase="primary"):
            nonlocal active, peak
            job_id = fold["fold_id"] if device == "cpu" else fold["fold_id"] + "_GPU_CONTROL"
            directory = output / phase / job_id
            directory.mkdir(parents=True, exist_ok=False)
            request = {"schema": "db-shared-blend46-worker-v1", "job_id": job_id,
                "cache_dir": str(cache_dir), "cache_manifest_sha256": cache_sha,
                "expected_source_hashes": checked["raw_source_hashes"], "expected_worker_source_hashes": pins,
                "protocol_sha256": locked_sha, "feature_columns": list(optimized.FEATURE_COLUMNS),
                "fold": fold, "device": device, "n_threads": 2,
                "execution_receipt": gpu_runtime["execution_receipt"] if device == "gpu" else None,
                "save_models": True}
            save(directory / "request.json", request)
            python = gpu_runtime["interpreter"] if device == "gpu" else str(APP_PYTHON)
            with mutex:
                active += 1
                peak = max(peak, active)
            clock = time.perf_counter()
            try:
                response = subprocess.run([python, "-X", "utf8", str(WORKER), "--request",
                    str(directory / "request.json"), "--output", str(directory / "result.json")],
                    cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", timeout=900,
                    creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0,
                    env={**os.environ, "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2",
                         "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"})
                (directory / "stdout.log").write_text(response.stdout, encoding="utf-8")
                (directory / "stderr.log").write_text(response.stderr, encoding="utf-8")
                if response.returncode != 0:
                    raise ValueError("blend46-worker-failed:" + job_id)
                result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
                if (result.get("schema") != request["schema"] or result.get("status") != "completed"
                        or result.get("protocol_sha256") != locked_sha or result.get("request_sha256") != object_hash(request)
                        or result.get("job_id") != job_id or result.get("fold") != fold
                        or result.get("feature_columns") != request["feature_columns"] or result.get("feature_count") != 46
                        or result.get("device") != device or result.get("source_hashes") != checked["raw_source_hashes"]
                        or result.get("cache_manifest_sha256") != cache_sha or result.get("snapshot_sha256") != SNAPSHOT_SHA
                        or result.get("pinned_worker_source_hashes") != pins):
                    raise ValueError("blend46-worker-result-binding-invalid")
                result["result_path"] = str(directory / "result.json")
                with mutex:
                    completed.append({"job_id": job_id, "fold_id": fold["fold_id"], "device": device,
                        "phase": phase, "wall_seconds": time.perf_counter() - clock,
                        "result_sha256": file_hash(directory / "result.json")})
                    progress("fold_completed", job_id=job_id, completed_count=len(completed))
                return result
            finally:
                with mutex:
                    active -= 1

        control_fold = checked["folds"][-1]
        gpu_result = None
        with ThreadPoolExecutor(max_workers=2 if gpu_runtime else 1) as pool:
            cpu_future = pool.submit(worker, control_fold)
            gpu_future = pool.submit(worker, control_fold, "gpu", "gpu-control") if gpu_runtime else None
            cpu_result = cpu_future.result()
            if gpu_future:
                try:
                    gpu_result = gpu_future.result()
                except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
                    gpu_failure = type(exc).__name__
        primary = [cpu_result]
        gpu_control = {"available": gpu_result is not None, "failure": gpu_failure, "used_for_primary_comparison": False}
        if gpu_result:
            cpu_records = read_artifact(cpu_result, "race_metrics", Path(cpu_result["result_path"]).parent)
            gpu_records = read_artifact(gpu_result, "race_metrics", Path(gpu_result["result_path"]).parent)
            identity_keys = ("target_hashes", "raw_feature_hashes", "encoded_feature_hashes",
                             "baseline_sha256", "labels_sha256", "categories", "partitions")
            identity_matches = all(cpu_result[key] == gpu_result[key] for key in identity_keys)
            gpu_control["data_encoder_baseline_identity_matches"] = identity_matches
            try:
                gpu_control.update(compare_gpu(cpu_records, gpu_records, design, protocol))
                gpu_control["quality_pass"] = gpu_control["quality_pass"] and identity_matches
            except ValueError as exc:
                gpu_control.update(quality_pass=False, comparison_failure=str(exc))
            gpu_control["cpu_worker_seconds"] = next(row["wall_seconds"] for row in completed if row["device"] == "cpu")
            gpu_control["gpu_worker_seconds"] = next(row["wall_seconds"] for row in completed if row["device"] == "gpu")
        save(output / "gpu-control.json", gpu_control)
        with ThreadPoolExecutor(max_workers=resources["cpu_workers"]) as pool:
            futures = [pool.submit(worker, fold) for fold in checked["folds"][:-1]]
            for future in as_completed(futures):
                primary.append(future.result())
        primary.sort(key=lambda result: result["fold"]["test_year"])
        import pandas as pd
        records = pd.concat([read_artifact(result, "race_metrics", Path(result["result_path"]).parent)
                             for result in primary], ignore_index=True)
        tests = design.validate_metric_cohort(records.loc[records.split.eq("TEST")], split="TEST", require_all_folds=True)
        records.to_parquet(output / "all-race-metrics.parquet", index=False)
        overall, yearly, segments = [], [], []
        for strategy, group in tests.groupby("strategy", sort=True):
            overall.append({"strategy": strategy, "accuracy": design.accuracy_summary(group),
                            "probability": design.probability_summary(group)})
        for (year, strategy), group in tests.groupby(["test_year", "strategy"], sort=True):
            yearly.append({"year": int(year), "strategy": strategy, "accuracy": design.accuracy_summary(group),
                           "probability": design.probability_summary(group)})
        for (venue, strategy), group in tests.groupby(["venue", "strategy"], sort=True):
            segments.append({"venue": str(venue), "strategy": strategy, "accuracy": design.accuracy_summary(group),
                "probability": design.probability_summary(group), "exploratory_not_adoption_evidence": True})
        progress("paired_date_bootstrap")
        comparisons = design.paired_comparisons(tests, repetitions=5000, seed=20261012)
        probability_comparisons = design.probability_comparisons(tests, repetitions=5000, seed=20261012)
        calibration_validation = [{"strategy": strategy,
            "validated_fold_count": sum(result["strategies"][strategy]["probability_validation"]["validated"] for result in primary),
            "fold_count": len(primary),
            "failed_folds": [{"year": result["fold"]["test_year"],
                "reason_codes": result["strategies"][strategy]["probability_validation"]["reason_codes"]}
                for result in primary if not result["strategies"][strategy]["probability_validation"]["validated"]],
            "pooled_metrics_override_year_gate": False, "eligible_for_ev": False} for strategy in protocol["strategies"]]
        after = preserve()
        if after != checked["preservation_before"]:
            raise ValueError("blend46-protected-state-changed")
        if (optimized.source_hashes() != checked["optimized_source_hashes"] or file_hash(checked["snapshot"]) != SNAPSHOT_SHA
                or file_hash(cache_dir / "manifest.json") != cache_sha):
            raise ValueError("blend46-feature-source-or-snapshot-changed")
        if any(file_hash(ROOT / path) != expected for path, expected in pins.items()):
            raise ValueError("blend46-study-source-changed")
        result = {"schema": "db-shared-blend46-result-v1", "status": "completed",
            "protocol_sha256": locked_sha, "run_directory": str(output), "quality": quality,
            "resources": resources, "overall": overall, "yearly": yearly, "venue_segments": segments,
            "comparisons": comparisons, "probability_comparisons": probability_comparisons,
            "calibration_validation": calibration_validation, "fold_results": primary, "gpu_control": gpu_control,
            "completed_worker_jobs": completed, "peak_concurrent_workers": peak,
            "elapsed_seconds": time.perf_counter() - started,
            "summed_worker_seconds_not_independent_sequential_benchmark": sum(row["wall_seconds"] for row in completed),
            "preservation_before": checked["preservation_before"], "preservation_after": after,
            "models_research_only": True, "artifact_published": False, "model_activated": False,
            "forward_changed": False, "roi_evaluated": False, "independent_future_validation": False,
            "probability_claim_scope": "per-fold CAL-fitted historical TEST gates; no served-model or profitability authority"}
        save(output / "results.json", result)
        files = {str(path.relative_to(output)): {"sha256": file_hash(path), "bytes": path.stat().st_size}
                 for path in output.rglob("*") if path.is_file() and path.name != "progress.json"}
        save(output / "artifact-manifest.json", {"files": files, "protocol_sha256": locked_sha})
        save(output / "progress.json", {"status": "completed", "elapsed_seconds": result["elapsed_seconds"],
            "completed_jobs": len(completed), "results_path": str(output / "results.json")}, replace=True)
        print(json.dumps({"status": "completed", "overall": overall,
            "elapsed_seconds": result["elapsed_seconds"], "output": str(output)}, ensure_ascii=False), flush=True)
        return result
    except BaseException as exc:
        save(output / "failure.json", {"status": "failed", "error": type(exc).__name__ + ": " + str(exc),
            "completed_jobs": completed, "elapsed_seconds": time.perf_counter() - started,
            "preservation_after": preserve()})
        save(output / "progress.json", {"status": "failed", "failure_path": str(output / "failure.json")}, replace=True)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--prepared-cache", type=Path, help="Read-only admission of a verified same-snapshot 2013 raw47 cache.")
    args = parser.parse_args()
    if args.execute:
        execute(args.prepared_cache)
    else:
        value = preflight()
        print(json.dumps({"status": "preflight_passed", "folds": value["folds"],
            "feature_count": 46, "source_count": len(value["optimized_source_hashes"]),
            "preservation": value["preservation_before"]["active_model_id"]}, ensure_ascii=False))
