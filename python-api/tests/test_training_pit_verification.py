"""Small isolated fixtures for the independent SQL/stdlib PIT recount."""
import csv
import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from verify_training_data_pit_audit import compare_outputs, event_class, recount, sha256, strict_date


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE training_data(id INTEGER,race_id TEXT,horse_number INTEGER,training_date TEXT,created_at TEXT,course TEXT);
            CREATE TABLE races_ultimate(race_id TEXT,race_date TEXT);
            CREATE TABLE race_results_ultimate(race_id TEXT,horse_number INTEGER);
            CREATE TABLE races(race_id TEXT);
            INSERT INTO races_ultimate VALUES ('202101010101','20210110'),('202101010102','20210101');
        """)
        values = [
            (1, "202101010101", 1, "2021/01/09", "2026-01-01 00:00:00", "A"),
            (2, "202101010101", 2, "2021/01/10", "2021-01-10 00:00:00", "A"),
            (3, "202101010101", 3, "2021/01/11", "2021-01-09 00:00:00", "A"),
            (4, "202101010101", 4, "2021/02/30", None, "A"),
            (5, "202101010199", 1, "2021/01/01", "bad", "A"),
            (6, "202101010102", 1, "2020/12/31", "2026-01-01 00:00:00", "B"),
            (7, "202101010101", 5, "2021/01/01", "2026-01-01 00:00:00", "B"),
        ]
        db.executemany("INSERT INTO training_data VALUES (?,?,?,?,?,?)", values)
        db.executemany("INSERT INTO race_results_ultimate VALUES (?,?)", [(v[1], v[2]) for v in values if v[0] != 5])
        db.execute("INSERT INTO race_results_ultimate VALUES ('202101010101',5)")
    return path


def count(source):
    return recount(source, sha256(source), {("202101010101", 1), ("202101010101", 2)})


def make_reports(output, result, records):
    inventory = {"rows": result["rows"], "safe_point_in_time_records": 0, "temporal_feature_gate": "NO",
        "event_date_classes": result["event_date_classes"],
        "year_inventory": [{"year": year, **row} for year, row in result["race_id_prefix_year_inventory"].items()],
        "cohort_reproduction": {"released_clean": result["released_clean_cohort"]},
        **{key: result["totals"][key] for key in ("duplicate_workout_key_rows", "repeated_race_runner_rows")}}
    (output / "training_data_inventory.json").write_text(json.dumps(inventory), encoding="utf-8")
    rows = [{"training_id": key, "race_id": row["race_id"], "horse_number": row["horse_number"],
             "training_date_raw": row["training_date"], "event_date_class": row["expected_event_date_class"],
             "source_capture_time_verified": False, "safe_point_in_time": False,
             "registration_is_availability_proof": False} for key, row in reversed(list(records.items()))]
    with (output / "training_point_in_time_audit.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return inventory, rows


def test_recounts_event_day_separate_from_registration_and_capture(source):
    before = sha256(source)
    report, records = count(source)
    assert report["rows"] == 7 and len(records) == 7
    assert report["event_date_classes"] == {"A_BEFORE_RACE": 3, "B_SAME_DATE_TIME_UNKNOWN": 1,
        "C_AFTER_RACE": 1, "D_DATE_OR_RACE_UNKNOWN": 2}
    assert report["totals"]["number_join_exact"] == 5
    assert report["totals"]["number_join_missing"] == 1
    assert report["totals"]["number_join_ambiguous"] == 1
    assert report["totals"]["db_insertion_day_before"] == 1
    assert report["totals"]["db_insertion_day_same"] == 1
    assert report["safe_point_in_time_records"] == 0
    assert report["legacy_registry_rows"] == 0
    assert report["released_clean_cohort"]["training_records"] == 2
    assert report["year_inventory"]["2021"]["rows"] == 6
    assert report["year_inventory"]["unknown"]["rows"] == 1
    assert sha256(source) == before


@pytest.mark.parametrize("value", ["20210230", "20210229", "2021011", "20210101suffix", "2021/1/1", None])
def test_strict_canonical_calendar(value):
    assert strict_date(value) is None


def test_previous_year_is_preserved_same_day_not_approved():
    assert event_class("2020/12/31", "20210101") == "A_BEFORE_RACE"
    assert event_class("2021/01/01", "20210101") == "B_SAME_DATE_TIME_UNKNOWN"
    assert event_class("12/31", "20210101") == "D_DATE_OR_RACE_UNKNOWN"


def test_hash_fail_closed_before_database_open(source):
    with pytest.raises(ValueError, match="hash mismatch"):
        recount(source, "0" * 64, set())


def test_ambiguous_race_keys_rejected(source):
    with sqlite3.connect(source) as db:
        db.execute("INSERT INTO races_ultimate VALUES ('202101010101','20210110')")
    with pytest.raises(ValueError, match="ambiguous canonical"):
        count(source)


def test_new_availability_column_requires_new_evidence_not_blind_zero(source):
    with sqlite3.connect(source) as db:
        db.execute("ALTER TABLE training_data ADD COLUMN received_at TEXT")
    with pytest.raises(ValueError, match="source-availability schema"):
        count(source)


def test_report_comparison_accepts_shuffled_rows(source, tmp_path):
    result, records = count(source)
    make_reports(tmp_path, result, records)
    assert len(compare_outputs(tmp_path, result, records)) == 2


@pytest.mark.parametrize("key,value", [("rows", 8), ("safe_point_in_time_records", 1), ("temporal_feature_gate", "YES")])
def test_false_inventory_or_pit_claim_rejected(source, tmp_path, key, value):
    result, records = count(source)
    inventory, _ = make_reports(tmp_path, result, records)
    inventory[key] = value
    (tmp_path / "training_data_inventory.json").write_text(json.dumps(inventory), encoding="utf-8")
    with pytest.raises(ValueError):
        compare_outputs(tmp_path, result, records)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "capture", "event", "horse", "date"])
def test_incomplete_or_modified_per_record_audit_rejected(source, tmp_path, mutation):
    result, records = count(source)
    _, rows = make_reports(tmp_path, result, records)
    if mutation == "missing": rows.pop()
    elif mutation == "duplicate": rows.append(rows[0].copy())
    elif mutation == "capture": rows[0]["source_capture_time_verified"] = True
    elif mutation == "event": rows[0]["event_date_class"] = "C_AFTER_RACE"
    elif mutation == "horse": rows[0]["horse_number"] = 18
    else: rows[0]["training_date_raw"] = "2020/01/01"
    with (tmp_path / "training_point_in_time_audit.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError):
        compare_outputs(tmp_path, result, records)
