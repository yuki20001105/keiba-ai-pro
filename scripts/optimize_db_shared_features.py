"""Finite offline safe-feature study; keep serving models and forward pins intact.

All candidate selection is CPU-only on TRAIN-internal chronological folds.
The GPU is an independent performance/equivalence control, never a candidate
selection confound. Later periods are already-known retrospective diagnostics.
Full-period artifacts are quarantined research fits, not calibrated offers.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import gc
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/research/db-shared-feature-optimization-20261012.json"
OUTPUT_ROOT = ROOT / "reports/generated/db-shared-feature-optimization"
WORKER = ROOT / "scripts/optimize_db_shared_features_worker.py"
DESIGN = ROOT / "scripts/db_shared_feature_optimization_design.py"
APP_PYTHON = ROOT / "python-api/.venv/Scripts/python.exe"


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def object_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def write_json(path, value, *, exclusive=True):
    destination = Path(path)
    if exclusive:
        with destination.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
    else:
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        os.replace(temporary, destination)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def modules():
    for directory in (ROOT / "python-api", ROOT / "keiba", ROOT / "scripts"):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    from training import db_shared_fit, db_shared_acceleration
    from evaluate_db_shared_venues import preservation_receipt, date_cluster_bootstrap
    from benchmark_db_shared_full_worker import read_cache
    import db_shared_feature_optimization_design as design
    return db_shared_fit, db_shared_acceleration, preservation_receipt, date_cluster_bootstrap, read_cache, design


def validate_protocol(protocol):
    if (not isinstance(protocol, dict)
            or protocol.get("schema") != "db-shared-feature-optimization-protocol-v1"
            or protocol.get("requested_period") != ["2016-01", "2026-09"]
            or protocol.get("selection_device") != "app CPU only"
            or protocol.get("maximum_total_candidates") != 76
            or protocol.get("training_end") != "20220521"
            or protocol.get("all_historical_evaluation_periods_previously_seen") is not True):
        raise ValueError("feature-study-protocol-invalid")
    for key in ("roi_used_for_selection", "win_probability_authority_granted", "full_period_fit_performance_claimed",
                "artifact_published", "active_model_changed", "forward_collection_changed", "commit_push", "real_purchase"):
        if protocol.get(key) is not False:
            raise ValueError("feature-study-protected-scope-invalid:" + key)
    if (protocol.get("maximum_cpu_workers") != 3 or protocol.get("native_threads_per_worker") != 2
            or protocol.get("maximum_initial_candidates") != 68 or protocol.get("maximum_refinement_candidates") != 8
            or protocol.get("rank_tie_tolerance") != .0002
            or protocol.get("maximum_development_rmse_relative_deterioration") != .01):
        raise ValueError("feature-study-predeclared-bounds-invalid")
    fixed = {
        "snapshot_sha256": "8880c290e31ae614f2c837dd00bc773f07e24ef6919e611a3f645927bc679a7e",
        "cache_manifest_sha256": "befeec2778200177dbb0b6ce3b9843e52df2fcfbd5102a108ed18deeae994c01",
        "cache_directory": "reports/generated/full-db-learning-benchmarks/20261011T092435131929Z-c0a39dfe/raw47-cache",
        "maximum_cpu_workers": 3, "native_threads_per_worker": 2,
        "maximum_initial_candidates": 68, "maximum_refinement_candidates": 8,
        "maximum_total_candidates": 76, "rank_tie_tolerance": .0002,
        "maximum_development_rmse_relative_deterioration": .01,
        "base_feature_count": 47, "reserved_physical_cores": 2, "reserved_memory_gib": 4,
        "minimum_memory_per_worker_gib": 1.5, "maximum_worker_seconds": 600,
        "bootstrap_repetitions": 2000, "bootstrap_seed": 20261012,
        "gpu_control_only": True, "full_period_fit_after_candidate_lock": True,
        "calibration_diagnostic_period": ["20220522", "20240721"],
        "test_diagnostic_period": ["20240727", "20260913"],
        "diagnostic_train_period": ["20160101", "20220521"],
        "fixed_preset": {"id": "standard", "num_boost_round": 200, "num_leaves": 31, "min_data_in_leaf": 30, "lambda_l2": 1.0},
        "gpu_quality_bounds": {"rank_correlation_absolute_difference": .001, "rmse_relative_difference": .001},
    }
    for key, expected in fixed.items():
        if type(protocol.get(key)) is not type(expected) or object_hash(protocol[key]) != object_hash(expected):
            raise ValueError("feature-study-fixed-condition-invalid:" + key)
    other_keys = {"schema", "requested_period", "primary", "secondary", "selection_device", "training_end",
        "all_historical_evaluation_periods_previously_seen", "test_label", "bootstrap_scope", "roi_used_for_selection",
        "win_probability_authority_granted", "full_period_fit_performance_claimed", "artifact_published", "active_model_changed",
        "forward_collection_changed", "commit_push", "real_purchase"}
    if set(protocol) != set(fixed) | other_keys:
        raise ValueError("feature-study-exact-protocol-schema-required")
    for key in ("snapshot_sha256", "cache_manifest_sha256"):
        if not isinstance(protocol.get(key), str) or re.fullmatch(r"[0-9a-f]{64}", protocol[key]) is None:
            raise ValueError("feature-study-digest-invalid")
    return protocol


def resource_allocation(protocol, features_bytes):
    import psutil
    validate_protocol(protocol)
    if type(features_bytes) is not int or features_bytes <= 0:
        raise ValueError("feature-study-feature-byte-size-invalid")
    physical = psutil.cpu_count(logical=False) or 1
    available = int(psutil.virtual_memory().available)
    per_worker = max(int(protocol["minimum_memory_per_worker_gib"] * 1024**3), features_bytes * 6)
    reserve = int(protocol["reserved_memory_gib"] * 1024**3)
    capacity = min(protocol["maximum_cpu_workers"],
                   max(0, (physical - protocol["reserved_physical_cores"]) // protocol["native_threads_per_worker"]),
                   max(0, (available - reserve) // per_worker))
    if capacity < 1:
        raise ValueError("feature-study-no-safe-resource-slot")
    return {"cpu_workers": int(capacity), "native_threads_per_worker": 2,
            "physical_cores": physical, "logical_cores": psutil.cpu_count(),
            "memory_available_bytes": available, "estimated_bytes_per_worker": per_worker,
            "reserved_memory_bytes": reserve, "reserved_physical_cores": 2,
            "gpu_concurrent_fits": 1}


def preflight():
    if os.environ.get("FEATURE_OPTIMIZATION_ENV", "local") not in {"local", "development", "test"}:
        raise ValueError("feature-study-offline-local-only")
    fit, acceleration, preserve, bootstrap, read_cache, design = modules()
    protocol = validate_protocol(json.loads(CONFIG.read_text(encoding="utf-8")))
    cache = (ROOT / protocol["cache_directory"]).resolve(strict=True)
    if not cache.is_relative_to((ROOT / "reports/generated").resolve()):
        raise ValueError("feature-study-cache-boundary-invalid")
    manifest_path = cache / "manifest.json"
    if file_hash(manifest_path) != protocol["cache_manifest_sha256"]:
        raise ValueError("feature-study-cache-manifest-changed")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    snapshot = ROOT / "keiba/data/model_retrain_snapshots" / (protocol["snapshot_sha256"] + ".db")
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(snapshot) + suffix)
        if sidecar.is_file() and sidecar.stat().st_size:
            raise ValueError("feature-study-checkpointed-snapshot-required")
    if file_hash(snapshot) != protocol["snapshot_sha256"]:
        raise ValueError("feature-study-snapshot-byte-hash-mismatch")
    if manifest["source_hashes"] != fit.source_hashes():
        raise ValueError("feature-study-source-pins-changed")
    request = {"snapshot_sha256": protocol["snapshot_sha256"], "period": protocol["requested_period"],
               "expected_source_hashes": manifest["source_hashes"]}
    target, features, checked = read_cache(cache, request, fit)
    split_indices = fit.date_partitions(target)
    partitions = {name: fit._partition_receipt(target.iloc[indices]) for name, indices in split_indices.items()}
    if (partitions["TRAIN"]["date_to"] != protocol["training_end"]
            or [partitions["CAL"]["date_from"], partitions["CAL"]["date_to"]] != protocol["calibration_diagnostic_period"]
            or [partitions["TEST"]["date_from"], partitions["TEST"]["date_to"]] != protocol["test_diagnostic_period"]):
        raise ValueError("feature-study-fixed-partitions-changed")
    specs = design.initial_candidates(list(fit.FEATURE_COLUMNS))
    if len(specs) > protocol["maximum_initial_candidates"]:
        raise ValueError("feature-study-initial-budget-exceeded")
    resources = resource_allocation(protocol, int(target.memory_usage(deep=True).sum() + features.memory_usage(deep=True).sum()))
    quality = {"row_count": len(target), "race_count": int(target.race_id.nunique()),
        "date_count": int(target.race_date.nunique()), "date_from": str(target.race_date.min()),
        "date_to": str(target.race_date.max()), "duplicate_runner_rows": int(target.duplicated(["race_id", "horse_number"]).sum()),
        "feature_missing_rate": float(features.isna().to_numpy().mean()),
        "feature_missingness": {column: float(features[column].isna().mean()) for column in features},
        "current_outcomes_consumed": checked["point_in_time_audit"]["current_outcomes_consumed"],
        "same_day_or_future_history_used": checked["point_in_time_audit"]["same_day_or_future_history_used"]}
    result = {"protocol": protocol, "protocol_config_sha256": file_hash(CONFIG), "cache": str(cache),
              "cache_manifest": checked, "partitions": partitions, "resources": resources,
              "quality": quality, "initial_candidates": specs, "development_folds": design.selection_folds(),
              "preservation_before": preserve()}
    del target, features
    gc.collect()
    return result


def _read_races(result, output_directory):
    import pandas as pd
    artifact = next(value for value in result["artifacts"].values()
                    if str(value["path"]).endswith("-races.parquet"))
    path = Path(artifact["path"])
    if not path.is_absolute():
        path = output_directory / path
    path = path.resolve(strict=True)
    if not path.is_relative_to(output_directory.resolve()) or file_hash(path) != artifact["sha256"]:
        raise ValueError("feature-study-race-artifact-invalid")
    return pd.read_parquet(path)


def _validate_records(records):
    import numpy as np
    import pandas as pd
    required = {"split_id", "race_id", "race_date", "runner_count", "rank_correlation", "squared_speed_error_sum"}
    if (not isinstance(records, pd.DataFrame) or records.empty or records.columns.duplicated().any()
            or not required.issubset(records.columns)):
        raise ValueError("feature-study-finite-complete-race-records-required")
    numeric = records[["runner_count", "rank_correlation", "squared_speed_error_sum"]].apply(pd.to_numeric, errors="coerce")
    if (not np.isfinite(numeric.to_numpy(float)).all() or not numeric.runner_count.between(2, 18).all()
            or not numeric.runner_count.eq(np.floor(numeric.runner_count)).all()
            or not numeric.rank_correlation.between(-1, 1).all() or numeric.squared_speed_error_sum.lt(0).any()
            or any(records[column].map(lambda value: isinstance(value, (bool, np.bool_))).any()
                   for column in numeric.columns)
            or any(not records[column].map(lambda value: isinstance(value, (int, float, np.integer, np.floating))).all()
                   for column in numeric.columns)
            or records.duplicated(["split_id", "race_id"]).any()
            or records.split_id.isna().any() or not records.split_id.map(lambda value:
                isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value) is not None).all()
            or records.race_id.isna().any() or records.race_id.astype(str).str.strip().eq("").any()):
        raise ValueError("feature-study-race-record-values-invalid")
    dates = records.race_date.astype(str)
    if not dates.str.fullmatch(r"[0-9]{8}").all() or pd.to_datetime(dates, format="%Y%m%d", errors="coerce").isna().any():
        raise ValueError("feature-study-race-record-date-invalid")


def _paired_statistics(baseline, candidate, bootstrap, protocol):
    _validate_records(baseline)
    _validate_records(candidate)
    keys = ["split_id", "race_id"]
    if baseline.duplicated(keys).any() or candidate.duplicated(keys).any():
        raise ValueError("feature-study-paired-duplicates")
    paired = candidate.merge(baseline[keys + ["race_date", "runner_count", "rank_correlation", "squared_speed_error_sum"]],
                             on=keys, suffixes=("", "_baseline"), how="outer", indicator=True, validate="one_to_one")
    if (paired._merge.ne("both").any() or not paired.race_date.eq(paired.race_date_baseline).all()
            or not paired.runner_count.eq(paired.runner_count_baseline).all()):
        raise ValueError("feature-study-paired-cohort-mismatch")
    paired["year"] = paired.race_date.astype(str).str[:4].astype(int)
    paired["global_rank_correlation"] = paired.rank_correlation_baseline
    paired["squared_error_sum"] = paired.squared_speed_error_sum
    paired["global_squared_error_sum"] = paired.squared_speed_error_sum_baseline
    result = bootstrap(paired, repetitions=protocol["bootstrap_repetitions"], seed=protocol["bootstrap_seed"])
    result["selection_adjusted"] = False
    result["interpretation"] = "paired historical diagnostic; not independent prospective evidence or multiplicity-adjusted feature importance"
    return result


def _summary(records):
    _validate_records(records)
    runners = int(records.runner_count.sum())
    return {"race_count": len(records), "runner_count": runners,
        "rank_correlation": float(records.rank_correlation.mean()),
        "rmse_speed_mps": float(math.sqrt(records.squared_speed_error_sum.sum() / runners)),
        "date_from": str(records.race_date.min()), "date_to": str(records.race_date.max())}


def run(execute=False):
    prepared = preflight()
    if not execute:
        return {"mode": "read-only-preflight", "quality": prepared["quality"],
                "partitions": prepared["partitions"], "resources": prepared["resources"],
                "candidate_count": len(prepared["initial_candidates"]), "learning_started": False}
    fit, acceleration, preserve, bootstrap, _, design = modules()
    started = time.perf_counter()
    protocol = prepared["protocol"]
    identifier = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
    output = OUTPUT_ROOT / identifier
    output.mkdir(parents=True, exist_ok=False)
    # Lock every initial schema, rule and source identity BEFORE the first fit.
    locked = {**prepared, "run_id": identifier,
              "source_manifest": {str(path.relative_to(ROOT)): file_hash(path) for path in
                  (Path(__file__), WORKER, DESIGN, CONFIG, ROOT / "scripts/benchmark_db_shared_full_worker.py")},
              "evaluation_scope": "all historical periods known; DEV selection only; CAL/TEST never used to create or choose candidates"}
    locked_sha = object_hash(locked)
    write_json(output / "protocol.json", locked)
    write_json(output / "protocol-hash.json", {"sha256": locked_sha})
    print(json.dumps({"event": "experiment_locked", "run_dir": str(output), "protocol_sha256": locked_sha,
                      "initial_candidates": len(prepared["initial_candidates"]), "resources": prepared["resources"]}), flush=True)
    runtimes = {"cpu": acceleration.resolve_runtime("cpu")}
    gpu_failure = None
    try:
        runtimes["gpu"] = acceleration.resolve_runtime("gpu")
    except ValueError as exc:
        gpu_failure = str(exc)
    write_json(output / "runtimes.json", {"runtimes": runtimes, "gpu_unavailable_reason": gpu_failure})
    results, completed, active, peak = {}, [], 0, 0
    lock = threading.Lock()

    def execute_candidate(spec, phase, splits, device="cpu", save_models=False):
        nonlocal active, peak
        directory = output / phase / spec["id"]
        directory.mkdir(parents=True, exist_ok=False)
        request = {"schema": "db-shared-feature-optimization-worker-v1", "candidate_id": spec["id"],
            "phase": "development" if phase in {"stage1", "stage2", "controls"} else "retrospective" if phase == "diagnostics" else "full_fit",
            "cache_dir": prepared["cache"], "cache_manifest_sha256": protocol["cache_manifest_sha256"],
            "expected_source_hashes": prepared["cache_manifest"]["source_hashes"], "protocol_sha256": locked_sha,
            "feature_columns": spec["feature_columns"], "splits": splits, "device": device, "n_threads": 2,
            "execution_receipt": runtimes[device]["execution_receipt"] if device == "gpu" else None,
            "save_models": save_models}
        request_path, result_path = directory / "request.json", directory / "result.json"
        write_json(request_path, request)
        clock = time.perf_counter()
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            with (directory / "stdout.log").open("x", encoding="utf-8") as stdout, (directory / "stderr.log").open("x", encoding="utf-8") as stderr:
                process = subprocess.run([runtimes[device]["interpreter"], "-X", "utf8", str(WORKER),
                    "--request", str(request_path), "--output", str(result_path)], cwd=str(ROOT),
                    stdout=stdout, stderr=stderr, timeout=protocol["maximum_worker_seconds"],
                    creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0,
                    env={**os.environ, "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2", "MKL_NUM_THREADS": "2", "PYTHONUNBUFFERED": "1"})
            if process.returncode != 0 or not result_path.is_file():
                raise RuntimeError("feature-study-worker-failed:" + phase + ":" + spec["id"])
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if (result.get("status") != "completed" or result.get("candidate_id") != spec["id"]
                    or result.get("device") != device or result.get("feature_columns") != spec["feature_columns"]):
                raise ValueError("feature-study-worker-result-binding-mismatch")
            result["subprocess_wall_seconds"] = time.perf_counter() - clock
            result["result_path"] = str(result_path)
            with lock:
                completed.append({"phase": phase, "id": spec["id"], "device": device,
                    "wall_seconds": result["subprocess_wall_seconds"], "result_sha256": file_hash(result_path)})
                write_json(output / "progress.json", {"status": "running", "completed": completed,
                    "active_workers": active - 1, "peak_workers": peak, "elapsed_seconds": time.perf_counter() - started}, exclusive=False)
                print(json.dumps({"event": "candidate_completed", "phase": phase, "id": spec["id"],
                    "device": device, "completed_jobs": len(completed), "seconds": result["subprocess_wall_seconds"]}), flush=True)
            return result
        finally:
            with lock:
                active -= 1

    try:
        baseline = next(spec for spec in prepared["initial_candidates"] if spec["id"] == "all47")
        folds = prepared["development_folds"]
        # Probe GPU independently. Homogeneous CPU selection remains fixed even
        # if this one control runs faster; a device change is not feature value.
        with ThreadPoolExecutor(max_workers=min(2, prepared["resources"]["cpu_workers"])) as executor:
            cpu_future = executor.submit(execute_candidate, baseline, "controls", folds)
            gpu_future = (executor.submit(execute_candidate, {**baseline, "id": "gpu-control47"}, "controls", folds, "gpu")
                          if "gpu" in runtimes else None)
            cpu_control = cpu_future.result()
            try:
                gpu_control = gpu_future.result() if gpu_future else None
            except (RuntimeError, ValueError) as exc:
                gpu_control, gpu_failure = None, str(exc)
        baseline_records = _read_races(cpu_control, Path(cpu_control["result_path"]).parent)
        results["all47"] = cpu_control
        gpu_comparison = {"available": gpu_control is not None, "failure": gpu_failure,
                          "used_for_feature_selection": False, "single_control_timing_not_repeated_benchmark": True}
        if gpu_control:
            gpu_records = _read_races(gpu_control, Path(gpu_control["result_path"]).parent)
            cpu_metrics, gpu_metrics = _summary(baseline_records), _summary(gpu_records)
            _paired_statistics(baseline_records, gpu_records, bootstrap, protocol)  # Assert whole cohort identity.
            rho_delta = gpu_metrics["rank_correlation"] - cpu_metrics["rank_correlation"]
            rmse_relative = abs(gpu_metrics["rmse_speed_mps"] - cpu_metrics["rmse_speed_mps"]) / cpu_metrics["rmse_speed_mps"]
            gpu_comparison.update({"cpu": cpu_metrics, "gpu": gpu_metrics, "rank_delta": rho_delta,
                "rmse_relative_difference": rmse_relative, "cpu_seconds": cpu_control["subprocess_wall_seconds"],
                "gpu_seconds": gpu_control["subprocess_wall_seconds"],
                "quality_pass": abs(rho_delta) <= .001 and rmse_relative <= .001})
        write_json(output / "gpu-control.json", gpu_comparison)

        def run_stage(specs, phase):
            with ThreadPoolExecutor(max_workers=prepared["resources"]["cpu_workers"]) as executor:
                futures = {executor.submit(execute_candidate, spec, phase, folds): spec for spec in specs}
                for future in as_completed(futures):
                    results[futures[future]["id"]] = future.result()

        run_stage([spec for spec in prepared["initial_candidates"] if spec["id"] != "all47"], "stage1")
        def summarize(specs):
            records = {spec["id"]: _read_races(results[spec["id"]], Path(results[spec["id"]]["result_path"]).parent) for spec in specs}
            return design.summarize_candidates(specs, records, [fold["split_id"] for fold in folds])
        stage1_summaries = summarize(prepared["initial_candidates"])
        write_json(output / "stage1-summaries.json", stage1_summaries)
        refinement = design.stage2_candidates(list(fit.FEATURE_COLUMNS), prepared["initial_candidates"], stage1_summaries)
        # Refinement depends only on DEV results; lock it before its first fit.
        write_json(output / "stage2-candidate-lock.json", {"candidate_specs": refinement,
            "stage1_result_sha256": file_hash(output / "stage1-summaries.json"), "maximum": 8,
            "cal_or_test_consulted": False})
        run_stage(refinement, "stage2")
        all_specs = prepared["initial_candidates"] + refinement
        if len(all_specs) > protocol["maximum_total_candidates"]:
            raise ValueError("feature-study-total-budget-exceeded")
        summaries = summarize(all_specs)
        selected = design.select_candidate(summaries)
        selected_spec = next(spec for spec in all_specs if spec["id"] == selected["id"])
        write_json(output / "selected-candidate-lock.json", {"summary": selected, "spec": selected_spec,
            "all_summaries_sha256": object_hash(summaries), "selection_scope": "TRAIN_ONLY",
            "cal_or_test_consulted": False})
        write_json(output / "development-comparison.json", summaries)
        print(json.dumps({"event": "candidate_frozen", "selected": selected}), flush=True)

        # Only now reveal reused later-period results. No subsequent candidate
        # selection, threshold tuning or pruning is allowed based on them.
        diagnostic_splits = [{"split_id": label, "train_date_from": "20160101", "train_date_to": protocol["training_end"],
            "eval_date_from": period[0], "eval_date_to": period[1]} for label, period in
            (("REUSED_CAL", protocol["calibration_diagnostic_period"]), ("REUSED_TEST", protocol["test_diagnostic_period"]))]
        diagnostic_specs = [baseline] + ([selected_spec] if selected_spec["id"] != "all47" else [])
        with ThreadPoolExecutor(max_workers=min(2, prepared["resources"]["cpu_workers"])) as executor:
            diagnostic_results = list(executor.map(lambda spec: execute_candidate(spec, "diagnostics", diagnostic_splits, save_models=True), diagnostic_specs))
        diagnostic_records = {row["candidate_id"]: _read_races(row, Path(row["result_path"]).parent) for row in diagnostic_results}
        baseline_diagnostics = diagnostic_records["all47"]
        winner_diagnostics = diagnostic_records[selected_spec["id"]]
        diagnosis = []
        for split_id, base in baseline_diagnostics.groupby("split_id", sort=True):
            candidate = winner_diagnostics.loc[winner_diagnostics.split_id.eq(split_id)]
            diagnosis.append({"split_id": split_id, "baseline": _summary(base), "selected": _summary(candidate),
                              "paired_bootstrap_95": _paired_statistics(base, candidate, bootstrap, protocol)})
        yearly = []
        for year, base in baseline_diagnostics.groupby(baseline_diagnostics.race_date.astype(str).str[:4]):
            candidate = winner_diagnostics.loc[winner_diagnostics.race_date.astype(str).str[:4].eq(year)]
            yearly.append({"year": year, "baseline": _summary(base), "selected": _summary(candidate)})
        write_json(output / "retrospective-diagnostics.json", {"periods": diagnosis, "by_year": yearly,
            "previously_seen_data": True, "used_for_selection": False, "roi_evaluated": False,
            "independent_future_validation_required": True})

        full_split = [{"split_id": "FULL2016_2026", "train_date_from": "20160101", "train_date_to": "20260930",
                       "eval_date_from": None, "eval_date_to": None}]
        full_models = []
        for spec, identifier_ in ((baseline, "full-baseline47-cpu"), (selected_spec, "full-selected-cpu")):
            full_models.append(execute_candidate({**spec, "id": identifier_}, "full_fit", full_split, save_models=True))
        if gpu_comparison.get("quality_pass"):
            full_models.append(execute_candidate({**selected_spec, "id": "full-selected-gpu"}, "full_fit", full_split, "gpu", True))
        after = preserve()
        if after != prepared["preservation_before"]:
            raise ValueError("feature-study-protected-state-changed")
        for relative, expected in locked["source_manifest"].items():
            if file_hash(ROOT / relative) != expected:
                raise ValueError("feature-study-source-changed-during-run")
        report = {"schema": "db-shared-feature-optimization-result-v1", "status": "completed",
            "run_id": locked["run_id"], "run_directory": str(output), "protocol_sha256": locked_sha,
            "quality": prepared["quality"], "partitions": prepared["partitions"], "resources": prepared["resources"],
            "candidate_count": len(all_specs), "development_baseline": next(row for row in summaries if row["id"] == "all47"),
            "selected": selected, "selected_spec": selected_spec,
            "highest_development_rank": max(summaries, key=lambda row: row["rank_correlation"]),
            "diagnostics": diagnosis, "yearly_diagnostics": yearly, "gpu_control": gpu_comparison,
            "full_period_fits": full_models, "elapsed_seconds": time.perf_counter() - started,
            "completed_worker_jobs": completed, "peak_concurrent_worker_processes": peak,
            "summed_worker_wall_seconds_not_standalone_sequential_benchmark": sum(row["wall_seconds"] for row in completed),
            "feature_generation_reused": True, "roi_evaluated": False, "probability_validation_granted": False,
            "full_period_model_out_of_sample_accuracy_claimed": False, "active_model_changed": False,
            "forward_collection_changed": False, "artifact_published": False, "future_validation_required": True,
            "preservation_before": prepared["preservation_before"], "preservation_after": after}
        write_json(output / "results.json", report)
        manifest_files = {str(path.relative_to(output)): {"sha256": file_hash(path), "bytes": path.stat().st_size}
                          for path in output.rglob("*") if path.is_file() and path.name != "progress.json"}
        write_json(output / "artifact-manifest.json", {"protocol_sha256": locked_sha, "files": manifest_files,
                                                      "all_models_research_only": True})
        write_json(output / "progress.json", {"status": "completed", "completed_jobs": len(completed),
            "candidate_count": len(all_specs), "elapsed_seconds": report["elapsed_seconds"], "results_path": str(output / "results.json")}, exclusive=False)
        return {"status": "completed", "results_path": str(output / "results.json"),
                "candidate_count": len(all_specs), "selected": selected,
                "diagnostics": diagnosis, "elapsed_seconds": report["elapsed_seconds"]}
    except BaseException as exc:
        write_json(output / "failure.json", {"status": "failed", "error": str(exc),
            "completed_jobs": completed, "elapsed_seconds": time.perf_counter() - started,
            "preservation_after": preserve()}, exclusive=True)
        write_json(output / "progress.json", {"status": "failed", "error": str(exc),
            "completed_jobs": len(completed)}, exclusive=False)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Run the predeclared finite offline study; otherwise read-only preflight")
    args = parser.parse_args()
    print(json.dumps(run(args.execute), ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
