"""The compatibility auditor must classify, not invent or replace evidence."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "scripts/audit_active_model_input_compatibility.py"
SPEC = importlib.util.spec_from_file_location("active_model_input_audit", SOURCE)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def test_missing_groups_are_exact_names_not_guessed_aliases():
    groups = audit.classify_columns([
        "prev3_race_time", "prev5_race_distance", "holding_middle_time_sec",
        "holding_long_babasa", "horse_weight_change", "prev2_race_time",
        "holding_long_speed", "past3_avg_last3f_time_x",
    ])
    assert groups["legacy_previous_3_to_5_raw"] == ["prev3_race_time", "prev5_race_distance"]
    assert groups["legacy_external_holding_time"] == ["holding_middle_time_sec", "holding_long_babasa"]
    assert groups["race_day_observation"] == ["horse_weight_change"]
    assert groups["other"] == ["prev2_race_time", "holding_long_speed", "past3_avg_last3f_time_x"]


def test_report_is_exclusive_and_cannot_replace_existing_evidence(tmp_path):
    target = tmp_path / "new" / "audit.json"
    audit.immutable_report(target, {"conclusion": audit.CONCLUSION})
    before = target.read_bytes()
    with pytest.raises(FileExistsError):
        audit.immutable_report(target, {"conclusion": "PASS"})
    assert target.read_bytes() == before
    assert json.loads(before)["conclusion"] == "NOT_SEMANTICALLY_VERIFIED"


def test_readonly_connection_cannot_write(tmp_path):
    import sqlite3
    database = tmp_path / "sample.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.execute("INSERT INTO sample VALUES ('preserved')")
    original = sqlite3.connect
    with audit.readonly_sqlite():
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT value FROM sample").fetchone()[0] == "preserved"
            with pytest.raises(sqlite3.OperationalError):
                connection.execute("DELETE FROM sample")
        with pytest.raises(ValueError):
            sqlite3.connect(database.resolve().as_uri() + "?mode=rw", uri=True)
    assert sqlite3.connect is original


def test_hash_metadata_does_not_claim_semantic_equivalence():
    assert audit.CONCLUSION == "NOT_SEMANTICALLY_VERIFIED"
    assert audit.classify_columns([]) == {
        "legacy_previous_3_to_5_raw": [], "legacy_external_holding_time": [],
        "race_day_observation": [], "other": [],
    }
