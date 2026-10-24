"""Synthetic only: preserving clocks cannot turn unknown provenance into PIT."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from training_reconstruction.normalization import (
    EVENT_FIELDS, MEASUREMENT_FIELDS, SLOTS, candidate_registry, classify_pit,
    day, event_validity, normalize_event, prior_reference, schema_definition,
)

DB_SHA = "a" * 64


def fixture():
    raw = {"id": 1, "race_id": "202105010101", "horse_number": 1, "horse_name": "テスト",
           "training_date": "2021/01/06", "course": " 栗坂 ", "track_condition": "良",
           "rider": "助手", "position": "内", "pace": "馬也", "grade": "A", "comment": "原文",
           "is_last_training": 1, "created_at": "2026-05-01 00:00:00",
           **{slot: None for slot in SLOTS}}
    raw.update(time_5f=52.1, time_4f=38.1, time_3f=25.0, time_1f=13.0, lap_1f_g=13.0)
    prior = {"training_id": "1", "race_id": raw["race_id"], "horse_number": "1",
             "normalized_training_name": "テスト", "training_date_raw": raw["training_date"],
             "race_date": "20210110", "identity_verified": "True", "resolved_horse_id": "horse-1",
             "event_date_class": "A_BEFORE_RACE", "record_pit_class": "B_SOURCE_DATE_VALID_BUT_NOT_CAPTURE_TIME_VERIFIED"}
    return raw, prior


def normalized():
    raw, prior = fixture()
    return normalize_event(raw, prior, DB_SHA)[0]


def receipt(event, **overrides):
    return {**{key: event[key] for key in ("race_id", "horse_number", "training_date", "course_normalized")},
            "raw_original_available": True, "raw_hash_verified": True, "row_values_verified": True,
            "raw_source_sha256": "b" * 64, "evidence_manifest_sha256": "c" * 64,
            "source_url": "https://example.test/historical", "retrieved_at": "2021-01-09T12:00:00+09:00",
            "contemporaneous_snapshot_verified": True, "publication_version_verified": True, **overrides}


def verified_measurement(**overrides):
    return {"race_id": "202105010201", "horse_id": "horse-1", "training_date": "20210113", "race_date": "20210117",
            "course_normalized": "栗坂", "track_condition": "良", "measurement_distance": 800,
            "measurement_kind": "cumulative", "time_seconds": 52.0, "pit_class": "A_STRICT_PIT",
            "raw_original_available": True, "capture_provenance": "contemporaneous_snapshot_verified",
            "distance_mapping_verified": True, "identity_verified": True, "quarantined": False,
            "source_hash": "b" * 64, "evidence_manifest_sha256": "c" * 64,
            "retrieved_at": "2021-01-16T12:00:00+09:00", **overrides}


def earlier(**overrides):
    return verified_measurement(**{"training_date": "20210106", "race_date": "20210110",
                                   "retrieved_at": "2021-01-09T12:00:00+09:00", **overrides})


def test_lossless_vertical_slots_and_nullable_distance():
    raw, prior = fixture()
    before = deepcopy((raw, prior))
    event, measurements = normalize_event(raw, prior, DB_SHA)
    assert (raw, prior) == before
    assert set(event) == {name for name, _ in EVENT_FIELDS}
    assert len(measurements) == 10
    assert [row["original_slot"] for row in measurements] == list(SLOTS)
    for row in measurements:
        assert set(row) == {name for name, _ in MEASUREMENT_FIELDS}
        assert json.loads(row["original_value"]) == raw[row["original_slot"]]
        assert row["measurement_distance"] is None
        assert row["lap_from_distance"] is row["lap_to_distance"] is None
        assert not row["distance_mapping_verified"] and not row["model_input_allowed"]
    assert measurements[0]["time_seconds"] is None
    assert measurements[1]["time_seconds"] == 52.1  # Retained in the old slot, not relabelled 800m.
    assert event["course_raw"] == " 栗坂 " and event["course_normalized"] == "栗坂"
    assert event["comment_raw"] == raw["comment"]
    assert event["source_hash"] is event["retrieved_at"] is event["source_url"] is None
    assert len(event["source_record_sha256"]) == 64


@pytest.mark.parametrize("value,status,seconds", [
    (None, "MISSING", None), ("-", "MISSING", None), ("", "MISSING", None),
    (0, "INVALID_VALUE", None), (-1.0, "INVALID_VALUE", None),
    (True, "INVALID_VALUE", None), ("13.0", "INVALID_VALUE", None),
    (13, "PRESENT_NUMERIC_DISTANCE_UNVERIFIED", 13.0),
])
def test_missing_and_invalid_are_never_zero_imputed_or_silently_cast(value, status, seconds):
    raw, prior = fixture()
    raw["time_6f"] = value
    _, rows = normalize_event(raw, prior, DB_SHA)
    assert json.loads(rows[0]["original_value"]) == value
    assert rows[0]["value_status"] == status and rows[0]["time_seconds"] == seconds
    if status == "MISSING":
        assert rows[0]["missing_reason"] == "UNKNOWN"  # Even on a hill course.


def test_created_at_and_old_b_do_not_establish_capture_or_new_b():
    raw, prior = fixture()
    raw["created_at"] = "2021-01-08 00:00:00"
    event, _ = normalize_event(raw, prior, DB_SHA)
    assert event["pit_class"] == "C_INVALID_OR_UNKNOWN"
    assert event["event_validity"] == "training_date_before_race"
    assert event["historical_backfill"] is None and not event["raw_original_available"]
    assert event["source_db_created_at"] == raw["created_at"] and event["retrieved_at"] is None
    assert not event["is_last_training_verified"] and event["legacy_is_last_training"] == 1


@pytest.mark.parametrize("date_value,expected", [
    ("2020/12/31", "training_date_before_race"), ("2021/01/10", "same_day_unknown"),
    ("2021/01/11", "after_race"), ("2021/02/30", "invalid"),
    ("01/06", "invalid"), ("2021/01/06 extra", "invalid"),
])
def test_strict_calendar_preserves_year_and_excludes_same_day(date_value, expected):
    assert event_validity(date_value, "20210110") == expected
    if expected == "invalid":
        assert day(date_value) is None


@pytest.mark.parametrize("identity,date_value,old_class,reasons", [
    (False, "2021/01/06", "A_BEFORE_RACE", ["horse_identity_unverified"]),
    (True, "2021/01/11", "C_AFTER_RACE", ["after_race"]),
    (False, "2021/01/11", "C_AFTER_RACE", ["horse_identity_unverified", "after_race"]),
    (True, "2021/01/10", "B_SAME_DATE_TIME_UNKNOWN", ["same_day_unknown"]),
])
def test_exceptions_quarantined_without_date_or_identity_repair(identity, date_value, old_class, reasons):
    raw, prior = fixture()
    raw["training_date"] = prior["training_date_raw"] = date_value
    prior.update(identity_verified=str(identity), event_date_class=old_class)
    event, rows = normalize_event(raw, prior, DB_SHA)
    assert event["training_date_raw"] == date_value
    assert json.loads(event["quarantine_reasons"]) == reasons
    assert event["quarantined"] and all(row["quarantined"] for row in rows)
    assert event["horse_id"] == ("horse-1" if identity else None)
    assert event["pit_class"] == "C_INVALID_OR_UNKNOWN"


@pytest.mark.parametrize("key,value", [("race_id", "other"), ("training_date_raw", "2020/01/06"),
                                      ("normalized_training_name", "別馬"), ("horse_number", "2"),
                                      ("event_date_class", "C_AFTER_RACE")])
def test_source_audit_binding_fails_closed(key, value):
    raw, prior = fixture()
    prior[key] = value
    with pytest.raises(ValueError, match="mismatch"):
        normalize_event(raw, prior, DB_SHA)


def test_categorical_values_have_no_invented_ordinal():
    raw, prior = fixture()
    raw.update(pace="Ｇ強", grade="Ａ")
    event, _ = normalize_event(raw, prior, DB_SHA)
    assert event["pace_raw"] == "Ｇ強" and event["pace_category"] == "G強"
    assert event["grade_raw"] == "Ａ" and event["grade_category"] == "A"


def test_verified_backfill_is_separate_from_strict_capture():
    event = normalized()
    evidence = receipt(event, retrieved_at="2026-09-20T09:00:00+09:00", source_historical_page=True)
    result = classify_pit(event, evidence, cutoff_at="2021-01-10T15:00:00+09:00")
    assert result == {"pit_class": "B_HISTORICAL_EVENT_VALID", "raw_original_available": True,
                      "capture_provenance": "source_historical_page_retrieved_later", "historical_backfill": True}
    event["quarantined"] = True
    assert classify_pit(event, evidence)["pit_class"] == "C_INVALID_OR_UNKNOWN"


@pytest.mark.parametrize("overrides", [{"raw_hash_verified": False}, {"row_values_verified": False},
    {"race_id": "other"}, {"course_normalized": "美坂"}, {"raw_source_sha256": "bad"},
    {"evidence_manifest_sha256": None}, {"source_url": None}, {"retrieved_at": "2021-01-09 12:00:00"},
    {"retrieved_at": "2021-01-05T12:00:00+09:00"}, {"publication_version_verified": False},
    {"contemporaneous_snapshot_verified": False}])
def test_incomplete_or_misbound_evidence_never_promotes(overrides):
    event = normalized()
    assert classify_pit(event, receipt(event, **overrides), cutoff_at="2021-01-10T15:00:00+09:00")["pit_class"] == "C_INVALID_OR_UNKNOWN"


def test_strict_receipt_cutoff_and_same_day_workout_are_not_guessed():
    event = normalized()
    evidence = receipt(event)
    assert classify_pit(event, evidence, cutoff_at="2021-01-10T15:00:00+09:00")["pit_class"] == "A_STRICT_PIT"
    evidence["retrieved_at"] = "2021-01-10T15:00:00+09:00"
    assert classify_pit(event, evidence, cutoff_at=evidence["retrieved_at"])["pit_class"] == "C_INVALID_OR_UNKNOWN"
    event["training_date"] = "20210110"
    evidence = receipt(event, retrieved_at="2021-01-10T10:00:00+09:00")
    assert classify_pit(event, evidence, cutoff_at="2021-01-10T15:00:00+09:00")["pit_class"] == "C_INVALID_OR_UNKNOWN"


def test_all_candidate_features_hold_except_rejected_hardcoded_flag():
    report = candidate_registry()
    assert len(report["candidates"]) == 20
    assert len({row["feature"] for row in report["candidates"]}) == 20
    assert report["counts"] == {"READY": 0, "HOLD": 19, "REJECT": 1}
    assert [r["feature"] for r in report["candidates"] if r["status"] == "REJECT"] == ["is_last_training"]
    assert all(not row["model_input_allowed"] for row in report["candidates"])
    assert report["comment_policy"]["status"] == "EXCLUDED"
    assert schema_definition()["feature_generation_in_this_export"] is False


def test_prior_reference_same_course_condition_distance_and_kind_only():
    target = verified_measurement()
    history = [earlier(time_seconds=50), earlier(time_seconds=54), earlier(course_normalized="美坂", time_seconds=900),
               earlier(track_condition="重", time_seconds=900), earlier(measurement_distance=600, time_seconds=900),
               earlier(measurement_kind="lap", time_seconds=900)]
    result = prior_reference(history, target, cutoff_at="2021-01-17T15:00:00+09:00")
    assert result["count"] == 2 and result["mean_seconds"] == 52
    assert result["std_seconds_sample"] == pytest.approx(8 ** .5)
    assert result["faster_or_equal_percentile"] == .5


def test_future_and_same_day_never_change_past_reference():
    target = verified_measurement()
    before = prior_reference([earlier(time_seconds=50)], target, cutoff_at="2021-01-17T15:00:00+09:00")
    extras = [verified_measurement(time_seconds=1),
              verified_measurement(training_date="20210113", race_date="20210116", time_seconds=1),
              verified_measurement(training_date="20210120", race_date="20210124", time_seconds=1,
                                   retrieved_at="2021-01-23T12:00:00+09:00"),
              earlier(time_seconds=1, pit_class="B_HISTORICAL_EVENT_VALID")]
    assert prior_reference([earlier(time_seconds=50), *extras], target,
                           cutoff_at="2021-01-17T15:00:00+09:00") == before


@pytest.mark.parametrize("change", [{"measurement_distance": None}, {"measurement_distance": True},
    {"measurement_distance": float("nan")}, {"measurement_distance": "800"}, {"distance_mapping_verified": False},
    {"source_hash": None}, {"evidence_manifest_sha256": None}, {"raw_original_available": False},
    {"capture_provenance": "capture_time_unknown"}, {"identity_verified": False}, {"quarantined": True},
    {"time_seconds": 0}, {"time_seconds": float("inf")}, {"pit_class": "B_HISTORICAL_EVENT_VALID"},
    {"retrieved_at": "2026-01-01T00:00:00+09:00"}, {"retrieved_at": "2021-01-01T00:00:00+09:00"}])
def test_relative_transform_rejects_unverified_target_and_excludes_unverified_references(change):
    with pytest.raises(ValueError, match="target lacks"):
        prior_reference([], verified_measurement(**change), cutoff_at="2021-01-17T15:00:00+09:00")
    broken_history = earlier(**change)
    result = prior_reference([broken_history], verified_measurement(), cutoff_at="2021-01-17T15:00:00+09:00")
    assert result["count"] == 0 and result["mean_seconds"] is None
    assert result["std_seconds_sample"] is None and result["faster_or_equal_percentile"] is None


def test_unknown_group_or_naive_cutoff_never_uses_global_fallback():
    with pytest.raises(ValueError, match="unknown comparison group"):
        prior_reference([earlier()], verified_measurement(track_condition=None), cutoff_at="2021-01-17T15:00:00+09:00")
    with pytest.raises(ValueError, match="timezone-aware"):
        prior_reference([], verified_measurement(), cutoff_at="2021-01-17 15:00:00")


def test_readonly_database_lifetime_does_not_close_before_transaction_exit(tmp_path):
    source = tmp_path / "snapshot.sqlite"
    original = sqlite3.connect(source)
    original.execute("CREATE TABLE training_data (id INTEGER)")
    original.execute("INSERT INTO training_data VALUES (1)")
    original.commit()
    original.close()
    script = Path(__file__).resolve().parents[2] / "scripts/normalize_training_reconstruction.py"
    spec = importlib.util.spec_from_file_location("normalization_cli_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    before = source.read_bytes()
    with module.readonly_snapshot(source) as db:
        assert db.execute("SELECT COUNT(*) FROM training_data").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("DELETE FROM training_data")
    assert source.read_bytes() == before
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        db.execute("SELECT 1")
