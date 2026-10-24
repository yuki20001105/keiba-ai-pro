from betting.reference import build_reference_bets
import pytest


def predictions():
    return [{"horse_number": n, "predicted_rank": r, "p_raw": 5.0 - r}
            for r, n in enumerate((5, 2, 9), 1)]


def test_fixed_reference_is_not_a_purchase_offer():
    output = build_reference_bets(predictions(), runner_count=3)
    assert output["purchase_eligible"] is False
    assert output["monetary_amounts_assigned"] is False
    assert [t["combination"] for t in output["tickets"]] == ["5", "2-5", "2-5", "5-2", "2-5-9", "5-2-9"]
    assert all(set(t) == {"bet_type", "combination", "horse_numbers"} for t in output["tickets"])


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, None, "1"])
def test_invalid_scores_do_not_produce_examples(bad):
    rows = predictions(); rows[0]["p_raw"] = bad
    assert not build_reference_bets(rows, runner_count=3)["tickets"]


def test_incomplete_duplicate_or_inconsistent_ranking_is_rejected():
    assert not build_reference_bets(predictions(), runner_count=4)["tickets"]
    rows = predictions(); rows[1]["horse_number"] = 5
    assert not build_reference_bets(rows, runner_count=3)["tickets"]
    rows = predictions(); rows[1]["p_raw"] = 100
    assert not build_reference_bets(rows, runner_count=3)["tickets"]


def test_market_and_results_never_change_reference():
    rows = predictions()
    original = build_reference_bets(rows, runner_count=3)
    for row in rows:
        row.update(odds=100, finish=1, win_probability=0.99)
    assert build_reference_bets(rows, runner_count=3) == original
