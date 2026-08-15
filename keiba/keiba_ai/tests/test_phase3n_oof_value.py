from __future__ import annotations

import pandas as pd
import pytest

from keiba_ai.point_in_time_odds import PointInTimeOddsPolicy
from scripts.evaluate_phase3n_oof_value import evaluate_oof_value_layer


def _predictions(years: list[int], *, oof: bool) -> pd.DataFrame:
    rows = []
    for year in years:
        race_id = f"{year}01010101"
        for horse in range(1, 6):
            rows.append(
                {
                    "race_id": race_id,
                    "horse_id": f"H{horse}",
                    "race_date": f"{year}-01-01",
                    "post_time": f"{year}-01-01T06:00:00Z",
                    "base_score": float(6 - horse),
                    "winner": int(horse == 1),
                    "odds": float(horse + 1),
                    "odds_observed_at": f"{year}-01-01T05:50:00Z",
                    "odds_source": "licensed",
                    "odds_snapshot_kind": "pre_race",
                    "base_prediction_is_oof": oof,
                }
            )
    return pd.DataFrame(rows)


def _space() -> dict[str, object]:
    return {
        "minimum_bets": 1,
        "maximum_drawdown_percent": 20.0,
        "minimum_expected_values": [1.0],
        "minimum_probability_edges": [-1.0],
        "minimum_odds": [1.01],
        "maximum_odds": [100.0],
        "target_bet_rates": [1.0],
    }


def test_oof_value_layer_keeps_candidate_unapproved_and_undeployed() -> None:
    report, _ = evaluate_oof_value_layer(
        inner_base_oof=_predictions([2019, 2020, 2021, 2022], oof=True),
        outer_base_predictions=_predictions([2023], oof=False),
        search_space=_space(),
        odds_policy=PointInTimeOddsPolicy(),
    )
    assert report["approved"] is False
    assert report["activated"] is False
    assert report["deployed"] is False
    assert report["deployment_eligible"] is False
    assert report["outer_metrics"] is not None
    assert [fold["validation_year"] for fold in report["meta_oof_folds"]] == [
        2021,
        2022,
    ]
    assert report["selected_condition_fixed_before_outer_evaluation"] is not None


def test_oof_value_layer_rejects_final_odds() -> None:
    inner = _predictions([2019, 2020, 2021, 2022], oof=True)
    inner.loc[0, "odds_snapshot_kind"] = "final"
    with pytest.raises(ValueError, match="complete fresh point-in-time odds"):
        evaluate_oof_value_layer(
            inner_base_oof=inner,
            outer_base_predictions=_predictions([2023], oof=False),
            search_space=_space(),
            odds_policy=PointInTimeOddsPolicy(),
        )


def test_oof_value_layer_rejects_overlapping_outer_period() -> None:
    with pytest.raises(ValueError, match="strictly after"):
        evaluate_oof_value_layer(
            inner_base_oof=_predictions([2019, 2020, 2021, 2022], oof=True),
            outer_base_predictions=_predictions([2022], oof=False),
            search_space=_space(),
            odds_policy=PointInTimeOddsPolicy(),
        )
