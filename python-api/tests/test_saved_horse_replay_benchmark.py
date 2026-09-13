"""The saved-horse replay must not hide changed data or invent HTTP inputs."""
import asyncio
import copy
import importlib.util
from pathlib import Path

import pytest


path = Path(__file__).resolve().parents[2] / "scripts/benchmark_saved_horse_reuse.py"
spec = importlib.util.spec_from_file_location("saved_horse_benchmark", path)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_only_reserved_audit_metadata_is_excluded():
    source = {"horses": [{"sire": "A", "optional": None,
                           "_acquisition_reuse": {"history": "new audit"}}]}
    assert benchmark.provider_fields(source) == {"horses": [{"sire": "A", "optional": None}]}
    changed = {"horses": [{"sire": "A"}]}
    assert benchmark.digest(benchmark.provider_fields(source)) != benchmark.digest(changed)


def test_exact_replay_does_not_invent_desktop_400():
    mobile = "https://db.sp.netkeiba.com/horse/result/1/"
    session = benchmark.StrictReplaySession({mobile: (mobile, 200, "{}", b"recorded", 1, 2)})
    assert asyncio.run(session.get(mobile).read()) == b"recorded"
    with pytest.raises(benchmark.MissingReplayInput):
        session.get("https://db.netkeiba.com/horse/result/1/")
    assert len(session.missing) == 1


def test_network_guard_never_connects():
    with pytest.raises(AssertionError, match="External network forbidden"):
        benchmark.block_network(("example.test", 443))


def reports():
    common = {"provider_sha256": "a", "missing_counts": {"optional": 1},
              "quality_sha256": "b", "saved_provider_sha256": "c",
              "input_sha256": "d", "seed_sha256": "e", "implementation_sha256": "f",
              "replay_requests": 12}
    return [{**common, "enabled": False, "local_replay_wall_sec": 4},
            {**common, "enabled": True, "local_replay_wall_sec": 2}]


def test_matching_provider_and_seed_allows_local_only_ratio():
    result = benchmark.compare(reports())
    assert result["all_provider_fields_missingness_quality_equal"]
    assert result["local_replay_speedup"] == 2


@pytest.mark.parametrize("key", ["provider_sha256", "missing_counts", "quality_sha256",
                                  "saved_provider_sha256", "input_sha256", "seed_sha256",
                                  "implementation_sha256"])
def test_comparison_rejects_any_provider_seed_or_code_difference(key):
    runs = copy.deepcopy(reports())
    runs[1][key] = "changed"
    result = benchmark.compare(runs)
    assert not result["all_provider_fields_missingness_quality_equal"]
    assert result["local_replay_speedup"] is None


def test_constructed_seed_is_explicit_valid_identity_and_distinct_fallback():
    payload = [{"horse_id": "2000100001", "detail": {"sire": "父", "dam": "母", "damsire": "母父"}}]
    verified = benchmark.fixture_seed(payload, "verified")
    assert verified[0]["horses"][0]["race_id"] == verified[0]["race_info"]["race_id"]
    assert "unverified alias" in benchmark.fixture_seed(payload, "ambiguous")[0]["horses"][0]["sire"]
    assert benchmark.fixture_seed(payload, "missing") == []
