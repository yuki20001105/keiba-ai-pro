"""Small isolated SQLite contracts for non-destructive quality repair."""
from __future__ import annotations

from copy import deepcopy
import json
import sqlite3

import pytest

from scraping import safe_repair as repair
from scraping.quality import classify_race_quality
from scraping.storage import _init_sqlite_db, _save_race_sqlite_only

RID = "202605010101"


def result():
    return {"race_info": {"race_id": RID, "date": "20260104", "venue": "東京",
                          "distance": 1600, "track_type": "芝", "num_horses": 3,
                          "field_condition": "良"},
        "horses": [{"race_id": RID, "horse_id": f"202000000{i}", "horse_number": i,
                    "horse_name": f"Horse {i}", "finish_position": i, "finish_time": "1:34.5",
                    "odds": 2 + i, "popularity": i, "weight_kg": 470 + i, "weight_change": i,
                    "sire": "Sire", "dam": "Dam", "damsire": "Damsire"} for i in (1, 2, 3)],
        "return_tables": [{"race_id": RID, "bet_type": "単勝", "combinations": "1", "payout": 300, "popularity": 1}]}


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "repair.db"
    _init_sqlite_db(path)
    repair.initialize_journal(path)
    return path


def contents(path):
    with sqlite3.connect(path) as db:
        return {table: db.execute("SELECT * FROM " + table + " ORDER BY 1").fetchall()
                for table in ("races_ultimate", "race_results_ultimate", "return_tables_ultimate",
                              "data_quality_repair_journal_v1")}


def save(path, candidate):
    return repair.save_result_repair(path, candidate, batch_id="offline-fixture",
        evidence={"source": "synthetic-unit-fixture", "not_prestart_evidence": True})


def test_missing_race_insert_is_journaled_and_idempotent(database):
    candidate = result()
    original = deepcopy(candidate)
    changed, merged = save(database, candidate)
    assert changed == {"inserted_races": 1, "updated_races": 0, "inserted_runners": 3,
                       "updated_runners": 0, "inserted_payouts": 1}
    assert candidate == original
    assert classify_race_quality(merged).valid_for_date_completion
    first = contents(database)
    assert len(first["data_quality_repair_journal_v1"]) == 5
    changed, _ = save(database, candidate)
    assert not any(changed.values())
    assert contents(database) == first


def test_healthy_values_and_ids_created_at_are_preserved_while_missing_fields_fill(database):
    existing = result()
    existing["horses"][0].update(sire=None, last_3f="**", odds=8.2, weight_kg=498)
    assert _save_race_sqlite_only(existing, database)
    before = contents(database)
    candidate = result()
    candidate["horses"][0].update(sire="NewSire", last_3f="34.2")
    changed, merged = save(database, candidate)
    assert changed["updated_runners"] == 1
    horse = merged["horses"][0]
    assert horse["odds"] == 8.2 and horse["weight_kg"] == 498
    assert horse["sire"] == "NewSire" and horse["last_3f"] == "34.2"
    after = contents(database)
    for old, new in zip(before["race_results_ultimate"], after["race_results_ultimate"]):
        assert (old[0], old[-1]) == (new[0], new[-1])
    journal = after["data_quality_repair_journal_v1"][0]
    assert json.loads(journal[5]) == json.loads(before["race_results_ultimate"][0][2])
    assert repair.digest(journal[5]) == journal[7]
    assert repair.digest(journal[6]) == journal[8]


def test_add_missing_runner_without_replacing_existing_rows(database):
    old = result()
    old["horses"].pop()
    assert _save_race_sqlite_only(old, database)
    before = contents(database)["race_results_ultimate"]
    changed, _ = save(database, result())
    assert changed["inserted_runners"] == 1 and changed["updated_runners"] == 0
    assert contents(database)["race_results_ultimate"][:2] == before


@pytest.mark.parametrize("change", ["finish", "time", "horse_id", "extra_saved_runner", "header_date", "header_distance"])
def test_conflicting_source_rejects_without_changing_any_rows(database, change):
    old = result()
    assert _save_race_sqlite_only(old, database)
    candidate = result()
    if change == "finish":
        candidate["horses"][0]["finish_position"] = 2
    elif change == "time":
        candidate["horses"][0]["finish_time"] = "1:35.6"
    elif change == "horse_id":
        candidate["horses"][0]["horse_id"] = "2020999999"
    elif change == "extra_saved_runner":
        candidate["horses"].pop()
        candidate["race_info"]["num_horses"] = 2
    elif change == "header_date":
        candidate["race_info"]["date"] = "20260105"
    else:
        candidate["race_info"]["distance"] = 1800
    before = contents(database)
    with pytest.raises(repair.RepairConflict):
        save(database, candidate)
    assert contents(database) == before


def test_late_payout_conflict_rolls_back_earlier_runner_and_journal_writes(database):
    old = result()
    old["horses"][0]["last_3f"] = "**"
    assert _save_race_sqlite_only(old, database)
    candidate = result()
    candidate["horses"][0]["last_3f"] = "34.2"
    candidate["return_tables"][0]["payout"] = 400
    before = contents(database)
    with pytest.raises(repair.RepairConflict, match="payout-conflict"):
        save(database, candidate)
    assert contents(database) == before


@pytest.mark.parametrize("amount", [None, 0, -100, "**", float("inf"), True])
def test_pending_or_invalid_payout_is_never_fabricated_as_zero(database, amount):
    candidate = result()
    candidate["return_tables"][0]["payout"] = amount
    changed, _ = save(database, candidate)
    assert changed["inserted_payouts"] == 0
    assert contents(database)["return_tables_ultimate"] == []


@pytest.mark.parametrize("status", ["取", "中", "除", "失", "取消", "中止"])
def test_unchanged_nonfinisher_does_not_conflict(database, status):
    old = result()
    old["horses"][-1].update(finish_position=status, finish_time=None)
    assert _save_race_sqlite_only(old, database)
    before = contents(database)
    changed, _ = save(database, deepcopy(old))
    assert not any(changed.values())
    assert contents(database) == before


def test_finalized_card_removes_marker_and_overrides_only_result_fields(database):
    old = result()
    for horse in old["horses"]:
        horse.update(finish_position=None, finish_time=None, _shutuba=True, odds=2.5,
                     custom_observation="keep")
    assert _save_race_sqlite_only(old, database)
    _, merged = save(database, result())
    assert classify_race_quality(merged).valid_for_date_completion
    for index, horse in enumerate(merged["horses"]):
        assert not horse.get("_shutuba")
        assert horse["custom_observation"] == "keep"
        assert horse["odds"] == result()["horses"][index]["odds"]


def test_finalized_card_uses_final_going_and_separates_old_capture_timestamp(database):
    old = result()
    old["race_info"].update(field_condition="良", weather="晴", data_observed_at="2026-01-04T01:00:00Z")
    for horse in old["horses"]:
        horse.update(finish_position=None, finish_time=None, _shutuba=True)
    assert _save_race_sqlite_only(old, database)
    candidate = result()
    candidate["race_info"].update(field_condition="稍重", weather="雨")
    _, merged = save(database, candidate)
    info = merged["race_info"]
    assert info["field_condition"] == "稍重" and info["weather"] == "雨"
    assert "data_observed_at" not in info
    assert info["_entry_observation_before_result"]["data_observed_at"] == "2026-01-04T01:00:00Z"
    assert info["result_observed_at_utc"]


@pytest.mark.parametrize("evidence", ["valid", "missing", "mismatch"])
def test_demotion_normalization_requires_matching_source_evidence_and_preserves_raw(database, evidence):
    old = result()
    old["horses"][1]["finish_position"] = "2(降)"
    assert _save_race_sqlite_only(old, database)
    candidate = result()
    candidate["horses"][1].update(finish_position_raw="2(降)", finish_annotation="降")
    if evidence != "missing":
        candidate["_source_evidence"] = {"decoded_html_sha256": "a" * 64}
        candidate["horses"][1]["_source_normalization"] = {"finish_position": {
            "status": "source_verified_demotion", "evidence_decoded_html_sha256":
                ("a" if evidence == "valid" else "b") * 64}}
    before = contents(database)
    if evidence == "valid":
        changed, merged = save(database, candidate)
        assert changed["updated_runners"] == 1
        horse = merged["horses"][1]
        assert horse["finish_position"] == 2 and horse["finish_position_raw"] == "2(降)"
        assert horse["finish_annotation"] == "降"
    else:
        with pytest.raises(repair.RepairConflict, match="demotion-source-evidence"):
            save(database, candidate)
        assert contents(database) == before


def test_finalizing_does_not_keep_conflicting_stale_canonical_aliases(database):
    old = result()
    for horse in old["horses"]:
        horse.update(finish_position=None, finish=0, finish_time=None, time_seconds=0,
                     weight_kg=470, horse_weight=470, weight_change=0, horse_weight_change=0)
    assert _save_race_sqlite_only(old, database)
    _, merged = save(database, result())
    assert classify_race_quality(merged).valid_for_date_completion
    for horse in merged["horses"]:
        assert horse.get("finish") in (None, horse["finish_position"])
        assert horse.get("time_seconds") in (None, 94.5)
        assert horse.get("horse_weight") in (None, horse["weight_kg"])
        assert horse.get("horse_weight_change") in (None, horse["weight_change"])


@pytest.mark.parametrize("conflict", ["finish", "time"])
def test_existing_canonical_only_result_is_not_misclassified_as_unfinished(database, conflict):
    old = result()
    for horse in old["horses"]:
        horse["finish"] = horse.pop("finish_position")
        horse["time_seconds"] = 94.5
        horse.pop("finish_time")
    assert _save_race_sqlite_only(old, database)
    candidate = result()
    if conflict == "finish":
        candidate["horses"][0]["finish_position"] = 2
    else:
        candidate["horses"][0]["finish_time"] = "1:35.0"
    before = contents(database)
    with pytest.raises(repair.RepairConflict):
        save(database, candidate)
    assert contents(database) == before


def test_incoming_canonical_time_cannot_override_healthy_existing_time(database):
    assert _save_race_sqlite_only(result(), database)
    candidate = result()
    for horse in candidate["horses"]:
        horse["finish"] = horse.pop("finish_position")
        horse["time_seconds"] = 95.0
        horse.pop("finish_time")
    before = contents(database)
    with pytest.raises(repair.RepairConflict):
        save(database, candidate)
    assert contents(database) == before


def test_payout_embedded_other_race_identity_is_rejected(database):
    candidate = result()
    candidate["return_tables"][0]["race_id"] = "202605010102"
    before = contents(database)
    with pytest.raises(repair.RepairConflict):
        save(database, candidate)
    assert contents(database) == before


def test_new_race_requires_venue_matching_race_id(database):
    candidate = result()
    candidate["race_info"]["venue"] = "京都"  # 05 is Tokyo, not Kyoto.
    before = contents(database)
    with pytest.raises(repair.RepairConflict):
        save(database, candidate)
    assert contents(database) == before


def test_conflicting_surface_alias_is_not_added_to_existing_header(database):
    old = result()
    old["race_info"].pop("track_type")
    old["race_info"]["surface"] = "芝"
    assert _save_race_sqlite_only(old, database)
    candidate = result()
    candidate["race_info"]["track_type"] = "ダート"
    before = contents(database)
    with pytest.raises(repair.RepairConflict):
        save(database, candidate)
    assert contents(database) == before


def test_duplicate_candidate_payout_conflict_rejects_whole_transaction(database):
    candidate = result()
    duplicate = deepcopy(candidate["return_tables"][0])
    duplicate["payout"] = 500
    candidate["return_tables"].append(duplicate)
    before = contents(database)
    with pytest.raises(repair.RepairConflict):
        save(database, candidate)
    assert contents(database) == before


def test_going_normalization_is_central_period_scoped_and_reversible(database):
    old = result()
    old["race_info"]["field_condition"] = "稍"
    assert _save_race_sqlite_only(old, database)
    before = contents(database)
    changed = repair.normalize_saved_going(database, batch_id="offline-going", date_from="20260101", date_to="20260131")
    assert changed == [RID]
    after = contents(database)
    assert before["race_results_ultimate"] == after["race_results_ultimate"]
    record = json.loads(after["races_ultimate"][0][1])
    assert record["field_condition"] == "稍重" and record["field_condition_raw"] == "稍"
    journal = after["data_quality_repair_journal_v1"][0]
    assert journal[5] == before["races_ultimate"][0][1]
    assert repair.normalize_saved_going(database, batch_id="offline-going-2") == []
    assert contents(database) == after
