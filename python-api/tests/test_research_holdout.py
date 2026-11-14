import importlib
import importlib.util
from pathlib import Path

import pandas as pd
import pytest


def module():
    assert importlib.util.find_spec("betting.research_holdout") is not None, "full holdout settlement is not implemented"
    return importlib.import_module("betting.research_holdout")


def field(finishes):
    return [{"horse_number": n, "finish": finish} for n, finish in enumerate(finishes, 1)]


def rows(wide, trio):
    return [
        {"bet_type": kind, "combinations": "-".join(map(str, key)), "payout": value}
        for kind, mapping in (("ワイド", wide), ("3連複", trio))
        for key, value in mapping.items()
    ]


def test_lower_finish_anomaly_does_not_discard_a_verified_top_three_settlement():
    payouts = module().verify_holdout_dividends(
        field([1, 2, 3, 4, 4, 6, 7]),
        rows({(1, 2): 200, (1, 3): 300, (2, 3): 400}, {(1, 2, 3): 1000}),
    )
    assert payouts["三連複"] == {(1, 2, 3): 1000}


def test_third_place_dead_heat_excludes_the_two_third_place_horses_wide():
    m = module()
    payouts = m.verify_holdout_dividends(
        field([1, 2, 3, 3, 5]),
        rows(
            {(1, 2): 200, (1, 3): 300, (2, 3): 400, (1, 4): 500, (2, 4): 600},
            {(1, 2, 3): 1000, (1, 2, 4): 2000},
        ),
    )
    assert (3, 4) not in payouts["ワイド"]
    assert m.settle_holdout_tickets([(1, 3), (1, 4), (3, 4)], payouts["ワイド"]) == {
        "ticket_count": 3, "cost_yen": 300, "return_yen": 800, "hit_ticket_count": 2,
    }


def test_second_place_dead_heat_requires_winner_in_every_winning_trio():
    m = module()
    payouts = m.verify_holdout_dividends(
        field([1, 2, 2, 2, 5]),
        rows(
            {(1, 2): 200, (1, 3): 300, (1, 4): 400, (2, 3): 500, (2, 4): 600, (3, 4): 700},
            {(1, 2, 3): 1000, (1, 2, 4): 2000, (1, 3, 4): 3000},
        ),
    )
    assert (2, 3, 4) not in payouts["三連複"]
    assert m.settle_holdout_tickets([(1, 2, 3), (1, 2, 4)], payouts["三連複"])["return_yen"] == 3000


def test_partial_dead_heat_payouts_are_not_assumed_to_be_losing_tickets():
    with pytest.raises(ValueError):
        module().verify_holdout_dividends(
            field([1, 2, 3, 3, 5]),
            rows(
                {(1, 2): 200, (1, 3): 300, (2, 3): 400, (1, 4): 500, (2, 4): 600},
                {(1, 2, 3): 1000},
            ),
        )


@pytest.mark.parametrize("finishes", [[1, 2, 2, 3, 5], [1, 3, 3, 4, 5]])
def test_inconsistent_top_three_competition_ranks_are_rejected(finishes):
    with pytest.raises(ValueError):
        module().verify_holdout_dividends(
            field(finishes),
            rows({(1, 2): 200, (1, 3): 300, (2, 3): 400}, {(1, 2, 3): 1000}),
        )


def test_missing_dividends_keep_known_stakes_and_unknown_return():
    assert module().settle_holdout_tickets([(1, 2), (1, 3)], None) == {
        "ticket_count": 2, "cost_yen": 200, "return_yen": None, "hit_ticket_count": None,
    }


def test_no_tickets_has_no_stake_or_payout_even_with_verified_dividends():
    assert module().settle_holdout_tickets([], {(1, 2, 3): 1000}) == {
        "ticket_count": 0, "cost_yen": 0, "return_yen": 0, "hit_ticket_count": 0,
    }


def test_full_denominator_does_not_silently_drop_unknown_settlement():
    path = Path(__file__).resolve().parents[2] / "scripts" / "backtest_market_axis_holdout.py"
    assert path.exists(), "full holdout reporting is not implemented"
    spec = importlib.util.spec_from_file_location("full_holdout_report", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    data = pd.DataFrame([
        {"race_date": "20260101", "ticket_count": 1, "cost_yen": 100, "return_yen": 200, "hit_ticket_count": 1},
        {"race_date": "20260102", "ticket_count": 1, "cost_yen": 100, "return_yen": None, "hit_ticket_count": None},
        {"race_date": "20260103", "ticket_count": 1, "cost_yen": 100, "return_yen": 0, "hit_ticket_count": 0},
    ])
    result = script.full_summary(data)
    assert result["cohort_races"] == 3
    assert result["settled_races"] == 2
    assert result["unsettled_races"] == 1
    assert result["cost_yen"] == 300
    assert result["return_yen"] is None
    assert result["gross_return_rate_percent"] is None
    assert result["settled_subset"]["gross_return_rate_percent"] == 100
