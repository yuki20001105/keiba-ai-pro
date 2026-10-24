"""Independent multi-batch audit contracts over tiny synthetic databases."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import audit_feature_gap_updates as audit
from scraping.safe_repair import _journal, digest, encode, initialize_journal
from services.prediction_source_revision import initialize_source_revision

RID = "202605010101"
PED = {"sire": "サンデーサイレンス", "dam": "エアグルーヴ", "damsire": "トニービン"}
BATCHES = {"legacy": "original", "pedigree-test": "pedigree", "clock-test": "last3f"}
NOW = "2026-10-17T07:00:00+00:00"


@pytest.fixture
def stores(tmp_path):
    current = tmp_path / "current.db"
    before = tmp_path / "before.db"
    with sqlite3.connect(current) as db:
        db.executescript("""CREATE TABLE races_ultimate(race_id TEXT PRIMARY KEY,data TEXT NOT NULL,created_at TEXT DEFAULT 'original');
          CREATE TABLE race_results_ultimate(id INTEGER PRIMARY KEY AUTOINCREMENT,race_id TEXT NOT NULL,data TEXT NOT NULL,created_at TEXT DEFAULT 'original');
          CREATE TABLE return_tables_ultimate(id INTEGER PRIMARY KEY,race_id TEXT,bet_type TEXT,combinations TEXT,payout INTEGER,popularity INTEGER,created_at TEXT);
          CREATE TABLE training_data(id INTEGER PRIMARY KEY,value TEXT);
          CREATE TABLE speed_figures(id INTEGER PRIMARY KEY,value TEXT);""")
        info = {"race_id": RID, "date": "20260104", "venue": "東京", "distance": 1600,
                "track_type": "芝", "num_horses": 2, "field_condition": "良"}
        db.execute("INSERT INTO races_ultimate(race_id,data) VALUES(?,?)", (RID, encode(info)))
        for number in (1, 2):
            item = {"race_id": RID, "horse_number": number, "horse_id": f"202000000{number}",
                    "horse_name": f"テスト{number}", "finish_position": number,
                    "finish_time": "1:35.5", "odds": 2.5, "popularity": number,
                    "last_3f": "**", "last_3f_rank": None, "arbitrary": {"preserve": [1, True, None]}}
            db.execute("INSERT INTO race_results_ultimate(race_id,data) VALUES(?,?)", (RID, encode(item)))
        db.execute("INSERT INTO training_data VALUES(1,'unchanged-training')")
        db.execute("INSERT INTO speed_figures VALUES(1,'unchanged-speed')")
    assert initialize_source_revision(current.resolve(), True)
    # This copies only a tiny synthetic unit-test fixture, never a user DB.
    shutil.copyfile(current, before)
    initialize_journal(current)
    return before, current, tmp_path


def raw(stores, row=1):
    with sqlite3.connect(stores[1]) as db:
        return db.execute("SELECT data FROM race_results_ultimate WHERE id=?", (row,)).fetchone()[0]


def pedigree_evidence(start, end, fields):
    return {"repair_kind": "static_pedigree_gap", "source_fact_kind": "static_pedigree_from_saved_evidence",
        "historical_capture_time_verified": False, "not_prestart_evidence": True,
        "filled_fields": sorted(fields), "plan_sha256": "a" * 64, "consensus_sha256": "b" * 64,
        "observed_at_utc": NOW, "source_revision_before": start, "source_revision_after": end}


def change(stores, batch, changes, *, row=1, evidence=None):
    with sqlite3.connect(stores[1]) as db:
        before = db.execute("SELECT data FROM race_results_ultimate WHERE id=?", (row,)).fetchone()[0]
        updated = {**json.loads(before), **changes}
        start = audit.previous.epoch(db)
        after = encode(updated)
        db.execute("UPDATE race_results_ultimate SET data=? WHERE id=?", (after, row))
        end = audit.previous.epoch(db)
        detail = pedigree_evidence(start, end, changes) if evidence is None else evidence
        _journal(db, batch, "race_results_ultimate", row, RID, before, after, detail)


def clock(stores, value=35.1, *, row=1, changes=None):
    before = json.loads(raw(stores, row))
    source = {"http_status": 200, "all_runner_identity_verified": True,
        "visible_race_date_verified": True, "historical_capture_time_proven": False,
        "raw_digest_verification": "verified_against_bytes", "raw_body_sha256": "c" * 64,
        "source_url": f"https://race.netkeiba.com/race/result.html?race_id={RID}", "observed_at_utc": NOW}
    metadata = {"version": "last3f-gap-repair-v1", "batch_id": "clock-test",
        "raw_values": {field: before.get(field) for field in audit.CLOCK_FIELDS},
        "source_body_sha256": source["raw_body_sha256"], "source_url": source["source_url"],
        "source_race_date": "20260104", "source_observed_at_utc": NOW, "repaired_at_utc": NOW,
        "historical_prestart_capture_proven": False, "legacy_last3f_rank_recomputed": False,
        "usage": "past race clock only; history_race_date < target_race_date"}
    update = {"last_3f": value, "last_3f_time": value, "_last3f_gap_repair": metadata}
    update.update(changes or {})
    change(stores, "clock-test", update, row=row, evidence={"repair_kind": "last3f_clock", "source": source,
        "source_race_date": "20260104", "normal_values_overwritten": False, "zero_fill": False})


def run(stores, batches=None, suffix="audit"):
    output = stores[2] / suffix
    output.mkdir()
    return audit.audit(stores[0], stores[1], batches=batches or BATCHES, output_dir=output,
                       yield_every=10, pause_seconds=.001)[0]


def test_multi_batch_chain_and_all_normal_rows_are_independently_verified(stores):
    change(stores, "legacy", {"weight_kg": 480}, evidence={"kind": "old-quality-repair"})
    change(stores, "pedigree-test", PED)
    clock(stores)
    result = run(stores)
    assert result["audit_pass"] is True
    assert result["normal_records_preserved_and_only_authorized_journaled_updates"] is True
    assert result["journal"]["entry_count"] == 3
    assert result["journal"]["chain_count"] == result["journal"]["used_chain_count"] == 1
    runners = next(row for row in result["comparison"] if row["table"] == "race_results_ultimate")
    assert runners["updated"] == 1 and runners["unchanged"] == 1 and runners["deleted"] == 0
    assert result["quality"]["scopes"]["jra_flat_201601_202609"]["fields"]["last_3f_time"]["counts"]["valid"] == 1
    assert result["execution"]["database_copy_or_physical_hash"] is False


@pytest.mark.parametrize("field,value", [("sire", "別名"), ("finish_position", 2), ("horse_number", 9),
    ("horse_id", "wrong"), ("arbitrary", {"preserve": [True, True, None]})])
def test_a_valid_hash_does_not_authorize_existing_or_unrelated_pedigree_changes(stores, field, value):
    if field == "sire":
        change(stores, "legacy", {"sire": PED["sire"]}, evidence={})
    change(stores, "pedigree-test", {**PED, field: value})
    result = run(stores)
    assert result["audit_pass"] is False
    assert result["journal"]["issues"]


@pytest.mark.parametrize("name", ["**", "Sire123", "日本語English(米)", "", None])
def test_pedigree_names_must_be_strict_and_nonempty(stores, name):
    change(stores, "pedigree-test", {**PED, "sire": name})
    assert run(stores)["audit_pass"] is False


def test_original_pedigree_alias_conflict_is_not_hidden(stores):
    change(stores, "legacy", {"broodmare_sire": "別名"}, evidence={})
    change(stores, "pedigree-test", PED)
    result = run(stores)
    assert result["journal"]["issues"]["pedigree_original_alias_conflict"] == 1


@pytest.mark.parametrize("value", ["**", None, "未計測", "計不", "---"])
def test_clock_masked_or_semantically_missing_to_real_clock_is_allowed(stores, value):
    change(stores, "legacy", {"last_3f_time": value}, evidence={})
    clock(stores)
    assert run(stores)["audit_pass"] is True


@pytest.mark.parametrize("value", [0, -1, "bad", 150])
def test_clock_invalid_values_are_not_authorized_as_missing(stores, value):
    change(stores, "legacy", {"last_3f_time": value}, evidence={})
    clock(stores)
    result = run(stores)
    assert result["audit_pass"] is False
    assert "last3f_not_missing_or_masked_to_valid" in result["journal"]["issues"]


def test_clock_existing_valid_alias_must_retain_exact_type_and_value(stores):
    change(stores, "legacy", {"last_3f": "35.1", "last_3f_time": "**"}, evidence={})
    clock(stores, changes={"last_3f": "35.1"})
    assert run(stores)["audit_pass"] is True


def test_clock_existing_numeric_alias_cannot_be_reformatted(stores):
    change(stores, "legacy", {"last_3f": "35.1", "last_3f_time": "**"}, evidence={})
    clock(stores)
    result = run(stores)
    assert result["audit_pass"] is False
    assert result["journal"]["issues"]["last3f_original_valid_alias_changed"] == 1


def test_clock_results_and_rank_are_not_changed(stores):
    clock(stores, changes={"last_3f_rank": 1})
    result = run(stores)
    assert result["audit_pass"] is False
    assert result["journal"]["issues"]["last3f_unrelated_or_no_clock_change"] == 1


def test_journal_hash_corruption_fails_even_when_raw_chain_covers_final_data(stores):
    change(stores, "pedigree-test", PED)
    with sqlite3.connect(stores[1]) as db:
        db.execute("UPDATE data_quality_repair_journal_v1 SET after_sha256='bad'")
    result = run(stores)
    assert result["audit_pass"] is False
    assert result["journal"]["issues"]["after_hash_mismatch"] == 1


def test_cross_batch_discontinuity_fails_even_with_recomputed_hash(stores):
    change(stores, "pedigree-test", PED)
    clock(stores)
    with sqlite3.connect(stores[1]) as db:
        before = db.execute("SELECT before_data FROM data_quality_repair_journal_v1 WHERE batch_id='clock-test'").fetchone()[0]
        altered = encode({**json.loads(before), "unrecorded_intermediate": True})
        db.execute("UPDATE data_quality_repair_journal_v1 SET before_data=?,before_sha256=? WHERE batch_id='clock-test'", (altered, digest(altered)))
    result = run(stores)
    assert result["audit_pass"] is False
    assert result["journal"]["issues"]["global_cross_batch_journal_discontinuity"] == 1


def test_omitted_batch_does_not_get_implicitly_authorized(stores):
    change(stores, "pedigree-test", PED)
    clock(stores)
    result = run(stores, {"legacy": "original", "pedigree-test": "pedigree"})
    assert result["audit_pass"] is False
    assert any(row["unauthorized_changes"] for row in result["comparison"])


def test_created_at_and_deletion_are_checked_outside_json_journal(stores):
    change(stores, "pedigree-test", PED)
    with sqlite3.connect(stores[1]) as db:
        db.execute("UPDATE race_results_ultimate SET created_at='changed' WHERE id=1")
        db.execute("DELETE FROM race_results_ultimate WHERE id=2")
    result = run(stores)
    assert result["audit_pass"] is False
    runners = next(row for row in result["comparison"] if row["table"] == "race_results_ultimate")
    assert runners["deleted"] == 1 and runners["unauthorized_changes"] == 2


@pytest.mark.parametrize("table", ["training_data", "speed_figures"])
def test_auxiliary_source_tables_are_not_overlooked(stores, table):
    with sqlite3.connect(stores[1]) as db:
        db.execute(f"UPDATE {table} SET value='unauthorized' WHERE id=1")
    result = run(stores)
    assert result["audit_pass"] is False
    assert next(row for row in result["comparison"] if row["table"] == table)["unauthorized_changes"] == 1


def test_audit_outputs_are_exclusive(stores):
    result = run(stores)
    with pytest.raises(FileExistsError):
        audit.audit(stores[0], stores[1], batches=BATCHES, output_dir=stores[2] / "audit")


def test_all_source_connections_are_read_only(stores):
    with audit.previous.open_ro(stores[1]) as db:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("UPDATE training_data SET value='bad'")


def test_legacy_payout_without_embedded_race_id_remains_strictly_covered(stores):
    payout = {"bet_type": "単勝", "combinations": "1", "payout": 500, "popularity": 1}
    with sqlite3.connect(stores[1]) as db:
        db.execute("INSERT INTO return_tables_ultimate VALUES(1,?,?,?,?,?,?)", (RID, "単勝", "1", 500, 1, "original"))
        _journal(db, "legacy", "return_tables_ultimate", 1, RID, None, encode(payout), {})
    result = run(stores)
    assert result["audit_pass"] is True
    compared = next(row for row in result["comparison"] if row["table"] == "return_tables_ultimate")
    assert compared["inserted"] == compared["journal_covered_changes"] == 1


def test_pedigree_cannot_modify_a_non_jra_race(stores):
    change(stores, "pedigree-test", PED)
    with sqlite3.connect(stores[1]) as db:
        # A malicious journal can correctly hash changes but cannot grant
        # this feature-repair class authority over a local-racing identity.
        for column in ("before_data", "after_data"):
            value = db.execute(f"SELECT {column} FROM data_quality_repair_journal_v1").fetchone()[0]
            value = value.replace(RID, "202644010101")
            db.execute(f"UPDATE data_quality_repair_journal_v1 SET {column}=?,{column.replace('_data', '_sha256')}=?", (value, digest(value)))
        db.execute("UPDATE data_quality_repair_journal_v1 SET race_id='202644010101'")
    result = run(stores)
    assert result["audit_pass"] is False
    assert result["journal"]["scope_issues"]["feature_repair_outside_jra_period"] == 1


def test_malformed_feature_evidence_is_reported_not_silently_accepted(stores):
    clock(stores)
    with sqlite3.connect(stores[1]) as db:
        db.execute("UPDATE data_quality_repair_journal_v1 SET evidence_json='[1,2]'")
    result = run(stores)
    assert result["audit_pass"] is False
    assert result["journal"]["issues"]["feature_repair_json_object_required"] == 1


def test_source_revision_changes_during_read_prevent_latest_pass(stores, monkeypatch):
    real = audit.previous.external_epoch
    calls = []
    def changed_epoch(path):
        value = real(path)
        calls.append(1)
        return value if len(calls) == 1 else [value[0], value[1] + 1, value[2]]
    monkeypatch.setattr(audit.previous, "external_epoch", changed_epoch)
    result = run(stores)
    assert result["normal_records_preserved_and_only_authorized_journaled_updates"] is True
    assert result["source_revision"]["stable_and_latest"] is False
    assert result["audit_pass"] is False
