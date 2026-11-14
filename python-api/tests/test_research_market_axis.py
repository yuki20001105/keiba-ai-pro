import copy
import importlib
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from forward_runtime.schedule import sha256


def module():
    assert importlib.util.find_spec("betting.research_market_axis") is not None, "market-axis research is not implemented"
    return importlib.import_module("betting.research_market_axis")


def runners():
    return [
        {"horse_number": n, "probability": p, "odds": o, "finish": n}
        for n, p, o in [
            (1, .30, 8), (2, .20, 2), (3, .15, 20), (4, .12, 30),
            (5, .10, 100), (6, .08, 15), (7, .05, 50),
        ]
    ]


def dividends():
    return [
        {"bet_type": "ワイド", "combinations": "1-2", "payout": 200},
        {"bet_type": "ワイド", "combinations": "1-3", "payout": 300},
        {"bet_type": "ワイド", "combinations": "2-3", "payout": 400},
        {"bet_type": "3連複", "combinations": "1-2-3", "payout": 1000},
    ]


def test_market_axis_is_favorite_not_ai_top_and_appears_in_every_trio():
    m = module()
    tickets = m.select_tickets(runners(), "market_trio_ai5")
    assert tickets == [(1, 2, 3), (1, 2, 4), (1, 2, 5), (2, 3, 4), (2, 3, 5), (2, 4, 5)]
    assert m.select_tickets(runners(), "market_wide_ai5") == [(1, 2), (2, 3), (2, 4), (2, 5)]


def test_market_favorite_outside_ai_top_five_keeps_all_five_ai_opponents():
    m = module()
    field = runners()
    field[-1]["odds"] = 1.2
    assert m.select_tickets(field, "market_wide_ai5") == [(1, 7), (2, 7), (3, 7), (4, 7), (5, 7)]
    assert m.select_tickets(field, "market_trio_ai5") == [
        (1, 2, 7), (1, 3, 7), (1, 4, 7), (1, 5, 7), (2, 3, 7),
        (2, 4, 7), (2, 5, 7), (3, 4, 7), (3, 5, 7), (4, 5, 7),
    ]


@pytest.mark.parametrize("method,want", [
    ("market_wide_ai2", [(1, 2), (2, 3)]),
    ("market_wide_ai3", [(1, 2), (2, 3), (2, 4)]),
    ("market_wide_ai4", [(1, 2), (2, 3), (2, 4), (2, 5)]),
    ("market_trio_ai2", [(1, 2, 3)]),
    ("market_trio_ai3", [(1, 2, 3), (1, 2, 4), (2, 3, 4)]),
    ("market_trio_ai4", [(1, 2, 3), (1, 2, 4), (1, 2, 5), (2, 3, 4), (2, 3, 5), (2, 4, 5)]),
])
def test_opponent_variants_take_two_three_or_four_after_excluding_axis(method, want):
    assert module().select_tickets(runners(), method) == want


def test_selections_do_not_consume_finish_or_payout():
    m = module()
    original = runners()
    changed = copy.deepcopy(original)
    for r in changed:
        r["finish"] = 8 - r["finish"]
        r["payout"] = 99999999
    assert m.select_tickets(original, "market_wide_ai5") == m.select_tickets(changed, "market_wide_ai5")


def test_multiple_wide_winners_are_summed_against_all_ticket_costs():
    m = module()
    payouts = m.verify_dividends(runners(), dividends())
    result = m.settle_tickets(m.select_tickets(runners(), "market_wide_ai5"), payouts["ワイド"])
    assert result == {"ticket_count": 4, "cost_yen": 400, "return_yen": 600, "hit_ticket_count": 2}


@pytest.mark.parametrize("rows", [
    dividends()[:-1],
    dividends()[1:],
    dividends() + [{"bet_type": "ワイド", "combinations": "1-2", "payout": 999}],
    dividends() + [{"bet_type": "3連複", "combinations": "1-2-4", "payout": 2000}],
])
def test_unknown_or_inconsistent_payouts_cannot_be_assumed_zero(rows):
    with pytest.raises(ValueError):
        module().verify_dividends(runners(), rows)


def test_partial_probabilities_and_nonpositive_odds_are_rejected():
    m = module()
    field = runners()
    field[-1]["probability"] = .01
    with pytest.raises(ValueError):
        m.select_tickets(field, "market_wide_ai5")
    field = runners()
    field[-1]["odds"] = 0
    with pytest.raises(ValueError):
        m.select_tickets(field, "market_wide_ai5")


def capture():
    snapshot = {
        "schema": "forward-race-snapshot-v2", "race_id": "202606040601",
        "post_time": "2026-09-20T01:00:00Z", "cutoff_at": "2026-09-20T00:55:00Z",
        "source_at": "2026-09-20T00:53:53Z", "received_at": "2026-09-20T00:54:31Z",
        "features_generated_at": "2026-09-20T00:54:33Z", "prediction_at": "2026-09-20T00:54:35Z",
        "stored_at": "2026-09-20T00:54:36Z", "capture_kind": "direct_fetch",
        "paper_only": True, "automatic_purchase": False, "expected_runner_count": 7,
        "runners": [{"horse_number": r["horse_number"], "odds": r["odds"]} for r in runners()],
    }
    return {"snapshot": snapshot, "snapshot_sha256": sha256(snapshot)}


def test_saved_prestart_market_preserves_original_odds_and_rejects_stale_or_late():
    m = module()
    envelope = capture()
    assert m.capture_odds(envelope, "202606040601") == {1: 8., 2: 2., 3: 20., 4: 30., 5: 100., 6: 15., 7: 50.}
    stale = copy.deepcopy(envelope)
    stale["snapshot"]["source_at"] = "2026-09-20T00:45:00Z"
    stale["snapshot_sha256"] = sha256(stale["snapshot"])
    with pytest.raises(ValueError):
        m.capture_odds(stale, "202606040601")
    late = copy.deepcopy(envelope)
    late["snapshot"]["stored_at"] = "2026-09-20T00:59:01Z"
    late["snapshot"]["cutoff_at"] = "2026-09-20T00:59:30Z"
    late["snapshot_sha256"] = sha256(late["snapshot"])
    with pytest.raises(ValueError):
        m.capture_odds(late, "202606040601")
    with pytest.raises(ValueError):
        m.capture_odds(envelope, "different-race")


def test_report_includes_skips_and_uses_total_stakes_not_mean_ticket_returns():
    path = Path(__file__).resolve().parents[2] / "scripts" / "backtest_market_axis.py"
    assert path.exists(), "market-axis report runner is not implemented"
    spec = importlib.util.spec_from_file_location("market_axis_report", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    rows = pd.DataFrame([
        {"race_date": "20260101", "cost_yen": 400, "return_yen": 600, "ticket_count": 4, "hit_ticket_count": 2},
        {"race_date": "20260102", "cost_yen": 200, "return_yen": 0, "ticket_count": 2, "hit_ticket_count": 0},
        {"race_date": "20260103", "cost_yen": 0, "return_yen": 0, "ticket_count": 0, "hit_ticket_count": 0},
    ])
    result = script.summarize(rows)
    assert result["cohort_races"] == 3
    assert result["bet_races"] == 2
    assert result["tickets"] == 6
    assert result["gross_return_rate_percent"] == 100
    assert result["hit_race_rate_percent"] == 50
    assert result["daily_max_drawdown_yen"] == 200
    assert result["top1_payout_race_share_percent"] == 100
