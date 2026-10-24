"""Synthetic driver guards: no training, runtime probe, database or live writes."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
import math
from pathlib import Path
import re
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "isolated_blend46_driver_tests", ROOT / "scripts/evaluate_db_shared_blend46.py",
)
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)
DESIGN_SPEC = importlib.util.spec_from_file_location(
    "isolated_blend46_driver_design", ROOT / "scripts/db_shared_blend46_design.py",
)
DESIGN = importlib.util.module_from_spec(DESIGN_SPEC)
DESIGN_SPEC.loader.exec_module(DESIGN)
GIB = 1024**3


@pytest.fixture(autouse=True)
def local_environment(monkeypatch):
    monkeypatch.setenv("BLEND46_RESEARCH_ENV", "local")


@pytest.fixture
def protocol():
    return json.loads((ROOT / "config/research/db-shared-blend46-20261012.json").read_text(encoding="utf-8"))


@pytest.fixture
def records():
    rows = []
    for strategy in ("global", "venue_only", "blend50"):
        for race_number, date, runners, rho, sse in ((1, "20260104", 2, .4, .02),
                                                    (2, "20260105", 4, .6, .16)):
            rows.append({
                "fold_id": "Y2026", "test_year": 2026, "strategy": strategy, "split": "TEST",
                "race_id": f"20260501010{race_number}", "race_date": date,
                "venue": "東京", "surface": "芝", "runner_count": runners,
                "rank_correlation": rho, "squared_speed_error_sum": sse,
                "probability_eligible": True, "nll": 1.2, "brier": .7,
                "uniform_nll": math.log(runners), "uniform_brier": 1. - 1. / runners,
                "top1_confidence": .6 if runners == 2 else .3,
                "top1_correct": int(runners == 2), "probability_top1_correct": int(runners == 2),
                "selection_bin": 6 if runners == 2 else 3,
            })
    return pd.DataFrame(rows)


def capacity(monkeypatch, physical=8, available_gib=12):
    import psutil
    monkeypatch.setattr(psutil, "cpu_count", lambda logical=True: physical * 2 if logical else physical)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=int(available_gib * GIB)))


def test_protocol_is_exact_and_does_not_mutate_or_authorize_deployment(protocol):
    original = deepcopy(protocol)
    assert DRIVER.validate_protocol(protocol) == original
    assert protocol == original
    assert protocol["feature_count"] == 46
    assert protocol["specialist_weight"] == .5
    assert protocol["historical_periods_and_feature_selection_already_seen"] is True
    assert all(protocol[key] is False for key in (
        "independent_future_validation", "roi_evaluated", "publish_models", "activate_models",
        "modify_forward_collection", "commit_push", "real_purchase", "gpu_used_for_primary_comparison",
    ))


@pytest.mark.parametrize("key,value", [
    ("requested_period", ["2016-01", "2026-10"]), ("cache_period", ["2016-01", "2026-09"]),
    ("snapshot_sha256", "a" * 64), ("feature_pipeline", "local-db-shared-v1"),
    ("feature_count", 47), ("excluded_features", []), ("strategies", ["global", "blend50"]),
    ("specialist_weight", .6), ("minimum_specialist_races", 1), ("minimum_specialist_runners", 1),
    ("minimum_specialist_dates", 1), ("preset", "large"), ("num_boost_round", 2000),
    ("maximum_cpu_workers", 100), ("native_threads_per_worker", 16),
    ("reserved_physical_cores", 0), ("reserved_memory_gib", 0), ("minimum_memory_per_worker_gib", 0.),
    ("maximum_worker_seconds", 0), ("gpu_control_fold", "Y2022"),
    ("gpu_control_absolute_rank_difference", .1), ("gpu_control_relative_rmse_nll_brier_difference", .1),
    ("gpu_control_absolute_ece_difference", .1), ("bootstrap_repetitions", 10),
    ("bootstrap_seed", 0), ("primary_comparison_family_size", 1),
    ("primary_simultaneous_confidence", .95), ("secondary_confidence", .5),
    ("calibration", "fit TEST temperature"), ("folds", "random split"),
    ("bootstrap_unit", "unpaired runners"), ("primary_metrics", ["ROI"]),
    ("shared_train_only_encoder", False), ("shared_train_only_speed_baseline", False),
    ("history_all_venues", False), ("same_day_and_future_history_excluded", False),
    ("historical_periods_and_feature_selection_already_seen", False),
    ("independent_future_validation", True), ("roi_evaluated", True), ("publish_models", True),
    ("activate_models", True), ("modify_forward_collection", True), ("commit_push", True),
    ("real_purchase", True), ("gpu_used_for_primary_comparison", True),
])
def test_protocol_rejects_changed_scope_before_fitting(protocol, key, value):
    protocol[key] = value
    with pytest.raises(ValueError):
        DRIVER.validate_protocol(protocol)


@pytest.mark.parametrize("key,value", [
    ("feature_count", 46.), ("maximum_cpu_workers", 3.), ("native_threads_per_worker", True),
    ("reserved_memory_gib", 4.), ("specialist_weight", "0.5"),
    ("minimum_memory_per_worker_gib", "1.5"), ("roi_evaluated", 0),
    ("same_day_and_future_history_excluded", 1), ("specialist_weight", float("nan")),
    ("primary_simultaneous_confidence", float("inf")),
])
def test_protocol_rejects_type_substitution_and_nonfinite_values(protocol, key, value):
    protocol[key] = value
    with pytest.raises(ValueError):
        DRIVER.validate_protocol(protocol)


@pytest.mark.parametrize("mutation", ["extra", "missing", "wrong_schema", "not_dict"])
def test_protocol_has_no_implicit_extensibility(protocol, mutation):
    if mutation == "extra":
        protocol["force_active_switch"] = True
    elif mutation == "missing":
        del protocol["reserved_memory_gib"]
    elif mutation == "wrong_schema":
        protocol["schema"] = "unfrozen"
    else:
        protocol = None
    with pytest.raises(ValueError):
        DRIVER.validate_protocol(protocol)


@pytest.mark.parametrize("environment", ["production", "staging", "unknown", "", "PUBLIC"])
def test_nonlocal_preflight_stops_before_imports_runtime_and_active_reads(monkeypatch, environment):
    monkeypatch.setenv("BLEND46_RESEARCH_ENV", environment)
    def forbidden():
        pytest.fail("Nonlocal preflight reached modules or protected state")
    monkeypatch.setattr(DRIVER, "modules", forbidden)
    with pytest.raises(ValueError):
        DRIVER.preflight()


def test_parallel_budget_reserves_cpu_and_memory(protocol, monkeypatch):
    capacity(monkeypatch)
    allocation = DRIVER.resource_allocation(protocol, 16 * 1024**2)
    assert allocation["cpu_workers"] == 3
    assert allocation["native_threads_per_worker"] == 2
    assert allocation["reserved_physical_cores"] == 2
    assert allocation["reserved_memory_bytes"] == 4 * GIB
    assert allocation["estimated_bytes_per_worker"] >= int(1.5 * GIB)
    assert allocation["gpu_control_concurrency"] == 1
    assert allocation["cpu_workers"] * 2 <= allocation["physical_cores"] - 2
    assert allocation["reservation_is_scheduling_budget_not_os_allocation"] is True


@pytest.mark.parametrize("physical,available_gib,frame_bytes,workers", [
    (4, 12, 16 * 1024**2, 1), (8, 7, 16 * 1024**2, 2),
    (8, 12, 768 * 1024**2, 1), (16, 32, 16 * 1024**2, 3),
])
def test_parallel_budget_respects_actual_ram_cpu_and_fixed_cap(protocol, monkeypatch, physical, available_gib, frame_bytes, workers):
    capacity(monkeypatch, physical, available_gib)
    assert DRIVER.resource_allocation(protocol, frame_bytes)["cpu_workers"] == workers


@pytest.mark.parametrize("physical,available_gib", [(2, 12), (8, 4), (8, 5.4)])
def test_no_safe_slot_never_forces_training(protocol, monkeypatch, physical, available_gib):
    capacity(monkeypatch, physical, available_gib)
    with pytest.raises(ValueError, match="safe-compute-slot"):
        DRIVER.resource_allocation(protocol, 16 * 1024**2)


@pytest.mark.parametrize("frame_bytes", [True, None, "1", 0, -1, 1.5, float("nan"), float("inf")])
def test_memory_estimate_is_a_strict_positive_integer(protocol, monkeypatch, frame_bytes):
    capacity(monkeypatch)
    with pytest.raises(ValueError):
        DRIVER.resource_allocation(protocol, frame_bytes)


def test_resource_helper_revalidates_protocol(protocol, monkeypatch):
    capacity(monkeypatch)
    protocol["reserved_memory_gib"] = 0
    with pytest.raises(ValueError):
        DRIVER.resource_allocation(protocol, 1)


def test_study_source_manifest_uses_posix_keys_required_by_worker_on_windows():
    pins = DRIVER.study_source_manifest()
    required = {
        "scripts/evaluate_db_shared_blend46_worker.py", "scripts/optimize_db_shared_features_worker.py",
        "scripts/benchmark_db_shared_full_worker.py", "scripts/evaluate_db_shared_venues.py",
        "python-api/training/db_shared_acceleration.py",
    }
    assert len(pins) == 8
    assert required.issubset(pins)
    assert all("\\" not in key and not Path(key).is_absolute() for key in pins)
    assert all(re.fullmatch(r"[0-9a-f]{64}", value) for value in pins.values())
    assert all(DRIVER.file_hash(ROOT / key) == digest for key, digest in pins.items())


def prepared_cache_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(DRIVER, "ROOT", tmp_path)
    boundary = tmp_path / "reports/generated"
    cache = boundary / "study/run/raw47-cache"
    cache.mkdir(parents=True)
    return boundary, cache


def test_prepared_cache_admission_is_read_only_and_returns_resolved_in_scope_path(tmp_path, monkeypatch):
    _, cache = prepared_cache_fixture(tmp_path, monkeypatch)
    before = list(cache.iterdir())
    assert DRIVER.reusable_cache_path(cache) == cache.resolve()
    assert list(cache.iterdir()) == before


@pytest.mark.parametrize("problem", ["outside", "boundary", "direct_child", "file", "missing"])
def test_prepared_cache_cannot_admit_workspace_system_or_unresolved_targets(tmp_path, monkeypatch, problem):
    boundary, cache = prepared_cache_fixture(tmp_path, monkeypatch)
    if problem == "outside":
        path = tmp_path / "python-api/models"
        path.mkdir(parents=True)
    elif problem == "boundary":
        path = boundary
    elif problem == "direct_child":
        path = boundary / "too_broad"
        path.mkdir()
    elif problem == "file":
        path = cache / "not_a_directory.json"
        path.write_text("{}", encoding="utf-8")
    else:
        path = cache / "missing"
    with pytest.raises((ValueError, FileNotFoundError)):
        DRIVER.reusable_cache_path(path)


@pytest.mark.parametrize("symbolic_target", ["cache", "parent"])
def test_prepared_cache_rejects_symbolic_link_at_any_path_component(tmp_path, monkeypatch, symbolic_target):
    _, cache = prepared_cache_fixture(tmp_path, monkeypatch)
    symbolic = cache if symbolic_target == "cache" else cache.parent
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == symbolic)
    with pytest.raises(ValueError, match="boundary-invalid|symlink-forbidden"):
        DRIVER.reusable_cache_path(cache)


def artifact_fixture(tmp_path):
    directory = tmp_path / "research"
    directory.mkdir()
    path = directory / "races.parquet"
    expected = pd.DataFrame({"race_id": ["r1"], "split": ["TEST"]})
    expected.to_parquet(path, index=False)
    result = {"artifacts": {"races": {"path": str(path), "sha256": DRIVER.file_hash(path)}}}
    return directory, path, expected, result


def test_artifact_read_requires_binding_and_returns_parquet(tmp_path):
    directory, _, expected, result = artifact_fixture(tmp_path)
    original = deepcopy(result)
    actual = DRIVER.read_artifact(result, "race_metrics", directory)
    pd.testing.assert_frame_equal(actual, expected)
    assert result == original


@pytest.mark.parametrize("problem", ["hash", "outside", "unknown_kind", "missing_kind", "missing_path"])
def test_artifact_read_denies_wrong_hash_path_or_kind(tmp_path, problem):
    directory, path, _, result = artifact_fixture(tmp_path)
    kind = "race_metrics"
    if problem == "hash":
        result["artifacts"]["races"]["sha256"] = "0" * 64
    elif problem == "outside":
        directory = tmp_path / "other"
        directory.mkdir()
    elif problem == "unknown_kind":
        kind = "live_model"
    elif problem == "missing_kind":
        result["artifacts"].clear()
    else:
        result["artifacts"]["races"]["path"] = str(path.with_name("missing.parquet"))
    with pytest.raises((ValueError, FileNotFoundError)):
        DRIVER.read_artifact(result, kind, directory)


def test_gpu_control_equal_metrics_are_only_a_control(protocol, records):
    original = records.copy(deep=True)
    result = DRIVER.compare_gpu(records, records.iloc[::-1].reset_index(drop=True), DESIGN, protocol)
    assert result["quality_pass"] is True
    assert result["used_for_primary_comparison"] is False
    assert result["single_run_timing_not_repeated_benchmark"] is True
    assert len(result["comparisons"]) == 3
    assert all(row["rank_delta"] == 0. and row["rmse_relative_difference"] == 0. for row in result["comparisons"])
    pd.testing.assert_frame_equal(records, original)


def test_gpu_control_does_not_mix_cal_metrics_into_test(protocol, records):
    cal = records.copy(deep=True)
    cal["split"] = "CAL"
    cal["race_date"] = "20250105"
    cal["rank_correlation"] = -.9
    cpu = pd.concat([records, cal], ignore_index=True)
    cal["rank_correlation"] = .9
    gpu = pd.concat([records.iloc[::-1], cal], ignore_index=True)
    assert DRIVER.compare_gpu(cpu, gpu, DESIGN, protocol)["quality_pass"] is True


@pytest.mark.parametrize("column,value", [
    ("rank_correlation", .8), ("squared_speed_error_sum", 2.),
    ("nll", 2.), ("brier", .9), ("top1_confidence", .8),
])
def test_gpu_control_marks_numerical_deterioration_instead_of_selecting_gpu(protocol, records, column, value):
    gpu = records.copy(deep=True)
    gpu.loc[(gpu.strategy == "blend50") & (gpu.runner_count == 2), column] = value
    if column == "top1_confidence":
        gpu.loc[(gpu.strategy == "blend50") & (gpu.runner_count == 2), "selection_bin"] = 8
    result = DRIVER.compare_gpu(records, gpu, DESIGN, protocol)
    assert result["quality_pass"] is False
    assert result["used_for_primary_comparison"] is False


@pytest.mark.parametrize("problem", ["missing_race", "duplicate", "date", "venue", "runners", "surface", "fold"])
def test_gpu_control_requires_exact_unique_test_cohort(protocol, records, problem):
    gpu = records.copy(deep=True)
    if problem == "missing_race":
        gpu = gpu.iloc[1:]
    elif problem == "duplicate":
        gpu = pd.concat([gpu, gpu.iloc[[0]]], ignore_index=True)
    else:
        key, value = {"date": ("race_date", "20260106"), "venue": ("venue", "京都"),
                      "runners": ("runner_count", 3), "surface": ("surface", "ダート"),
                      "fold": ("fold_id", "Y2025")}[problem]
        gpu.loc[0, key] = value
    with pytest.raises(ValueError):
        DRIVER.compare_gpu(records, gpu, DESIGN, protocol)


def test_gpu_control_requires_the_same_probability_eligible_cohort(protocol, records):
    gpu = records.copy(deep=True)
    gpu.loc[gpu.race_id == records.race_id.iloc[0], "probability_eligible"] = False
    with pytest.raises(ValueError, match="probability-cohort-mismatch"):
        DRIVER.compare_gpu(records, gpu, DESIGN, protocol)


def test_gpu_control_requires_identical_optional_surface_schema(protocol, records):
    with pytest.raises(ValueError, match="surface-schema-mismatch"):
        DRIVER.compare_gpu(records, records.drop(columns="surface"), DESIGN, protocol)


@pytest.mark.parametrize("metric,value", [
    ("nll", None), ("nll", 0.), ("nll", float("nan")),
    ("brier", None), ("brier", 0.), ("brier", float("inf")),
    ("selection_ece", None), ("selection_ece", float("nan")),
])
def test_gpu_control_cannot_treat_unavailable_or_invalid_probability_metrics_as_equivalent(protocol, records, metric, value):
    def probability_summary(_records):
        result = DESIGN.probability_summary(_records)
        result[metric] = value
        return result
    fake_design = SimpleNamespace(accuracy_summary=DESIGN.accuracy_summary,
                                 probability_summary=probability_summary,
                                 validate_metric_cohort=DESIGN.validate_metric_cohort)
    with pytest.raises(ValueError):
        DRIVER.compare_gpu(records, records.copy(), fake_design, protocol)

