"""Offline temporary-DB checks for non-destructive ordinary acquisition."""
from copy import deepcopy
import json
import sqlite3

import pytest

from scraping.storage import _save_race_sqlite_only, _save_race_to_ultimate_db
from scraping.storage_preservation import PRESERVATION_POLICY_VERSION


RID = "202606040812"


def payload():
    return {
        "race_info": {"race_id": RID, "date": "20260926", "venue": "中山", "distance": 1800, "num_horses": 2},
        "horses": [
            {"race_id": RID, "horse_id": f"202000000{number}", "horse_number": number,
             "horse_name": f"Horse {number}", "finish_position": number, "finish_time": "1:50.1",
             "last_3f": "35.5", "last_3f_time": 35.5, "weight_kg": 480, "weight_change": 2,
             "sire": "父", "dam": "母", "damsire": "母父", "odds": 3.5, "popularity": number}
            for number in (1, 2)
        ],
        "return_tables": [{"bet_type": "単勝", "combinations": "1", "payout": 350, "popularity": 1}],
    }


def read(db):
    with sqlite3.connect(db) as conn:
        return {
            "race_info": json.loads(conn.execute("SELECT data FROM races_ultimate WHERE race_id=?", (RID,)).fetchone()[0]),
            "horses": [json.loads(row[0]) for row in conn.execute("SELECT data FROM race_results_ultimate WHERE race_id=? ORDER BY id", (RID,))],
            "return_tables": [dict(zip(("bet_type", "combinations", "payout", "popularity"), row)) for row in conn.execute(
                "SELECT bet_type,combinations,payout,popularity FROM return_tables_ultimate WHERE race_id=? ORDER BY id", (RID,),
            )],
        }


def dump(db):
    with sqlite3.connect(db) as conn:
        return list(conn.iterdump())


@pytest.mark.parametrize("writer", [_save_race_sqlite_only, _save_race_to_ultimate_db])
@pytest.mark.parametrize("missing", [None, "", " ", "-", "--", "**", "—", "NaN", float("nan"), float("inf")])
def test_missing_fields_keep_known_values_and_input_unchanged(tmp_path, writer, missing):
    db = tmp_path / "races.db"
    original = payload()
    assert writer(original, db)
    incoming = deepcopy(original)
    incoming["race_info"]["venue"] = missing
    incoming["horses"][0].update(last_3f=missing, last_3f_time=missing, sire=missing, weight_kg=missing, finish_time=missing)
    before = json.dumps(incoming, ensure_ascii=False)
    assert writer(incoming, db)
    assert read(db) == original
    assert json.dumps(incoming, ensure_ascii=False) == before
    assert writer(incoming, db)
    assert read(db) == original


def test_valid_corrections_aliases_and_meaningful_zero_are_saved(tmp_path):
    db = tmp_path / "races.db"
    old = payload()
    old["horses"][0]["time_seconds"] = 110.1
    assert _save_race_sqlite_only(old, db)
    incoming = payload()
    incoming["race_info"]["distance"] = 2000
    incoming["horses"][0].update(last_3f=34.0, last_3f_time=None, finish_time="1:49.2", weight_change=0, prize_money=0, running_style=0)
    assert _save_race_sqlite_only(incoming, db)
    saved = read(db)
    row = saved["horses"][0]
    assert saved["race_info"]["distance"] == 2000
    assert row["last_3f"] == row["last_3f_time"] == 34.0
    assert row["finish_time"] == "1:49.2" and row["time_seconds"] == 109.2
    assert row["weight_change"] == row["prize_money"] == row["running_style"] == 0


def test_positive_measurement_zero_is_missing_not_a_correction(tmp_path):
    db = tmp_path / "races.db"
    assert _save_race_sqlite_only(payload(), db)
    changed = payload()
    changed["horses"][0].update(last_3f=0, last_3f_time="0", finish_time="0:00.0", weight_kg=0, odds=0)
    assert _save_race_sqlite_only(changed, db)
    assert read(db) == payload()


@pytest.mark.parametrize("token", ["取消", "中止", "除外", "失格", "DNF"])
def test_explicit_nonfinish_never_inherits_old_result_fields(tmp_path, token):
    db = tmp_path / "races.db"
    old = payload()
    old["horses"][0].update(corner_positions="1-1", corner_1=1, last_3f_rank=1, prize_money=10000, time_seconds=110.1)
    assert _save_race_sqlite_only(old, db)
    changed = payload()
    changed["horses"][0] = {"race_id": RID, "horse_id": "2020000001", "horse_number": 1, "finish_position": token, "finish_time": None}
    assert _save_race_sqlite_only(changed, db)
    row = read(db)["horses"][0]
    assert row["finish_position"] == token and row["finish_time"] is None
    assert row["sire"] == "父"
    assert not {"last_3f", "last_3f_time", "time_seconds", "corner_positions", "corner_1", "last_3f_rank", "prize_money"} & row.keys()


@pytest.mark.parametrize("change", [
    lambda p: p["horses"][0].update(horse_id="different-horse"),
    lambda p: p["horses"][0].update(horse_number=3),
    lambda p: p["horses"][0].update(race_id="202606040811"),
    lambda p: p["horses"][0].update(horse_id="2020000002"),
    lambda p: p["horses"].append(deepcopy(p["horses"][0])),
    lambda p: p["horses"].pop(),
    lambda p: p["race_info"].update(date="20260927"),
    lambda p: p["horses"][0].update(last_3f=34.0, last_3f_time=36.0),
    lambda p: p["horses"][0].update(_acquisition_reuse={"bad": float("nan")}),
])
def test_conflicts_leave_all_original_rows_unchanged(tmp_path, change):
    db = tmp_path / "races.db"
    assert _save_race_sqlite_only(payload(), db)
    before = dump(db)
    incoming = payload()
    change(incoming)
    assert not _save_race_sqlite_only(incoming, db)
    assert dump(db) == before
    assert _save_race_sqlite_only(payload(), db)  # rollback released the lock


@pytest.mark.parametrize("missing_key", ["horse_id", "horse_number"])
def test_one_missing_identity_can_use_the_other_unique_identifier(tmp_path, missing_key):
    db = tmp_path / "races.db"
    assert _save_race_sqlite_only(payload(), db)
    changed = payload()
    changed["horses"][0].pop(missing_key)
    changed["horses"][0].pop("last_3f")
    assert _save_race_sqlite_only(changed, db)
    assert read(db) == payload()


def test_legacy_missing_identity_can_be_filled_without_cross_matching(tmp_path):
    db = tmp_path / "races.db"
    old = payload()
    old["horses"][0].pop("horse_id")
    old["horses"][0].update(last_3f=None, last_3f_time=None)
    assert _save_race_sqlite_only(old, db)
    assert _save_race_sqlite_only(payload(), db)
    assert read(db) == payload()


@pytest.mark.parametrize("amount", [None, 0, -100, "--", float("nan")])
def test_bad_return_amount_does_not_erase_a_known_return(tmp_path, amount):
    db = tmp_path / "races.db"
    assert _save_race_sqlite_only(payload(), db)
    changed = payload()
    changed["return_tables"][0]["payout"] = amount
    assert _save_race_sqlite_only(changed, db)
    assert read(db) == payload()


@pytest.mark.parametrize("amount", [None, 0, -100, 12.5, float("inf")])
def test_new_invalid_return_is_not_stored_as_zero(tmp_path, amount):
    db = tmp_path / "races.db"
    assert _save_race_sqlite_only(payload(), db)
    before = dump(db)
    changed = payload()
    changed["return_tables"].append({"bet_type": "複勝", "combinations": "2", "payout": amount})
    assert not _save_race_sqlite_only(changed, db)
    assert dump(db) == before


def test_return_correction_and_partial_return_snapshot(tmp_path):
    db = tmp_path / "races.db"
    old = payload()
    old["return_tables"].append({"bet_type": "複勝", "combinations": "2", "payout": 120, "popularity": None})
    assert _save_race_sqlite_only(old, db)
    changed = payload()
    changed["return_tables"][0]["payout"] = 400
    assert _save_race_sqlite_only(changed, db)
    assert [row["payout"] for row in read(db)["return_tables"]] == [400, 120]


def test_late_sql_error_rolls_back_race_runner_returns_and_metadata(tmp_path):
    db = tmp_path / "races.db"
    assert _save_race_sqlite_only(payload(), db)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TRIGGER reject_returns BEFORE UPDATE ON return_tables_ultimate BEGIN SELECT RAISE(ABORT, 'test fault'); END")
    before = dump(db)
    changed = payload()
    changed["race_info"]["distance"] = 2000
    changed["horses"][0]["sire"] = "new sire"
    assert not _save_race_sqlite_only(changed, db)
    assert dump(db) == before


def test_audit_metadata_stays_outside_feature_rows_and_does_not_mutate_input(tmp_path):
    db = tmp_path / "races.db"
    original = payload()
    original["horses"][0]["_acquisition_reuse"] = {"target_date": "20260926", "history": {"audit_only": True}}
    before = deepcopy(original)
    assert _save_race_sqlite_only(original, db)
    assert original == before
    assert read(db) == payload()
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM scrape_horse_reuse_audit").fetchone()[0] == 1
    assert PRESERVATION_POLICY_VERSION == "normal-acquisition-safe-v1"


def test_identical_refresh_preserves_row_ids_timestamps_and_sequence(tmp_path):
    db = tmp_path / "races.db"
    assert _save_race_sqlite_only(payload(), db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE race_results_ultimate SET created_at='2000-01-01 00:00:00'")
        conn.execute("UPDATE races_ultimate SET created_at='2000-01-01 00:00:00'")
        conn.execute("UPDATE return_tables_ultimate SET created_at='2000-01-01 00:00:00'")
    before = dump(db)
    assert _save_race_sqlite_only(payload(), db)
    assert dump(db) == before


@pytest.mark.parametrize("bad", [None, [], {}, {"race_info": []}, {"race_info": {"race_id": RID}, "horses": []}])
def test_invalid_input_returns_false_without_creating_a_database(tmp_path, bad):
    db = tmp_path / "races.db"
    assert not _save_race_sqlite_only(bad, db)
    assert not db.exists()
