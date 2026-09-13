"""The replay harness must fail closed and compare the complete data payload."""
from __future__ import annotations

import asyncio
import copy
import importlib.util
from pathlib import Path

import pytest


_path = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_acquisition_replay.py"
_spec = importlib.util.spec_from_file_location("acquisition_replay_benchmark", _path)
benchmark = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(benchmark)


def test_payload_digest_checks_optional_fields_as_well_as_core_fields():
    original = [{"race_info": {"race_id": "202501010101"}, "horses": [{"sire": "A", "prev_date": "20241201"}]}]
    reordered = [{"horses": [{"prev_date": "20241201", "sire": "A"}], "race_info": {"race_id": "202501010101"}}]
    missing_optional = [{"race_info": {"race_id": "202501010101"}, "horses": [{"sire": "A"}]}]
    assert benchmark.digest(original) == benchmark.digest(reordered)
    assert benchmark.digest(original) != benchmark.digest(missing_optional)


def test_replay_uses_exact_saved_body_and_actual_failure_status():
    url = "https://db.sp.netkeiba.com/horse/result/2020100001/"
    failure_url = "https://db.netkeiba.com/horse/ped/2020100001/"
    body = "<html>保存済みの本文</html>".encode()
    session = benchmark.ReplaySession({
        url: (url, 200, "{}", body, 123.0, 999.0),
        failure_url: (failure_url, 400, "{}", b"", 123.0, 123.0),
    })
    assert asyncio.run(session.get(url).read()) == body
    assert session.get(failure_url).status == 400
    assert session.synthetic_400 == 0


def test_missing_replay_response_is_never_treated_as_success():
    session = benchmark.ReplaySession({})
    url = "https://db.netkeiba.com/horse/ped/2020100001/"
    with pytest.raises(AssertionError, match="Missing frozen response"):
        session.get(url)
    assert session.unresolved == {url}


def test_live_socket_guard_rejects_connection():
    with pytest.raises(AssertionError, match="Live network is forbidden"):
        benchmark._block_network(("db.netkeiba.com", 443))


def _comparison_reports():
    def report(name, seconds):
        return {"workload": "races", "corpus": {"body_hash": "same", "seed_pedigree": "same"},
                "profiling_enabled": False, "external_http_requests": 0, "limitations": [],
                "runs": [{"stage": "cold", "implementation": name, "package_sha256": name,
                          "local_replay_wall_sec": seconds, "payload_sha256": "payload",
                          "quality_sha256": "quality", "training_eligibility": {"rows": 5},
                          "external_http_requests": 0}]}
    return report("optimized", 2), report("baseline", 10)


def test_separate_reference_comparison_is_explicit_and_retains_raw_provenance():
    current, baseline = _comparison_reports()
    combined = benchmark.combine_reference_reports(current, baseline, "new.json", "old.json")
    assert combined["comparisons"][0]["local_replay_speedup"] == 5
    assert "Non-interleaved" in combined["comparison_sampling"]
    assert combined["raw_reports"] == {"baseline": "old.json", "optimized": "new.json"}
    assert combined["runs"][0]["raw_report"] == "old.json"
    assert "raw_report" not in baseline["runs"][0]


def test_separate_reference_rejects_changed_seed_even_when_body_hash_is_equal():
    current, baseline = _comparison_reports()
    baseline["corpus"]["seed_pedigree"] = "different"
    with pytest.raises(ValueError, match="exact frozen corpus"):
        benchmark.combine_reference_reports(current, baseline, "new.json", "old.json")


def test_separate_reference_never_claims_speedup_for_changed_payload():
    current, baseline = _comparison_reports()
    current["runs"][0]["payload_sha256"] = "missing_optional_field"
    combined = benchmark.combine_reference_reports(current, baseline, "new.json", "old.json")
    assert combined["comparisons"][0]["identical_all_fields_quality_and_training"] is False
    assert combined["comparisons"][0]["local_replay_speedup"] is None


def test_separate_reference_rejects_mixed_source_versions():
    current, baseline = _comparison_reports()
    current["runs"].append(copy.deepcopy(current["runs"][0]))
    current["runs"][1]["package_sha256"] = "changed_mid_run"
    baseline["runs"].append(copy.deepcopy(baseline["runs"][0]))
    with pytest.raises(ValueError, match="source changed"):
        benchmark.combine_reference_reports(current, baseline, "new.json", "old.json")
