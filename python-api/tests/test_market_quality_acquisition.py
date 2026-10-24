from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from market_quality.acquisition import (  # noqa: E402
    AUTHORITATIVE, audit_acquisition, classify_acquisition, read_scoped_evidence,
)


RID = "202106010101"
DAY = "20210105"


@pytest.fixture
def source(tmp_path):
    cohort = pd.DataFrame([
        {"race_id": RID, "race_date": DAY, "horse_id": "2018100001", "horse_number": 1},
        {"race_id": RID, "race_date": DAY, "horse_id": "2018100002", "horse_number": 2},
    ])
    db = tmp_path / "source.db"
    with sqlite3.connect(db) as c:
        c.executescript("""
          CREATE TABLE scrape_race_acquisition_state(race_id TEXT PRIMARY KEY,race_date TEXT,
            lifecycle TEXT,quality_status TEXT,field_states_json TEXT,quality_errors_json TEXT,
            completed_at TEXT,excluded_from_standard INTEGER,last_source_status TEXT);
          CREATE TABLE scrape_date_completeness(race_date TEXT PRIMARY KEY,expected_race_ids_json TEXT,
            expected_race_count INTEGER,saved_race_ids_json TEXT,complete_race_ids_json TEXT,
            missing_race_ids_json TEXT,quarantined_race_ids_json TEXT,status TEXT);
          CREATE TABLE scrape_repair_queue(entity_type TEXT,entity_id TEXT,repair_kind TEXT,
            status TEXT,provenance_json TEXT,PRIMARY KEY(entity_type,entity_id,repair_kind));
          CREATE TABLE scraped_dates(date TEXT PRIMARY KEY,race_count INTEGER,no_race INTEGER);
          CREATE TABLE races_ultimate(race_id TEXT PRIMARY KEY,data TEXT,created_at TEXT);
          CREATE TABLE race_results_ultimate(id INTEGER PRIMARY KEY,race_id TEXT,data TEXT);
          CREATE INDEX ix_result_race ON race_results_ultimate(race_id);
          CREATE TABLE scrape_race_observations(race_id TEXT,lifecycle TEXT,observed_at TEXT);
        """)
        fields = dict.fromkeys(["odds", "popularity", "horse_weight", "finish_position", "finish_time", "pedigree"], "available")
        c.execute("INSERT INTO scrape_race_acquisition_state VALUES(?,?,?,?,?,?,?,?,?)",
                  (RID, DAY, "settled", "complete", json.dumps(fields), "[]", "2026-09-15T09:14:57Z", 0, "http_200"))
        ids = json.dumps([RID])
        c.execute("INSERT INTO scrape_date_completeness VALUES(?,?,?,?,?,?,?,?)",
                  (DAY, ids, 1, ids, ids, "[]", "[]", "complete"))
        c.execute("INSERT INTO scraped_dates VALUES(?,?,?)", (DAY, 1, 0))
        c.execute("INSERT INTO races_ultimate VALUES(?,?,?)", (RID, json.dumps({"race_id": RID, "date": DAY}), "2026-09-15"))
        c.executemany("INSERT INTO race_results_ultimate(race_id,data) VALUES(?,?)", [(RID, "{}"), (RID, "{}")])
        # Later and out-of-cohort evidence must not be exported.
        c.execute("INSERT INTO races_ultimate VALUES(?,?,?)", ("202306010101", '{"finish":99}', "2026-09-15"))
    return db, cohort


def _evidence(source):
    return read_scoped_evidence(source[0], source[1], immutable=True)


def test_clean_scope_and_read_only(source, tmp_path):
    db, cohort = source
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    output = tmp_path / "new-artifacts"
    rows, summary = audit_acquisition(db, cohort, live_db_path=db, output_dir=output)
    assert rows.iloc[0].acquisition_pass
    assert rows.iloc[0].acquisition_classification == "A_CLEAN"
    assert summary["live_comparison"]["same_scoped_evidence"]
    assert summary["policy"]["legacy_used_for_quality"] is False
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    sources = json.loads((output / "acquisition_sources.json").read_text("utf-8"))
    assert len(sources["snapshot"]["tables"]["races_ultimate"]) == 1
    assert "202306010101" not in (output / "acquisition_sources.json").read_text("utf-8")
    for spec in summary["artifacts"].values():
        assert hashlib.sha256(Path(spec["path"]).read_bytes()).hexdigest() == spec["sha256"]
    with pytest.raises(FileExistsError):
        audit_acquisition(db, cohort, output_dir=output)


@pytest.mark.parametrize("missing", AUTHORITATIVE)
def test_missing_authority_never_inferred_from_raw(source, missing):
    evidence = _evidence(source)
    evidence["missing_tables"].append(missing)
    evidence["tables"][missing] = []
    rows, _ = classify_acquisition(source[1], evidence)
    assert not rows.iloc[0].acquisition_pass
    assert "authoritative_table_missing" in rows.iloc[0].blockers_json


def test_missing_state_even_raw_present_is_not_clean(source):
    e = _evidence(source)
    e["tables"][AUTHORITATIVE[1]] = []
    row = classify_acquisition(source[1], e)[0].iloc[0]
    assert not row.acquisition_pass
    assert "race_state_missing" in row.blockers_json


def test_partial_date_other_race_does_not_veto_cohort_race(source):
    e = _evidence(source)
    day = e["tables"][AUTHORITATIVE[0]][0]
    day.update(status="partial", expected_race_count=2,
               expected_race_ids_json=json.dumps([RID, "202106010102"]),
               missing_race_ids_json='["202106010102"]')
    row = classify_acquisition(source[1], e)[0].iloc[0]
    assert row.acquisition_pass and row.date_partial_outside_cohort
    assert row.legacy_conflict  # legacy contradiction is visible, not authority.


@pytest.mark.parametrize("field,value", [
    ("lifecycle", "entry"), ("quality_status", "stored_partial"),
    ("completed_at", None), ("completed_at", "bad-time"),
    ("quality_errors_json", '["incomplete"]'), ("field_states_json", "bad-json"),
])
def test_bad_race_state_blocks(source, field, value):
    e = _evidence(source)
    e["tables"][AUTHORITATIVE[1]][0][field] = value
    assert not classify_acquisition(source[1], e)[0].iloc[0].acquisition_pass


@pytest.mark.parametrize("kind,expected", [("pedigree", True), ("horse_weight", False), ("odds", False), ("full_race", False)])
def test_only_pedigree_pending_is_provisionally_allowed(source, kind, expected):
    e = _evidence(source)
    e["tables"][AUTHORITATIVE[2]].append({
        "entity_type": "horse", "entity_id": "2018100001", "repair_kind": kind, "status": "pending",
    })
    row = classify_acquisition(source[1], e)[0].iloc[0]
    assert bool(row.acquisition_pass) == expected
    if expected:
        assert row.allowed_missing == ["pedigree"]
        assert row.acquisition_classification == "B_CLEAN_WITH_ALLOWED_MISSING"


def test_completed_repair_is_not_pending(source):
    e = _evidence(source)
    e["tables"][AUTHORITATIVE[2]].append({
        "entity_type": "race", "entity_id": RID, "repair_kind": "full_race", "status": "completed",
    })
    assert classify_acquisition(source[1], e)[0].iloc[0].acquisition_pass


@pytest.mark.parametrize("change", ["missing", "wrong_date", "excluded", "overlap", "wrong_count", "not_saved"])
def test_contradictions_and_exclusion_fail_closed(source, change):
    e = _evidence(source)
    s, d = e["tables"][AUTHORITATIVE[1]][0], e["tables"][AUTHORITATIVE[0]][0]
    if change == "missing": e["tables"]["races_ultimate"] = []
    if change == "wrong_date": s["race_date"] = "20210106"
    if change == "excluded": s["excluded_from_standard"] = 1
    if change == "overlap": d["missing_race_ids_json"] = json.dumps([RID])
    if change == "wrong_count": d["expected_race_count"] = 2
    if change == "not_saved": d["saved_race_ids_json"] = "[]"
    assert not classify_acquisition(source[1], e)[0].iloc[0].acquisition_pass


def test_cohort_binding_and_future_guard(source):
    e = _evidence(source)
    changed = source[1].copy()
    changed.loc[0, "horse_id"] = "other"
    with pytest.raises(ValueError, match="different cohort"):
        classify_acquisition(changed, e)
    future = source[1].assign(race_date="20230101")
    with pytest.raises(ValueError, match="2021/2022"):
        read_scoped_evidence(source[0], future, immutable=True)


def test_unknown_optional_state_is_not_invented_allowed_missing(source):
    e = _evidence(source)
    state = e["tables"][AUTHORITATIVE[1]][0]
    fields = json.loads(state["field_states_json"])
    del fields["pedigree"]
    state["field_states_json"] = json.dumps(fields)
    assert not classify_acquisition(source[1], e)[0].iloc[0].acquisition_pass


def test_immutable_reader_rejects_pending_wal(source):
    # A finalized-copy contract must not silently discard live WAL evidence.
    wal = Path(str(source[0]) + "-wal")
    wal.write_bytes(b"pending-uncheckpointed-data")
    with pytest.raises(ValueError, match="pending WAL"):
        read_scoped_evidence(source[0], source[1], immutable=True)
