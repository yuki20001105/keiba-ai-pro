"""Training-only search for an unapproved Phase3N wagering candidate."""

from __future__ import annotations

import itertools
import math
from dataclasses import asdict, dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .point_in_time_odds import normalized_market_probability


@dataclass(frozen=True)
class StrategyCondition:
    minimum_expected_value: float
    minimum_probability_edge: float
    minimum_odds: float
    maximum_odds: float
    target_bet_rate: float
    selection_score_floor: float


def _value_frame(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    work["market_probability"] = normalized_market_probability(work)
    work["expected_value"] = work["probability"] * work["odds"]
    work["probability_edge"] = work["probability"] - work["market_probability"]

    return work


def _candidate_per_race(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    ranked = work.sort_values(
        ["race_id", "expected_value", "probability_edge", "horse_id"],
        ascending=[True, False, False, True],
        kind="stable",
    )
    return ranked.groupby("race_id", sort=False).head(1).copy()


def _selected(frame: pd.DataFrame, condition: StrategyCondition) -> pd.DataFrame:
    work = _value_frame(frame)
    eligible = work[
        work["expected_value"].ge(condition.minimum_expected_value)
        & work["probability_edge"].ge(condition.minimum_probability_edge)
        & work["odds"].between(condition.minimum_odds, condition.maximum_odds)
        & work["expected_value"].ge(condition.selection_score_floor)
    ].copy()
    return _candidate_per_race(eligible) if not eligible.empty else eligible


def evaluate_strategy(
    frame: pd.DataFrame,
    condition: StrategyCondition,
    *,
    stake_yen: float = 100.0,
    initial_bankroll_yen: float = 100_000.0,
) -> dict[str, float | int]:
    selected = _selected(frame, condition).sort_values(["race_date", "race_id"])
    race_count = int(frame["race_id"].nunique())
    if selected.empty:
        return {
            "race_count": race_count,
            "bet_count": 0,
            "win_count": 0,
            "bet_rate": 0.0,
            "roi_percent": 0.0,
            "roi_lower_90_percent": -100.0,
            "max_drawdown_percent": 0.0,
        }
    returns = np.where(selected["winner"].eq(1), selected["odds"] * stake_yen, 0.0)
    profit_units = returns / stake_yen - 1.0
    bankroll = initial_bankroll_yen + np.cumsum(returns - stake_yen)
    peaks = np.maximum.accumulate(np.r_[initial_bankroll_yen, bankroll])[:-1]
    drawdown = (
        np.divide(
            np.maximum(peaks - bankroll, 0.0),
            np.maximum(peaks, 1.0),
        )
        * 100.0
    )
    standard_error = (
        float(np.std(profit_units, ddof=1) / math.sqrt(len(profit_units)))
        if len(profit_units) > 1
        else float("inf")
    )
    roi = float(np.mean(profit_units) * 100.0)
    return {
        "race_count": race_count,
        "bet_count": int(len(selected)),
        "win_count": int(selected["winner"].sum()),
        "bet_rate": float(len(selected) / race_count) if race_count else 0.0,
        "roi_percent": roi,
        "roi_lower_90_percent": float(roi - 1.645 * standard_error * 100.0),
        "max_drawdown_percent": float(drawdown.max()),
    }


def _grid(search_space: dict[str, Iterable[float]]) -> Iterable[tuple[float, ...]]:
    keys = (
        "minimum_expected_values",
        "minimum_probability_edges",
        "minimum_odds",
        "maximum_odds",
        "target_bet_rates",
    )
    missing = set(keys) - set(search_space)
    if missing:
        raise ValueError(
            "strategy search space is missing: " + ", ".join(sorted(missing))
        )
    return itertools.product(*(search_space[key] for key in keys))


def select_nested_strategy(
    inner_oof: pd.DataFrame,
    *,
    search_space: dict[str, Iterable[float]],
    minimum_bets: int = 100,
    maximum_drawdown_percent: float = 20.0,
) -> dict[str, Any]:
    """Choose a condition using inner OOF labels only.

    The selected score floor is learned from inner candidates and carried as a
    fixed numeric threshold to outer evaluation.  No outer rows are accepted
    by this function.
    """

    required = {
        "race_id",
        "horse_id",
        "race_date",
        "winner",
        "probability",
        "odds",
        "base_prediction_is_oof",
        "probability_prediction_is_oof",
    }
    missing = required - set(inner_oof.columns)
    if missing:
        raise ValueError(
            "nested strategy input is missing: " + ", ".join(sorted(missing))
        )
    if not inner_oof["base_prediction_is_oof"].eq(True).all():  # noqa: E712
        raise ValueError("nested strategy selection requires inner OOF rows only")
    if not inner_oof["probability_prediction_is_oof"].eq(True).all():  # noqa: E712
        raise ValueError(
            "nested strategy selection requires meta-level OOF probabilities"
        )
    value_frame = _value_frame(inner_oof)
    evaluated: list[dict[str, Any]] = []
    for minimum_ev, minimum_edge, minimum_odds, maximum_odds, target_rate in _grid(
        search_space
    ):
        if not 0 < float(target_rate) <= 1:
            raise ValueError("target_bet_rates must be in (0, 1]")
        if float(minimum_odds) > float(maximum_odds):
            continue
        structural = value_frame[
            value_frame["probability_edge"].ge(float(minimum_edge))
            & value_frame["odds"].between(float(minimum_odds), float(maximum_odds))
        ]
        candidates = (
            _candidate_per_race(structural) if not structural.empty else structural
        )
        score_floor = (
            float(candidates["expected_value"].quantile(1.0 - float(target_rate)))
            if not candidates.empty
            else float("inf")
        )
        condition = StrategyCondition(
            minimum_expected_value=float(minimum_ev),
            minimum_probability_edge=float(minimum_edge),
            minimum_odds=float(minimum_odds),
            maximum_odds=float(maximum_odds),
            target_bet_rate=float(target_rate),
            selection_score_floor=score_floor,
        )
        metrics = evaluate_strategy(inner_oof, condition)
        feasible = (
            int(metrics["bet_count"]) >= minimum_bets
            and float(metrics["max_drawdown_percent"]) <= maximum_drawdown_percent
        )
        evaluated.append(
            {"condition": asdict(condition), "metrics": metrics, "feasible": feasible}
        )
    feasible_rows = [row for row in evaluated if row["feasible"]]
    if not feasible_rows:
        return {
            "selected": None,
            "selection_status": "no-feasible-inner-oof-condition",
            "evaluated_condition_count": len(evaluated),
            "deployment_eligible": False,
        }
    best = max(
        feasible_rows,
        key=lambda row: (
            row["metrics"]["roi_lower_90_percent"],
            row["metrics"]["roi_percent"],
            -row["metrics"]["bet_rate"],
        ),
    )
    return {
        "selected": best,
        "selection_status": "research-candidate-selected-from-inner-oof",
        "evaluated_condition_count": len(evaluated),
        "deployment_eligible": False,
    }
