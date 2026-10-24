"""Small isolated fixtures; never open live source stores or use the network."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from scraping import pedigree_gap_repair as repair
from services.prediction_source_revision import initialize_source_revision


RID = "202605010101"
OTHER = "201305010101"
PED = {"sire": "サンデーサイレンス", "dam": "エアグルーヴ", "damsire": "トニービン"}


@pytest.fixture
def stores(tmp_path):
    db_path, cache_path = tmp_path / "source.db", tmp_path / "pedigree.db"
    with sqlite3.connect(db_path) as db:
        db.executescript("""CREATE TABLE races_ultimate(race_id TEXT PRIMARY KEY,data TEXT NOT NULL,created_at TEXT DEFAULT 'original');
          CREATE TABLE race_results_ultimate(id INTEGER PRIMARY KEY AUTOINCREMENT,race_id TEXT NOT NULL,data TEXT NOT NULL,created_at TEXT DEFAULT 'original');
          CREATE TABLE horse_details(horse_id TEXT PRIMARY KEY,sire TEXT,dam TEXT,damsire TEXT);""")
        for race_id, day in ((RID, "20260104"), (OTHER, "20130504"), ("202644010101", "20260104"), ("202605010102", "20261017")):
            db.execute("INSERT INTO races_ultimate(race_id,data) VALUES(?,?)", (race_id, json.dumps({"race_id": race_id, "date": day})))
    assert initialize_source_revision(db_path.resolve(), True)
    with sqlite3.connect(cache_path) as db:
        db.execute("CREATE TABLE pedigree_cache(horse_id TEXT PRIMARY KEY,sire TEXT,dam TEXT,damsire TEXT)")
    return db_path, cache_path, tmp_path


def add(stores, value=None, *, race_id=RID, horse_id="2020000001"):
    item = {"horse_id": horse_id, "race_id": race_id, "horse_number": 1,
            "finish_position": 1, "finish_time": "1:35.5", "odds": 3.0, "other": {"keep": "exact"}}
    item.update(value or {})
    with sqlite3.connect(stores[0]) as db:
        cursor = db.execute("INSERT INTO race_results_ultimate(race_id,data) VALUES(?,?)", (race_id, json.dumps(item, ensure_ascii=False)))
        return cursor.lastrowid


def cached(stores, pedigree=None, horse_id="2020000001"):
    values = pedigree or PED
    with sqlite3.connect(stores[1]) as db:
        db.execute("INSERT INTO pedigree_cache VALUES(?,?,?,?)", (horse_id, *(values.get(field) for field in repair.FIELDS)))


def plan(stores):
    run = stores[2] / "fixture-batch"
    run.mkdir()
    return run, repair.plan_repair(stores[0], stores[1], run, pause_seconds=0)


def rows(stores):
    with sqlite3.connect(stores[0]) as db:
        return db.execute("SELECT * FROM race_results_ultimate ORDER BY id").fetchall()


def test_plan_is_read_only_and_seals_exact_source_evidence(stores):
    add(stores)
    cached(stores)
    before = rows(stores)
    with sqlite3.connect(stores[0]) as db:
        epoch = repair.revision(db)
    run, result = plan(stores)
    assert rows(stores) == before
    assert result["source_database_writes"] == result["network_requests"] == 0
    assert result["counts"]["planned_rows"] == 1
    assert repair.validate_plan(run)[0] == result
    targets = list(repair.jsonl_read(run / "planned.jsonl"))
    assert targets[0]["fill"] == PED
    assert targets[0]["before_sha256"] == repair.digest(before[0][2])
    assert result["source_revision"] == epoch
    ledger = list(repair.jsonl_read(run / "consensus_evidence.jsonl"))
    assert ledger[0]["historical_capture_time_verified"] is False
    assert ledger[0]["source_references"][0]["source"] == "pedigree_cache"


def test_apply_preserves_nonpedigree_values_row_identity_created_at_and_journals(stores):
    add(stores, {"sire": PED["sire"]})
    cached(stores)
    before = rows(stores)
    run, result = plan(stores)
    execution = repair.apply_repair(run, chunk_size=1, pause_seconds=0)
    after = rows(stores)
    assert before[0][0:2] == after[0][0:2] and before[0][3] == after[0][3]
    original, updated = json.loads(before[0][2]), json.loads(after[0][2])
    assert {k: v for k, v in updated.items() if k not in {"dam", "damsire"}} == original
    assert {field: updated[field] for field in repair.FIELDS} == PED
    assert execution["counts"]["updated_rows"] == 1
    assert execution["remaining_planned_rows"] == 0
    with sqlite3.connect(stores[0]) as db:
        journal = db.execute("SELECT before_data,after_data,before_sha256,after_sha256,evidence_json FROM data_quality_repair_journal_v1").fetchall()
    assert len(journal) == 1 and journal[0][0] == before[0][2] and journal[0][1] == after[0][2]
    assert journal[0][2] == repair.digest(before[0][2]) and journal[0][3] == repair.digest(after[0][2])
    evidence = json.loads(journal[0][4])
    assert evidence["not_prestart_evidence"] is True
    assert evidence["historical_capture_time_verified"] is False
    assert evidence["observed_at_utc"].startswith("20")
    assert evidence["source_revision_after"][1] == evidence["source_revision_before"][1] + 1


@pytest.mark.parametrize("value", ["サンデーSunday(米)", "French Deputy (米)", "Sire123", "<Sire>", "**", "A\x00B", True])
def test_ambiguous_source_name_holds_entire_horse(stores, value):
    add(stores)
    cached(stores, {**PED, "sire": value})
    run, result = plan(stores)
    assert result["counts"].get("planned_rows", 0) == 0
    assert result["counts"]["held_rows_ambiguous_saved_name"] == 1
    assert list(repair.jsonl_read(run / "planned.jsonl")) == []


@pytest.mark.parametrize("field", repair.FIELDS)
def test_conflicting_embedded_values_reject_entire_horse(stores, field):
    add(stores)
    add(stores, {**PED, field: "別名"}, race_id=OTHER)
    cached(stores)
    _, result = plan(stores)
    assert result["counts"].get("planned_rows", 0) == 0
    assert result["counts"]["held_rows_conflicting_saved_pedigree"] == 1


def test_alias_conflict_is_not_silently_ignored(stores):
    add(stores, {"broodmare_sire": "別名"})
    cached(stores)
    _, result = plan(stores)
    assert result["counts"].get("planned_rows", 0) == 0
    assert result["counts"]["held_rows_conflicting_saved_pedigree"] == 1


def test_matching_alias_can_fill_missing_canonical_slot_without_changing_alias(stores):
    add(stores, {"broodmare_sire": PED["damsire"]})
    cached(stores)
    run, _ = plan(stores)
    repair.apply_repair(run, pause_seconds=0)
    updated = json.loads(rows(stores)[0][2])
    assert updated["damsire"] == updated["broodmare_sire"] == PED["damsire"]


def test_all_three_fields_required_even_if_one_missing_slot_is_available(stores):
    add(stores)
    cached(stores, {"sire": PED["sire"], "dam": "", "damsire": None})
    _, result = plan(stores)
    assert result["counts"].get("planned_rows", 0) == 0
    assert result["counts"]["held_rows_incomplete_saved_pedigree"] == 1


def test_consensus_can_use_saved_past_or_later_static_records_but_not_claim_capture(stores):
    add(stores)
    add(stores, PED, race_id=OTHER)
    run, result = plan(stores)
    assert result["counts"]["planned_rows"] == 1
    assert result["historical_prestart_capture_proven"] is False
    assert all(ref["source"] == "race_results_ultimate" for ref in next(repair.jsonl_read(run / "consensus_evidence.jsonl"))["source_references"])


def test_full_reference_count_and_ordered_digest_survive_bounded_sampling(stores):
    add(stores)
    for _ in range(25):
        add(stores, PED, race_id=OTHER)
    cached(stores)
    run, result = plan(stores)
    evidence = next(repair.jsonl_read(run / "consensus_evidence.jsonl"))
    assert evidence["source_references_are_sample_only"] is True
    assert len(evidence["source_references"]) == repair.MAX_REFERENCE_SAMPLES == 3
    assert evidence["total_reference_count"] == 26
    assert result["reference_storage"]["full_reference_lists_retained_in_memory"] is False
    expected = [{"source": "pedigree_cache", "horse_id": "2020000001",
                 "value_sha256": repair.digest(repair.encode(PED)),
                 "values": {field: [PED[field]] for field in repair.FIELDS}, "ambiguous_name": False}]
    for row_id, race_id, raw, _created in rows(stores)[1:]:
        expected.append({"source": "race_results_ultimate", "row_id": row_id, "race_id": race_id,
            "raw_sha256": repair.digest(raw), "values": {field: [PED[field]] for field in repair.FIELDS},
            "ambiguous_name": False})
    digest = hashlib.sha256()
    for reference in expected:
        digest.update((repair.encode(reference) + "\n").encode("utf-8"))
    assert evidence["all_reference_digest"] == digest.hexdigest()
    assert evidence["source_references"] == expected[:3]


def test_conflict_beyond_reference_sample_still_blocks_repair(stores):
    add(stores)
    for _ in range(15):
        add(stores, PED, race_id=OTHER)
    add(stores, {**PED, "sire": "別名"}, race_id=OTHER)
    cached(stores)
    _, result = plan(stores)
    assert result["counts"].get("planned_rows", 0) == 0
    assert result["counts"]["held_rows_conflicting_saved_pedigree"] == 1


def test_local_races_and_after_cutoff_are_not_targets(stores):
    add(stores, race_id="202644010101")
    add(stores, race_id="202605010102")
    add(stores, race_id=OTHER)
    cached(stores)
    _, result = plan(stores)
    assert result["counts"].get("planned_rows", 0) == 0


def test_replay_is_idempotent_and_limited_apply_resumes_from_atomic_journal(stores):
    for horse_id in ("2020000001", "2020000002", "2020000003"):
        add(stores, horse_id=horse_id)
        cached(stores, horse_id=horse_id)
    run, _ = plan(stores)
    first = repair.apply_repair(run, chunk_size=1, pause_seconds=0, limit=1)
    assert first["counts"]["updated_rows"] == 1 and first["remaining_planned_rows"] == 2
    second = repair.apply_repair(run, chunk_size=2, pause_seconds=0)
    assert second["counts"]["already_applied_rows"] == 1 and second["counts"]["updated_rows"] == 2
    assert second["planned_source_revision"] == first["planned_source_revision"]
    assert second["source_revision_before"] == first["source_revision_after"]
    assert second["source_revision_before"] != second["planned_source_revision"]
    unchanged = rows(stores)
    third = repair.apply_repair(run, pause_seconds=0)
    assert third["counts"]["already_applied_rows"] == 3 and third["counts"].get("updated_rows", 0) == 0
    assert rows(stores) == unchanged


def test_external_source_write_invalidates_initial_plan(stores):
    add(stores)
    cached(stores)
    run, _ = plan(stores)
    add(stores, PED, horse_id="2020000002")
    before = rows(stores)
    with pytest.raises(ValueError, match="source-changed-replan"):
        repair.apply_repair(run, pause_seconds=0)
    assert rows(stores) == before


def test_external_write_between_resume_attempts_is_not_authorized(stores):
    add(stores)
    add(stores, horse_id="2020000002")
    cached(stores)
    cached(stores, horse_id="2020000002")
    run, _ = plan(stores)
    repair.apply_repair(run, pause_seconds=0, limit=1)
    add(stores, PED, horse_id="2020000003")
    with pytest.raises(ValueError, match="source-changed-replan"):
        repair.apply_repair(run, pause_seconds=0)


def test_cache_change_is_not_silently_adopted(stores):
    add(stores)
    cached(stores)
    run, _ = plan(stores)
    with sqlite3.connect(stores[1]) as db:
        db.execute("UPDATE pedigree_cache SET sire='別名'")
    with pytest.raises(ValueError, match="cache-changed-replan"):
        repair.apply_repair(run, pause_seconds=0)


def test_unversioned_horse_details_change_is_also_guarded(stores):
    add(stores)
    cached(stores)
    run, _ = plan(stores)
    with sqlite3.connect(stores[0]) as db:
        db.execute("INSERT INTO horse_details VALUES(?,?,?,?)", ("2020000001", "別名", PED["dam"], PED["damsire"]))
    with pytest.raises(ValueError, match="horse-details-changed-replan"):
        repair.apply_repair(run, pause_seconds=0)


@pytest.mark.parametrize("name", ["plan.json", "planned.jsonl", "consensus_evidence.jsonl"])
def test_ledger_tampering_is_rejected(stores, name):
    add(stores)
    cached(stores)
    run, _ = plan(stores)
    with (run / name).open("a", encoding="utf-8") as stream:
        stream.write(" \n")
    with pytest.raises(ValueError, match="binding-invalid|ledger-changed"):
        repair.apply_repair(run, pause_seconds=0)


def test_plan_never_overwrites_existing_artifacts(stores):
    add(stores)
    cached(stores)
    run, _ = plan(stores)
    before = (run / "plan.json").read_bytes()
    with pytest.raises(FileExistsError):
        repair.plan_repair(stores[0], stores[1], run)
    assert (run / "plan.json").read_bytes() == before


@pytest.mark.parametrize("chunk", [0, -1, 101, 1000])
def test_batch_limit_is_enforced(stores, chunk):
    add(stores)
    cached(stores)
    run, _ = plan(stores)
    with pytest.raises(ValueError, match="batch-budget"):
        repair.apply_repair(run, chunk_size=chunk)


def test_journal_failure_rolls_back_source_and_revision(stores, monkeypatch):
    add(stores)
    cached(stores)
    run, result = plan(stores)
    original = rows(stores)
    def fail(*_args, **_kwargs):
        raise RuntimeError("fixture interruption")
    monkeypatch.setattr(repair, "_journal", fail)
    with pytest.raises(RuntimeError, match="fixture interruption"):
        repair.apply_repair(run, pause_seconds=0)
    assert rows(stores) == original
    with sqlite3.connect(stores[0]) as db:
        assert repair.revision(db) == result["source_revision"]
        assert db.execute("SELECT COUNT(*) FROM data_quality_repair_journal_v1").fetchone()[0] == 0


def test_interruption_after_second_row_rolls_back_entire_chunk(stores, monkeypatch):
    for horse_id in ("2020000001", "2020000002"):
        add(stores, horse_id=horse_id)
        cached(stores, horse_id=horse_id)
    run, result = plan(stores)
    original = rows(stores)
    real = repair._journal
    calls = []
    def fail_second(*args):
        calls.append(1)
        real(*args)
        if len(calls) == 2:
            raise RuntimeError("fixture second-row interruption")
    monkeypatch.setattr(repair, "_journal", fail_second)
    with pytest.raises(RuntimeError, match="second-row"):
        repair.apply_repair(run, chunk_size=2, pause_seconds=0)
    assert rows(stores) == original
    with sqlite3.connect(stores[0]) as db:
        assert repair.revision(db) == result["source_revision"]
        assert db.execute("SELECT COUNT(*) FROM data_quality_repair_journal_v1").fetchone()[0] == 0


def test_tampered_journal_hash_is_rejected_on_resume(stores):
    add(stores)
    cached(stores)
    run, _ = plan(stores)
    repair.apply_repair(run, pause_seconds=0)
    with sqlite3.connect(stores[0]) as db:
        db.execute("UPDATE data_quality_repair_journal_v1 SET before_sha256='bad'")
    with pytest.raises(ValueError, match="journal-binding-invalid"):
        repair.apply_repair(run, pause_seconds=0)


def test_code_binding_change_is_rejected(stores, monkeypatch):
    add(stores)
    cached(stores)
    run, _ = plan(stores)
    monkeypatch.setattr(repair, "code_hashes", lambda: {"changed": "hash"})
    with pytest.raises(ValueError, match="code-binding-changed"):
        repair.apply_repair(run, pause_seconds=0)


def test_replaced_database_file_identity_is_rejected(stores, monkeypatch):
    add(stores)
    cached(stores)
    run, _ = plan(stores)
    monkeypatch.setattr(repair, "file_identity", lambda _path: [-1, -1])
    with pytest.raises(ValueError, match="database-file-replaced"):
        repair.apply_repair(run, pause_seconds=0)


def test_protection_guard_runs_before_any_mutation(stores):
    add(stores)
    cached(stores)
    run, _ = plan(stores)
    original = rows(stores)
    def reject(_expected):
        raise ValueError("fixture protected model changed")
    with pytest.raises(ValueError, match="protected model"):
        repair.apply_repair(run, protected_check=reject)
    assert rows(stores) == original


def test_run_directory_rejects_broad_or_traversal_paths(tmp_path):
    with pytest.raises(ValueError, match="out-of-scope"):
        repair.checked_run_directory(tmp_path, tmp_path / "reports/generated/data-quality/feature-gap/../bad", create=True)
    expected = tmp_path / repair.RUN_PARENT / "isolated-v1"
    assert repair.checked_run_directory(tmp_path, expected, create=True) == expected
    with pytest.raises(FileExistsError):
        repair.checked_run_directory(tmp_path, expected, create=True)


def test_missing_source_revision_is_fail_closed(stores):
    add(stores)
    cached(stores)
    with sqlite3.connect(stores[0]) as db:
        db.execute("DROP TABLE _prediction_source_revision_v1")
    with pytest.raises(ValueError, match="revision-unavailable"):
        plan(stores)
