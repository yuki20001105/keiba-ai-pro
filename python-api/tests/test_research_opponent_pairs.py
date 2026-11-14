from __future__ import annotations

import importlib
from pathlib import Path
import runpy

import pandas as pd
import pytest


def api():
    return importlib.import_module("betting.research_opponent_pairs")


def runners():
    return [
        {"horse_number": n, "probability": p, "odds": odds}
        for n, p, odds in [
            (1, .36, 2), (2, .22, 4), (3, .16, 8),
            (4, .12, 15), (5, .09, 30), (6, .05, 60),
        ]
    ]


def recipe(**changes):
    return {"ranking": "hole", "pair": [2, 3], "wide_mask": 2,
            "trio": True, "axis_positions": [2], **changes}


def test_probability_and_single_win_score_rankings_are_not_conflated():
    m = api()
    axis, ai, hole = m.opponent_orders(runners())
    assert axis == 1
    assert ai == [2, 3, 4, 5, 6]
    assert hole == [6, 5, 4, 3, 2]


def test_historical_four_points_keep_h3_wide_and_ordered_axis_second():
    m = api()
    legs = m.select_tickets(runners(), recipe())
    assert legs == {
        "ワイド": [(1, 4)], "三連複": [(1, 4, 5)],
        "三連単": [(4, 1, 5), (5, 1, 4)],
    }
    assert m.stake_per_point(legs) == 300


def test_first_two_opponents_wide_is_a_distinct_two_point_recipe():
    m = api()
    legs = m.select_tickets(runners(), recipe(pair=[1, 2], wide_mask=3, trio=False,
                                            axis_positions=[]))
    assert legs == {"ワイド": [(1, 5), (1, 6)]}
    assert m.stake_per_point(legs) == 700


def test_missing_unused_partner_does_not_skip_an_available_single_wide():
    m = api()
    small = runners()[:5]
    small[0]["probability"] += .05
    available = m.select_tickets(small, recipe(ranking="ai", pair=[1, 5], wide_mask=1,
                                              trio=False, axis_positions=[]))
    assert available == {"ワイド": [(1, 2)]}
    assert m.select_tickets(small, recipe(ranking="ai", pair=[1, 5], wide_mask=3,
                                        trio=False, axis_positions=[])) is None


def test_three_axis_positions_are_all_six_legal_orders_with_no_duplicate():
    m = api()
    legs = m.select_tickets(runners(), recipe(ranking="ai", pair=[1, 2], wide_mask=0,
                                              trio=False, axis_positions=[1, 2, 3]))
    assert set(legs["三連単"]) == {
        (1, 2, 3), (1, 3, 2), (2, 1, 3),
        (2, 3, 1), (3, 1, 2), (3, 2, 1),
    }
    assert m.stake_per_point(legs) == 200


def test_settlement_never_sorts_trifecta_or_zeros_an_unverified_market():
    m = api()
    value = m.settle(
        {"三連単": [(4, 1, 5), (5, 1, 4)]},
        {"三連単": {"4-1-5": 700}},
    )
    assert value["cost_yen"] == 1400
    assert value["return_yen"] == 4900
    assert value["hit_ticket_count"] == 1
    with pytest.raises(ValueError):
        m.settle({"三連単": [(4, 1, 5)]}, {})
    assert m.settle(None, {})["cost_yen"] == 0


@pytest.mark.parametrize("kind", ["zero_odds", "duplicate", "not_normalized", "bool_number"])
def test_invalid_runner_market_is_rejected(kind):
    m = api()
    field = runners()
    if kind == "zero_odds":
        field[0]["odds"] = 0.0
    elif kind == "duplicate":
        field[0]["horse_number"] = 2
    elif kind == "not_normalized":
        field[0]["probability"] = .9
    else:
        field[0]["horse_number"] = True
    with pytest.raises(ValueError):
        m.opponent_orders(field)


def test_every_registered_recipe_has_one_to_six_points_and_legal_stakes():
    m = api()
    for definition in m.registry().values():
        legs = m.select_tickets(runners(), definition)
        points = sum(len(t) for t in legs.values())
        assert 1 <= points <= 6
        assert len({(kind, t) for kind, tickets in legs.items() for t in tickets}) == points
        assert all(1 in t for tickets in legs.values() for t in tickets)
        assert points * m.stake_per_point(legs) <= 1500


def selection_frame():
    records = []
    for i in range(8):
        for method in ["steady", "jackpot"]:
            returned = 130 if method == "steady" else 10000 if i == 0 else 0
            records.append({
                "race_id": str(i), "race_date": f"202501{i + 1:02d}", "method": method,
                "cost_yen": 100, "return_yen": returned,
                "hit_ticket_count": int(returned > 0), "ticket_count": 1,
            })
    return pd.DataFrame(records)


def test_past_selection_requires_hit_support_and_rejects_future_data():
    m = api()
    data = selection_frame()
    choice = m.choose_past_recipe(data, "20260101", min_bets=5, min_dates=5, min_hits=3)
    assert choice["method"] == "steady"
    with pytest.raises(ValueError):
        m.choose_past_recipe(data, "20250102", min_bets=5, min_dates=5, min_hits=3)
    data["return_yen"] = 0
    data["hit_ticket_count"] = 0
    assert m.choose_past_recipe(data, "20260101", min_bets=5, min_dates=5,
                                min_hits=3)["method"] is None


def test_descriptive_summary_retains_skips_and_unspent_cash():
    m = api()
    frame = pd.DataFrame([
        {"race_id": "1", "race_date": "20250101", "cost_yen": 1200, "return_yen": 2400,
         "ticket_count": 4, "hit_ticket_count": 2},
        {"race_id": "2", "race_date": "20250102", "cost_yen": 0, "return_yen": 0,
         "ticket_count": 0, "hit_ticket_count": 0},
    ])
    value = m.summarize(frame, bootstrap_samples=30)
    assert value["cohort_races"] == 2 and value["bet_races"] == 1
    assert value["gross_return_rate_percent"] == 200
    assert value["ending_cash_yen"] == 1001200
    assert value["highest_payout_zero_roi_percent"] == 0


def test_evaluation_refuses_existing_output_without_reading_sources(tmp_path):
    script = Path(__file__).resolve().parents[2] / "scripts" / "evaluate_market_axis_pairs.py"
    evaluate = runpy.run_path(str(script))["evaluate"]
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(FileExistsError):
        evaluate(tmp_path / "missing", output)


def test_missing_source_evidence_does_not_create_a_result_directory(tmp_path):
    script = Path(__file__).resolve().parents[2] / "scripts" / "evaluate_market_axis_pairs.py"
    evaluate = runpy.run_path(str(script))["evaluate"]
    output = tmp_path / "result"
    with pytest.raises(FileNotFoundError):
        evaluate(tmp_path / "missing", output)
    assert not output.exists()
