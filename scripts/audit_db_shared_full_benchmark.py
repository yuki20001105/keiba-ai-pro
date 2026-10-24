"""Read-only evidence audit and new derived result; never rerun a fit.

The initial parent merge replaced optional full-row measurement dictionaries
with request booleans. Immutable worker JSONs retain all nine measurements.
Recover only from manifest-verified raw evidence without changing that run.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import statistics
import uuid

ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = ROOT / "reports/generated/full-db-learning-benchmarks"
OUTPUT_ROOT = ROOT / "reports/generated/full-db-learning-benchmark-analysis"


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def audit(run):
    import numpy as np
    run = Path(run).resolve(strict=True)
    if run.parent != RUN_ROOT.resolve():
        raise ValueError("benchmark-audit-original-run-boundary-required")
    report = json.loads((run / "results.json").read_text(encoding="utf-8"))
    manifest = json.loads((run / "artifact_manifest.json").read_text(encoding="utf-8"))
    for relative, expected in manifest["files"].items():
        path = (run / relative).resolve(strict=True)
        if not path.is_relative_to(run) or file_hash(path) != expected:
            raise ValueError("benchmark-audit-evidence-file-mismatch")
    actual_files = {str(path.relative_to(run)) for path in run.rglob("*") if path.is_file() and path.name != "artifact_manifest.json"}
    if actual_files != set(manifest["files"]):
        raise ValueError("benchmark-audit-inventory-mismatch")
    if report["status"] != "completed" or not all(report[name] for name in (
            "preservation_unchanged", "snapshot_unchanged", "source_unchanged", "scripts_unchanged")):
        raise ValueError("benchmark-audit-original-preservation-pass-required")
    original_parent = ROOT / "scripts/benchmark_db_shared_full.py"
    parent_relative = str(original_parent.relative_to(ROOT))
    expected_parent = report["script_hashes"][parent_relative]
    if file_hash(original_parent) != expected_parent:
        archived_sources = [path for path in OUTPUT_ROOT.glob("*/measurement-source/benchmark_db_shared_full.py")
                            if file_hash(path) == expected_parent]
        if not archived_sources:
            raise ValueError("benchmark-audit-needs-original-parent-source-before-correction")
        original_parent = sorted(archived_sources)[0]
    spec = importlib.util.spec_from_file_location("benchmark_parent_evidence", original_parent)
    parent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parent)
    if parent.object_hash(report["protocol"]) != report["protocol_sha256"] or report["protocol_sha256"] != parent.PROTOCOL_SHA:
        raise ValueError("benchmark-audit-fixed-protocol-mismatch")
    runs, reference, reference_vectors = [], None, None
    for aggregated, planned in zip(report["runs"], parent.schedule(report["protocol"]), strict=True):
        path = Path(aggregated["worker_json"]).resolve(strict=True)
        if path.parent != run or str(path.relative_to(run)) not in manifest["files"]:
            raise ValueError("benchmark-audit-worker-boundary-required")
        raw = json.loads(path.read_text(encoding="utf-8"))
        mode = report["protocol"]["modes"][planned["mode"]]
        if (raw["status"] != "completed" or raw["request"]["mode"] != planned["mode"]
                or raw["request"]["input_mode"] != planned["input_mode"]
                or raw["request"]["full_row_timing"] != planned["full_row_timing"]
                or raw["script_sha256"] != report["script_hashes"][str(Path("scripts/benchmark_db_shared_full_worker.py"))]):
            raise ValueError("benchmark-audit-worker-plan-or-script-mismatch")
        evidence = dict(raw)
        evidence.update({key: value for key, value in planned.items() if key != "full_row_timing"})
        evidence.update(runtime=mode["runtime"], device=mode["device"], worker_json=str(path),
                        full_row_timing_requested=planned["full_row_timing"],
                        run_processing_seconds=raw["timings"]["run_processing_seconds"],
                        stage_seconds={"fit_pipeline": raw["timings"]["fit_selection_calibration_evaluation_seconds"]},
                        input_identity={"cohort": raw["cohort"], "input_hashes": raw["input_hashes"],
                                        "cache_key_sha256": raw["cache_key_sha256"]})
        with np.load(raw["vectors"]["path"], allow_pickle=False) as vector_file:
            vectors = dict(vector_file)
        if reference is None:
            reference, reference_vectors = evidence, vectors
        quality = parent.quality_check(reference, evidence, report["protocol"], vectors_reference=reference_vectors, vectors_actual=vectors)
        if quality != aggregated["quality"] or not quality["passed"]:
            raise ValueError("benchmark-audit-quality-recheck-mismatch")
        evidence["quality"] = quality
        if planned["full_row_timing"]:
            probe = evidence["full_row_timing"]
            if (probe["runner_count"] != 453857 or probe["race_count"] != 32519
                    or probe["preset"]["id"] != "standard" or probe["preset"]["num_boost_round"] != 200
                    or not probe["finite_predictions"] or probe["finite_sample_count"] != 32
                    or probe["independent_test_evaluated"] or probe["calibrated"] or probe["model_saved"]):
                raise ValueError("benchmark-audit-all-row-scope-mismatch")
        if mode["device"] == "gpu" and not raw["gpu_learner_confirmed"]:
            raise ValueError("benchmark-audit-actual-gpu-evidence-required")
        runs.append(evidence)
    if parent.summarize(runs, report["protocol"]) != report["summary"]:
        raise ValueError("benchmark-audit-existing-summary-mismatch")
    summary = parent.summarize(runs, report["protocol"])
    full_rows = parent.summarize_full_rows(runs)
    if len(full_rows) != 3 or sum(row["repetitions"] for row in full_rows) != 9:
        raise ValueError("benchmark-audit-nine-all-row-measurements-required")
    stage_summary = []
    for input_mode, mode in (("cold", "C"), ("cache", "C"), ("cache", "GPU")):
        group = [row for row in runs if not row["initialization"] and row["input_mode"] == input_mode and row["mode"] == mode]
        names = ("db_read_seconds", "feature_generation_seconds", "prepare_validation_seconds", "cache_validate_and_read_seconds", "fit_selection_calibration_evaluation_seconds")
        entry = {"input_mode": input_mode, "mode": mode, "repetitions": len(group)}
        entry["median_timings"] = {name: statistics.median(row["timings"][name] for row in group) for name in names if name in group[0]["timings"]}
        entry["median_booster_time_sum_not_parallel_wall"] = statistics.median(sum(stage["seconds"] for stage in row["fit_stages"] if stage["kind"] == "booster") for row in group)
        for name in ("fit_temperature", "probability_metrics"):
            entry["median_" + name + "_seconds"] = statistics.median(sum(stage["seconds"] for stage in row["fit_stages"] if stage["kind"] == name) for row in group)
        stage_summary.append(entry)
    cpu = next(row for row in summary if row["mode"] == "C" and row["input_mode"] == "cache")
    gpu = next(row for row in summary if row["mode"] == "GPU")
    control = next(row for row in summary if row["mode"] == "GPU_BUILD_CPU")
    gpu_rows = [row for row in runs if row["mode"] == "GPU"]
    result = {"schema": "full-db-learning-benchmark-evidence-audit-v1", "status": "PASS",
              "original_results": str(run / "results.json"), "original_results_sha256": file_hash(run / "results.json"),
              "original_manifest_sha256": file_hash(run / "artifact_manifest.json"), "verified_file_count": len(manifest["files"]),
              "source_run_unmodified": True, "actual_learning_rerun": False,
              "all_20_quality_checks_recomputed_and_passed": True,
              "aggregation_correction": "Recover full_row_timing dictionaries from immutable raw worker JSONs; original parent metadata merge overwrote only the aggregate field with request bool. Original files retained.",
              "summary": summary, "full_row_summary": full_rows, "stage_summary": stage_summary,
              "cache_GPU_pipeline_speedup_vs_cache_current_CPU": cpu["median_processing_seconds"] / gpu["median_processing_seconds"],
              "same_GPU_build_device_speedup_whole_pipeline": control["median_processing_seconds"] / gpu["median_processing_seconds"],
              "gpu_metrics": [{"primary": row["fit_result"]["evaluation"]["primary"],
                               "nll": row["fit_result"]["probability_metrics"]["nll"],
                               "brier": row["fit_result"]["probability_metrics"]["brier"],
                               "quality": row["quality"]} for row in gpu_rows],
              "reference_metrics": {"primary": reference["fit_result"]["evaluation"]["primary"],
                                    "probability_metrics": reference["fit_result"]["probability_metrics"]},
              "partitions": reference["fit_result"]["partitions"],
              "preservation_unchanged": report["preservation_unchanged"], "study_wall_seconds": report["study_wall_seconds"]}
    destination = OUTPUT_ROOT / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8])
    destination.mkdir(parents=True, exist_ok=False)
    archived = destination / "measurement-source"
    archived.mkdir()
    for relative, expected in report["script_hashes"].items():
        original = original_parent if relative == parent_relative else ROOT / relative
        if file_hash(original) != expected:
            raise ValueError("benchmark-audit-original-script-before-archive-mismatch")
        target = archived / original.name
        shutil.copyfile(original, target)
        if file_hash(target) != expected:
            raise ValueError("benchmark-audit-source-archive-hash-mismatch")
    shutil.copyfile(Path(__file__), archived / Path(__file__).name)
    write_json(destination / "analysis.json", result)
    write_json(destination / "artifact_manifest.json", {"files": {str(path.relative_to(destination)): file_hash(path) for path in destination.rglob("*") if path.is_file()},
                                                       "audit_script_sha256": file_hash(__file__)})
    print(json.dumps({"status": "PASS", "analysis": str(destination / "analysis.json"),
                      "verified_original_files": len(manifest["files"]), "recovered_all_row_fits": 9}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    audit(parser.parse_args().run)
