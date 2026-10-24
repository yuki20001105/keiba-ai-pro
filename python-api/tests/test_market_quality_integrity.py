from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from market_quality.integrity import audit_integrity, audit_race, read_scoped_source  # noqa: E402


RID = "202105010101"
DATE = "20210105"


@pytest.fixture
def case():
    meta = {"race_id": RID, "date": DATE, "venue": "東京", "distance": 1600,
            "num_horses": 3, "surface": "芝", "track_type": "芝", "race_class": "未勝利"}
    records = []
    raw = []
    for number in (1, 2, 3):
        runner = {"race_id": RID, "horse_id": f"201810000{number}", "horse_number": number,
                  "finish_position": number, "finish_time": 98 + number,
                  "odds": [3.0, 5.0, 10.0][number - 1], "popularity": number}
        raw.append((RID, number, json.dumps(runner), "2026-05-31"))
        records.append({"race_id": RID, "race_date": DATE, "horse_id": runner["horse_id"],
                        "horse_number": number, "finish": number, "odds": runner["odds"],
                        "popularity": number, "tansho_payout": 300 if number == 1 else np.nan})
    cohort = pd.DataFrame(records)
    return {"race_id": RID, "cohort": cohort, "oof": cohort.copy(deep=True),
            "race_rows": [(RID, json.dumps(meta), "2026-05-31")], "raw_results": raw,
            "payouts": [(RID, 10, "単勝", "1", 300, 1, "2026-05-31")]}


def _raw_change(case, row, **changes):
    data = json.loads(case["raw_results"][row][2])
    data.update(changes)
    rid, key, _, when = case["raw_results"][row]
    case["raw_results"][row] = (rid, key, json.dumps(data), when)


def test_complete_cohort_and_payout_pass_without_mutating(case):
    before = copy.deepcopy(case)
    runner, payout = audit_race(**case)
    assert runner["runner_pass"] and payout["payout_pass"]
    assert runner["identity_sets_equal"] and payout["ROI_payout_matches"]
    assert payout["payout_winner_set_equal"]
    pd.testing.assert_frame_equal(before["cohort"], case["cohort"])
    pd.testing.assert_frame_equal(before["oof"], case["oof"])
    assert before["raw_results"] == case["raw_results"]


@pytest.mark.parametrize("missing", ["header", "results", "payout", "oof", "cohort"])
def test_absence_fails_appropriate_gate(case, missing):
    key = {"header": "race_rows", "results": "raw_results", "payout": "payouts"}.get(missing, missing)
    case[key] = case[key].iloc[:0] if isinstance(case[key], pd.DataFrame) else []
    runner, payout = audit_race(**case)
    if missing != "payout": assert not runner["runner_pass"]
    if missing in {"header", "results", "payout", "oof", "cohort"}: assert not payout["payout_pass"]


@pytest.mark.parametrize("payload", ["bad json", "[]", "null", "2", '{}'])
def test_bad_metadata_is_a_failed_race_not_exception(case, payload):
    case["race_rows"] = [(RID, payload, "2026-05-31")]
    runner, payout = audit_race(**case)
    assert not runner["runner_pass"] and not payout["payout_pass"]


@pytest.mark.parametrize("payload", ["bad json", "[]", "null", "2", '{}'])
def test_bad_runner_retained_as_invalid(case, payload):
    case["raw_results"][0] = (RID, 1, payload, "2026-05-31")
    runner, _ = audit_race(**case)
    assert not runner["runner_pass"]
    assert runner["raw_runner_count"] == 3
    assert json.loads(runner["invalid_rows_json"])


@pytest.mark.parametrize("value", ["bad", None, 0, -1, 1.5, float("inf"), True])
def test_bad_saved_runner_numbers_fail_without_conversion_crash(case, value):
    case["cohort"]["horse_number"] = case["cohort"].horse_number.astype(object)
    case["cohort"].loc[1, "horse_number"] = value
    runner, _ = audit_race(**case)
    assert not runner["runner_pass"]


@pytest.mark.parametrize("part", ["cohort", "oof"])
@pytest.mark.parametrize("column", ["finish", "odds", "popularity"])
def test_malformed_saved_numeric_is_per_race_conflict(case, part, column):
    case[part][column] = case[part][column].astype(object)
    case[part].loc[1, column] = "unknown"
    runner, _ = audit_race(**case)
    assert not runner["runner_pass"]
    assert "raw_vs_saved_value_conflict" in runner["issues_json"]


@pytest.mark.parametrize("part", ["cohort", "oof"])
@pytest.mark.parametrize("column,value", [("horse_id", "wrong-horse"), ("race_id", "202105010102"),
                                          ("race_date", "20210106"), ("finish", 3),
                                          ("odds", 99.0), ("popularity", 3)])
def test_saved_identity_or_values_cannot_silently_pass(case, part, column, value):
    case[part].loc[1, column] = value
    assert not audit_race(**case)[0]["runner_pass"]


@pytest.mark.parametrize("part", ["raw", "cohort", "oof"])
def test_duplicates_are_not_collapsed(case, part):
    if part == "raw":
        case["raw_results"].append(case["raw_results"][0])
    else:
        case[part] = pd.concat([case[part], case[part].iloc[[0]]], ignore_index=True)
    runner, _ = audit_race(**case)
    assert not runner["runner_pass"]
    if part == "raw":
        assert runner["raw_duplicate_identity_rows"] == 2
        assert runner["raw_duplicate_number_rows"] == 2


@pytest.mark.parametrize("value", ["取消", "除外", "中止", "失格", None, "", 0])
def test_cancelled_or_unsettled_runner_not_silently_dropped(case, value):
    _raw_change(case, 2, finish_position=value)
    runner, _ = audit_race(**case)
    assert not runner["runner_pass"]
    assert runner["raw_runner_count"] == 3
    assert runner["cancelled_excluded_or_unsettled"] == 1


def test_null_preferred_finish_alias_uses_valid_secondary(case):
    _raw_change(case, 0, finish=None)
    assert audit_race(**case)[0]["runner_pass"]


def test_conflicting_finish_alias_fails(case):
    _raw_change(case, 0, finish=2)
    assert not audit_race(**case)[0]["runner_pass"]


def test_official_nonwinner_finish_tie_is_not_duplicate_runner(case):
    _raw_change(case, 2, finish_position=2)
    for part in ("cohort", "oof"):
        case[part].loc[2, "finish"] = 2
    runner, payout = audit_race(**case)
    assert runner["runner_pass"] and payout["payout_pass"]
    assert runner["raw_duplicate_identity_rows"] == 0


def test_popularity_tie_with_different_odds_fails(case):
    _raw_change(case, 2, popularity=2)
    for part in ("cohort", "oof"):
        case[part].loc[2, "popularity"] = 2
    runner, _ = audit_race(**case)
    assert not runner["runner_pass"]
    assert "popularity_tie_odds_conflict" in runner["issues_json"]


def test_official_popularity_tie_equal_odds_is_allowed(case):
    _raw_change(case, 2, popularity=2, odds=5.0)
    for part in ("cohort", "oof"):
        case[part].loc[2, "popularity"] = 2
        case[part].loc[2, "odds"] = 5.0
    assert audit_race(**case)[0]["runner_pass"]


@pytest.mark.parametrize("field", ["finish_position", "popularity"])
def test_invalid_competition_rank_gap_fails_even_all_sources_agree(case, field):
    _raw_change(case, 1, **{field: 3})
    _raw_change(case, 2, **{field: 2})
    for part in ("cohort", "oof"):
        case[part].loc[1, "finish" if field == "finish_position" else field] = 3
        case[part].loc[2, "finish" if field == "finish_position" else field] = 3
    _raw_change(case, 2, **{field: 3})
    assert not audit_race(**case)[0]["runner_pass"]


def test_deadheat_winners_fail_fixed_single_winner_cohort(case):
    _raw_change(case, 1, finish_position=1)
    for part in ("cohort", "oof"):
        case[part].loc[1, "finish"] = 1
    runner, payout = audit_race(**case)
    assert runner["runner_pass"] and not payout["payout_pass"]
    assert payout["dead_heat"] and payout["winner_count"] == 2


@pytest.mark.parametrize("value", [0, -100, 99, 105, 300.5, True, "bad", None, float("inf")])
def test_official_payout_amount_contract(case, value):
    payout = list(case["payouts"][0]); payout[4] = value
    case["payouts"] = [tuple(payout)]
    for part in ("cohort", "oof"):
        case[part]["tansho_payout"] = case[part].tansho_payout.astype(object)
        case[part].loc[0, "tansho_payout"] = value
    assert not audit_race(**case)[1]["payout_pass"]


@pytest.mark.parametrize("field,value", [(0, "202105010102"), (2, "複勝"), (3, "2"),
                                         (3, "1-2"), (3, "00"), (4, 310)])
def test_wrong_official_payout_binding_or_value(case, field, value):
    payout = list(case["payouts"][0]); payout[field] = value
    case["payouts"] = [tuple(payout)]
    result = audit_race(**case)[1]
    assert not result["payout_pass"]
    if field == 3: assert not result["ROI_payout_matches"]


@pytest.mark.parametrize("part", ["cohort", "oof"])
@pytest.mark.parametrize("value", [100, -100, float("inf"), "bad"])
def test_nonwinner_payout_is_checked_in_both_saved_sources(case, part, value):
    case[part]["tansho_payout"] = case[part].tansho_payout.astype(object)
    case[part].loc[2, "tansho_payout"] = value
    assert not audit_race(**case)[1]["payout_pass"]


def test_duplicate_payout_even_identical_is_not_assumed_safe(case):
    case["payouts"].append(case["payouts"][0])
    payout = audit_race(**case)[1]
    assert payout["payout_duplicate"] and not payout["payout_pass"]


@pytest.mark.parametrize("kind", ["race_rows", "raw_results"])
def test_outer_sql_race_identity_not_ignored(case, kind):
    row = list(case[kind][0]); row[0] = "202105010102"
    case[kind][0] = tuple(row)
    assert not audit_race(**case)[0]["runner_pass"]


def _database(tmp_path, case):
    path = tmp_path / "snapshot.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
          CREATE TABLE races_ultimate(race_id TEXT PRIMARY KEY,data TEXT,created_at TEXT);
          CREATE TABLE race_results_ultimate(race_id TEXT,id INTEGER PRIMARY KEY,data TEXT,created_at TEXT);
          CREATE INDEX idx_results ON race_results_ultimate(race_id);
          CREATE TABLE return_tables_ultimate(race_id TEXT,id INTEGER PRIMARY KEY,bet_type TEXT,
            combinations TEXT,payout INTEGER,popularity INTEGER,created_at TEXT);
          CREATE INDEX idx_payout ON return_tables_ultimate(race_id);
        """)
        db.executemany("INSERT INTO races_ultimate VALUES(?,?,?)", case["race_rows"])
        db.executemany("INSERT INTO race_results_ultimate VALUES(?,?,?,?)", case["raw_results"])
        db.executemany("INSERT INTO return_tables_ultimate VALUES(?,?,?,?,?,?,?)", case["payouts"])
        db.execute("INSERT INTO races_ultimate VALUES('202305010101','do not parse later data','2026-05-31')")
    return path


def test_end_to_end_scoped_readonly_stable_hash(case, tmp_path):
    db = _database(tmp_path, case)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    runners, payouts, receipt = audit_integrity(db, case["cohort"], case["oof"])
    assert runners.runner_pass.all() and payouts.payout_pass.all()
    assert receipt["row_counts"] == {"races": 1, "results": 3, "payouts": 1}
    assert receipt["post_2022_rows_read"] is False
    assert read_scoped_source(db, [RID])[1]["scoped_raw_sha256"] == receipt["scoped_raw_sha256"]
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_global_schema_and_scope_guards(case, tmp_path):
    db = _database(tmp_path, case)
    with pytest.raises(ValueError, match="required"):
        audit_integrity(db, case["cohort"].drop(columns="horse_id"), case["oof"])
    with pytest.raises(ValueError, match="2022"):
        audit_integrity(db, case["cohort"], case["oof"].assign(race_date="20230101"))
    with pytest.raises(ValueError, match="2021-2022"):
        read_scoped_source(db, ["202305010101"])
    Path(str(db) + "-wal").write_bytes(b"pending")
    with pytest.raises(ValueError, match="WAL"):
        read_scoped_source(db, [RID])
