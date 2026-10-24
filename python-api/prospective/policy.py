"""Predeclared paper-only win-bet policy. No order placement or app mutation."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

RULE = {
    "schema": "fixed-paper-win-policy-v1", "bet_type": "win", "stake_yen": 100,
    "maximum_tickets_per_race": 1, "minimum_stressed_expected_return": 1.10,
    "odds_multiplier": 0.90, "tie_break": "smaller horse number",
    "selection": "maximum probability * indicative win odds; strict greater-than threshold",
    "score_is_probability": False, "live_bets": False, "tuned_on_confirmation": False,
    "odds_are_locked_execution_price": False,
}


def decide(runners: list[dict], *, odds_multiplier: float = .9) -> dict:
    """Return an intended PAPER decision on complete probabilities, not scores.

    The caller must separately verify input timing/inference. This pure function
    deliberately makes no assertion of executable odds or pre-race provenance.
    """
    if odds_multiplier not in (.9, 1.0):
        raise ValueError("only predeclared base/stress policies are supported")
    if not runners:
        return {"decision": "skip", "reason": "empty_race"}
    numbers, probabilities, odds = [], [], []
    for horse in runners:
        number = horse.get("horse_number")
        if isinstance(number, bool) or not isinstance(number, (int, np.integer)) or number < 1:
            return {"decision": "skip", "reason": "invalid_runner_identity"}
        numbers.append(int(number))
        probability = horse.get("win_probability")
        if isinstance(probability, bool) or not isinstance(probability, (int, float, np.number)) or not math.isfinite(probability) or not 0 <= probability <= 1:
            return {"decision": "skip", "reason": "invalid_probability"}
        probabilities.append(float(probability))
        value = horse.get("odds")
        if isinstance(value, bool) or not isinstance(value, (int, float, np.number)) or not math.isfinite(value) or value < 1:
            return {"decision": "skip", "reason": "missing_or_invalid_odds"}
        odds.append(float(value))
    if len(numbers) != len(set(numbers)):
        return {"decision": "skip", "reason": "duplicate_runner"}
    if not math.isclose(sum(probabilities), 1, abs_tol=1e-7, rel_tol=0):
        return {"decision": "skip", "reason": "probabilities_do_not_sum_to_one"}
    selected = min(range(len(numbers)), key=lambda i: (-probabilities[i] * odds[i], numbers[i]))
    ev = probabilities[selected] * odds[selected]
    stressed = ev * odds_multiplier
    if stressed <= RULE["minimum_stressed_expected_return"]:
        return {"decision": "skip", "reason": "insufficient_expected_return", "best_stressed_expected_return": stressed}
    return {"decision": "paper_bet", "horse_number": numbers[selected], "stake_yen": 100,
            "win_probability": probabilities[selected], "indicative_odds": odds[selected],
            "expected_return": ev, "stressed_expected_return": stressed, "odds_multiplier": odds_multiplier,
            "live_bet": False, "execution_guaranteed": False}


def _settlement_summary(records: pd.DataFrame) -> dict:
    selected = records.loc[records["decision"].eq("paper_bet")]
    complete = selected.loc[selected["payout_yen"].notna()]
    cost = int(len(selected) * 100)
    missing = int(selected["payout_yen"].isna().sum())
    payout = float(selected["payout_yen"].sum()) if not missing else None
    verified_cost = len(complete) * 100
    # Equity is ordered by day; intra-day sequence is not reconstructible here.
    drawdown = None
    if not missing and len(selected):
        daily = selected.assign(net=selected["payout_yen"] - 100).groupby("race_date")["net"].sum().sort_index()
        equity = np.r_[0., daily.cumsum().to_numpy()]
        drawdown = float(np.max(np.maximum.accumulate(equity) - equity))
    return {"race_count": len(records), "bets": len(selected), "skipped_races": len(records) - len(selected),
            "distinct_bet_dates": int(selected["race_date"].nunique()), "stake_yen": cost,
            "payout_missing_bets": missing, "payout_yen": payout,
            "net_yen": payout - cost if payout is not None else None,
            "gross_return_percent": payout / cost * 100 if cost and payout is not None else None,
            "verified_subset_bets": len(complete),
            "verified_subset_gross_return_percent": float(complete["payout_yen"].sum() / verified_cost * 100) if verified_cost else None,
            "maximum_end_of_day_drawdown_yen": drawdown,
            "skip_reasons": records.loc[records["decision"].eq("skip"), "reason"].value_counts().to_dict()}


def _bootstrap(records: pd.DataFrame, *, samples: int = 500, seed: int = 42) -> list[float] | None:
    selected = records.loc[records["decision"].eq("paper_bet")]
    if selected.empty or selected["payout_yen"].isna().any():
        return None
    # Include no-bet days in resampling; resample whole race days, not horses.
    days = pd.Index(sorted(records["race_date"].unique()))
    payout = selected.groupby("race_date")["payout_yen"].sum().reindex(days, fill_value=0).to_numpy()
    costs = (selected.groupby("race_date").size().reindex(days, fill_value=0) * 100).to_numpy()
    rng = np.random.default_rng(seed)
    estimates = []
    for _ in range(samples):
        index = rng.integers(0, len(days), len(days))
        cost = costs[index].sum()
        if cost:
            estimates.append(float(payout[index].sum() / cost * 100))
    return [float(x) for x in np.quantile(estimates, [.025, .975])] if estimates else None


def evaluate_policy(frame: pd.DataFrame, *, mode: str = "retrospective_market_proxy", odds_multiplier: float = .9) -> tuple[dict, pd.DataFrame]:
    """Evaluate old odds as a clearly non-executable diagnostic, never live ROI.

    Future captured races must be settled by their frozen pre-result decisions;
    this historical evaluator intentionally refuses a prospective mode.
    """
    if mode != "retrospective_market_proxy":
        raise ValueError("historical rows cannot certify prospective execution")
    required = {"race_id", "race_date", "horse_number", "expected_runner_count", "finish", "odds", "tansho_payout", "win_probability"}
    if not required.issubset(frame.columns) or frame.empty:
        raise ValueError("complete probability/evaluation frame required")
    records = []
    for race_id, race in frame.groupby("race_id", sort=True):
        if race["race_date"].nunique() != 1 or race["horse_number"].duplicated().any():
            raise ValueError("inconsistent race identity")
        expected = pd.to_numeric(race["expected_runner_count"], errors="coerce")
        if expected.isna().any() or expected.nunique() != 1 or not expected.eq(len(race)).all() or len(race) < 2:
            raise ValueError("incomplete runner cohort")
        finishes = pd.to_numeric(race["finish"], errors="coerce")
        if finishes.isna().any() or not np.isfinite(finishes).all() or not finishes.eq(np.floor(finishes)).all() or (finishes <= 0).any() or (finishes > len(race)).any():
            decision = {"decision": "skip", "reason": "outcome_incomplete"}
        elif int(finishes.eq(1).sum()) != 1:
            decision = {"decision": "skip", "reason": "non_single_winner"}
        else:
            decision = decide(race[["horse_number", "odds", "win_probability"]].to_dict("records"), odds_multiplier=odds_multiplier)
        row = {"race_id": str(race_id), "race_date": str(race["race_date"].iloc[0]), "payout_yen": None, "reason": None, **decision}
        if decision["decision"] == "paper_bet":
            horse = race.loc[race["horse_number"].eq(decision["horse_number"])].iloc[0]
            if float(horse["finish"]) != 1:
                row["payout_yen"] = 0.
            else:
                payout = horse["tansho_payout"]
                # Never invent a winning payout from historical odds.
                if not isinstance(payout, (bool, np.bool_)) and pd.notna(payout) and np.isfinite(float(payout)) and float(payout) >= 100:
                    row["payout_yen"] = float(payout)
        records.append(row)
    decisions = pd.DataFrame(records)
    overall = _settlement_summary(decisions)
    overall["gross_return_reference_ci95"] = _bootstrap(decisions)
    annual = {str(year): _settlement_summary(part) for year, part in decisions.groupby(decisions.race_date.str[:4])}
    report = {"mode": mode, "rule": {**RULE, "odds_multiplier": odds_multiplier}, "overall": overall, "by_year": annual,
              "future_operational_evidence": False, "inference_asof_inputs_verified": False,
              "warning": "Historical odds observation times are unverified. This is not executable/forward ROI; indicative odds may change.",
              "policy_selected_by_this_result": False, "real_purchase": False}
    return report, decisions
