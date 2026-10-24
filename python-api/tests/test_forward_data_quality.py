import json
import sqlite3

import numpy as np
import pandas as pd
import pytest

from experiments.common import file_sha256, read_json
from forward_runtime.data_quality import audit_clean_history, prepare_daily_history


def snapshot(tmp_path, mutation=None):
    path = tmp_path / "snapshot.db"
    db = sqlite3.connect(path)
    db.executescript("CREATE TABLE races_ultimate(race_id TEXT,data TEXT); CREATE TABLE race_results_ultimate(race_id TEXT,data TEXT);")
    races, horses = [], []
    for day in (18, 19):
        rid = f"2025050101{day - 17:02d}"
        race = {"race_id": rid, "date": f"202509{day}", "surface": "turf", "venue": "東京", "distance": 1600, "num_horses": 4}
        races.append((rid, json.dumps(race)))
        for number in range(1, 5):
            horse = {"race_id": rid, "horse_id": f"horse{number}", "horse_number": number,
                     "finish": number, "time_seconds": 90 + number, "odds": number * 2, "popularity": number,
                     "sire": "S", "damsire": "D", "horse_weight_change": 0}
            if mutation == "missing_target" and day == 19 and number == 4:
                horse["time_seconds"] = None
            if mutation == "blood_conflict" and day == 19 and number == 1:
                horse["sire"] = "OTHER"
            horses.append((rid, json.dumps(horse)))
    if mutation == "exact":
        horses.append(horses[0]); races.append(races[0])
    if mutation in ("conflict", "nonfeature_conflict"):
        row = json.loads(horses[0][1]); row["time_seconds" if mutation == "conflict" else "comment"] = 101
        horses.append((horses[0][0], json.dumps(row)))
    if mutation == "race_conflict":
        row = json.loads(races[0][1]); row["weather"] = "雨"
        races.append((races[0][0], json.dumps(row)))
    if mutation == "duplicate_horse":
        row = json.loads(horses[1][1]); row["horse_id"] = "horse1"
        horses[1] = (horses[1][0], json.dumps(row))
    db.executemany("INSERT INTO races_ultimate VALUES (?,?)", races)
    db.executemany("INSERT INTO race_results_ultimate VALUES (?,?)", horses)
    db.commit(); db.close()
    return path


def test_exact_duplicates_collapse_only_in_new_export_and_keep_original(tmp_path):
    source = snapshot(tmp_path, "exact"); before = file_sha256(source)
    report = audit_clean_history(source, tmp_path / "clean", before_date="20250920")
    assert report["raw_counts"]["raw_runner_rows"] == 9
    assert report["raw_counts"]["exact_duplicate_runner_rows"] == 1
    assert report["raw_counts"]["exact_duplicate_race_rows"] == 1
    assert report["clean_rows"] == 8 and report["clean_races"] == 2
    assert file_sha256(source) == before
    assert report["clean_missingness"]["horse_weight_change"]["missing"] == 0
    assert report["clean_missingness"]["age"]["missing"] == 8
    frame = pd.read_parquet(tmp_path / "clean/clean_history.parquet")
    assert frame.age.isna().all() and frame.horse_weight_change.eq(0).all()
    assert report["originals_modified"] is False


@pytest.mark.parametrize("mutation", ["conflict", "nonfeature_conflict", "race_conflict", "duplicate_horse"])
def test_any_conflicting_duplicate_quarantines_whole_race(tmp_path, mutation):
    report = audit_clean_history(snapshot(tmp_path, mutation), tmp_path / "clean", before_date="20250920")
    assert report["clean_rows"] == 4 and report["clean_races"] == 1
    repair = read_json(tmp_path / "clean/repair_manifest.json")
    assert "202505010101" in repair["quarantined_races"]


def test_missing_target_excludes_whole_race_not_only_runner(tmp_path):
    report = audit_clean_history(snapshot(tmp_path, "missing_target"), tmp_path / "clean", before_date="20250920")
    assert report["canonical_prequality_missingness"]["time_seconds"]["missing"] == 1
    assert report["clean_races"] == 1 and report["clean_rows"] == 4


def test_current_day_is_not_training_history(tmp_path):
    report = audit_clean_history(snapshot(tmp_path), tmp_path / "clean", before_date="20250919")
    assert report["clean_date_to"] == "20250918"
    assert report["scope_and_invalid_reasons"]["on_or_after_target_day"] == 1


def test_daily_state_and_bloodline_map_are_hash_bound_past_only(tmp_path):
    report = audit_clean_history(snapshot(tmp_path, "blood_conflict"), tmp_path / "clean", before_date="20250920")
    result = prepare_daily_history(tmp_path / "clean", tmp_path / "state", target_date="20250920")
    assert result["target_date"] == "20250920" and result["history_max_source_date"] == "20250919"
    assert file_sha256(result["state_path"]) == result["state_sha256"]
    assert result["clean_history_sha256"] == report["clean_history"]["sha256"]
    assert result["live_provider_parser_verified"] is False
    blood = read_json(result["bloodline_path"])
    assert blood["horses"]["horse1"]["sire"] is None
    assert blood["horses"]["horse1"]["damsire"] == "D"
    assert blood["horses"]["horse2"]["sire"] == "S"
    assert blood["conflicting_fields_by_horse"] == {"horse1": ["sire"]}
    envelope = read_json(result["state_path"])
    assert envelope["state_sha256"] == result["state_content_sha256"]


def test_later_clean_export_cannot_feed_earlier_state(tmp_path):
    audit_clean_history(snapshot(tmp_path), tmp_path / "clean", before_date="20250920")
    with pytest.raises(ValueError, match="cutoff-later"):
        prepare_daily_history(tmp_path / "clean", tmp_path / "state", target_date="20250919")


def test_changed_clean_data_and_output_overwrite_rejected(tmp_path):
    source = snapshot(tmp_path)
    audit_clean_history(source, tmp_path / "clean", before_date="20250920")
    with pytest.raises(ValueError, match="new-empty"):
        audit_clean_history(source, tmp_path / "clean", before_date="20250920")
    frame = pd.read_parquet(tmp_path / "clean/clean_history.parquet")
    frame.loc[0, "time_seconds"] = 100
    frame.to_parquet(tmp_path / "clean/clean_history.parquet", index=False)
    with pytest.raises(ValueError, match="hash-mismatch"):
        prepare_daily_history(tmp_path / "clean", tmp_path / "state", target_date="20250920")


def test_raw_invalid_json_is_quarantined_and_counted(tmp_path):
    source = snapshot(tmp_path)
    db = sqlite3.connect(source)
    db.execute("INSERT INTO race_results_ultimate VALUES (?,?)", ("202505010101", "invalid JSON")); db.commit(); db.close()
    result = audit_clean_history(source, tmp_path / "clean", before_date="20250920")
    assert result["scope_and_invalid_reasons"]["invalid_runner_json_rows"] == 1
    assert result["clean_races"] == 1


def test_recorded_incomplete_quality_cannot_be_cleaned_into_complete(tmp_path):
    source = snapshot(tmp_path)
    db = sqlite3.connect(source)
    db.execute("CREATE TABLE scrape_race_acquisition_state(race_id TEXT,lifecycle TEXT,quality_status TEXT,excluded_from_standard INTEGER)")
    db.execute("INSERT INTO scrape_race_acquisition_state VALUES ('202505010101','entry','pending',0)"); db.commit(); db.close()
    result = audit_clean_history(source, tmp_path / "clean", before_date="20250920")
    assert result["clean_races"] == 1


def test_sidecar_sources_require_their_own_contract(tmp_path):
    source = snapshot(tmp_path)
    db = sqlite3.connect(source); db.execute("CREATE TABLE official_history_entries(id INTEGER)")
    db.execute("INSERT INTO official_history_entries VALUES (1)"); db.commit(); db.close()
    with pytest.raises(ValueError, match="sidecar"):
        audit_clean_history(source, tmp_path / "clean", before_date="20250920")
