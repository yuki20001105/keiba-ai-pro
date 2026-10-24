from __future__ import annotations

from copy import deepcopy
import importlib.util
from itertools import combinations
import json
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from bet_types.eligibility import audit_eligibility, parse_combination


def fixture(n=8, *, finishes=None, place_positions=None):
    rid, day = "202105010101", "20211001"
    ranks = list(range(1, n + 1)) if finishes is None else finishes
    frame = pd.DataFrame({"race_id": [rid] * n, "race_date": [day] * n, "phase": ["development"] * n,
        "horse_number": list(range(1, n + 1)), "finish": ranks, "field_size": [n] * n, "expected_runner_count": [n] * n})
    headers = {rid: [{"race_id": rid, "data": json.dumps({"race_id": rid, "date": day, "num_horses": n})}]}
    place_positions = place_positions or (3 if n >= 8 else 2)
    rows = []
    for kind, combos in [("単勝", ["1"]), ("複勝", [str(i) for i in range(1, place_positions + 1)]),
                         ("ワイド", ["-".join(map(str, pair)) for pair in combinations((1, 2, 3), 2)]),
                         ("馬連", ["1-2"]), ("馬単", ["1→2"]), ("3連複", ["1-2-3"]), ("3連単", ["1→2→3"])]:
        rows.extend({"race_id": rid, "bet_type": kind, "combinations": combo, "payout": 100} for combo in combos)
    return frame, headers, {rid: rows}


def test_complete_no_ties_consistent_labels_and_no_input_mutation():
    frame, headers, payouts = fixture()
    original, old_headers, old_payouts = frame.copy(deep=True), deepcopy(headers), deepcopy(payouts)
    race, labels, coverage = audit_eligibility(frame, headers, payouts)
    assert race.common_phase_a_eligible.all() and race.eligible_ranking.all()
    assert labels.label_pass.all() and len(labels) == 7
    assert race.place_paid_positions_verified.iloc[0] == 3
    assert race.sales_opening_runner_count.isna().all() and not race.sales_opening_count_verified.any()
    assert coverage.iloc[0].common_eligible_races == 1
    pd.testing.assert_frame_equal(frame, original)
    assert headers == old_headers and payouts == old_payouts


@pytest.mark.parametrize("ranks,top2,top3", [([1, 2, 2, 4, 5, 6, 7, 8], True, True),
    ([1, 2, 3, 3, 5, 6, 7, 8], False, True), ([1, 2, 3, 4, 4, 6, 7, 8], False, False),
    ([1, 1, 3, 4, 5, 6, 7, 8], True, True)])
def test_legal_ties_are_explicit_conservative_exclusion_not_corruption(ranks, top2, top3):
    race, labels, _ = audit_eligibility(*fixture(finishes=ranks))
    row = race.iloc[0]
    assert row.core_valid and row.finish_competition_rank_valid and row.has_finish_ties
    assert not row.common_phase_a_eligible and not row.eligible_ranking
    assert bool(row.top2_tie) == top2 and bool(row.top3_tie) == top3
    assert json.loads(row.core_issues_json) == []
    assert json.loads(row.exclusion_reasons_json) == ["research_policy_excludes_legal_finish_ties"]
    assert not labels.label_pass.any()
    assert all("label_not_checked_dead_heat_policy" in json.loads(s) for s in labels.issues_json)


@pytest.mark.parametrize("n", [5, 6, 7])
@pytest.mark.parametrize("paid_places", [2, 3])
def test_sales_opening_count_not_inferred_from_actual_starters(n, paid_places):
    race, labels, _ = audit_eligibility(*fixture(n, place_positions=paid_places))
    row = race.iloc[0]
    assert row.core_valid and row.eligible_ranking and not row.common_phase_a_eligible
    assert row.place_paid_positions_verified == paid_places
    assert pd.isna(row.place_main_target_positions) and pd.isna(row.sales_opening_runner_count)
    assert labels.label_pass.all()
    assert json.loads(row.exclusion_reasons_json) == ["place_sales_opening_count_unproven_for_fewer_than_eight_starters"]


def test_eight_starters_with_only_two_place_payouts_is_label_problem_not_hidden_race_filter():
    race, labels, _ = audit_eligibility(*fixture(8, place_positions=2))
    assert race.common_phase_a_eligible.all() and not race.phase_a_payout_labels_complete.any()
    place = labels.loc[labels.bet_type.eq("place")].iloc[0]
    assert not place.label_pass
    assert "place_payout_inconsistent_with_at_least_eight_starters" in json.loads(place.issues_json)


@pytest.mark.parametrize("mutation", ["missing", "wrong_combo", "duplicate", "conflict_amount", "negative", "fractional", "not_ten_yen", "wrong_race"])
def test_bad_or_missing_payout_does_not_select_predictive_cohort(mutation):
    frame, headers, payouts = fixture()
    rows = payouts[frame.race_id.iloc[0]]
    wide = next(r for r in rows if r["bet_type"] == "ワイド")
    if mutation == "missing": rows.remove(wide)
    elif mutation == "wrong_combo": wide["combinations"] = "1-8"
    elif mutation == "duplicate": rows.append(wide.copy())
    elif mutation == "conflict_amount": rows.append({**wide, "payout": 200})
    elif mutation == "negative": wide["payout"] = -10
    elif mutation == "fractional": wide["payout"] = 100.5
    elif mutation == "not_ten_yen": wide["payout"] = 101
    elif mutation == "wrong_race": wide["race_id"] = "202105010102"
    race, labels, _ = audit_eligibility(frame, headers, payouts)
    assert race.common_phase_a_eligible.all()
    assert not labels.loc[labels.bet_type.eq("wide"), "label_pass"].iloc[0]
    assert not labels.used_for_predictive_cohort_selection.any() and not labels.payout_is_prematch_odds.any()


@pytest.mark.parametrize("ranks", [[1, 2, 2, 3, 5, 6, 7, 8], [1, 2, 3, 5, 6, 7, 8, 8],
    [1, 2, 3, 4, 5, 6, 7, "取消"], [1, 2, 3, 4, 5, 6, 7, None], [1, 2, 3, 4, 5, 6, 7, 9]])
def test_invalid_competition_rank_and_cancelled_fail_core(ranks):
    race, _, _ = audit_eligibility(*fixture(finishes=ranks))
    assert not race.core_valid.any() and not race.common_phase_a_eligible.any()
    assert "finish_competition_rank_invalid" in json.loads(race.iloc[0].core_issues_json)


@pytest.mark.parametrize("mutation", ["duplicate_number", "declared_count", "raw_count", "missing_header", "duplicate_header", "wrong_header_date", "malformed_header"])
def test_incomplete_or_conflicting_core_explicitly_excluded(mutation):
    frame, headers, payouts = fixture()
    rid = frame.race_id.iloc[0]
    if mutation == "duplicate_number": frame.loc[0, "horse_number"] = 2
    elif mutation == "declared_count": frame.loc[0, "expected_runner_count"] = 9
    elif mutation == "missing_header": headers[rid] = []
    elif mutation == "duplicate_header": headers[rid].append(headers[rid][0].copy())
    elif mutation == "malformed_header": headers[rid][0]["data"] = "null"
    else:
        meta = json.loads(headers[rid][0]["data"])
        meta["num_horses" if mutation == "raw_count" else "date"] = 9 if mutation == "raw_count" else "20221001"
        headers[rid][0]["data"] = meta
    race, labels, _ = audit_eligibility(frame, headers, payouts)
    assert not race.core_valid.any() and not race.common_phase_a_eligible.any()
    assert not labels.label_pass.any()


@pytest.mark.parametrize("combo,kind,expected", [("01-02", "wide", (1, 2)), ("2-1", "quinella", (1, 2)),
    ("2→1", "exacta", (2, 1)), ("2-1", "exacta", (2, 1)), ("1→2", "wide", None),
    ("12 → 8 → 9", "trifecta", (12, 8, 9)), ("12-8-9", "trifecta", (12, 8, 9)),
    ("8 - 12", "wide", (8, 12)), ("8-12→9", "trifecta", None), ("8 9-12", "wide", None),
    ("1-1", "wide", None), ("horse1-2", "wide", None), ("19", "win", None), ("特払", "place", None)])
def test_combination_not_arbitrary_digit_extraction(combo, kind, expected):
    assert parse_combination(combo, kind) == expected


def test_no_model_fields_required_and_extra_model_scores_never_change_eligibility():
    frame, headers, payouts = fixture()
    left = audit_eligibility(frame, headers, payouts)
    frame["h04_score"], frame["probability"], frame["odds"] = float("nan"), -1000, "not read"
    right = audit_eligibility(frame, headers, payouts)
    for a, b in zip(left, right): pd.testing.assert_frame_equal(a, b)


def test_out_of_scope_dates_or_source_rejected():
    frame, headers, payouts = fixture()
    frame["race_date"] = "20230101"
    with pytest.raises(ValueError, match="2021/2022"):
        audit_eligibility(frame, headers, payouts)
    frame, headers, payouts = fixture()
    headers["202305010101"] = []
    with pytest.raises(ValueError, match="out-of-scope"):
        audit_eligibility(frame, headers, payouts)


def test_read_scope_is_exact_readonly_and_rejects_unfinalized_wal(tmp_path):
    script = Path(__file__).resolve().parents[2] / "scripts/audit_bet_type_eligibility.py"
    spec = importlib.util.spec_from_file_location("bet_eligibility_script_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path / "snapshot.db"
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE races_ultimate(race_id TEXT,data TEXT,created_at TEXT); CREATE TABLE return_tables_ultimate(race_id TEXT,id INTEGER,bet_type TEXT,combinations TEXT,payout INTEGER,popularity INTEGER,created_at TEXT);")
        for rid in ("202105010101", "202305010101"):
            db.execute("INSERT INTO races_ultimate VALUES(?,?,?)", (rid, "{}", "2026"))
            db.execute("INSERT INTO return_tables_ultimate VALUES(?,1,'単勝','1',100,1,'2026')", (rid,))
    before = module.sha256(path)
    source, receipt = module.read_scope(path, ["202105010101"])
    assert set(source["races"]) == set(source["payouts"]) == {"202105010101"}
    assert receipt["read_row_counts"] == {"races": 1, "payouts": 1}
    assert before == module.sha256(path)
    with pytest.raises(ValueError, match="2021/2022"):
        module.read_scope(path, ["202305010101"])
    Path(str(path) + "-wal").write_bytes(b"pending")
    with pytest.raises(ValueError, match="pending WAL"):
        module.read_scope(path, ["202105010101"])
