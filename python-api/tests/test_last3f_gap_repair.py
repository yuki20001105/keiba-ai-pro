"""Synthetic, journalled clock repairs; no external HTTP or live DB."""
from copy import deepcopy
import hashlib
import json
import sqlite3

import pytest

from scraping.last3f_gap_repair import apply_evidence, clock_state
from scraping.safe_repair import RepairConflict

RID = "202605010101"


@pytest.fixture
def stored(tmp_path):
    path = tmp_path / "fixture.db"
    meta = {"race_id": RID, "date": "20260104", "venue": "東京", "distance": 1600,
            "track_type": "芝", "num_horses": 2, "field_condition": "良"}
    runners = [{"horse_number": i, "horse_id": f"202000000{i}", "horse_name": f"テスト{i}",
                "finish_position": i, "finish_time": "1:34.5", "jockey_weight": 57,
                "odds": i + 1, "popularity": i, "weight_kg": 470, "last_3f": "**",
                "last_3f_rank": None, "sire": "父", "custom": {"keep": [True, None, "原本"]}}
               for i in (1, 2)]
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE races_ultimate (race_id TEXT PRIMARY KEY,data TEXT)")
        db.execute("CREATE TABLE race_results_ultimate (id INTEGER PRIMARY KEY,race_id TEXT,data TEXT,created_at TEXT DEFAULT 'original')")
        db.execute("INSERT INTO races_ultimate VALUES (?,?)", (RID, json.dumps(meta, ensure_ascii=False)))
        for i, row in enumerate(runners, 1):
            db.execute("INSERT INTO race_results_ultimate (id,race_id,data) VALUES (?,?,?)", (i, RID, json.dumps(row, ensure_ascii=False)))
    evidence = {"race_id": RID, "race_date": "20260104", "runner_count": 2,
        "rows": [{"horse_number": h["horse_number"], "horse_id": h["horse_id"], "horse_name": h["horse_name"],
                  "last3f_state": "valid", "last3f_seconds": 35.0 + h["horse_number"] / 10}
                 for h in runners],
        "source": {"http_status": 200, "raw_body_sha256": "a" * 64,
                   "source_url": "https://race.netkeiba.com/race/result.html?race_id=" + RID,
                   "all_runner_identity_verified": True, "visible_race_date_verified": True,
                   "historical_capture_time_proven": False, "raw_digest_verification": "verified_against_bytes",
                   "observed_at_utc": "2026-10-17T07:00:00+00:00"}}
    return path, runners, evidence


def rows(path):
    with sqlite3.connect(path) as db:
        return [(i, json.loads(raw), stamp) for i, raw, stamp in db.execute("SELECT id,data,created_at FROM race_results_ultimate ORDER BY id")]


def test_only_missing_clocks_are_changed_and_journalled(stored):
    path, before, evidence = stored
    result = apply_evidence(path, evidence, batch_id="clock-fixture")
    assert result["updated_runners"] == 2
    for (_, row, stamp), original in zip(rows(path), before):
        assert stamp == "original"
        for key in original.keys() - {"last_3f"}:
            assert row[key] == original[key]
        assert row["last_3f"] == row["last_3f_time"] > 0
        assert row["_last3f_gap_repair"]["historical_prestart_capture_proven"] is False
        assert row["_last3f_gap_repair"]["raw_values"]["last_3f"] == "**"
    with sqlite3.connect(path) as db:
        journal = db.execute("SELECT before_data,after_data,before_sha256,after_sha256 FROM data_quality_repair_journal_v1").fetchall()
        assert len(journal) == 2
        assert all(hashlib.sha256(raw.encode()).hexdigest() == digest for entry in journal
                   for raw, digest in ((entry[0], entry[2]), (entry[1], entry[3])))
    assert apply_evidence(path, evidence, batch_id="clock-fixture")["updated_runners"] == 0


@pytest.mark.parametrize("value", [None, "", "-", "--", "**", "****"])
def test_missing_and_masked_slots_use_real_numeric_only(stored, value):
    path, before, evidence = stored
    before[0]["last_3f"] = value
    with sqlite3.connect(path) as db:
        db.execute("UPDATE race_results_ultimate SET data=? WHERE id=1", (json.dumps(before[0]),))
    assert apply_evidence(path, evidence, batch_id="clock-fixture")["updated_runners"] == 2


@pytest.mark.parametrize("value", ["bad", "nan", float("inf"), -1, 0, 101, True])
def test_invalid_is_not_silently_replaced(stored, value):
    path, before, evidence = stored
    before[0]["last_3f"] = value
    with sqlite3.connect(path) as db:
        db.execute("UPDATE race_results_ultimate SET data=? WHERE id=1", (json.dumps(before[0]),))
    assert apply_evidence(path, evidence, batch_id="clock-fixture")["updated_runners"] == 1
    assert rows(path)[0][1]["last_3f"] == value


def test_valid_clock_conflict_rolls_back_whole_race(stored):
    path, before, evidence = stored
    before[1]["last_3f"] = "39.9"
    with sqlite3.connect(path) as db:
        db.execute("UPDATE race_results_ultimate SET data=? WHERE id=2", (json.dumps(before[1]),))
    with pytest.raises(RepairConflict, match="existing-clock"):
        apply_evidence(path, evidence, batch_id="clock-fixture")
    assert rows(path)[0][1]["last_3f"] == "**"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM data_quality_repair_journal_v1").fetchone()[0] == 0


def test_masked_priority_alias_is_repaired_without_replacing_normal_alias(stored):
    path, before, evidence = stored
    before[0].update(last_3f="35.1", last_3f_time="**")
    assert clock_state(before[0]) == ("masked_shadowing_valid_alias", 35.1)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE race_results_ultimate SET data=? WHERE id=1", (json.dumps(before[0]),))
    apply_evidence(path, evidence, batch_id="clock-fixture")
    assert rows(path)[0][1]["last_3f"] == "35.1"
    assert rows(path)[0][1]["last_3f_time"] == 35.1


def test_valid_aliases_cannot_disagree(stored):
    path, before, evidence = stored
    before[0].update(last_3f="35.1", last_3f_time="36.1")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE race_results_ultimate SET data=? WHERE id=1", (json.dumps(before[0]),))
    with pytest.raises(RepairConflict, match="alias-conflict"):
        apply_evidence(path, evidence, batch_id="clock-fixture")


@pytest.mark.parametrize("marker", ["未計測", "計不", "---", "-", "--"])
def test_priority_missing_marker_cannot_shadow_valid_lower_alias(stored, marker):
    path, before, evidence = stored
    before[0].update(last_3f="35.1", last_3f_time=marker)
    assert clock_state(before[0]) == ("missing_shadowing_valid_alias", 35.1)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE race_results_ultimate SET data=? WHERE id=1", (json.dumps(before[0]),))
    apply_evidence(path, evidence, batch_id="clock-fixture")
    assert rows(path)[0][1]["last_3f"] == "35.1"
    assert rows(path)[0][1]["last_3f_time"] == 35.1


def test_failed_journal_insert_rolls_back_all_row_updates(stored, monkeypatch):
    from scraping import last3f_gap_repair as gap
    path, _, evidence = stored
    before = rows(path)
    def fail_journal(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic journal fault")
    monkeypatch.setattr(gap, "_journal", fail_journal)
    with pytest.raises(sqlite3.OperationalError, match="journal fault"):
        apply_evidence(path, evidence, batch_id="clock-fixture")
    assert rows(path) == before
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM data_quality_repair_journal_v1").fetchone()[0] == 0


@pytest.mark.parametrize("state", ["masked", "missing", "invalid"])
def test_unavailable_source_never_becomes_zero(stored, state):
    path, _, evidence = stored
    for entry in evidence["rows"]:
        entry.update(last3f_state=state, last3f_seconds=None)
    assert apply_evidence(path, evidence, batch_id="clock-fixture")["updated_runners"] == 0
    assert rows(path)[0][1]["last_3f"] == "**"


@pytest.mark.parametrize("field,value", [("http_status", 403), ("all_runner_identity_verified", False),
    ("visible_race_date_verified", False), ("historical_capture_time_proven", True),
    ("raw_body_sha256", "bad"), ("raw_digest_verification", "caller_digest_supplied")])
def test_source_proof_is_required(stored, field, value):
    path, _, evidence = stored
    evidence["source"][field] = value
    with pytest.raises(RepairConflict, match="source-proof"):
        apply_evidence(path, evidence, batch_id="clock-fixture")


@pytest.mark.parametrize("field,value", [("horse_id", "wrong"), ("horse_number", 9), ("horse_name", "他の馬")])
def test_all_runner_identity_must_still_match(stored, field, value):
    path, _, evidence = stored
    evidence["rows"][0][field] = value
    with pytest.raises(RepairConflict, match="identity"):
        apply_evidence(path, evidence, batch_id="clock-fixture")


@pytest.mark.parametrize("day", ["20151231", "20261017", "20270101"])
def test_out_of_scope_dates_cannot_be_repaired(stored, day):
    path, _, evidence = stored
    evidence["race_date"] = day
    with pytest.raises(RepairConflict, match="scope"):
        apply_evidence(path, evidence, batch_id="clock-fixture")
