"""Tiny fixtures only: no live DB, whole-file hashes or network."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("quality_after_audit", ROOT / "scripts/audit_quality_repair_after.py")
audit_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)
from scraping import safe_repair
from scraping.storage import _init_sqlite_db, _save_race_sqlite_only
from services.prediction_source_revision import initialize_source_revision

RID = "202605010101"
BATCH = "isolated-after-fixture"


def result(rid=RID, day="20260104"):
    return {"race_info": {"race_id": rid, "date": day, "venue": "東京", "distance": 1600,
                          "track_type": "芝", "num_horses": 3, "field_condition": "良"},
        "horses": [{"race_id": rid, "horse_id": f"202000000{i}", "horse_number": i,
                    "horse_name": f"Horse {i}", "finish_position": i, "finish_time": "1:34.5",
                    "odds": 2 + i, "popularity": i, "weight_kg": 470 + i, "weight_change": 0,
                    "sire": "Sire", "dam": "Dam", "damsire": "Damsire", "last_3f": "**"} for i in (1, 2, 3)],
        "return_tables": []}


@pytest.fixture
def databases(tmp_path):
    current = tmp_path / "current.db"
    before = tmp_path / "before.db"
    _init_sqlite_db(current)
    assert _save_race_sqlite_only(result(), current)
    with sqlite3.connect(current) as db:
        for table in ("training_data", "speed_figures"):
            db.execute(f"CREATE TABLE {table}(id INTEGER PRIMARY KEY, value TEXT)")
            db.execute(f"INSERT INTO {table} VALUES (1,'original')")
    assert initialize_source_revision(current.resolve(), local_enabled=True)
    with sqlite3.connect(current) as source, sqlite3.connect(before) as target:
        source.backup(target)
    return before, current, tmp_path


def run(databases, *, plan=None, coverage=None):
    before, current, directory = databases
    return audit_module.audit(before, current, batch_id=BATCH, plan=plan or {"targets": []},
        baseline_coverage=coverage or {}, output_dir=directory / "post", yield_every=2, pause_seconds=0)[0]


def save(current, candidate):
    return safe_repair.save_result_repair(current, candidate, batch_id=BATCH,
        evidence={"source": "isolated-unit-fixture", "not_capture_proof": True})


def test_unchanged_source_all_rows_equal_and_revision_bound(databases):
    safe_repair.initialize_journal(databases[1])
    report = run(databases)
    assert report["normal_records_preserved_and_only_journaled_additions_or_updates"]
    assert report["source_revision"]["stable_and_latest"]
    assert not report["physical_db_hash_or_copy_performed"]
    assert all(table["updated"] == table["inserted"] == table["deleted"] == 0 for table in report["comparison"])
    quality = report["quality"]["scopes"]["jra_flat_201601_202609"]
    assert quality["runner_rows"] == 3
    assert quality["fields"]["last_3f_time"]["counts"]["masked"] == 3
    assert quality["fields"]["horse_weight_change"]["counts"]["valid"] == 3


def test_journaled_update_and_insert_keep_unaffected_originals_identical(databases):
    _, current, _ = databases
    candidate = result()
    candidate["horses"][0]["last_3f"] = "34.5"
    save(current, candidate)
    new_id = "202605010102"
    candidate = result(new_id)
    candidate["return_tables"] = [{"race_id": new_id, "bet_type": "単勝", "combinations": "1", "payout": 500, "popularity": 1}]
    save(current, candidate)
    report = run(databases, coverage={"jra_expected_id_audit_201601_202609": {"raw_absent_expected_race_ids": [new_id]}})
    assert report["normal_records_preserved_and_only_journaled_additions_or_updates"]
    assert report["original_186_expected_ids"]["counts"] == {"mandatory_complete": 1}
    rows = {value["table"]: value for value in report["comparison"]}
    assert rows["race_results_ultimate"]["updated"] == 1
    assert rows["race_results_ultimate"]["inserted"] == 3
    assert rows["race_results_ultimate"]["unchanged"] == 2
    assert rows["return_tables_ultimate"]["inserted"] == 1


@pytest.mark.parametrize("operation", ["update", "delete", "insert", "created_at", "auxiliary"])
def test_unjournaled_mutation_or_deletion_fails_preservation(databases, operation):
    _, current, _ = databases
    safe_repair.initialize_journal(current)
    with sqlite3.connect(current) as db:
        if operation == "update":
            row = db.execute("SELECT id,data FROM race_results_ultimate ORDER BY id LIMIT 1").fetchone()
            body = json.loads(row[1]); body["odds"] = 999
            db.execute("UPDATE race_results_ultimate SET data=? WHERE id=?", (json.dumps(body), row[0]))
        elif operation == "delete":
            db.execute("DELETE FROM race_results_ultimate WHERE id=(SELECT min(id) FROM race_results_ultimate)")
        elif operation == "insert":
            db.execute("INSERT INTO race_results_ultimate(race_id,data) VALUES (?,?)", (RID, json.dumps(result()["horses"][0])))
        elif operation == "created_at":
            db.execute("UPDATE race_results_ultimate SET created_at='2099-01-01'")
        else:
            db.execute("UPDATE training_data SET value='changed'")
    report = run(databases)
    assert not report["normal_records_preserved_and_only_journaled_additions_or_updates"]
    assert any(value["unauthorized_changes"] for value in report["comparison"])


def test_journal_tamper_fails_even_if_changed_row_is_otherwise_covered(databases):
    _, current, _ = databases
    candidate = result(); candidate["horses"][0]["last_3f"] = "34.5"
    save(current, candidate)
    with sqlite3.connect(current) as db:
        db.execute("UPDATE data_quality_repair_journal_v1 SET after_sha256=?", ("0" * 64,))
    report = run(databases)
    assert not report["normal_records_preserved_and_only_journaled_additions_or_updates"]
    assert report["journal"]["issues"]["after_hash_mismatch"] == 1


def test_multiple_updates_same_row_form_one_valid_chain(databases):
    _, current, _ = databases
    first = result(); first["horses"][0]["last_3f"] = "34.5"
    save(current, first)
    # Add another previously absent field, rather than overwrite a healthy value.
    second = deepcopy(first); second["horses"][0]["corner_positions"] = "1-1"
    save(current, second)
    report = run(databases)
    assert report["normal_records_preserved_and_only_journaled_additions_or_updates"]
    assert not report["journal"]["unused_chains"]


def test_pending_and_card_markers_do_not_count_as_results(databases):
    _, current, _ = databases
    safe_repair.initialize_journal(current)
    with sqlite3.connect(current) as db:
        row = db.execute("SELECT id,data FROM race_results_ultimate LIMIT 1").fetchone()
        body = json.loads(row[1]); body.update(finish_position=None, finish_time=None, _shutuba=True)
        db.execute("UPDATE race_results_ultimate SET data=? WHERE id=?", (json.dumps(body), row[0]))
    report = run(databases, plan={"targets": [{"race_id": RID, "reasons": ["recent_results_not_saved"]}]})
    assert report["original_75_uncollected_results"]["counts"] == {"results_pending": 1}


def test_duplicate_identity_and_declared_count_are_independently_reported(databases):
    _, current, _ = databases
    safe_repair.initialize_journal(current)
    with sqlite3.connect(current) as db:
        db.execute("INSERT INTO race_results_ultimate(race_id,data) VALUES (?,?)", (RID, json.dumps(result()["horses"][0])))
    report = run(databases)
    assert report["quality"]["declared_runner_count_mismatches"][0]["saved"] == 4
    assert report["quality"]["runner_issues"]["horse_id_duplicate"] == 1
    assert report["quality"]["runner_issues"]["horse_number_duplicate"] == 1


def test_nonfinish_and_demotion_are_not_scrape_failures():
    accumulator = audit_module.QualityAccumulator()
    data = result()
    accumulator.add_metadata({"race_id": RID, "data": json.dumps(data["race_info"])})
    data["horses"][1].update(finish_position="中", finish_time=None)
    data["horses"][2].update(finish_position="3(降)")
    for horse in data["horses"]:
        accumulator.add_runner({"race_id": RID, "data": json.dumps(horse)})
    assert accumulator.race_status(RID) == "mandatory_complete"
    assert accumulator.finish()["remaining_unparsed_demotion_races"] == [{"race_id": RID, "rows": 1}]


def test_auxiliary_write_during_scan_cannot_claim_latest_even_when_core_epoch_unchanged(databases, monkeypatch):
    _, current, _ = databases
    safe_repair.initialize_journal(current)
    original = audit_module.stream_compare
    changed = []

    def interleaved(before, after, table, chains, **kwargs):
        value = original(before, after, table, chains, **kwargs)
        if not changed:
            with sqlite3.connect(current) as writer:
                writer.execute("UPDATE speed_figures SET value='concurrently changed'")
            changed.append(True)
        return value

    monkeypatch.setattr(audit_module, "stream_compare", interleaved)
    report = run(databases)
    assert report["normal_records_preserved_and_only_journaled_additions_or_updates"]
    assert report["source_revision"]["core_generation_stable"]
    assert not report["source_revision"]["stable_and_latest"]
    assert not report["source_revision"]["database_write_free_during_read"]
