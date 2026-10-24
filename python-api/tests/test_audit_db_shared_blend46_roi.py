"""Pure selected-ticket calibration guards; no real research data is changed."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import audit_db_shared_blend46_roi as audit
import db_shared_blend46_betting as betting


def cohort():
    rows = []
    for i, date in enumerate(("20250105", "20260105")):
        for n in range(1, 4):
            winner = 1 if i == 0 else 2
            rows.append({"race_id": date + "01", "race_date": date, "horse_number": n,
                "horse_id": str(n), "finish": 1 if n == winner else (2 if n == 1 else 3),
                "odds": (2., 5., 30.)[n - 1], "probability": (.6, .3, .1)[n - 1],
                "raw_score": (3., 2., 1.)[n - 1], "venue": "東京", "surface": "turf",
                "expected_runner_count": 3, "tansho_payout": 210. if n == winner else np.nan,
                "popularity": n})
    return pd.DataFrame(rows)


def test_selected_calibration_uses_actual_bets_not_top1():
    frame = cohort()
    bets = pd.DataFrame([{"race_id": frame.race_id.iloc[0], "horse_number": 2, "stake": 100}])
    result = audit.selected_calibration(frame, bets)
    assert result["bets"] == 1
    assert result["predicted_win_rate"] == .3
    assert result["actual_win_rate"] == 0.
    assert result["absolute_calibration_bias"] == .3
    assert result["actual_over_predicted_win_rate"] == 0.
    assert result["top1_ece_reused_for_selected_bets"] is False
    assert result["indicative_expected_gross_jpy"] == 150.
    assert result["saved_netkeiba_actual_gross_jpy"] == 0.


def test_expected_odds_money_is_not_actual_payout():
    frame = cohort()
    bets = betting.select_bets(frame, "top1")
    result = audit.selected_calibration(frame, bets)
    assert result["predicted_win_rate"] == .6
    assert result["actual_win_rate"] == .5
    assert result["indicative_expected_gross_jpy"] == 240.
    assert result["saved_netkeiba_actual_gross_jpy"] == 210.
    assert result["actual_over_indicative_expected_gross"] == .875
    assert result["signed_calibration_bias_actual_minus_predicted"] == pytest.approx(-.1)
    assert result["eligible_for_ev"] is False


def test_tampered_ticket_metadata_never_changes_calibration():
    frame = cohort()
    bets = betting.select_bets(frame, "top1")
    expected = audit.selected_calibration(frame, bets)
    bets["probability"], bets["odds"], bets["finish"], bets["tansho_payout"] = 1., 9999., 1, 999999
    assert audit.selected_calibration(frame, bets) == expected


def test_fixed_half_open_odds_bins_and_correlated_tickets():
    frame = cohort()
    bets = frame[["race_id", "horse_number"]].assign(stake=100)
    result = audit.selected_calibration(frame, bets)
    assert [b["bets"] for b in result["fixed_odds_bins"]] == [2, 0, 2, 0, 0, 2]
    assert result["fixed_odds_bins"][-1]["odds_upper_exclusive"] is None
    assert result["multiple_ticket_races"] == 2
    assert result["formal_calibration_band"] is False
    assert result["tickets_within_race_not_independent"] is True


def test_empty_bets_do_not_invent_calibration_or_roi():
    frame = cohort()
    empty = pd.DataFrame(columns=["race_id", "horse_number", "stake"])
    result = audit.selected_calibration(frame, empty)
    assert result["bets"] == 0
    assert result["predicted_win_rate"] is None
    assert result["actual_win_rate"] is None
    assert result["actual_over_indicative_expected_gross"] is None
    assert result["indicative_expected_gross_jpy"] == 0.
    assert all(b["bets"] == 0 for b in result["fixed_odds_bins"])


@pytest.mark.parametrize("mutation", ["missing_payout", "outside_ticket", "duplicate_ticket"])
def test_fail_closed_settlement(mutation):
    frame = cohort()
    bets = betting.select_bets(frame, "top1")
    if mutation == "missing_payout":
        frame.loc[frame.finish.eq(1), "tansho_payout"] = np.nan
    elif mutation == "outside_ticket":
        bets.loc[0, "horse_number"] = 9
    else:
        bets = pd.concat([bets, bets.iloc[:1]], ignore_index=True)
    with pytest.raises(ValueError):
        audit.selected_calibration(frame, bets)


def test_duplicate_manifest_path_escape_rejected(tmp_path):
    with pytest.raises(ValueError, match="artifact-boundary"):
        audit._bounded_file(tmp_path, "../not-there.json")


def test_source_cohort_checks_outcomes_odds_and_identity():
    frame = cohort()
    audit._same_source_cohort(frame, frame.copy())
    changed = frame.copy()
    changed.loc[0, "odds"] = 9.
    with pytest.raises(AssertionError):
        audit._same_source_cohort(frame, changed)


def test_source_cohort_ignores_only_model_probabilities():
    frame = cohort()
    changed = frame.copy()
    changed["probability"] = [1 / 3] * len(changed)
    audit._same_source_cohort(frame, changed)
