"""Synthetic-only tests for fixed ticket accounting, including JRA dead heats."""
from __future__ import annotations

import importlib.util
from itertools import permutations
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/db_shared_blend46_ticket_diagnostics.py"
SPEC = importlib.util.spec_from_file_location("blend46_ticket_diagnostics_under_test", SCRIPT)
diagnostics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostics)

RID = "202505010101"
PAY = {"単勝": 240, "馬連": 880, "馬単": 1250, "ワイド": 450, "三連複": 1600, "三連単": 5000}
NORMAL = {
    "単勝": [(1,)], "馬連": [(1, 2)], "馬単": [(1, 2)],
    "ワイド": [(1, 2), (1, 3), (2, 3)], "三連複": [(1, 2, 3)], "三連単": [(1, 2, 3)],
}


def runners(*, race_id=RID, date="20250501", finish=(1, 2, 3, 4), scores=(4., 3., 2., 1.), proof=None):
    value = pd.DataFrame({"race_id": race_id, "race_date": date,
        "horse_number": range(1, len(finish) + 1), "finish": finish, "raw_score": scores})
    if proof is not None:
        value["race_payout_complete"] = proof
    return value


def payouts(*, race_id=RID, wins=None, ordered_hyphens=False, aliases=False):
    rows = []
    for kind, choices in (wins or NORMAL).items():
        label = kind.replace("三連", "3連") if aliases else kind
        separator = "-" if ordered_hyphens or kind not in diagnostics.ORDERED else "→"
        for numbers in choices:
            rows.append({"race_id": race_id, "bet_type": label,
                "combinations": separator.join(map(str, numbers)), "payout": PAY[kind]})
    return pd.DataFrame(rows)


def run(frame=None, table=None, **kwargs):
    if frame is None:
        frame = runners()
    if table is None:
        table = payouts()
    kwargs.setdefault("expected_verified_race_ids", set(frame.race_id.astype(str)))
    return diagnostics.diagnose_rank_tickets(frame, table, **kwargs)


def ledger(result):
    return pd.DataFrame(result["ledger"])


def test_fixed_six_one_point_tickets_financial_accounting():
    result = run(return_ledger=True)
    assert len(result["ledger"]) == 6
    assert result["metadata"]["one_ticket_per_race_per_bet_type"]
    assert result["metadata"]["six_separate_strategies_not_one_600_yen_recommendation"]
    for row in result["totals"]:
        kind = row["bet_type"]
        assert row["stake_yen"] == 100
        assert row["return_yen"] == PAY[kind]
        assert row["roi_percent"] == PAY[kind]
        assert row["profit_yen"] == PAY[kind] - 100
        assert row["hit_count"] == 1 and row["hit_rate"] == 1.
        assert row["missing_payout_bets"] == 0 and row["roi_evaluable"]


def test_default_result_has_no_dataframe_and_never_promotes():
    result = run()
    assert "ledger" not in result
    for name in ("odds_used", "probabilities_used", "expected_value_available",
                 "roi_used_for_selection", "live_purchase_allowed", "eligible_for_ev"):
        assert result["metadata"][name] is False


def test_extra_odds_and_probabilities_are_not_consumed_or_changed():
    frame = runners()
    frame["odds"] = np.nan
    frame["win_probability"] = "must not be read"
    before, source = frame.copy(deep=True), payouts()
    before_source = source.copy(deep=True)
    assert run(frame, source) == run()
    pd.testing.assert_frame_equal(frame, before)
    pd.testing.assert_frame_equal(source, before_source)


def test_raw_score_ties_choose_horse_number_not_winner():
    frame = runners(finish=(2, 3, 4, 1), scores=(0., 0., 0., 0.)).sample(frac=1, random_state=1)
    wins = {"単勝": [(4,)], "馬連": [(1, 4)], "馬単": [(4, 1)],
        "ワイド": [(1, 4), (2, 4), (1, 2)], "三連複": [(1, 2, 4)], "三連単": [(4, 1, 2)]}
    rows = ledger(run(frame, payouts(wins=wins), return_ledger=True)).set_index("bet_type")
    assert rows.loc["単勝", "horses"] == "1"
    assert rows.loc["馬単", "horses"] == "1→2"
    assert rows.loc["三連単", "horses"] == "1→2→3"
    assert rows.loc["ワイド", "payout_yen"] == 450
    assert rows.drop(index="ワイド").payout_yen.eq(0).all()


@pytest.mark.parametrize("aliases", [False, True])
@pytest.mark.parametrize("hyphens", [False, True])
def test_aliases_and_ordered_source_separators(aliases, hyphens):
    assert run(table=payouts(aliases=aliases, ordered_hyphens=hyphens)) == run()


def test_unordered_source_combination_normalizes_but_ordered_never_sorts():
    source = payouts()
    source.loc[source.bet_type.eq("馬連"), "combinations"] = "2-1"
    source.loc[source.bet_type.eq("三連複"), "combinations"] = "3-1-2"
    assert run(table=source) == run()
    source.loc[source.bet_type.eq("馬単"), "combinations"] = "2→1"
    with pytest.raises(ValueError, match="winning-combination-set-mismatch"):
        run(table=source)


DEAD_HEATS = [
    ((1, 1, 3, 4), {"単勝": [(1,), (2,)], "馬連": [(1, 2)], "馬単": [(1, 2), (2, 1)],
        "ワイド": [(1, 2), (1, 3), (2, 3)], "三連複": [(1, 2, 3)],
        "三連単": [(1, 2, 3), (2, 1, 3)]}),
    ((1, 2, 2, 4), {"単勝": [(1,)], "馬連": [(1, 2), (1, 3)], "馬単": [(1, 2), (1, 3)],
        "ワイド": [(1, 2), (1, 3), (2, 3)], "三連複": [(1, 2, 3)],
        "三連単": [(1, 2, 3), (1, 3, 2)]}),
    ((1, 2, 3, 3), {"単勝": [(1,)], "馬連": [(1, 2)], "馬単": [(1, 2)],
        "ワイド": [(1, 2), (1, 3), (2, 3), (1, 4), (2, 4)],
        "三連複": [(1, 2, 3), (1, 2, 4)], "三連単": [(1, 2, 3), (1, 2, 4)]}),
    ((1, 1, 1, 4), {"単勝": [(1,), (2,), (3,)], "馬連": [(1, 2), (1, 3), (2, 3)],
        "馬単": list(permutations((1, 2, 3), 2)), "ワイド": [(1, 2), (1, 3), (2, 3)],
        "三連複": [(1, 2, 3)], "三連単": list(permutations((1, 2, 3)))}),
    ((1, 2, 2, 2), {"単勝": [(1,)], "馬連": [(1, 2), (1, 3), (1, 4)],
        "馬単": [(1, 2), (1, 3), (1, 4)],
        "ワイド": [(1, 2), (1, 3), (1, 4), (2, 3), (2, 4), (3, 4)],
        "三連複": [(1, 2, 3), (1, 2, 4), (1, 3, 4)],
        "三連単": [(1, *pair) for pair in permutations((2, 3, 4), 2)]}),
]


@pytest.mark.parametrize("finish,wins", DEAD_HEATS)
def test_all_dead_heat_winner_combinations_checked(finish, wins):
    result = run(runners(finish=finish), payouts(wins=wins))
    assert result["metadata"]["dead_heat_races"] == 1
    assert all(row["hit_count"] == 1 for row in result["totals"])


def test_third_third_wide_is_not_a_winning_ticket():
    finish, wins = DEAD_HEATS[2]
    frame = runners(finish=finish, scores=(1., 2., 4., 3.))
    result = run(frame, payouts(wins=wins), return_ledger=True)
    wide = ledger(result).loc[lambda rows: rows.bet_type.eq("ワイド")].iloc[0]
    assert wide.horses == "3-4" and wide.payout_yen == 0 and not wide.hit
    wrong = payouts(wins={**wins, "ワイド": [*wins["ワイド"], (3, 4)]})
    with pytest.raises(ValueError, match="winning-combination-set-mismatch"):
        run(frame, wrong)


@pytest.mark.parametrize("missing_kind", diagnostics.KINDS)
def test_complete_proof_cannot_hide_missing_type(missing_kind):
    table = payouts().loc[lambda value: ~value.bet_type.eq(missing_kind)]
    with pytest.raises(ValueError, match="complete-proof-missing-bet-type"):
        run(table=table)


@pytest.mark.parametrize("missing_kind", diagnostics.KINDS)
def test_missing_winning_combo_rejected_for_all_types(missing_kind):
    finish, wins = DEAD_HEATS[0]
    if missing_kind not in {"単勝", "馬単", "三連単", "ワイド"}:
        finish, wins = DEAD_HEATS[1]
    table = payouts(wins=wins)
    # Some types have only one row in this fixture, handled by missing-type guard.
    table = table.drop(table.loc[table.bet_type.eq(missing_kind)].index[0])
    with pytest.raises(ValueError, match="missing-bet-type|combination-set-mismatch"):
        run(runners(finish=finish), table)


def test_proof_required_even_if_all_payout_rows_exist():
    with pytest.raises(ValueError, match="explicit-payout-completeness-proof-required"):
        diagnostics.diagnose_rank_tickets(runners(), payouts())
    result = diagnostics.diagnose_rank_tickets(runners(proof=True), payouts())
    assert all(row["roi_evaluable"] for row in result["totals"])


def test_unverified_never_becomes_zero_loss_or_successful_roi():
    empty = payouts().iloc[:0]
    result = diagnostics.diagnose_rank_tickets(runners(proof=False), empty, return_ledger=True)
    assert result["metadata"]["unverified_payout_races"] == 1
    assert ledger(result).payout_yen.isna().all()
    assert ledger(result).profit_yen.isna().all()
    assert ledger(result).hit.isna().all()
    json.dumps(result, allow_nan=False)
    for row in result["totals"]:
        assert row["return_yen"] is None and row["roi_percent"] is None
        assert row["hit_rate"] is None and row["maximum_drawdown_daily_yen"] is None
        assert row["missing_payout_bets"] == 1 and not row["roi_evaluable"]


def test_mixed_unknowns_invalidate_only_their_aggregation_without_dropping_rows():
    first, second = runners(proof=True), runners(race_id="202605010101", date="20260501", proof=False)
    result = diagnostics.diagnose_rank_tickets(pd.concat([first, second]), payouts())
    assert len(result["by_year"]) == 12
    assert all(row["bets"] == 2 and row["roi_percent"] is None for row in result["totals"])
    assert all(row["roi_evaluable"] for row in result["by_year"] if row["year"] == 2025)
    assert all(not row["roi_evaluable"] for row in result["by_year"] if row["year"] == 2026)


@pytest.mark.parametrize("proof", [1, "True", np.nan])
def test_nonboolean_proof_rejected(proof):
    with pytest.raises(ValueError, match="boolean-payout-proof-required"):
        diagnostics.diagnose_rank_tickets(runners(proof=proof), payouts())


def test_conflicting_runner_proof_and_collection_disagreement_rejected():
    frame = runners(proof=True)
    frame.loc[0, "race_payout_complete"] = False
    with pytest.raises(ValueError, match="conflicting-payout-proof"):
        diagnostics.diagnose_rank_tickets(frame, payouts())
    with pytest.raises(ValueError, match="payout-proof-disagreement"):
        run(runners(proof=False))


def test_identical_duplicate_and_alias_duplicate_not_double_counted():
    table = payouts()
    duplicate = table.loc[table.bet_type.eq("三連複")].copy()
    duplicate["bet_type"] = "3連複"
    result = run(table=pd.concat([table, table.iloc[[0]], duplicate]))
    assert result["totals"] == run()["totals"]
    assert result["metadata"]["identical_duplicate_payout_rows"] == 2
    duplicate["payout"] += 10
    with pytest.raises(ValueError, match="conflicting-duplicate-payout"):
        run(table=pd.concat([table, duplicate]))


@pytest.mark.parametrize("amount", [0, 99, 101, -100, np.inf, np.nan, "1,000", "invalid"])
def test_invalid_dividends_rejected(amount):
    table = payouts()
    table["payout"] = table.payout.astype(object)
    table.loc[0, "payout"] = amount
    with pytest.raises(ValueError, match="invalid-official-payout"):
        run(table=table)


@pytest.mark.parametrize("combo", ["1-1", "1→2", "0-2", "1-19", "one-two", "1-2-3", None])
def test_invalid_unordered_combinations_rejected(combo):
    table = payouts()
    table.loc[table.bet_type.eq("馬連"), "combinations"] = combo
    with pytest.raises(ValueError, match="payout-combination|payout-horse-number"):
        run(table=table)


def test_outside_race_payout_horse_rejected():
    table = payouts()
    table.loc[table.bet_type.eq("馬連"), "combinations"] = "1-5"
    with pytest.raises(ValueError, match="payout-horse-not-in-race"):
        run(table=table)


@pytest.mark.parametrize("column,value,error", [
    ("horse_number", 0, "horse-number"), ("horse_number", 1.5, "horse-number"),
    ("finish", 0, "finish"), ("finish", np.nan, "finish"),
    ("raw_score", np.inf, "raw-score"), ("raw_score", np.nan, "raw-score"),
    ("race_date", "20250230", "race-date"), ("race_date", "2025-05-01", "race-date"),
    ("race_id", None, "race-id"),
])
def test_invalid_runner_inputs_rejected(column, value, error):
    frame = runners()
    frame[column] = frame[column].astype(object)
    frame.loc[0, column] = value
    with pytest.raises(ValueError, match=error):
        run(frame)


def test_duplicate_runner_and_conflicting_date_rejected():
    frame = runners()
    with pytest.raises(ValueError, match="duplicate-runner"):
        run(pd.concat([frame, frame.iloc[[0]]]))
    frame.loc[0, "race_date"] = "20250502"
    with pytest.raises(ValueError, match="conflicting-date"):
        run(frame)


@pytest.mark.parametrize("finish", [(1, 2, 4, 5), (1, 1, 2, 3), (2, 3, 4, 5)])
def test_incomplete_finish_ranking_not_a_completed_outcome(finish):
    with pytest.raises(ValueError, match="invalid-finish-ranking"):
        run(runners(finish=finish))


def test_daily_drawdown_and_year_season_totals_recomputed_not_mean_roi():
    frames, tables = [], []
    for index, (date, scores) in enumerate([
        ("20250101", (4., 3., 2., 1.)), ("20250401", (1., 2., 3., 4.)),
        ("20250402", (1., 2., 3., 4.)), ("20260101", (4., 3., 2., 1.)),
    ]):
        rid = f"{date[:4]}05010{index:03d}"
        frames.append(runners(race_id=rid, date=date, scores=scores))
        tables.append(payouts(race_id=rid))
    result = run(pd.concat(frames), pd.concat(tables))
    win = next(row for row in result["totals"] if row["bet_type"] == "単勝")
    assert win["bets"] == 4 and win["return_yen"] == 480
    assert win["roi_percent"] == 120. and win["profit_yen"] == 80
    assert win["maximum_drawdown_daily_yen"] == 200.
    assert win["roi_excluding_largest_ticket_percent"] == 80.
    assert win["roi_excluding_top3_tickets_percent"] == 0.
    assert {row["season"] for row in result["by_season"]} == {"2025-Q1", "2025-Q2", "2026-Q1"}
    years = {row["year"]: row for row in result["by_year"] if row["bet_type"] == "単勝"}
    assert years[2025]["return_yen"] == 240 and years[2025]["stake_yen"] == 300
    assert years[2026]["return_yen"] == 240 and years[2026]["stake_yen"] == 100


def test_same_date_drawdown_is_end_of_day_not_intra_day():
    first = runners()
    second = runners(race_id="202505010102", scores=(1., 2., 3., 4.))
    result = run(pd.concat([first, second]), pd.concat([payouts(), payouts(race_id="202505010102")]))
    win = next(row for row in result["totals"] if row["bet_type"] == "単勝")
    assert win["maximum_drawdown_daily_yen"] == 0.


def test_unrelated_races_and_other_types_do_not_enter_six_diagnostics():
    extra = payouts(race_id="not-in-input")
    extra.loc[:, "payout"] = -1  # Not the requested race cohort.
    other = pd.DataFrame({"race_id": [RID], "bet_type": ["複勝"], "combinations": ["1"], "payout": [130]})
    assert run(table=pd.concat([payouts(), extra, other])) == run()


def test_nonconsecutive_horse_numbers_supported():
    frame = runners()
    frame["horse_number"] = [3, 6, 9, 12]
    conversion = {1: 3, 2: 6, 3: 9, 4: 12}
    wins = {kind: [tuple(conversion[number] for number in choice) for choice in choices]
            for kind, choices in NORMAL.items()}
    result = run(frame, payouts(wins=wins), return_ledger=True)
    assert ledger(result).loc[lambda rows: rows.bet_type.eq("三連単"), "horses"].iloc[0] == "3→6→9"
    assert all(row["hit_count"] == 1 for row in result["totals"])
