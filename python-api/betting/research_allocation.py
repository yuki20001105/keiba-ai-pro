"""Offline allocation sensitivity, not executable recommendations or Kelly."""
from __future__ import annotations

import numpy as np
import pandas as pd

from experiments.evaluation import race_evaluation_records


POLICIES = ("uniform", "score_gap_tiers")


def allocation_policy(annual_budget_yen: int = 10_000_000, planned_races: int = 3600) -> dict:
    if (type(annual_budget_yen) is not int or annual_budget_yen < 100
            or annual_budget_yen % 100 or type(planned_races) is not int or planned_races < 1):
        raise ValueError("Positive planned races and a positive budget in 100-yen units are required")
    flat = annual_budget_yen // (planned_races * 100) * 100
    base = annual_budget_yen // (planned_races * 3 * 100) * 100
    if base < 100:
        raise ValueError("Budget cannot fund the predeclared three-tier schedule")
    return {
        "annual_turnover_cap_yen": annual_budget_yen,
        "planned_races": planned_races,
        "uniform_stake_yen": flat,
        "tier_base_stake_yen": base,
        "standardized_gap_boundaries": [0.25, 0.75],
        "tier_weights": [1, 2, 3],
        "thresholds_fit_on_evaluation_data": False,
        "ranking_scores_are_probabilities": False,
        "production_eligible": False,
        "reference_only": True,
    }


def allocate_races(predictions: pd.DataFrame, policy: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Assign amounts without using results, market, future races or annual totals."""
    records = race_evaluation_records(predictions, task="ranking")
    score = predictions[["race_id", "horse_number", "prediction"]].copy()
    score = score.sort_values(
        ["race_id", "prediction", "horse_number"], ascending=[True, False, True], kind="stable",
    )
    score["_rank"] = score.groupby("race_id", sort=False).cumcount()
    top = score.loc[score["_rank"].lt(2)].pivot(
        index="race_id", columns="_rank", values="prediction",
    )
    gap = top[0] - top[1]
    scale = score.groupby("race_id")["prediction"].std(ddof=0)
    standardized = gap.div(scale.where(scale.gt(0))).replace([np.inf, -np.inf], np.nan)
    records["score_gap"] = records["race_id"].map(gap)
    records["standardized_gap"] = records["race_id"].map(standardized)
    low, high = policy["standardized_gap_boundaries"]
    records["tier_weight"] = np.select(
        [records["standardized_gap"].ge(high), records["standardized_gap"].ge(low),
         records["standardized_gap"].gt(0)],
        [3, 2, 1], default=0,
    )
    records["tier_reason"] = records["tier_weight"].map({
        0: "tied_or_constant_scores_skip", 1: "small_score_gap",
        2: "medium_score_gap", 3: "large_score_gap",
    })
    records = records.sort_values(["race_date", "race_id"], kind="stable").reset_index(drop=True)
    allocations = []
    for name in POLICIES:
        requested = (
            pd.Series(policy["uniform_stake_yen"], index=records.index)
            if name == "uniform"
            else records["tier_weight"] * policy["tier_base_stake_yen"]
        )
        remaining = {}
        for index, race in records.iterrows():
            year = race["year"]
            available = remaining.get(year, policy["annual_turnover_cap_yen"])
            stake = min(int(requested.loc[index]), available)
            remaining[year] = available - stake
            allocations.append({
                "policy": name, "race_id": race["race_id"], "race_date": race["race_date"],
                "year": year, "picked_horse_number": race["picked_horse_number"],
                "tier_weight": int(race["tier_weight"]), "tier_reason": race["tier_reason"],
                "score_gap": float(race["score_gap"]),
                "standardized_gap": (
                    float(race["standardized_gap"]) if pd.notna(race["standardized_gap"]) else None
                ),
                "research_stake_yen": stake,
                "budget_remaining_yen": remaining[year],
                "production_buy_amount_yen": 0,
                "production_block_reason": "uncalibrated_score_and_unverified_prebet_market",
                "payout_verified": bool(race["payout_verified"]),
                "observed_win": bool(race["top1_win"]),
                "observed_return_per_100_yen": (
                    float(race["win_return_yen"]) if race["payout_verified"] else None
                ),
            })
    return pd.DataFrame(allocations), records


def summarize_allocations(allocations: pd.DataFrame, policy: dict) -> list[dict]:
    summaries = []
    for (name, year), group in allocations.groupby(["policy", "year"], sort=True):
        bets = group.loc[group["research_stake_yen"].gt(0)].copy()
        turnover = int(bets["research_stake_yen"].sum())
        missing = int((~bets["payout_verified"]).sum())
        complete = missing == 0
        payout = profit = rate = drawdown = None
        if complete:
            bets["_net"] = bets["research_stake_yen"] * (
                bets["observed_return_per_100_yen"] / 100 - 1
            )
            profit = float(bets["_net"].sum())
            payout = profit + turnover
            rate = payout / turnover * 100 if turnover else None
            daily = bets.groupby("race_date", sort=True)["_net"].sum().cumsum().to_numpy()
            equity = np.r_[0.0, daily]
            drawdown = float((np.maximum.accumulate(equity) - equity).max())
        summaries.append({
            "policy": name, "year": str(year), "eligible_races": len(group),
            "bet_races": len(bets), "skipped_races": len(group) - len(bets),
            "turnover_yen": turnover,
            "unused_turnover_cap_yen": policy["annual_turnover_cap_yen"] - turnover,
            "gross_payout_yen": payout, "net_profit_yen": profit,
            "return_rate_percent": rate,
            "net_roi_percent": rate - 100 if rate is not None else None,
            "missing_payout_bet_races": missing,
            "daily_settled_max_drawdown_yen": drawdown,
            "hit_rate_percent": float(bets["observed_win"].mean() * 100) if len(bets) else None,
        })
    return summaries


def annual_flat_equivalents(records: pd.DataFrame, annual_turnover_yen: int) -> list[dict]:
    """Pure monetary scaling, not an executable all-year allocation policy."""
    result = []
    for year, group in records.groupby("year", sort=True):
        complete = bool(group["payout_verified"].eq(1).all())
        rate = float(group["win_return_yen"].mean()) if complete else None
        payout = annual_turnover_yen * rate / 100 if rate is not None else None
        result.append({
            "year": str(year), "race_count": len(group), "return_rate_percent": rate,
            "hypothetical_turnover_yen": annual_turnover_yen,
            "hypothetical_payout_yen": payout,
            "hypothetical_profit_yen": payout - annual_turnover_yen if payout is not None else None,
            "executable_policy": False,
            "caveat": "Linear flat-stake scaling; no ticket rounding, taxes or market impact.",
        })
    return result


def quality_summary(frame: pd.DataFrame, manifest: dict, diagnostics: dict, years: list[int]) -> dict:
    development = frame.loc[
        frame["race_date"].between(
            f'{diagnostics["train_start_year"]}0101', f"{max(years)}1231",
        )
    ]
    columns = manifest["feature_sets"]["M03"]
    categorical = set(manifest["categorical_features"])
    annual = {}
    for year, group in development.groupby(development["race_date"].str[:4], sort=True):
        missing, nonpositive = {}, {}
        for name in columns:
            values = group[name]
            if name in categorical:
                absent = values.isna() | values.astype("string").str.strip().eq("")
            else:
                values = pd.to_numeric(values, errors="coerce")
                absent = values.isna() | ~np.isfinite(values)
            missing[name] = {"count": int(absent.sum()), "fraction": float(absent.mean())}
            if name in {"safe_age", "safe_burden_weight", "safe_horse_weight"}:
                nonpositive[name] = int(values.le(0).sum())
        annual[str(year)] = {
            "runner_rows": len(group), "races": int(group["race_id"].nunique()),
            "observed_month_count": int(group["race_date"].str[:6].nunique()),
            "missing": missing, "nonpositive_values": nonpositive,
        }
    return {
        "source_audit": manifest["source_audit"],
        "training_eligibility": manifest["training_eligibility"],
        "complete_cohort_exclusions": manifest["exclusion_reason_counts"]["complete_runner_target_cohort"],
        "history_rows": manifest["history_row_count"], "history_races": manifest["history_race_count"],
        "stored_rows": manifest["row_count"], "stored_races": manifest["race_count"],
        "diagnostic_rows": len(development), "diagnostic_races": int(development["race_id"].nunique()),
        "development_missing_reasons": {
            name: diagnostics["features"][name]["missing_reason_counts"] for name in columns
        },
        "by_year": annual, "point_in_time_audit": manifest["point_in_time_audit"],
        "official_calendar_completeness": "not_verified; a nonempty month does not prove complete coverage",
        "prediction_time_field_availability": "not_verified; no per-field observed_at in this dataset",
        "calibrated_win_probability": "not_available_for_ranking_model",
        "prebet_market_join": "not_available_in_these_experiment_artifacts; other stores not audited",
        "unseen_validation": False, "promotion_eligible": False,
    }
