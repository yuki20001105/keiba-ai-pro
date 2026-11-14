from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from betting.research_allocation import (
    allocation_policy, allocate_races, annual_flat_equivalents, quality_summary,
    summarize_allocations,
)


def predictions():
    return pd.DataFrame([
        {
            "race_id": f"{year}-{day}", "race_date": f"{year}010{day}",
            "horse_id": f"h{runner}", "horse_number": runner, "surface": "turf",
            "finish": runner, "prediction": score, "popularity": runner,
            "tansho_payout": 250.0 if runner == 1 else np.nan,
            "expected_runner_count": 3,
        }
        for year in (2023, 2024) for day in range(1, 4)
        for runner, score in enumerate((2.0, 1.0, -1.0), 1)
    ])


def test_budget_cap_is_sequential_and_resets_annually_without_future_normalization():
    policy = allocation_policy(1200, 1)
    amounts, _ = allocate_races(predictions(), policy)
    for _, group in amounts.groupby(["policy", "year"]):
        assert group.research_stake_yen.tolist() == [1200, 0, 0]
        assert group.budget_remaining_yen.tolist() == [0, 0, 0]
    shortened, _ = allocate_races(predictions().query("race_date.str.endswith('1')"), policy)
    assert shortened.research_stake_yen.eq(1200).all()
    assert amounts.production_buy_amount_yen.eq(0).all()


def test_allocations_do_not_change_with_results_odds_or_later_races():
    frame = predictions()
    policy = allocation_policy()
    before, _ = allocate_races(frame, policy)
    frame["finish"] = 4 - frame["finish"]
    frame["popularity"] = 4 - frame["popularity"]
    frame["tansho_payout"] = np.where(frame["finish"].eq(1), 9000.0, np.nan)
    frame["odds"] = 1000.0
    after, _ = allocate_races(frame, policy)
    for column in ("research_stake_yen", "tier_weight", "tier_reason"):
        assert before[column].equals(after[column])
    first = frame.loc[frame["race_date"].eq("20230101")]
    first_amounts, _ = allocate_races(first, policy)
    assert first_amounts.research_stake_yen.tolist() == before.groupby("policy", sort=False).first().research_stake_yen.tolist()


def test_unknown_winner_payout_nulls_full_year_profit_even_when_pick_loses():
    frame = predictions()
    frame["finish"] = 4 - frame["finish"]
    frame["tansho_payout"] = np.nan
    policy = allocation_policy()
    amounts, records = allocate_races(frame, policy)
    for row in summarize_allocations(amounts, policy):
        assert row["missing_payout_bet_races"] == 3
        assert row["gross_payout_yen"] is None
        assert row["net_profit_yen"] is None
        assert row["daily_settled_max_drawdown_yen"] is None
    assert all(r["return_rate_percent"] is None for r in annual_flat_equivalents(records, 10_000_000))


def test_tied_scores_skip_tier_policy_and_positive_affine_scores_preserve_tiers():
    frame = predictions()
    policy = allocation_policy()
    before, _ = allocate_races(frame, policy)
    frame["prediction"] = frame.prediction * 10 + 90
    after, _ = allocate_races(frame, policy)
    assert before.research_stake_yen.equals(after.research_stake_yen)
    frame["prediction"] = 0.0
    tied, _ = allocate_races(frame, policy)
    assert tied.loc[tied.policy.eq("score_gap_tiers"), "research_stake_yen"].eq(0).all()
    rows = summarize_allocations(tied, policy)
    assert next(r for r in rows if r["policy"] == "score_gap_tiers")["return_rate_percent"] is None


def test_flat_scaling_and_drawdown_use_gross_return_and_include_initial_zero():
    frame = predictions()
    frame["finish"] = 4 - frame["finish"]
    frame["tansho_payout"] = np.where(frame.finish.eq(1), 250.0, np.nan)
    policy = allocation_policy()
    amounts, records = allocate_races(frame, policy)
    for row in summarize_allocations(amounts, policy):
        assert row["daily_settled_max_drawdown_yen"] == row["turnover_yen"]
        assert row["return_rate_percent"] == 0
        assert row["net_roi_percent"] == -100
    assert annual_flat_equivalents(records, 10_000_000)[0]["hypothetical_profit_yen"] == -10_000_000


def test_quality_uses_explicit_window_and_counts_null_or_infinite_values():
    frame = pd.DataFrame({
        "race_date": ["20160101", "20230101", "20260101"], "race_id": ["a", "b", "c"],
        "safe_age": pd.Series([None, 0, 3], dtype="Float64"),
        "safe_horse_weight": [np.inf, 450, 500], "safe_sex": ["", "M", "F"],
    })
    columns = ["safe_age", "safe_horse_weight", "safe_sex"]
    manifest = {
        "feature_sets": {"M03": columns}, "categorical_features": ["safe_sex"],
        "source_audit": {}, "training_eligibility": {},
        "exclusion_reason_counts": {"complete_runner_target_cohort": {}},
        "history_row_count": 3, "history_race_count": 3,
        "row_count": 3, "race_count": 3, "point_in_time_audit": {},
    }
    diagnostics = {
        "train_start_year": 2016,
        "features": {c: {"missing_reason_counts": {}} for c in columns},
    }
    result = quality_summary(frame, manifest, diagnostics, [2023])
    assert result["diagnostic_rows"] == 2
    assert "2026" not in result["by_year"]
    missing = result["by_year"]["2016"]["missing"]
    assert all(missing[c]["count"] == 1 for c in columns)
    assert result["by_year"]["2023"]["nonpositive_values"]["safe_age"] == 1


@pytest.mark.parametrize("budget,count", [(0, 1), (10001, 10), (True, 10), (100, 10), (1000, 0)])
def test_invalid_or_unfundable_policy_is_explicit(budget, count):
    with pytest.raises(ValueError):
        allocation_policy(budget, count)
