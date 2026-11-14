from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

path = Path(__file__).resolve().parents[2] / "scripts" / "repair_linked_history.py"
spec = importlib.util.spec_from_file_location("linked_history_repair", path)
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


def example():
    metadata = {
        "race_id": "201506050305", "date": "20151212", "venue": "中山",
        "distance": 1200, "track_type": "ダート", "num_horses": 2,
    }
    runners = [
        {"horse_id": "2013102589", "horse_number": 1, "horse_name": "A",
         "finish_position": 1, "finish_time": "1:12.0"},
        {"horse_id": "2013104864", "horse_number": 2, "horse_name": "B", "finish_position": None},
    ]
    proof = {"race_id": "201506050305", "rows": [
        {"horse_id": "2013102589", "finish": 1, "time_seconds": 72, "finish_source_raw": "1"},
        {"horse_id": "2013104864", "finish": None, "time_seconds": None, "finish_source_raw": "除"},
    ]}
    return proof, metadata, runners


def test_explicit_source_nonstarter_can_recover_a_whole_result_without_mutating_input():
    proof, metadata, runners = example()
    before = deepcopy(runners)
    repair.confirmable_result(proof, metadata, runners)
    assert runners == before


def test_a_missing_finish_is_not_assumed_to_be_a_nonstarter():
    proof, metadata, runners = example()
    proof["rows"][1]["finish_source_raw"] = ""
    with pytest.raises(ValueError, match="nonfinish-not-explicit"):
        repair.confirmable_result(proof, metadata, runners)


def test_existing_outcome_conflict_is_not_overwritten_by_a_quality_repair():
    proof, metadata, runners = example()
    proof["rows"][0]["time_seconds"] = 73
    with pytest.raises(ValueError, match="outcome-conflict"):
        repair.confirmable_result(proof, metadata, runners)


def test_source_course_must_match_and_cannot_smuggle_jumping_into_flat_history():
    _, metadata, _ = example()
    repair.validate_source_course('<div class="data_intro"><p class="smalltxt">ダ右1200m</p></div>', metadata)
    repair.validate_source_course(
        '<div class="data_intro"><dl class="racedata fc"><dd><p>ダ右1200m</p></dd></dl>'
        '<p class="smalltxt">2015年12月12日</p></div>', metadata,
    )
    with pytest.raises(ValueError, match="not-exactly-confirmed"):
        repair.validate_source_course('<div class="data_intro"><p class="smalltxt">芝右1200m</p></div>', metadata)
    with pytest.raises(ValueError, match="published-jump"):
        repair.validate_source_course('<div class="data_intro"><p class="smalltxt">障芝右1200m</p></div>', metadata)
