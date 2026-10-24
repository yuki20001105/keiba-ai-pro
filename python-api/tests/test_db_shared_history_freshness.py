"""RO freshness inventory does not relax canonical or complete-field gates."""
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
AUDIT = importlib.import_module("audit_db_shared_history_freshness")


def fixture_db(path):
    connection = sqlite3.connect(path)
    connection.executescript("""
    CREATE TABLE races_ultimate(race_id TEXT PRIMARY KEY,data TEXT);
    CREATE TABLE race_results_ultimate(race_id TEXT,data TEXT);
    CREATE INDEX runner_race ON race_results_ultimate(race_id);
    CREATE TABLE scrape_race_acquisition_state(
      race_id TEXT,lifecycle TEXT,quality_status TEXT,excluded_from_standard INTEGER);
    """)
    data = (("202605010101", "20260913", "complete"),
            ("202605010201", "20260914", "card"),
            ("202605010301", "20260915", "partial"),
            ("202605010401", "20260916", "jump"),
            ("202605010501", "20260917", "bad_runner"),
            ("202605010601", "20260918", "time_missing"),
            ("202605010701", "20260919", "future"))
    for rid, day, mode in data:
        info = {"race_id": rid, "date": day, "venue": "東京", "distance": 1600,
            "track_type": "芝", "num_horses": 3, "race_name": "障害未勝利" if mode == "jump" else "未勝利"}
        connection.execute("INSERT INTO races_ultimate VALUES (?,?)", (rid, json.dumps(info)))
        for number in range(1, 4 if mode != "partial" else 3):
            row = {"race_id": rid, "horse_id": "h" + str(number), "horse_number": number,
                "finish_position": number, "finish_time": 90. + number, "odds": 3. + number,
                "popularity": number}
            if mode == "card":
                row.pop("finish_position")
                row.pop("finish_time")
            if mode == "bad_runner" and number == 2:
                row["venue"] = "京都"
            if mode == "time_missing" and number == 2:
                row["finish_time"] = "-"
            connection.execute("INSERT INTO race_results_ultimate VALUES (?,?)", (rid, json.dumps(row)))
    connection.commit()
    connection.close()
    return path


def test_freshness_reuses_frozen_gates_and_read_only_transaction(tmp_path):
    path = fixture_db(tmp_path / "source.db")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    value = AUDIT.audit_database(path, cutoff="20260919", focus_from="20260914", focus_to="20260918")
    assert value["read_transaction"]["query_only"] == 1
    assert value["read_transaction"]["in_transaction"] is True
    assert value["database_writes"] == value["network_requests"] == 0
    assert value["source_file_changed_during_read_transaction"] is False
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert value["eligible_complete_history_date_range"]["to"] == "20260913"
    assert value["raw_metadata_date_range_all_saved_domains"]["to"] == "20260919"
    races = {item["race_id"]: item for item in value["recent_races"]}
    assert races["202605010201"]["stage"] == "card_only_or_unsettled_observation"
    assert "result_not_settled" in races["202605010201"]["exclusion_reasons"]
    assert "horse_count_inconsistent" in races["202605010301"]["exclusion_reasons"]
    assert "jump_evidence_quarantined" in races["202605010401"]["exclusion_reasons"]
    assert "runner_race_metadata_conflict" in races["202605010501"]["exclusion_reasons"]
    assert "partial_selected_race_rejected_by_shared_complete_cohort" in races["202605010601"]["exclusion_reasons"]
    assert all(not item["eligible"] for item in races.values())


def test_unknown_saved_days_not_claimed_as_no_meeting(tmp_path):
    value = AUDIT.audit_database(fixture_db(tmp_path / "source.db"), cutoff="20260920",
        focus_from="20260914", focus_to="20260919")
    assert value["daily"][0]["no_saved_races_is_not_proof_of_no_meeting"] is False
    value = AUDIT.audit_database(tmp_path / "source.db", cutoff="20260921",
        focus_from="20260920", focus_to="20260920")
    assert value["daily"][0]["saved_metadata_races"] == 0
    assert value["daily"][0]["no_saved_races_is_not_proof_of_no_meeting"] is True


def test_malformed_metadata_and_quality_exclusion_remain_audited(tmp_path):
    path = fixture_db(tmp_path / "source.db")
    connection = sqlite3.connect(path)
    connection.execute("UPDATE races_ultimate SET data='not-json' WHERE race_id='202605010701'")
    connection.execute("INSERT INTO scrape_race_acquisition_state VALUES (?,?,?,?)",
        ("202605010101", "pre_start", "partial", 1))
    connection.commit()
    connection.close()
    value = AUDIT.audit_database(path, cutoff="20260919", focus_from="20260914", focus_to="20260918")
    assert value["metadata_scope_exclusions"]["race_json_invalid"] == 1
    assert value["eligible_complete_history_races"] == 0
    assert value["eligibility_receipt"]["eligibility"]["exclusion_reason_counts"]["recorded_quality_incomplete"]["races"] == 1


def test_structural_nonfinishers_not_imputed_zero():
    summary = AUDIT._raw_runner_summary([{"finish_position": "取消", "finish_time": "-"},
                                       {"finish_position": 1, "finish_time": "1:31.0"}])
    assert summary["positive_finish_rows"] == summary["positive_time_rows"] == 1
    assert summary["explicit_nonfinisher_labels"] == {"取消": 1}
    assert AUDIT._raw_runner_summary([{"finish_position": "除", "finish_time": ""}])[
        "explicit_nonfinisher_labels"] == {"除": 1}


@pytest.mark.parametrize("cutoff,start,end", [("20261012", "20261012", "20261012"),
    ("2026-10-12", "20260914", "20261011"), ("20261012", "20260230", "20261011")])
def test_period_must_be_strictly_before_target(tmp_path, cutoff, start, end):
    with pytest.raises(ValueError, match="freshness-period-invalid"):
        AUDIT.audit_database(tmp_path / "nonexistent", cutoff=cutoff, focus_from=start, focus_to=end)


def test_current_capture_identity_date_and_no_career_coverage_claim(tmp_path):
    source = fixture_db(tmp_path / "source.db")
    capture = tmp_path / "capture"
    (capture / "sources").mkdir(parents=True)
    path = capture / "sources/canonical_inputs.json"
    path.write_text(json.dumps({"current": [{"horse_id": "h1", "horse_number": 1, "race_date": "20260919"},
                                            {"horse_id": "new", "horse_number": 2, "race_date": "20260919"}]}))
    value = AUDIT.audit_database(source, cutoff="20260919", focus_from="20260914", focus_to="20260918",
        current_capture=capture)
    assert value["target_horse_history"][0]["raw_latest_prior_date"] == "20260918"
    assert value["target_horse_history"][0]["eligible_latest_prior_date"] == "20260913"
    assert value["target_horse_history"][0]["raw_rows_newer_than_eligible"] is True
    assert value["target_horse_history"][1]["no_eligible_history"] is True
    assert value["target_horse_history"][1]["days_since_latest_eligible_history"] is None
    assert not value["complete_career_coverage_verified"]
    newer = {item["race_id"]: item for item in value["target_horse_excluded_newer_races"]}
    assert newer["202605010201"]["raw_runner_summary"]["rows"] == 3
    assert newer["202605010201"]["affected_current_horse_ids"] == ["h1"]
    assert "horse_count_inconsistent" in newer["202605010301"]["exclusion_reasons"]
    assert value["logical_transaction_source_sha256"]["all_races_ultimate_in_scan_order"]
    assert value["source_file_before"]["data_sidecars"] == {"-wal": None, "-journal": None}
    path.write_text(json.dumps({"current": [{"horse_id": "h1", "race_date": "20260920"}]}))
    with pytest.raises(ValueError, match="capture-date-invalid"):
        AUDIT.audit_database(source, cutoff="20260919", focus_from="20260914", focus_to="20260918",
            current_capture=capture)


def test_persistence_new_immutable_object_and_manifest(tmp_path):
    value = AUDIT.audit_database(fixture_db(tmp_path / "source.db"), cutoff="20260919",
        focus_from="20260914", focus_to="20260918")
    first = AUDIT.persist_audit(value, tmp_path / "out")
    second = AUDIT.persist_audit(value, tmp_path / "out")
    assert first != second
    manifest = json.loads((first / "artifact_manifest.json").read_text())
    assert manifest["files"]["audit.json"]["sha256"] == AUDIT.sha(first / "audit.json")
    assert manifest["receipt_sha256"] == AUDIT.object_sha(value)
