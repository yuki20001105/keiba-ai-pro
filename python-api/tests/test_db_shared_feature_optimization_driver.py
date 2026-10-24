"""Synthetic driver checks; no model fitting, runtime probes or race-file I/O."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "isolated_feature_optimization_driver_tests", ROOT / "scripts/optimize_db_shared_features.py",
)
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)
GIB = 1024**3


@pytest.fixture(autouse=True)
def isolated_offline_environment(monkeypatch):
    # This dedicated switch must not mutate the running app's environment.
    monkeypatch.setenv("FEATURE_OPTIMIZATION_ENV", "local")


@pytest.fixture
def protocol():
    return json.loads((ROOT / "config/research/db-shared-feature-optimization-20261012.json").read_text(encoding="utf-8"))


@pytest.fixture
def records():
    return pd.DataFrame([
        {"split_id": "REUSED_TEST", "race_id": "202505010101", "race_date": "20250105",
         "runner_count": 2, "rank_correlation": .4, "squared_speed_error_sum": .02},
        {"split_id": "REUSED_TEST", "race_id": "202505010102", "race_date": "20250106",
         "runner_count": 4, "rank_correlation": .6, "squared_speed_error_sum": .16},
    ])


def mock_capacity(monkeypatch, *, cores=8, available_gib=12):
    import psutil
    monkeypatch.setattr(psutil, "cpu_count", lambda logical=True: cores * 2 if logical else cores)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=int(available_gib * GIB)))


def test_static_protocol_is_accepted_without_mutating_it(protocol):
    original = deepcopy(protocol)
    assert DRIVER.validate_protocol(protocol) == original
    assert protocol == original
    assert protocol["all_historical_evaluation_periods_previously_seen"] is True
    assert protocol["maximum_total_candidates"] == 76


@pytest.mark.parametrize("key,value", [
    ("roi_used_for_selection", True), ("win_probability_authority_granted", True),
    ("full_period_fit_performance_claimed", True), ("artifact_published", True),
    ("active_model_changed", True), ("forward_collection_changed", True),
    ("commit_push", True), ("real_purchase", True),
    ("all_historical_evaluation_periods_previously_seen", False),
    ("full_period_fit_after_candidate_lock", False), ("gpu_control_only", False),
    ("selection_device", "GPU best-score selection"),
    ("requested_period", ["2016-01", "2026-10"]), ("training_end", "20260913"),
    ("calibration_diagnostic_period", ["20210101", "20220521"]),
    ("test_diagnostic_period", ["20230101", "20260913"]),
    ("snapshot_sha256", "a" * 64), ("cache_manifest_sha256", "b" * 64),
    ("cache_directory", "../../outside-live-models"),
    ("cache_directory", "C:/Users/yuki2/Documents/ws/keiba-ai-pro/python-api/models"),
    ("fixed_preset", {"id": "standard", "num_boost_round": 5000}),
    ("gpu_quality_bounds", {"rank_correlation_absolute_difference": .5, "rmse_relative_difference": .5}),
    ("maximum_total_candidates", 100000), ("maximum_initial_candidates", 1000),
    ("maximum_refinement_candidates", 100), ("maximum_cpu_workers", 100),
    ("native_threads_per_worker", 16), ("reserved_physical_cores", 0),
    ("reserved_memory_gib", -1), ("minimum_memory_per_worker_gib", 0),
    ("maximum_worker_seconds", -1), ("bootstrap_repetitions", 10000000),
    ("rank_tie_tolerance", .1), ("maximum_development_rmse_relative_deterioration", 1.),
])
def test_changed_scope_or_bounds_fail_before_execution(protocol, key, value):
    protocol[key] = value
    with pytest.raises(ValueError):
        DRIVER.validate_protocol(protocol)


@pytest.mark.parametrize("mutation", ["extra_key", "missing_key", "non_dict"])
def test_protocol_schema_cannot_be_partially_or_silently_extended(protocol, mutation):
    if mutation == "extra_key":
        protocol["production_override"] = True
    elif mutation == "missing_key":
        del protocol["reserved_memory_gib"]
    else:
        protocol = []
    with pytest.raises(ValueError):
        DRIVER.validate_protocol(protocol)


@pytest.mark.parametrize("environment", ["production", "staging", "unknown", "", "PUBLIC"])
def test_nonlocal_preflight_is_rejected_before_loading_runtime_modules(monkeypatch, environment):
    monkeypatch.setenv("FEATURE_OPTIMIZATION_ENV", environment)
    def prohibited_modules():
        pytest.fail("Forbidden environment reached runtime imports/probes")
    monkeypatch.setattr(DRIVER, "modules", prohibited_modules)
    with pytest.raises(ValueError):
        DRIVER.preflight()


def test_cpu_resources_reserve_two_cores_and_four_gib(protocol, monkeypatch):
    mock_capacity(monkeypatch)
    allocation = DRIVER.resource_allocation(protocol, 16 * 1024**2)
    assert allocation["cpu_workers"] == 3
    assert allocation["native_threads_per_worker"] == 2
    assert allocation["reserved_physical_cores"] == 2
    assert allocation["reserved_memory_bytes"] == 4 * GIB
    assert allocation["estimated_bytes_per_worker"] >= int(1.5 * GIB)
    assert allocation["gpu_concurrent_fits"] == 1
    assert allocation["cpu_workers"] * allocation["native_threads_per_worker"] <= 8 - 2


@pytest.mark.parametrize("cores,available_gib,features_bytes,expected", [
    (4, 12, 16 * 1024**2, 1),
    (8, 7, 16 * 1024**2, 2),
    (8, 12, 768 * 1024**2, 1),
])
def test_parallel_capacity_is_bounded_by_both_host_ram_and_cpu(protocol, monkeypatch, cores, available_gib, features_bytes, expected):
    mock_capacity(monkeypatch, cores=cores, available_gib=available_gib)
    assert DRIVER.resource_allocation(protocol, features_bytes)["cpu_workers"] == expected


@pytest.mark.parametrize("cores,available_gib", [(2, 12), (8, 4), (8, 5.4)])
def test_no_safe_slot_is_not_forced_into_parallel_training(protocol, monkeypatch, cores, available_gib):
    mock_capacity(monkeypatch, cores=cores, available_gib=available_gib)
    with pytest.raises(ValueError, match="safe-resource-slot"):
        DRIVER.resource_allocation(protocol, 16 * 1024**2)


@pytest.mark.parametrize("feature_bytes", [True, None, "16", -1, 1.5, float("nan"), float("inf")])
def test_memory_estimate_requires_finite_nonnegative_integer_bytes(protocol, monkeypatch, feature_bytes):
    mock_capacity(monkeypatch)
    with pytest.raises(ValueError):
        DRIVER.resource_allocation(protocol, feature_bytes)


def test_resource_helper_revalidates_the_safety_protocol(protocol, monkeypatch):
    mock_capacity(monkeypatch)
    protocol["reserved_memory_gib"] = 0
    with pytest.raises(ValueError):
        DRIVER.resource_allocation(protocol, 16 * 1024**2)


def test_summary_uses_macro_race_rank_and_runner_weighted_physical_error(records):
    original = records.copy(deep=True)
    summary = DRIVER._summary(records)
    assert summary["race_count"] == 2
    assert summary["runner_count"] == 6
    assert summary["rank_correlation"] == pytest.approx(.5)
    assert summary["rmse_speed_mps"] == pytest.approx(math.sqrt(.18 / 6))
    assert summary["date_from"] == "20250105"
    assert summary["date_to"] == "20250106"
    pd.testing.assert_frame_equal(records, original)


@pytest.mark.parametrize("column,value", [
    ("runner_count", 0), ("runner_count", -1), ("runner_count", 2.5), ("runner_count", True),
    ("runner_count", 19), ("runner_count", float("nan")), ("runner_count", float("inf")),
    ("rank_correlation", float("nan")), ("rank_correlation", float("inf")),
    ("rank_correlation", 1.1), ("rank_correlation", -1.1), ("rank_correlation", True),
    ("squared_speed_error_sum", -1.), ("squared_speed_error_sum", float("nan")),
    ("squared_speed_error_sum", float("inf")), ("squared_speed_error_sum", True),
    ("race_date", "20250230"), ("race_date", "2025015"), ("race_date", None),
    ("race_id", ""), ("split_id", ""),
])
def test_summary_rejects_invalid_race_records_before_any_pooling(records, column, value):
    records[column] = records[column].astype(object)
    records.loc[0, column] = value
    with pytest.raises(ValueError):
        DRIVER._summary(records)


@pytest.mark.parametrize("problem", ["empty", "duplicate", "missing_column", "duplicate_columns", "not_frame"])
def test_summary_requires_a_nonempty_unique_complete_race_table(records, problem):
    if problem == "empty":
        records = records.iloc[0:0]
    elif problem == "duplicate":
        records = pd.concat([records, records.iloc[[0]]], ignore_index=True)
    elif problem == "missing_column":
        records = records.drop(columns="rank_correlation")
    elif problem == "duplicate_columns":
        records = pd.concat([records, records[["rank_correlation"]]], axis=1)
    else:
        records = []
    with pytest.raises(ValueError):
        DRIVER._summary(records)


def test_pairing_is_by_race_keys_and_never_mutates_or_grants_forward_authority(records, protocol):
    candidate = records.iloc[::-1].reset_index(drop=True)
    candidate["rank_correlation"] += .1
    candidate["squared_speed_error_sum"] *= .9
    baseline_before, candidate_before = records.copy(deep=True), candidate.copy(deep=True)
    calls = []
    def bootstrap(frame, **kwargs):
        calls.append((frame.copy(deep=True), kwargs))
        return {"status": "synthetic", "rank_difference": .1}
    result = DRIVER._paired_statistics(records, candidate, bootstrap, protocol)
    assert len(calls) == 1
    paired, kwargs = calls[0]
    assert kwargs == {"repetitions": 2000, "seed": 20261012}
    assert paired["year"].tolist() == [2025, 2025]
    assert np.allclose(paired["rank_correlation"] - paired["global_rank_correlation"], .1)
    assert np.allclose(paired["squared_error_sum"], paired["global_squared_error_sum"] * .9)
    assert result["selection_adjusted"] is False
    assert "not independent prospective evidence" in result["interpretation"]
    pd.testing.assert_frame_equal(records, baseline_before)
    pd.testing.assert_frame_equal(candidate, candidate_before)


@pytest.mark.parametrize("problem", ["missing_race", "different_date", "different_runner_count", "duplicate", "nonfinite_metric"])
def test_pairing_denies_cohort_or_metric_inconsistency_before_bootstrap(records, protocol, problem):
    candidate = records.copy(deep=True)
    if problem == "missing_race":
        candidate = candidate.iloc[[0]]
    elif problem == "different_date":
        candidate.loc[0, "race_date"] = "20250107"
    elif problem == "different_runner_count":
        candidate.loc[0, "runner_count"] = 3
    elif problem == "duplicate":
        candidate = pd.concat([candidate, candidate.iloc[[0]]], ignore_index=True)
    else:
        candidate.loc[0, "rank_correlation"] = np.nan
    def forbidden_bootstrap(*args, **kwargs):
        pytest.fail("Invalid paired input reached bootstrap")
    with pytest.raises(ValueError):
        DRIVER._paired_statistics(records, candidate, forbidden_bootstrap, protocol)


def test_preflight_only_run_never_requests_runtime_or_fit_execution(monkeypatch):
    prepared = {"quality": {"runner_count": 6}, "partitions": {}, "resources": {"cpu_workers": 1},
                "initial_candidates": [{"id": "all47"}]}
    monkeypatch.setattr(DRIVER, "preflight", lambda: deepcopy(prepared))
    def forbidden_modules():
        pytest.fail("Read-only run attempted runtime execution")
    monkeypatch.setattr(DRIVER, "modules", forbidden_modules)
    result = DRIVER.run(execute=False)
    assert result["mode"] == "read-only-preflight"
    assert result["candidate_count"] == 1
    assert result["learning_started"] is False
