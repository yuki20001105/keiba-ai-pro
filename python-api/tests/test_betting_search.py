"""Small OOF fixtures only; no model fitting, real markets, or purchases."""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from experiments import betting_search as search


def fixture_frame():
    rows = []
    for year in range(2021, 2027):
        for day in range(1, 7):
            winner = 1 if day % 2 else 2
            for number in range(1, 4):
                rows.append({
                    "race_id": f"{year}-{day}", "race_date": f"{year}010{day}",
                    "horse_number": number, "prediction": (4 - number) * (day + 1),
                    "finish": 1 if number == winner else (2 if number in (1, 2) else 3),
                    "odds": [2.0, 8.0, 30.0][number - 1], "popularity": number,
                    "tansho_payout": (300.0 if winner == 1 else 800.0) if number == winner else np.nan,
                    "num_horses": 3, "fold_train_date_to": f"{year - 1}1231",
                })
    return pd.DataFrame(rows)


def evaluate(frame, directory, **kwargs):
    options = {"model_id": "fixed-model-01", "min_bets": 2, "min_races": 2,
               "min_bets_per_year": 1, "min_dates": 2, "bootstrap_samples": 100,
               "prediction_provenance": {"model_sha256": "a" * 64, "kind": "fixed_model_oof"}}
    options.update(kwargs)
    return search.evaluate_betting_search(frame, directory, **options)


def baseline(report):
    return next(item for item in report["development_search"] if item["rule"]["rule_id"] == "baseline_top1")


def test_finite_search_has_baseline_provenance_and_immutable_outputs(tmp_path):
    report = evaluate(fixture_frame(), tmp_path / "run")
    assert len(report["development_search"]) == 52 <= 60
    assert baseline(report)["rule"]["ranks"] == [1]
    assert report["market_policy"] == "retrospective_market_proxy"
    assert report["score_is_probability"] is False
    assert report["expected_value_estimated"] is False
    assert report["automatic_purchase"] is False
    assert report["production_rule_activated"] is False
    assert report["profitability_guaranteed"] is False
    assert report["live_trading_reproduction"] is False
    assert report["input_audit"]["oof_status"] == "date_boundary_checked"
    frozen_bytes = (tmp_path / "run/frozen_rule.json").read_bytes()
    assert hashlib.sha256(frozen_bytes).hexdigest() == report["frozen_rule_sha256"]
    assert (tmp_path / "run/frozen_rule.sha256").read_text().strip() == report["frozen_rule_sha256"]
    assert json.loads((tmp_path / "run/betting_search_report.json").read_text(encoding="utf-8"))["model_id"] == "fixed-model-01"
    with pytest.raises(FileExistsError):
        evaluate(fixture_frame(), tmp_path / "run")
    assert (tmp_path / "run/frozen_rule.json").read_bytes() == frozen_bytes


def test_future_market_results_scores_cannot_change_development_selection(tmp_path):
    frame = fixture_frame()
    before = evaluate(frame, tmp_path / "before")
    changed = frame.copy()
    future = changed["race_date"].str[:4].astype(int).ge(2023)
    changed.loc[future, "prediction"] *= -1000
    changed.loc[future, "odds"] *= 100
    changed.loc[future, "popularity"] = 4 - changed.loc[future, "popularity"]
    changed.loc[future & changed["finish"].eq(1), "tansho_payout"] = 999999
    after = evaluate(changed, tmp_path / "after")
    assert before["selection"] == after["selection"]
    assert before["development_search"] == after["development_search"]
    assert before["frozen_rule_sha256"] == after["frozen_rule_sha256"]
    assert before["source_predictions_sha256"] != after["source_predictions_sha256"]
    assert before["confirmation"]["selected"] != after["confirmation"]["selected"]


def test_frozen_rule_is_written_before_confirmation_or_retrospective_evaluation(tmp_path, monkeypatch):
    original = search._evaluate_rule
    observed = []

    def instrumented(frame, rule, years, bootstrap):
        if years != (2021, 2022):
            frozen = json.loads((tmp_path / "run/frozen_rule.json").read_text(encoding="utf-8"))
            assert (tmp_path / "run/frozen_rule.sha256").exists()
            assert rule in (frozen["selected_rule"], frozen["baseline_rule"])
            observed.append((years, rule["rule_id"]))
        return original(frame, rule, years, bootstrap)

    monkeypatch.setattr(search, "_evaluate_rule", instrumented)
    report = evaluate(fixture_frame(), tmp_path / "run")
    assert len(observed) == 4  # baseline + selected, evaluated once in each later phase
    assert report["confirmation"]["rule_reselected"] is False
    assert report["known_reused_retrospective"]["unseen"] is False


def test_winner_only_payouts_count_confirmed_losses_as_zero(tmp_path):
    report = evaluate(fixture_frame(), tmp_path / "run")
    metrics = baseline(report)["metrics"]
    assert metrics["bet_count"] == 12
    assert metrics["turnover_yen"] == 1200
    assert metrics["gross_return_yen"] == 1800
    assert metrics["net_profit_yen"] == 600
    assert metrics["gross_return_rate_percent"] == 150
    assert metrics["net_roi_percent"] == 50
    assert metrics["payout_missing_races"] == 0
    assert metrics["race_coverage"] == 1


def test_unknown_winner_payout_nulls_full_rule_metrics_and_ci_even_if_pick_lost(tmp_path):
    frame = fixture_frame()
    missing = frame["race_id"].eq("2021-2") & frame["finish"].eq(1)
    frame.loc[missing, "tansho_payout"] = np.nan  # top1 loses this race, still require race payout completeness
    report = evaluate(frame, tmp_path / "run")
    item = baseline(report)
    metrics = item["metrics"]
    assert metrics["payout_missing_races"] == 1
    for key in ("gross_return_yen", "net_profit_yen", "gross_return_rate_percent", "net_roi_percent", "max_drawdown_yen"):
        assert metrics[key] is None
    assert metrics["date_bootstrap_95_ci"]["metrics"]["net_roi_percent"]["lower"] is None
    assert metrics["date_bootstrap_95_ci"]["metrics"]["net_profit_yen"]["lower"] is None
    assert metrics["verified_subset"]["bet_count"] == 11
    assert metrics["verified_subset"]["turnover_yen"] == 1100
    assert metrics["verified_subset"]["gross_return_yen"] == 1800
    assert metrics["verified_subset"]["not_full_rule_performance"] is True
    assert "unverified_winner_payout" in item["rejection_reasons"]


def test_dead_heat_requires_payout_for_every_winner(tmp_path):
    frame = fixture_frame()
    frame.loc[frame["race_id"].eq("2021-1") & frame["horse_number"].eq(2), "finish"] = 1
    report = evaluate(frame, tmp_path / "run")
    assert baseline(report)["metrics"]["net_roi_percent"] is None
    frame.loc[frame["race_id"].eq("2021-1") & frame["horse_number"].eq(2), "tansho_payout"] = 250
    verified = evaluate(frame, tmp_path / "verified")
    assert baseline(verified)["metrics"]["net_roi_percent"] == 50


def test_rank_two_three_rule_uses_100_yen_per_ticket(tmp_path):
    report = evaluate(fixture_frame(), tmp_path / "run")
    item = next(item for item in report["development_search"] if item["rule"]["rule_id"] == "rank2_3")
    assert item["metrics"]["bet_count"] == 24
    assert item["metrics"]["bet_race_count"] == 12
    assert item["metrics"]["turnover_yen"] == 2400
    assert item["metrics"]["gross_return_yen"] == 4800


def test_score_gap_thresholds_are_development_quantiles_only(tmp_path):
    frame = fixture_frame()
    frame.loc[frame["race_date"].str.startswith("2026"), "prediction"] *= 100000
    report = evaluate(frame, tmp_path / "run")
    rule = next(item["rule"] for item in report["development_search"] if item["rule"]["rule_id"] == "rank1_gap50")
    assert rule["gap_min"] == pytest.approx(4.5)


def test_missing_market_information_is_not_included_by_exclusion_filters(tmp_path):
    frame = fixture_frame()
    frame.loc[frame["horse_number"].eq(2), ["odds", "popularity"]] = np.nan
    report = evaluate(frame, tmp_path / "run")
    filtered = [item for item in report["development_search"] if item["rule"]["ranks"] == [2]
                and (item["rule"]["odds_min"] is not None or item["rule"]["popularity_min"] is not None)]
    assert all(item["metrics"]["bet_count"] == 0 for item in filtered)
    assert baseline(report)["metrics"]["bet_count"] == 12


def test_insufficient_counts_decline_selection_without_repeated_search(tmp_path):
    report = evaluate(fixture_frame(), tmp_path / "run", min_bets=10000)
    assert len(report["development_search"]) == 52
    assert report["selection"]["rule"]["rule_id"] == "baseline_top1"
    assert report["selection"]["eligible"] is False
    assert report["selection"]["reason"] == "selection_declined_no_eligible_rule"
    assert all("insufficient_bet_count" in candidate["rejection_reasons"] for candidate in report["development_search"])


def test_annual_instability_rejects_high_raw_roi_rule(tmp_path):
    frame = fixture_frame()
    frame.loc[frame["race_date"].str.startswith("2022") & frame["finish"].eq(1), "tansho_payout"] *= 100
    report = evaluate(frame, tmp_path / "run", max_annual_net_roi_spread_pp=10)
    assert baseline(report)["metrics"]["gross_return_rate_percent"] > 100
    assert "unstable_annual_roi_spread" in baseline(report)["rejection_reasons"]


def test_less_than_100_percent_in_every_year_is_not_reported_as_target_achieved(tmp_path):
    frame = fixture_frame()
    frame.loc[frame["finish"].eq(1), "tansho_payout"] = 100
    report = evaluate(frame, tmp_path / "run")
    assert report["selection"]["development_all_years_observed_gross_return_at_least_100_percent"] is False
    assert report["profitability_guaranteed"] is False


def test_lower_ci_not_highest_gross_roi_determines_selection(tmp_path, monkeypatch):
    original = search._evaluate_rule

    def different_uncertainty(frame, rule, years, bootstrap):
        metrics = original(frame, rule, years, bootstrap)
        if years == (2021, 2022) and rule["rule_id"] in ("baseline_top1", "rank2", "rank3"):
            lower, gross = {"baseline_top1": (-20, 150), "rank2": (10, 200), "rank3": (-50, 9999)}[rule["rule_id"]]
            metrics["date_bootstrap_95_ci"]["metrics"]["net_roi_percent"]["lower"] = lower
            metrics["gross_return_rate_percent"] = gross
        elif years == (2021, 2022):
            metrics["bet_count"] = 0
        return metrics

    monkeypatch.setattr(search, "_evaluate_rule", different_uncertainty)
    report = evaluate(fixture_frame(), tmp_path / "run")
    assert report["selection"]["rule"]["rule_id"] == "rank2"


def test_daily_settlement_drawdown_not_runner_order():
    bets = pd.DataFrame({"race_date": ["20210101", "20210101", "20210102", "20210103", "20210104"],
                         "_return_yen": [0.0, 500.0, 0.0, 0.0, 400.0]})
    assert search._drawdown(bets) == 200.0
    assert search._drawdown(bets.iloc[::-1]) == 200.0


def test_date_cluster_bootstrap_uses_one_weight_for_same_day_races(tmp_path):
    frame = fixture_frame()
    normalized, _ = search._normalize(frame)
    days, weights = search._bootstrap_plan(normalized, 100, 42)
    assert weights.shape == (100, len(days))
    np.testing.assert_array_equal(weights.sum(axis=1), np.full(100, len(days)))
    first = evaluate(frame, tmp_path / "first")
    second = evaluate(frame.sample(frac=1, random_state=20), tmp_path / "second")
    assert first["frozen_rule_sha256"] == second["frozen_rule_sha256"]
    assert first["development_search"] == second["development_search"]
    assert baseline(first)["metrics"]["date_bootstrap_95_ci"]["unit"] == "race_date"


@pytest.mark.parametrize("kind", ["duplicate", "missing_runner", "nonfinite_score", "same_day_training", "future_training"])
def test_invalid_or_leaky_oof_cohort_is_rejected(tmp_path, kind):
    frame = fixture_frame()
    if kind == "duplicate":
        frame = pd.concat([frame, frame.iloc[:1]], ignore_index=True)
    elif kind == "missing_runner":
        frame = frame.iloc[1:]
    elif kind == "nonfinite_score":
        frame.loc[0, "prediction"] = np.nan
    elif kind == "same_day_training":
        frame.loc[0, "fold_train_date_to"] = frame.loc[0, "race_date"]
    else:
        frame.loc[0, "fold_train_date_to"] = "20270101"
    with pytest.raises(ValueError):
        evaluate(frame, tmp_path / kind)
    assert not (tmp_path / kind).exists()


@pytest.mark.parametrize("kwargs", [
    {"development_years": (2021, 2023)}, {"confirmation_years": (2022, 2023)},
    {"retrospective_years": ()}, {"bootstrap_samples": 0}, {"payout_source": "final_odds"},
    {"min_bets": 0}, {"min_dates": 1}, {"max_annual_net_roi_spread_pp": float("nan")},
])
def test_invalid_or_overlapping_policy_is_rejected(tmp_path, kwargs):
    with pytest.raises(ValueError):
        evaluate(fixture_frame(), tmp_path / "bad", **kwargs)


def test_no_provenance_boundary_is_honestly_reported(tmp_path):
    frame = fixture_frame().drop(columns=["fold_train_date_to", "num_horses"])
    report = evaluate(frame, tmp_path / "run", prediction_provenance=None)
    assert report["input_audit"]["oof_status"] == "caller_attested_not_independently_verified"
    assert report["input_audit"]["cohort_size_status"] == "observed_cohort_only_no_independent_size_proof"


def test_missing_confirmation_data_is_not_invented(tmp_path):
    frame = fixture_frame()
    frame = frame.loc[frame["race_date"].str[:4].astype(int).le(2022)]
    report = evaluate(frame, tmp_path / "run")
    assert report["confirmation"]["selected"]["eligible_race_count"] == 0
    assert report["confirmation"]["selected"]["net_roi_percent"] is None
    assert report["known_reused_retrospective"]["selected"]["net_roi_percent"] is None


def test_output_cannot_be_under_production_model_or_source_database_directories():
    for protected in (search.REPO_ROOT / "python-api/models", search.REPO_ROOT / "keiba/data"):
        with pytest.raises(ValueError, match="isolated"):
            evaluate(fixture_frame(), protected / "betting-search-test-must-not-create")
