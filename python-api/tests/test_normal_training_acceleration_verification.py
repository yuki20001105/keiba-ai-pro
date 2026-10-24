"""Verification checks only: no API writes, fits, restarts or purchases."""
from __future__ import annotations

from copy import deepcopy
import importlib
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
verification = importlib.import_module("verify_normal_training_acceleration")


@pytest.fixture
def reports():
    reference = {
        "job_id": "cpu-cold", "snapshot_sha256": "a" * 64,
        "fit_audit": {"partitions": {"TRAIN": {"runner_count": 273695},
                                   "CAL": {"runner_count": 91189}, "TEST": {"runner_count": 88973}},
                      "selected_preset": "standard", "source_hashes": {"fit": "b" * 64}},
        "evaluation": {"primary": {"rank_correlation": .3, "rmse": 1.2}},
        "probability_validation": {"gate_results": {"validated": True},
                                   "metrics": {"nll": 2., "brier": .8}},
        "acceleration": {"cache_receipt": {
                         "input_hashes": {"target": "c" * 64, "features": "d" * 64},
                         "cache_key_sha256": "f" * 64, "source_hashes": {"feature": "e" * 64},
                         "point_in_time_validated": True},
                         "total_seconds": 80., "device": "cpu", "cache_hit": False},
        "whole_job_seconds": 100., "feature_source_hashes": {"feature": "e" * 64},
        "feature_columns": [f"feature-{i}" for i in range(47)], "feature_count": 47,
        "training_execution": {"device": "cpu", "gpu_device_verified": False},
    }
    cached_cpu = deepcopy(reference)
    cached_cpu.update(job_id="cpu-cache", whole_job_seconds=50.)
    cached_cpu["acceleration"].update(total_seconds=30., cache_hit=True)
    gpu = deepcopy(cached_cpu)
    gpu.update(job_id="gpu-cache", whole_job_seconds=45.)
    gpu["acceleration"].update(total_seconds=28., device="gpu")
    gpu["training_execution"].update(device="gpu", gpu_device_verified=True,
        gpu_fit_records=[{"gpu_device_verified": True} for _ in range(3)])
    return [reference, cached_cpu, gpu]


def test_same_inputs_partition_selection_and_gates_pass(reports):
    result = verification.compare(reports)
    assert result["reference_job"] == "cpu-cold"
    assert result["quality_pass"] is True
    assert result["actual_gpu_verified"] is True
    assert result["feature_reuse_verified"] is True
    assert result["acceleration_path_pass"] is True
    for row in result["comparisons"]:
        assert row["same_snapshot"] is True
        assert row["same_raw_inputs"] is True
        assert row["same_partitions"] is True
        assert row["same_selected_preset"] is True
        assert row["same_probability_gates"] is True
        assert row["same_sources"] is True
        assert row["same_feature_schema"] is True
        assert row["same_cache_lineage"] is True
        assert row["finite_metrics_and_timings"] is True
        assert row["quality_pass"] is True


@pytest.mark.parametrize("path,new_value", [
    (("snapshot_sha256",), "changed"),
    (("acceleration", "cache_receipt", "input_hashes", "features"), "changed"),
    (("acceleration", "cache_receipt", "input_hashes", "target"), "changed"),
    (("fit_audit", "partitions", "TEST", "runner_count"), 88972),
    (("fit_audit", "selected_preset"), "lean"),
    (("probability_validation", "gate_results", "validated"), False),
    (("feature_source_hashes", "feature"), "changed"),
    (("fit_audit", "source_hashes", "fit"), "changed"),
    (("feature_columns",), ["different"] * 47),
    (("feature_count",), 46),
    (("acceleration", "cache_receipt", "cache_key_sha256"), "changed"),
    (("acceleration", "cache_receipt", "source_hashes", "feature"), "changed"),
    (("acceleration", "cache_receipt", "point_in_time_validated"), False),
])
def test_lineage_or_selection_change_rejects_comparison(reports, path, new_value):
    current = reports[2]
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = new_value
    result = verification.compare(reports)
    assert result["quality_pass"] is False
    assert result["comparisons"][1]["quality_pass"] is False


@pytest.mark.parametrize("rho", [.30101, .29899])
def test_rank_delta_is_absolute_and_predefined(reports, rho):
    reports[2]["evaluation"]["primary"]["rank_correlation"] = rho
    result = verification.compare(reports)
    assert result["quality_pass"] is False


@pytest.mark.parametrize("rho", [.30099, .29901])
def test_rank_within_predefined_tolerance_passes(reports, rho):
    reports[2]["evaluation"]["primary"]["rank_correlation"] = rho
    assert verification.compare(reports)["quality_pass"] is True


@pytest.mark.parametrize("field", ["rmse", "nll", "brier"])
def test_relative_deterioration_over_one_tenth_percent_fails(reports, field):
    if field == "rmse":
        reports[2]["evaluation"]["primary"][field] *= 1.00101
    else:
        reports[2]["probability_validation"]["metrics"][field] *= 1.00101
    assert verification.compare(reports)["quality_pass"] is False


@pytest.mark.parametrize("field", ["rmse", "nll", "brier"])
def test_relative_improvement_or_within_bound_is_allowed(reports, field):
    if field == "rmse":
        reports[2]["evaluation"]["primary"][field] *= .95
    else:
        reports[2]["probability_validation"]["metrics"][field] *= .95
    assert verification.compare(reports)["quality_pass"] is True


def test_pipeline_and_whole_job_speedups_are_not_mixed(reports):
    result = verification.compare(reports)
    cpu, gpu = result["comparisons"]
    assert cpu["pipeline_speedup"] == pytest.approx(80 / 30)
    assert cpu["whole_job_speedup"] == pytest.approx(100 / 50)
    assert gpu["pipeline_speedup"] == pytest.approx(80 / 28)
    assert gpu["whole_job_speedup"] == pytest.approx(100 / 45)


def test_all_three_cases_are_required(reports):
    assert verification.compare(reports[:2])["quality_pass"] is False


@pytest.mark.parametrize("path,new_value", [
    (("evaluation", "primary", "rank_correlation"), float("nan")),
    (("evaluation", "primary", "rank_correlation"), float("inf")),
    (("evaluation", "primary", "rmse"), float("nan")),
    (("evaluation", "primary", "rmse"), float("-inf")),
    (("evaluation", "primary", "rmse"), -1.),
    (("probability_validation", "metrics", "nll"), float("nan")),
    (("probability_validation", "metrics", "brier"), float("inf")),
    (("whole_job_seconds",), float("nan")),
    (("whole_job_seconds",), -1.),
    (("acceleration", "total_seconds"), float("inf")),
    (("acceleration", "total_seconds"), -1.),
])
def test_nonfinite_metrics_or_invalid_timing_never_pass(reports, path, new_value):
    value = reports[2]
    for key in path[:-1]:
        value = value[key]
    value[path[-1]] = new_value
    assert verification.compare(reports)["quality_pass"] is False


@pytest.mark.parametrize("path", [
    ("whole_job_seconds",),
    ("acceleration", "total_seconds"),
])
def test_zero_candidate_speed_denominator_fails_closed(reports, path):
    value = reports[2]
    for key in path[:-1]:
        value = value[key]
    value[path[-1]] = 0.
    # An invalid receipt must never yield a speedup/quality PASS. Currently
    # this malformed synthetic receipt is rejected by arithmetic validation.
    with pytest.raises(ZeroDivisionError):
        verification.compare(reports)


@pytest.mark.parametrize("path", [
    ("evaluation", "primary", "rmse"),
    ("probability_validation", "metrics", "nll"),
    ("probability_validation", "metrics", "brier"),
])
def test_zero_baseline_metric_denominator_fails_closed(reports, path):
    value = reports[0]
    for key in path[:-1]:
        value = value[key]
    value[path[-1]] = 0.
    with pytest.raises(ZeroDivisionError):
        verification.compare(reports)


@pytest.mark.parametrize("path,new_value", [
    (("acceleration", "device"), "cpu"),
    (("training_execution", "device"), "cpu"),
    (("training_execution", "gpu_device_verified"), False),
    (("training_execution", "gpu_fit_records"), [{"gpu_device_verified": True}] * 2),
    (("training_execution", "gpu_fit_records"), [{"gpu_device_verified": True}] * 4),
    (("training_execution", "gpu_fit_records"), [{"gpu_device_verified": True},
        {"gpu_device_verified": False}, {"gpu_device_verified": True}]),
])
def test_gpu_fallback_or_incomplete_evidence_not_counted_as_gpu_speedup(reports, path, new_value):
    value = reports[2]
    for key in path[:-1]:
        value = value[key]
    value[path[-1]] = new_value
    result = verification.compare(reports)
    assert result["actual_gpu_verified"] is False
    assert result["acceleration_path_pass"] is False


@pytest.mark.parametrize("case", [1, 2])
def test_cache_miss_cannot_be_counted_as_reuse_benefit(reports, case):
    reports[case]["acceleration"]["cache_hit"] = False
    result = verification.compare(reports)
    assert result["feature_reuse_verified"] is False
    assert result["acceleration_path_pass"] is False


def test_baseline_point_in_time_gate_is_required(reports):
    reports[0]["acceleration"]["cache_receipt"]["point_in_time_validated"] = False
    assert verification.compare(reports)["quality_pass"] is False


def test_publication_requires_completed_durable_published_record(monkeypatch, tmp_path):
    monkeypatch.setattr(verification, "ROOT", tmp_path)
    paths = []
    class ReadOnlyDB:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def execute(self, query, values):
            assert "p.state='published'" in query
            assert "j.job_id=?" in query
            assert values == ("the-job",)
            return self
        def fetchone(self):
            return None
    def connect(path, uri):
        paths.append(path)
        assert uri is True
        assert path.endswith("?mode=ro")
        return ReadOnlyDB()
    monkeypatch.setattr(verification.sqlite3, "connect", connect)
    with pytest.raises(ValueError, match="completed-publication-required"):
        verification.publication("the-job")
    assert len(paths) == 1


def test_publication_hash_validation_precedes_deserialization(monkeypatch, tmp_path):
    import joblib
    from services import db_shared_input
    monkeypatch.setattr(verification, "ROOT", tmp_path)
    model = tmp_path / "model.joblib"
    snapshot = tmp_path / "snapshot.db"
    class DB:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def execute(self, *_):
            return self
        def fetchone(self):
            return ("completed", str(snapshot), "a" * 64,
                "2026-10-11T00:00:00Z", "2026-10-11T00:01:00Z", str(model), "b" * 64, "{}")
    monkeypatch.setattr(verification.sqlite3, "connect", lambda *_, **__: DB())
    monkeypatch.setattr(db_shared_input, "validate_published_artifact", lambda *_: "wrong-hash")
    monkeypatch.setattr(joblib, "load", lambda *_: pytest.fail("Unverified joblib loaded"))
    with pytest.raises(ValueError, match="publication-hash-mismatch"):
        verification.publication("the-job")


def _stub_publication(monkeypatch, tmp_path, *, local_job="the-job", local_snapshot="a" * 64,
                      postload_hash="b" * 64, actual_snapshot_hash="a" * 64):
    import joblib
    from services import db_shared_input
    from training import db_shared_fit
    monkeypatch.setattr(verification, "ROOT", tmp_path)
    model, snapshot = tmp_path / "model.joblib", tmp_path / "snapshot.db"
    class DB:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def execute(self, *_):
            return self
        def fetchone(self):
            return ("completed", str(snapshot), "a" * 64,
                "2026-10-11T00:00:00Z", "2026-10-11T00:01:00Z", str(model), "b" * 64, "{}")
    monkeypatch.setattr(verification.sqlite3, "connect", lambda *_, **__: DB())
    monkeypatch.setattr(db_shared_input, "validate_published_artifact", lambda *_: "b" * 64)
    monkeypatch.setattr(joblib, "load", lambda *_: {"local_execution": {
        "job_id": local_job, "data_snapshot_sha256": local_snapshot}})
    monkeypatch.setattr(db_shared_input, "verify_bundle_sources", lambda *_: None)
    monkeypatch.setattr(db_shared_fit, "_validate_prediction_bundle", lambda *_: None)
    monkeypatch.setattr(verification.protection, "file_hash",
        lambda path: postload_hash if Path(path) == model else actual_snapshot_hash)


def test_publication_file_mutation_during_load_fails(monkeypatch, tmp_path):
    _stub_publication(monkeypatch, tmp_path, postload_hash="changed")
    with pytest.raises(ValueError, match="changed-during-load"):
        verification.publication("the-job")


def test_publication_snapshot_hash_is_independently_rechecked(monkeypatch, tmp_path):
    _stub_publication(monkeypatch, tmp_path, actual_snapshot_hash="changed")
    with pytest.raises(ValueError, match="snapshot-hash-mismatch"):
        verification.publication("the-job")


@pytest.mark.parametrize("options", [{"local_job": "another-job"}, {"local_snapshot": "another-snapshot"}])
def test_publication_bundle_must_bind_to_actual_job_and_snapshot(monkeypatch, tmp_path, options):
    _stub_publication(monkeypatch, tmp_path, **options)
    with pytest.raises(ValueError, match="job-binding-mismatch"):
        verification.publication("the-job")
