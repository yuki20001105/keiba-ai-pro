"""Full-period parent benchmark contracts; no real fit or subprocess runs."""
from collections import Counter
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[2]


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


benchmark = load_script("full_benchmark_parent_test_fixture", "benchmark_db_shared_full.py")
worker = load_script("full_benchmark_request_test_fixture", "benchmark_db_shared_full_worker.py")


@pytest.fixture
def protocol():
    return json.loads(benchmark.PROTOCOL.read_text(encoding="utf-8"))


def evidence(*, runtime="app", device="cpu"):
    return {
        "runtime": runtime,
        "device": device,
        "input_identity": {"cohort": {"runner_count": 12, "race_count": 4},
                           "input_hashes": {"target": "a" * 64, "raw47": "b" * 64},
                           "cache_key_sha256": "c" * 64},
        "fit_result": {
            "partitions": {"TRAIN": {"date_to": "20200101"},
                           "CAL": {"date_from": "20200102"},
                           "TEST": {"date_from": "20210101"}},
            "selected_preset": "lean",
            "source_hashes": {"fixture": "d" * 64},
            "probability_validation": {"gate_results": {"finite": True, "enough_races": False}},
            "evaluation": {"primary": {"rank_correlation": .5, "rmse": 2.}},
            "probability_metrics": {"nll": 4., "brier": .5},
        },
    }


def vectors():
    return {name: np.array([1., 2., 3.])
            for name in ("inner_lean", "inner_standard", "cal", "test")}


def compare(protocol, candidate=None, actual_vectors=None, reference=None, reference_vectors=None):
    return benchmark.quality_check(
        reference or evidence(), candidate or evidence(), protocol,
        vectors_reference=vectors() if reference_vectors is None else reference_vectors,
        vectors_actual=vectors() if actual_vectors is None else actual_vectors,
    )


def test_protocol_digest_and_twenty_process_schedule_are_fixed(protocol):
    assert benchmark.object_hash(protocol) == benchmark.PROTOCOL_SHA
    original = deepcopy(protocol)
    rows = benchmark.schedule(protocol)
    assert protocol == original
    assert len(rows) == 20
    assert rows[0] == {"mode": "A", "input_mode": "cold", "repeat": 0,
                       "initialization": True, "full_row_timing": False}
    assert [row["mode"] for row in rows[1:5]] == ["C", "A", "A", "C"]
    assert [row["mode"] for row in rows[5:10]] == ["A", "B", "C", "GPU_BUILD_CPU", "GPU"]
    assert [row["mode"] for row in rows[10:15]] == ["GPU", "GPU_BUILD_CPU", "C", "B", "A"]
    assert [row["mode"] for row in rows[15:20]] == ["C", "GPU_BUILD_CPU", "GPU", "A", "B"]
    assert Counter((row["input_mode"], row["mode"]) for row in rows[1:]) == {
        ("cold", "A"): 2, ("cold", "C"): 2,
        **{("cache", mode): 3 for mode in protocol["cached_modes"]},
    }
    assert sum(row["initialization"] for row in rows) == 1
    probes = [row for row in rows if row["full_row_timing"]]
    assert len(probes) == 9
    assert all(row["input_mode"] == "cache" for row in probes)
    assert Counter(row["mode"] for row in probes) == {"A": 3, "GPU_BUILD_CPU": 3, "GPU": 3}


def test_cpu_exact_prediction_equivalence_accepts_same_evidence(protocol):
    checked = compare(protocol)
    assert checked["passed"] is True
    assert all(checked["checks"].values())
    assert set(checked["max_abs_score_differences"]) == set(vectors())
    assert all(value == 0 for value in checked["max_abs_score_differences"].values())
    assert checked["equivalence_scope"] == "deterministic CPU prediction equality"


@pytest.mark.parametrize("distance,passed", [(5e-11, True), (2e-10, False)])
def test_cpu_tolerance_covers_inner_cal_and_test_predictions(protocol, distance, passed):
    changed = vectors()
    changed["inner_standard"][0] += distance
    checked = compare(protocol, actual_vectors=changed)
    assert checked["passed"] is passed
    assert checked["checks"]["all_prediction_vectors_exact_within_tolerance"] is passed


@pytest.mark.parametrize("damage", ["empty_keys", "missing", "extra", "empty_vector",
                                   "shape", "matrix", "nan", "inf"])
@pytest.mark.parametrize("runtime,device", [("app", "cpu"), ("gpu", "gpu")])
def test_required_vectors_fail_closed_for_cpu_and_gpu(protocol, damage, runtime, device):
    changed = vectors()
    reference = vectors()
    if damage == "empty_keys":
        changed, reference = {}, {}
    elif damage == "missing":
        changed.pop("cal")
    elif damage == "extra":
        changed["unregistered"] = np.ones(3)
    elif damage == "empty_vector":
        changed["test"] = reference["test"] = np.array([])
    elif damage == "shape":
        changed["test"] = np.ones(2)
    elif damage == "matrix":
        changed["test"] = reference["test"] = np.ones((3, 1))
    else:
        changed["cal"][0] = np.nan if damage == "nan" else np.inf
    checked = compare(protocol, candidate=evidence(runtime=runtime, device=device),
                      actual_vectors=changed, reference_vectors=reference)
    assert checked["passed"] is False
    json.dumps(checked, allow_nan=False)


@pytest.mark.parametrize("damage,check", [
    ("partition", "partitions_match"), ("preset", "selected_preset_match"),
    ("source", "source_hashes_match"), ("gate", "probability_gate_results_match"),
    ("identity", "input_identity_match"),
])
@pytest.mark.parametrize("runtime,device", [("app", "cpu"), ("gpu", "gpu")])
def test_shared_lineage_and_selected_preset_are_mandatory(protocol, damage, check, runtime, device):
    changed = evidence(runtime=runtime, device=device)
    if damage == "partition":
        changed["fit_result"]["partitions"]["TEST"]["date_from"] = "20220101"
    elif damage == "preset":
        changed["fit_result"]["selected_preset"] = "standard"
    elif damage == "source":
        changed["fit_result"]["source_hashes"]["fixture"] = "e" * 64
    elif damage == "gate":
        changed["fit_result"]["probability_validation"]["gate_results"]["enough_races"] = True
    else:
        changed["input_identity"]["input_hashes"]["raw47"] = "f" * 64
    checked = compare(protocol, candidate=changed)
    assert checked["passed"] is False
    assert checked["checks"][check] is False


@pytest.mark.parametrize("runtime,device", [("gpu", "cpu"), ("gpu", "gpu")])
def test_gpu_build_control_and_device_use_fixed_metric_bounds(protocol, runtime, device):
    changed = evidence(runtime=runtime, device=device)
    changed["fit_result"]["evaluation"]["primary"].update(rank_correlation=.5005, rmse=2.001)
    changed["fit_result"]["probability_metrics"].update(nll=4.002, brier=.50025)
    altered = {key: value + .2 for key, value in vectors().items()}
    checked = compare(protocol, candidate=changed, actual_vectors=altered)
    assert checked["passed"] is True
    assert "all_prediction_vectors_exact_within_tolerance" not in checked["checks"]
    assert checked["equivalence_scope"].startswith("predefined non-deterioration bounds")


@pytest.mark.parametrize("metric,value,check", [
    ("rank_correlation", .502, "rank_correlation_bound"),
    ("rmse", 2.003, "rmse_non_deterioration_bound"),
    ("nll", 4.005, "nll_non_deterioration_bound"),
    ("brier", .501, "brier_non_deterioration_bound"),
])
def test_gpu_quality_deterioration_rejected_per_registered_bound(protocol, metric, value, check):
    changed = evidence(runtime="gpu", device="gpu")
    container = (changed["fit_result"]["evaluation"]["primary"] if metric in {"rank_correlation", "rmse"}
                 else changed["fit_result"]["probability_metrics"])
    container[metric] = value
    checked = compare(protocol, candidate=changed)
    assert checked["passed"] is False
    assert checked["checks"][check] is False


@pytest.mark.parametrize("metric", ["rank_correlation", "rmse", "nll", "brier"])
@pytest.mark.parametrize("nonfinite", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_metrics_rejected_with_json_safe_failure_evidence(protocol, metric, nonfinite):
    changed = evidence()
    container = (changed["fit_result"]["evaluation"]["primary"] if metric in {"rank_correlation", "rmse"}
                 else changed["fit_result"]["probability_metrics"])
    container[metric] = nonfinite
    checked = compare(protocol, candidate=changed)
    assert checked["passed"] is False
    assert checked["checks"]["evaluation_metrics_finite"] is False
    assert checked["test_metric_differences"][metric] is None
    json.dumps(checked, allow_nan=False)


def timing(mode, input_mode, seconds, *, passed=True, initialization=False, status="completed"):
    return {"mode": mode, "input_mode": input_mode, "run_processing_seconds": seconds,
            "stage_seconds": {"fit_pipeline": seconds / 2}, "quality": {"passed": passed},
            "initialization": initialization, "status": status}


def test_summary_uses_measured_medians_excludes_initialization_and_failures(protocol):
    runs = [timing("A", "cold", 100., initialization=True), timing("A", "cold", 2.),
            timing("A", "cold", 4.), timing("A", "cold", .01, status="failed"),
            timing("C", "cold", 6.), timing("C", "cold", 10.),
            timing("C", "cache", 4.), timing("B", "cache", 2.)]
    summaries = {(row["input_mode"], row["mode"]): row for row in benchmark.summarize(runs, protocol)}
    cold_a = summaries[("cold", "A")]
    assert cold_a["repetitions"] == 2
    assert cold_a["median_processing_seconds"] == 3
    assert (cold_a["min_processing_seconds"], cold_a["max_processing_seconds"]) == (2, 4)
    assert cold_a["speedup_vs_current_uncached_C"] == pytest.approx(8 / 3)
    assert summaries[("cache", "B")]["fit_pipeline_speedup_vs_cached_C"] == 2
    assert "excludes live API" in cold_a["speedup_scope"]


@pytest.mark.parametrize("bad_baseline", [False, True])
def test_summary_rejects_speedup_if_candidate_or_comparison_baseline_fails(protocol, bad_baseline):
    runs = [timing("C", "cold", 8., passed=not bad_baseline),
            timing("C", "cache", 4., passed=not bad_baseline),
            timing("B", "cache", 2., passed=bad_baseline)]
    candidate = next(row for row in benchmark.summarize(runs, protocol) if row["mode"] == "B")
    assert candidate["speedup_vs_current_uncached_C"] is None
    assert candidate["fit_pipeline_speedup_vs_cached_C"] is None


def test_summary_without_current_baseline_cannot_claim_speedup(protocol):
    summary = benchmark.summarize([timing("A", "cache", 2.)], protocol)
    assert summary[0]["speedup_vs_current_uncached_C"] is None
    assert summary[0]["fit_pipeline_speedup_vs_cached_C"] is None


def test_all_row_summary_reports_timing_and_no_independent_accuracy_claim():
    runs = [timing("A", "cache", 8.), timing("GPU", "cache", 3.), timing("GPU", "cache", 4.)]
    for row, total in zip(runs, (6., 2., 4.)):
        row["full_row_timing"] = {"runner_count": 12, "race_count": 4,
                                 "total_seconds": total, "booster_seconds": total / 2}
    runs.append(timing("B", "cache", 2.))
    summaries = {row["mode"]: row for row in benchmark.summarize_full_rows(runs)}
    assert set(summaries) == {"A", "GPU"}
    assert summaries["GPU"]["repetitions"] == 2
    assert summaries["GPU"]["median_total_seconds"] == 3
    assert summaries["GPU"]["timing_ratio_vs_all_row_CPU_A"] == 2
    for row in summaries.values():
        assert row["independent_test_evaluated"] is row["calibrated"] is row["model_saved"] is False
        assert row["accuracy_equivalence_claimed_for_all_row_fit"] is False


def raw_worker_evidence():
    raw = evidence()
    identity = raw.pop("input_identity")
    raw.pop("runtime")
    raw.pop("device")
    raw.update(identity, status="completed",
               timings={"run_processing_seconds": 8.,
                        "fit_selection_calibration_evaluation_seconds": 6.})
    return raw


def full_row_measurement(total):
    return {"runner_count": 12, "race_count": 4, "total_seconds": total,
            "prepare_seconds": total / 4, "booster_seconds": total / 2,
            "finite_probe_seconds": total / 4, "finite_predictions": True,
            "independent_test_evaluated": False, "calibrated": False,
            "in_sample_metrics_reported": False, "model_saved": False}


def test_merge_preserves_all_row_measurement_and_records_request_separately(protocol, tmp_path):
    raw = raw_worker_evidence()
    raw["full_row_timing"] = full_row_measurement(12.)
    item = {"mode": "A", "input_mode": "cache", "repeat": 1,
            "initialization": False, "full_row_timing": True}
    mode = protocol["modes"]["A"]
    raw_before, item_before, mode_before = deepcopy(raw), deepcopy(item), deepcopy(mode)
    result_path = tmp_path / "A.json"
    merged = benchmark.merge_worker_output(raw, item, mode, result_path, 9.)
    assert raw == raw_before and item == item_before and mode == mode_before
    assert merged["full_row_timing"] == raw_before["full_row_timing"]
    assert isinstance(merged["full_row_timing"], dict)
    assert merged["full_row_timing_requested"] is True
    assert merged["repeat"] == 1 and merged["initialization"] is False
    assert merged["runtime"] == "app" and merged["device"] == "cpu"
    assert merged["worker_json"] == str(result_path)
    assert merged["subprocess_wall_seconds"] == 9
    assert merged["run_processing_seconds"] == 8
    assert merged["stage_seconds"]["fit_pipeline"] == 6
    assert merged["input_identity"] == {key: raw_before[key]
                                         for key in ("cohort", "input_hashes", "cache_key_sha256")}


def test_merge_no_all_row_probe_remains_false(protocol, tmp_path):
    raw = raw_worker_evidence()
    item = {"mode": "C", "input_mode": "cache", "repeat": 1,
            "initialization": False, "full_row_timing": False}
    merged = benchmark.merge_worker_output(raw, item, protocol["modes"]["C"], tmp_path / "C.json", 9.)
    assert merged["full_row_timing_requested"] is False
    assert merged.get("full_row_timing", False) is False
    assert benchmark.summarize_full_rows([merged]) == []
    assert "full_row_timing" not in raw


@pytest.mark.parametrize("probe", ["missing", False, True, None, []])
def test_merge_requested_probe_missing_measurement_is_rejected(protocol, tmp_path, probe):
    raw = raw_worker_evidence()
    if probe != "missing":
        raw["full_row_timing"] = probe
    item = {"mode": "A", "input_mode": "cache", "repeat": 1,
            "initialization": False, "full_row_timing": True}
    with pytest.raises(ValueError, match="full-row"):
        benchmark.merge_worker_output(raw, item, protocol["modes"]["A"], tmp_path / "A.json", 9.)


def test_twenty_merged_worker_outputs_retain_nine_all_row_fits_and_three_repetitions(protocol, tmp_path):
    runs = []
    total_by_mode = {"A": 10., "GPU_BUILD_CPU": 20., "GPU": 3.}
    for index, item in enumerate(benchmark.schedule(protocol)):
        raw = raw_worker_evidence()
        if item["full_row_timing"]:
            raw["full_row_timing"] = full_row_measurement(total_by_mode[item["mode"]] + item["repeat"] - 1)
        merged = benchmark.merge_worker_output(raw, item, protocol["modes"][item["mode"]],
                                               tmp_path / f"{index}.json", 9.)
        merged["quality"] = {"passed": True}
        runs.append(merged)
    assert len(runs) == 20
    assert sum(row["full_row_timing_requested"] for row in runs) == 9
    assert sum(isinstance(row.get("full_row_timing"), dict) for row in runs) == 9
    summary = {row["mode"]: row for row in benchmark.summarize_full_rows(runs)}
    assert set(summary) == {"A", "GPU_BUILD_CPU", "GPU"}
    assert sum(row["repetitions"] for row in summary.values()) == 9
    assert all(row["repetitions"] == 3 for row in summary.values())
    assert summary["A"]["median_total_seconds"] == 11
    assert summary["GPU_BUILD_CPU"]["median_total_seconds"] == 21
    assert summary["GPU"]["median_total_seconds"] == 4
    assert summary["GPU_BUILD_CPU"]["timing_ratio_vs_all_row_CPU_A"] == pytest.approx(11 / 21)
    assert summary["GPU"]["timing_ratio_vs_all_row_CPU_A"] == pytest.approx(11 / 4)
    assert all(row["accuracy_equivalence_claimed_for_all_row_fit"] is False for row in summary.values())


def forbid_execution(*_args, **_kwargs):
    pytest.fail("A unit test must not execute a learning pipeline or subprocess")


def test_default_cli_protocol_only_starts_no_learning(monkeypatch, capsys):
    monkeypatch.setattr(benchmark, "source_modules", forbid_execution)
    monkeypatch.setattr(benchmark.subprocess, "run", forbid_execution)
    benchmark.run()
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "protocol_only"
    assert report["scheduled_processes"] == 20
    assert report["no_learning_started"] is True


@pytest.mark.parametrize("damage", ["snapshot", "period", "tolerance", "bounds", "repetitions", "publication"])
def test_any_precommitted_protocol_change_fails_before_imports(tmp_path, monkeypatch, protocol, damage):
    if damage == "snapshot": protocol["snapshot_sha256"] = "0" * 64
    elif damage == "period": protocol["period"] = ["2024-01", "2025-12"]
    elif damage == "tolerance": protocol["cpu_prediction_absolute_tolerance"] = .1
    elif damage == "bounds": protocol["gpu_quality_bounds"]["rmse_relative_deterioration"] = .1
    elif damage == "repetitions": protocol["cached_repetitions"] = 1
    else: protocol["models_published"] = True
    path = tmp_path / "tampered-protocol.json"
    path.write_text(json.dumps(protocol), encoding="utf-8")
    monkeypatch.setattr(benchmark, "PROTOCOL", path)
    monkeypatch.setattr(benchmark, "source_modules", forbid_execution)
    monkeypatch.setattr(benchmark.subprocess, "run", forbid_execution)
    with pytest.raises(ValueError, match="pinned-source-period"):
        benchmark.run(execute=True)


@pytest.mark.parametrize("cores,memory", [(None, 8 * 1024**3), (4, 8 * 1024**3), (8, 5 * 1024**3)])
def test_hardware_budget_fail_closed_before_source_imports(monkeypatch, cores, memory):
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(
        cpu_count=lambda **_kwargs: cores, virtual_memory=lambda: SimpleNamespace(available=memory)))
    monkeypatch.setattr(benchmark, "source_modules", forbid_execution)
    monkeypatch.setattr(benchmark.subprocess, "run", forbid_execution)
    with pytest.raises(RuntimeError, match="eight-cores-and-six-GiB"):
        benchmark.run(execute=True)


@pytest.mark.parametrize("damage", ["hash", "wal", "journal"])
def test_snapshot_integrity_fails_before_source_imports(tmp_path, monkeypatch, damage):
    snapshot = tmp_path / "immutable.db"
    snapshot.write_bytes(b"fake snapshot, never read as SQLite")
    if damage != "hash":
        Path(str(snapshot) + "-" + damage).write_bytes(b"pending transaction")
    monkeypatch.setattr(benchmark, "SNAPSHOT", snapshot)
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(
        cpu_count=lambda **_kwargs: 8, virtual_memory=lambda: SimpleNamespace(available=8 * 1024**3)))
    monkeypatch.setattr(benchmark, "source_modules", forbid_execution)
    monkeypatch.setattr(benchmark.subprocess, "run", forbid_execution)
    with pytest.raises(ValueError, match="snapshot"):
        benchmark.run(execute=True)


@pytest.fixture
def isolated_parent(tmp_path, monkeypatch):
    snapshot = tmp_path / "fixture.db"
    snapshot.write_bytes(b"fixture")
    generated = tmp_path / "reports/generated"
    source_hashes = {"fixture": "d" * 64}
    preservation = {"active_model_id": "fixture", "active_pointer_sha256": "e" * 64,
                    "forward_processes": [{"pid": 7, "created_at_epoch": 10.}]}
    real_hash = benchmark.file_hash
    monkeypatch.setattr(benchmark, "SNAPSHOT", snapshot)
    monkeypatch.setattr(benchmark, "OUTPUT_ROOT", generated / "full-benchmark")
    monkeypatch.setattr(worker, "OUTPUT_ROOT", generated)
    monkeypatch.setattr(benchmark, "file_hash", lambda path: benchmark.SNAPSHOT_SHA
                        if Path(path) == snapshot else real_hash(path))
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(
        cpu_count=lambda **_kwargs: 8, virtual_memory=lambda: SimpleNamespace(available=8 * 1024**3)))
    monkeypatch.setattr(benchmark, "source_modules", lambda: SimpleNamespace(source_hashes=lambda: source_hashes.copy()))
    monkeypatch.setattr(benchmark, "load_module", lambda *_args: SimpleNamespace(preservation_receipt=lambda: deepcopy(preservation)))
    monkeypatch.setattr(benchmark, "runtime_receipt", lambda executable: {"python": str(executable), "fixture": True})
    monkeypatch.setattr(benchmark, "schedule", lambda _protocol: [{"mode": "A", "input_mode": "cold", "repeat": 0,
                        "initialization": True, "full_row_timing": False}])
    monkeypatch.setattr(benchmark.subprocess, "run", forbid_execution)
    return generated, preservation


@pytest.mark.parametrize("failure", [RuntimeError("fixture interruption"), KeyboardInterrupt("fixture cancellation")])
def test_parent_worker_request_schema_and_interruption_preservation(isolated_parent, monkeypatch, failure):
    generated, before = isolated_parent
    calls = []
    def stop_before_fit(command, *, cwd, env, check):
        request_path = Path(command[command.index("--request") + 1])
        destination = Path(command[command.index("--output") + 1])
        request = json.loads(request_path.read_text(encoding="utf-8"))
        assert set(request) == worker.REQUEST_FIELDS
        worker.validate_request(request, destination)
        assert env["OMP_NUM_THREADS"] == env["OPENBLAS_NUM_THREADS"] == env["MKL_NUM_THREADS"] == "4"
        assert cwd == str(ROOT) and check is False
        calls.append(request)
        raise failure
    monkeypatch.setattr(benchmark.subprocess, "run", stop_before_fit)
    with pytest.raises(type(failure), match="fixture"):
        benchmark.run(execute=True)
    assert len(calls) == 1
    path = next((generated / "full-benchmark").glob("*/results.json"))
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["preservation_after"] == before
    assert report["preservation_unchanged"] is True
    assert report["snapshot_unchanged"] is report["source_unchanged"] is report["scripts_unchanged"] is True
    assert report["models_published"] is report["active_model_changed"] is report["forward_collection_changed"] is False
    manifest = json.loads((path.parent / "artifact_manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"]["results.json"] == benchmark.file_hash(path)


@pytest.mark.parametrize("damage", ["boundary", "hash"])
def test_parent_rejects_worker_vector_boundary_or_hash_before_quality(isolated_parent, tmp_path, monkeypatch, damage):
    generated, _ = isolated_parent
    def fake_worker(command, **_kwargs):
        destination = Path(command[command.index("--output") + 1])
        vector_path = (tmp_path / "outside.vectors.npz" if damage == "boundary"
                       else destination.with_suffix(".vectors.npz"))
        np.savez(vector_path, **vectors())
        report = {"status": "completed", "cohort": {}, "input_hashes": {}, "cache_key_sha256": "c" * 64,
                  "timings": {"run_processing_seconds": 1., "fit_selection_calibration_evaluation_seconds": .5},
                  "vectors": {"path": str(vector_path), "sha256": "0" * 64}}
        destination.write_text(json.dumps(report), encoding="utf-8")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(benchmark.subprocess, "run", fake_worker)
    monkeypatch.setattr(benchmark, "quality_check", forbid_execution)
    with pytest.raises(ValueError, match="vector-" + damage + "-mismatch"):
        benchmark.run(execute=True)
    report = json.loads(next((generated / "full-benchmark").glob("*/results.json")).read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["runs"] == []
    assert report["preservation_unchanged"] is True
