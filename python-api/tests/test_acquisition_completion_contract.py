"""Regression boundaries for durable acquisition completion, no HTTP/live DB."""
from __future__ import annotations

import copy
import json
import sqlite3

import pytest

from scraping.acquisition_scope import hydrate_scoped_quality, init_scoped_date_ledger
from scraping.quality import (
    classify_race_quality, evaluate_date_completeness, hydrate_quality_from_existing_data,
    init_acquisition_quality_db, queue_repair, record_race_quality,
)
from scraping.storage import _init_sqlite_db, _save_race_sqlite_only

RID = "202605010101"
DAY = "20260104"


def race():
    return {"race_info": {"race_id": RID, "date": DAY, "venue": "東京", "distance": 1600,
                          "num_horses": 3},
            "horses": [{"race_id": RID, "horse_id": f"202000000{i}", "horse_number": i,
                        "horse_name": f"Horse {i}", "finish_position": i, "finish_time": "1:34.5",
                        "odds": 2.0 + i, "popularity": i, "weight_kg": 470,
                        "sire": "Sire", "dam": "Dam", "damsire": "Damsire"} for i in (1, 2, 3)],
            "return_tables": []}


def test_exact_declared_count_complete_and_no_mutation():
    data = race()
    original = copy.deepcopy(data)
    report = classify_race_quality(data)
    assert report.valid_for_date_completion
    assert report.as_dict()["completion_policy"] == "acquisition-complete-v2"
    assert report.declared_horse_count == 3
    assert report.field_states["horse_count"] == "available"
    assert data == original


@pytest.mark.parametrize("count", [None, 0, -1, 3.5, True, "NaN", float("inf")])
def test_missing_or_invalid_count_never_certifies_complete(count):
    data = race()
    data["race_info"]["num_horses"] = count
    report = classify_race_quality(data)
    assert not report.valid_for_storage
    assert not report.valid_for_date_completion
    assert any(error.startswith("horse_count_") for error in report.required_errors)


@pytest.mark.parametrize("count", [2, 4])
def test_even_all_settled_rows_cannot_hide_count_mismatch(count):
    data = race()
    data["race_info"]["num_horses"] = count
    report = classify_race_quality(data)
    assert not report.valid_for_date_completion
    assert "horse_count_mismatch" in report.required_errors


@pytest.mark.parametrize("status", ["中", "中止", "競走中止", "取", "取消", "出走取消",
                                   "除", "除外", "競走除外", "失", "失格"])
def test_legitimate_nonfinish_not_a_failed_scrape(status):
    data = race()
    horse = data["horses"][-1]
    horse.update(finish_position=status, finish_time=None, odds=None, popularity=None, weight_kg=None)
    report = classify_race_quality(data)
    assert report.valid_for_date_completion
    assert report.field_states["finish_time"] == "available_with_domain_exceptions"
    assert "finish_time_missing" not in report.required_errors


def test_withdrawal_does_not_silently_reduce_declared_full_roster_count():
    data = race()
    data["horses"][-1].update(finish_position="取", finish_time=None)
    data["race_info"]["num_horses"] = 2
    report = classify_race_quality(data)
    assert not report.valid_for_date_completion
    assert "horse_count_mismatch" in report.required_errors


def test_demotion_is_settled_but_still_requires_finish_time():
    data = race()
    data["horses"][1]["finish_position"] = "2(降)"
    assert classify_race_quality(data).valid_for_date_completion
    data["horses"][1]["finish_time"] = "**"
    assert "finish_time_missing" in classify_race_quality(data).required_errors


@pytest.mark.parametrize("value", ["取消予定", "中断予定", "未確定", 1.5, True, float("inf"), 0])
def test_unconfirmed_or_invalid_finish_never_certifies_complete(value):
    data = race()
    data["horses"][0]["finish_position"] = value
    assert not classify_race_quality(data).valid_for_date_completion


@pytest.mark.parametrize("value", ["**", "garbage", "0", "1:99.2", True, float("inf")])
def test_nonempty_but_invalid_time_cannot_certify_complete(value):
    data = race()
    data["horses"][0]["finish_time"] = value
    report = classify_race_quality(data)
    assert not report.valid_for_date_completion
    assert "finish_time_missing" in report.required_errors


def test_canonical_time_seconds_and_finish_alias_are_supported():
    data = race()
    for horse in data["horses"]:
        horse["finish"] = horse.pop("finish_position")
        horse["time_seconds"] = 94.5
        horse.pop("finish_time")
    assert classify_race_quality(data).valid_for_date_completion


@pytest.mark.parametrize("change", ["horse_id", "horse_number", "empty_id", "bad_number", "out_of_range_number", "nonobject"])
def test_bad_or_duplicate_identity_blocks_completion(change):
    data = race()
    if change == "horse_id":
        data["horses"][1]["horse_id"] = data["horses"][0]["horse_id"]
    elif change == "horse_number":
        data["horses"][1]["horse_number"] = "01"
    elif change == "empty_id":
        data["horses"][1]["horse_id"] = "unknown_local"
    elif change == "bad_number":
        data["horses"][1]["horse_number"] = 1.5
    elif change == "out_of_range_number":
        data["horses"][1]["horse_number"] = 19
    else:
        data["horses"][1] = ["not", "a", "runner"]
    assert not classify_race_quality(data).valid_for_date_completion


@pytest.mark.parametrize("change", ["header_date", "date_alias", "runner_date", "runner_race"])
def test_metadata_identity_conflicts_block_completion(change):
    data = race()
    if change == "header_date":
        data["race_info"]["date"] = "20260230"
    elif change == "date_alias":
        data["race_info"]["race_date"] = "20260105"
    elif change == "runner_date":
        data["horses"][0]["date"] = "20260105"
    else:
        data["horses"][0]["race_id"] = "202605010102"
    assert not classify_race_quality(data).valid_for_date_completion


@pytest.mark.parametrize("flag", ["card", "provisional", "pending"])
def test_card_or_provisional_payload_cannot_claim_confirmed_result(flag):
    data = race()
    if flag == "card":
        data["horses"][0]["_shutuba"] = True
    elif flag == "provisional":
        data["race_info"]["_provisional_result"] = True
    else:
        data["race_info"]["result_status"] = "pending"
    report = classify_race_quality(data)
    assert not report.valid_for_date_completion
    assert "result_not_confirmed" in report.required_errors


def database(tmp_path):
    path = tmp_path / "ultimate.db"
    _init_sqlite_db(path)
    init_acquisition_quality_db(path)
    init_scoped_date_ledger(path)
    return path


def test_partial_result_does_not_close_full_race_repair(tmp_path):
    path = database(tmp_path)
    data = race()
    data["horses"][-1]["finish_position"] = None
    data["horses"][-1]["finish_time"] = None
    report = classify_race_quality(data)
    assert report.valid_for_storage and not report.valid_for_date_completion
    queue_repair(path, entity_type="race", entity_id=RID, repair_kind="full_race", error="test")
    record_race_quality(path, race_date=DAY, race_id=RID, report=report, record_observation=False)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT status FROM scrape_repair_queue WHERE repair_kind='full_race'").fetchone()[0] != "completed"


def test_quarantine_cannot_be_counted_as_complete_even_with_stale_quality_status(tmp_path):
    path = database(tmp_path)
    record_race_quality(path, race_date=DAY, race_id=RID, report=classify_race_quality(race()))
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE scrape_race_acquisition_state SET excluded_from_standard=1 WHERE race_id=?", (RID,))
    result = evaluate_date_completeness(path, DAY, [RID])
    assert result["status"] == "blocked"
    assert result["complete_race_count"] == 0


def test_scoped_hydration_reclassifies_stored_undersized_result(tmp_path):
    path = database(tmp_path)
    data = race()
    data["horses"].pop()
    assert _save_race_sqlite_only(data, path)
    result = hydrate_scoped_quality(path, DAY, [RID])
    assert result["status"] == "partial" and result["complete_race_count"] == 0


def test_legacy_hydration_does_not_drop_malformed_extra_row_and_pass(tmp_path):
    path = database(tmp_path)
    assert _save_race_sqlite_only(race(), path)
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO race_results_ultimate(race_id,data) VALUES (?,?)", (RID, json.dumps(["bad"])))
    result = hydrate_quality_from_existing_data(path, DAY, [RID])
    assert result["status"] == "partial" and result["complete_race_count"] == 0
