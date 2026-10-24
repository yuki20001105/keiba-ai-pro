"""V2 payout journal projection and audit scopes: isolated fixture DBs only."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("quality_after_audit_v2", ROOT / "scripts/audit_quality_repair_after_v2.py")
audit_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)

# Reuse the small fixture, not its auditor (the old audit stays frozen).
from test_quality_repair_after_audit import databases, result, save, RID, BATCH
from scraping import safe_repair


def run(databases, *, plan=None, discovery=None):
    before, current, directory = databases
    return audit_module.audit(before, current, batch_id=BATCH, plan=plan or {"targets": []},
        baseline_coverage={}, discovery=discovery, output_dir=directory / "post-v2",
        yield_every=2, pause_seconds=0)[0]


def payout():
    return {"bet_type": "単勝", "combinations": "1", "payout": 500, "popularity": 1}


def test_payout_json_missing_race_id_requires_independent_journal_identity():
    columns = ["_rowid", "id", "race_id", "bet_type", "combinations", "payout", "popularity", "created_at"]
    row = [7, 7, RID, "単勝", "1", 500, 1, "2026-10-17"]
    assert audit_module.payout_matches(row, columns, json.dumps(payout()), RID)
    assert not audit_module.payout_matches(row, columns, json.dumps(payout()), "202605010102")
    embedded = {**payout(), "race_id": RID}
    assert audit_module.payout_matches(row, columns, json.dumps(embedded), RID)
    embedded["race_id"] = "202605010102"
    assert not audit_module.payout_matches(row, columns, json.dumps(embedded), RID)


@pytest.mark.parametrize("field,value", [
    ("bet_type", "複勝"), ("combinations", "2"), ("payout", 501),
    ("payout", 500.5), ("payout", 0), ("payout", None), ("popularity", 2),
])
def test_payout_projection_still_rejects_any_semantic_difference(field, value):
    columns = ["_rowid", "id", "race_id", "bet_type", "combinations", "payout", "popularity"]
    row = [7, 7, RID, "単勝", "1", 500, 1]
    candidate = payout(); candidate[field] = value
    assert not audit_module.payout_matches(row, columns, json.dumps(candidate), RID)


def test_all_payout_inserts_are_independently_covered_and_discovery_is_unioned(databases):
    _, current, _ = databases
    new_id = "202605010102"
    candidate = result(new_id)
    candidate["return_tables"] = [payout(), {**payout(), "bet_type": "複勝", "payout": 200}]
    save(current, candidate)
    report = run(databases, plan={"targets": [{"race_id": RID}]},
        discovery=[{"race_ids": [RID, new_id, new_id]}])
    assert report["normal_records_preserved_and_only_journaled_additions_or_updates"]
    comparison = next(row for row in report["comparison"] if row["table"] == "return_tables_ultimate")
    assert comparison["inserted"] == comparison["journal_covered_changes"] == 2
    assert comparison["unauthorized_changes"] == comparison["deleted"] == 0
    assert report["journal"]["used_chain_count"] == report["journal"]["chain_count"]
    assert report["all_repair_targets_including_discovery"]["target_count"] == 2
    assert report["discovery_48_targets"]["counts"] == {"mandatory_complete": 2}


@pytest.mark.parametrize("mutation", ["row_race", "body_race", "journal_race", "row_amount", "hash"])
def test_payout_identity_value_and_hash_tampering_are_rejected(databases, mutation):
    _, current, _ = databases
    candidate = result("202605010102"); candidate["return_tables"] = [payout()]
    save(current, candidate)
    with sqlite3.connect(current) as db:
        if mutation == "row_race":
            db.execute("UPDATE return_tables_ultimate SET race_id=?", (RID,))
        elif mutation == "row_amount":
            db.execute("UPDATE return_tables_ultimate SET payout=501")
        elif mutation == "hash":
            db.execute("UPDATE data_quality_repair_journal_v1 SET after_sha256=? WHERE table_name='return_tables_ultimate'", ("0" * 64,))
        elif mutation == "journal_race":
            db.execute("UPDATE data_quality_repair_journal_v1 SET race_id=? WHERE table_name='return_tables_ultimate'", (RID,))
        else:
            body = json.dumps({**payout(), "race_id": RID})
            db.execute("UPDATE data_quality_repair_journal_v1 SET after_data=?,after_sha256=? WHERE table_name='return_tables_ultimate'",
                       (body, audit_module.sha_text(body)))
    report = run(databases)
    assert not report["normal_records_preserved_and_only_journaled_additions_or_updates"]


def test_time_missing_nonfinish_and_outside_target_demotion_are_explicit():
    accumulator = audit_module.QualityAccumulator()
    for rid, day in [(RID, "20260104"), ("201305010101", "20130104")]:
        data = result(rid, day)
        data["horses"][1].update(finish_position="中", finish_time=None)
        if day.startswith("2013"):
            data["horses"][2]["finish_position"] = "3(降)"
        accumulator.add_metadata({"race_id": rid, "data": json.dumps(data["race_info"])})
        for horse in data["horses"]:
            accumulator.add_runner({"race_id": rid, "data": json.dumps(horse)})
    report = accumulator.finish()
    assert report["scopes"]["jra_flat_201601_202609"]["time_missing_by_finish"] == {"legitimate_nonfinish": 1}
    assert not report["remaining_target_jra_demotion_annotations"]
    assert report["remaining_outside_target_demotion_annotations"] == [{"race_id": "201305010101", "rows": 1}]


def test_v2_still_rejects_unjournaled_auxiliary_change(databases):
    safe_repair.initialize_journal(databases[1])
    with sqlite3.connect(databases[1]) as db:
        db.execute("UPDATE speed_figures SET value='different'")
    report = run(databases)
    assert not report["normal_records_preserved_and_only_journaled_additions_or_updates"]
