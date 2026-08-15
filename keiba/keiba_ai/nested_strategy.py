"""Training-only search for an unapproved Phase3N wagering candidate."""

from __future__ import annotations

import itertools
import math
import statistics
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
) -> dict[str, Any]:
    selected = _selected(frame, condition).sort_values(["race_date", "race_id"])
    race_count = int(frame["race_id"].nunique())
    metrics = _settled_metrics(
        selected,
        race_count=race_count,
        stake_yen=stake_yen,
        initial_bankroll_yen=initial_bankroll_yen,
    )
    metrics["maximum_wagers_in_any_race"] = (
        int(selected.groupby("race_id").size().max()) if not selected.empty else 0
    )
    yearly: dict[str, dict[str, float | int | str]] = {}
    if not selected.empty:
        selected = selected.copy()
        selected["evaluation_year"] = pd.to_datetime(
            selected["race_date"], errors="raise"
        ).dt.year
        all_years = sorted(
            pd.to_datetime(frame["race_date"], errors="raise").dt.year.unique()
        )
        frame_years = pd.to_datetime(frame["race_date"], errors="raise").dt.year
        for year in all_years:
            year_selected = selected[selected["evaluation_year"].eq(year)]
            year_race_count = int(frame.loc[frame_years.eq(year), "race_id"].nunique())
            yearly[str(int(year))] = _settled_metrics(
                year_selected,
                race_count=year_race_count,
                stake_yen=stake_yen,
                initial_bankroll_yen=initial_bankroll_yen,
            )
    metrics["yearly_metrics"] = yearly
    yearly_with_bets = [value for value in yearly.values() if value["bet_count"] > 0]
    metrics["years_with_bets"] = len(yearly_with_bets)
    metrics["profitable_year_rate"] = (
        float(
            sum(value["roi_percent"] > 0 for value in yearly_with_bets)
            / len(yearly_with_bets)
        )
        if yearly_with_bets
        else 0.0
    )
    metrics["minimum_annual_roi_percent"] = (
        float(min(value["roi_percent"] for value in yearly_with_bets))
        if yearly_with_bets
        else 0.0
    )
    return metrics


def _settled_metrics(
    selected: pd.DataFrame,
    *,
    race_count: int,
    stake_yen: float,
    initial_bankroll_yen: float,
) -> dict[str, float | int | str]:
    if selected.empty:
        return {
            "race_count": race_count,
            "bet_count": 0,
            "win_count": 0,
            "bet_rate": 0.0,
            "roi_percent": 0.0,
            "roi_lower_90_percent": -100.0,
            "roi_upper_90_percent": 100.0,
            "roi_lower_95_percent": -100.0,
            "roi_upper_95_percent": 100.0,
            "roi_confidence_interval_method": "normal-approximation-per-wager",
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
        else None
    )
    roi = float(np.mean(profit_units) * 100.0)
    lower_90 = (
        roi - 1.645 * standard_error * 100.0
        if standard_error is not None
        else -100.0
    )
    upper_90 = (
        roi + 1.645 * standard_error * 100.0
        if standard_error is not None
        else max(roi, 100.0)
    )
    lower_95 = (
        roi - 1.96 * standard_error * 100.0
        if standard_error is not None
        else -100.0
    )
    upper_95 = (
        roi + 1.96 * standard_error * 100.0
        if standard_error is not None
        else max(roi, 100.0)
    )
    return {
        "race_count": race_count,
        "bet_count": int(len(selected)),
        "win_count": int(selected["winner"].sum()),
        "bet_rate": float(len(selected) / race_count) if race_count else 0.0,
        "roi_percent": roi,
        "roi_lower_90_percent": float(lower_90),
        "roi_upper_90_percent": float(upper_90),
        "roi_lower_95_percent": float(lower_95),
        "roi_upper_95_percent": float(upper_95),
        "roi_confidence_interval_method": "normal-approximation-per-wager",
        "max_drawdown_percent": float(drawdown.max()),
    }


def evaluate_baseline_strategy(
    frame: pd.DataFrame,
    *,
    stake_yen: float = 100.0,
    initial_bankroll_yen: float = 100_000.0,
) -> dict[str, float | int | str]:
    """Evaluate the approved lowest-valid-win-odds, one-wager baseline."""

    required = {"race_id", "horse_id", "race_date", "winner", "odds"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError("baseline input is missing: " + ", ".join(sorted(missing)))
    work = frame.copy()
    work["odds"] = pd.to_numeric(work["odds"], errors="coerce")
    if work["odds"].isna().any() or work["odds"].le(1.0).any():
        raise ValueError("baseline odds must be finite and greater than 1.0")
    selected = (
        work.sort_values(
            ["race_id", "odds", "horse_id"],
            ascending=[True, True, True],
            kind="stable",
        )
        .groupby("race_id", sort=False)
        .head(1)
        .sort_values(["race_date", "race_id"])
    )
    metrics = _settled_metrics(
        selected,
        race_count=int(frame["race_id"].nunique()),
        stake_yen=stake_yen,
        initial_bankroll_yen=initial_bankroll_yen,
    )
    metrics["maximum_wagers_in_any_race"] = (
        int(selected.groupby("race_id").size().max()) if not selected.empty else 0
    )
    return metrics


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
    minimum_bet_rate: float = 0.05,
    maximum_bet_rate: float = 0.15,
    minimum_baseline_roi_delta_percent: float = 1.0,
    minimum_years_with_bets: int = 1,
    minimum_profitable_year_rate: float = 0.0,
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
    if not 0 <= minimum_bet_rate <= maximum_bet_rate <= 1:
        raise ValueError("bet-rate bounds must satisfy 0 <= minimum <= maximum <= 1")
    if minimum_bets <= 0:
        raise ValueError("minimum_bets must be positive")
    if minimum_years_with_bets <= 0:
        raise ValueError("minimum_years_with_bets must be positive")
    if not 0 <= minimum_profitable_year_rate <= 1:
        raise ValueError("minimum_profitable_year_rate must be in [0, 1]")
    value_frame = _value_frame(inner_oof)
    baseline_metrics = evaluate_baseline_strategy(inner_oof)
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
        metrics["baseline_roi_percent"] = float(baseline_metrics["roi_percent"])
        metrics["baseline_roi_delta_percent"] = float(
            metrics["roi_percent"] - baseline_metrics["roi_percent"]
        )
        checks = {
            "minimum_bets": int(metrics["bet_count"]) >= minimum_bets,
            "maximum_drawdown": float(metrics["max_drawdown_percent"])
            <= maximum_drawdown_percent,
            "minimum_bet_rate": float(metrics["bet_rate"]) >= minimum_bet_rate,
            "maximum_bet_rate": float(metrics["bet_rate"]) <= maximum_bet_rate,
            "baseline_roi_delta": float(metrics["baseline_roi_delta_percent"])
            >= minimum_baseline_roi_delta_percent,
            "minimum_years_with_bets": int(metrics["years_with_bets"])
            >= minimum_years_with_bets,
            "minimum_profitable_year_rate": float(metrics["profitable_year_rate"])
            >= minimum_profitable_year_rate,
            "maximum_one_wager_per_race": int(
                metrics["maximum_wagers_in_any_race"]
            )
            <= 1,
        }
        feasible = all(checks.values())
        evaluated.append(
            {
                "condition": asdict(condition),
                "metrics": metrics,
                "feasible": feasible,
                "feasibility_checks": checks,
                "rejection_reasons": [
                    name for name, passed in checks.items() if not passed
                ],
            }
        )
    _attach_neighborhood_robustness(evaluated, search_space)
    feasible_rows = [row for row in evaluated if row["feasible"]]
    if not feasible_rows:
        return {
            "selected": None,
            "selection_status": "no-feasible-inner-oof-condition",
            "evaluated_condition_count": len(evaluated),
            "evaluated_conditions": evaluated,
            "baseline_metrics": baseline_metrics,
            "selection_constraints": {
                "minimum_bets": minimum_bets,
                "maximum_drawdown_percent": maximum_drawdown_percent,
                "minimum_bet_rate": minimum_bet_rate,
                "maximum_bet_rate": maximum_bet_rate,
                "minimum_baseline_roi_delta_percent": minimum_baseline_roi_delta_percent,
                "minimum_years_with_bets": minimum_years_with_bets,
                "minimum_profitable_year_rate": minimum_profitable_year_rate,
                "maximum_wagers_per_race": 1,
            },
            "deployment_eligible": False,
        }
    best = max(
        feasible_rows,
        key=lambda row: (
            row["metrics"]["roi_lower_95_percent"],
            -row["metrics"]["max_drawdown_percent"],
            row["metrics"]["baseline_roi_delta_percent"],
            row["metrics"]["profitable_year_rate"],
            row["metrics"]["minimum_annual_roi_percent"],
            row["neighborhood_robustness"]["feasible_neighbor_ratio"],
            row["neighborhood_robustness"]["median_neighbor_roi_lower_95_percent"],
            row["metrics"]["roi_percent"],
            -row["metrics"]["bet_rate"],
        ),
    )
    return {
        "selected": best,
        "selection_status": "research-candidate-selected-from-inner-oof",
        "evaluated_condition_count": len(evaluated),
        "evaluated_conditions": evaluated,
        "baseline_metrics": baseline_metrics,
        "selection_constraints": {
            "minimum_bets": minimum_bets,
            "maximum_drawdown_percent": maximum_drawdown_percent,
            "minimum_bet_rate": minimum_bet_rate,
            "maximum_bet_rate": maximum_bet_rate,
            "minimum_baseline_roi_delta_percent": minimum_baseline_roi_delta_percent,
            "minimum_years_with_bets": minimum_years_with_bets,
            "minimum_profitable_year_rate": minimum_profitable_year_rate,
            "maximum_wagers_per_race": 1,
        },
        "selection_ranking": [
            "roi_lower_95_percent",
            "lower_max_drawdown_percent",
            "baseline_roi_delta_percent",
            "profitable_year_rate",
            "minimum_annual_roi_percent",
            "feasible_neighbor_ratio",
            "median_neighbor_roi_lower_95_percent",
            "roi_percent",
            "lower_bet_rate",
        ],
        "deployment_eligible": False,
    }


def _attach_neighborhood_robustness(
    evaluated: list[dict[str, Any]],
    search_space: dict[str, Iterable[float]],
) -> None:
    """Attach one-grid-step sensitivity evidence to every evaluated condition."""

    condition_fields = (
        ("minimum_expected_value", "minimum_expected_values"),
        ("minimum_probability_edge", "minimum_probability_edges"),
        ("minimum_odds", "minimum_odds"),
        ("maximum_odds", "maximum_odds"),
        ("target_bet_rate", "target_bet_rates"),
    )
    grid_indices = {
        field: {float(value): index for index, value in enumerate(search_space[key])}
        for field, key in condition_fields
    }

    def coordinates(row: dict[str, Any]) -> tuple[int, ...]:
        return tuple(
            grid_indices[field][float(row["condition"][field])]
            for field, _ in condition_fields
        )

    rows_with_coordinates = [(row, coordinates(row)) for row in evaluated]
    for row, current in rows_with_coordinates:
        neighbors = [
            other
            for other, candidate in rows_with_coordinates
            if sum(abs(left - right) for left, right in zip(current, candidate)) == 1
        ]
        feasible_neighbors = [other for other in neighbors if other["feasible"]]
        lower_bounds = [
            float(other["metrics"]["roi_lower_95_percent"])
            for other in feasible_neighbors
        ]
        row["neighborhood_robustness"] = {
            "neighbor_count": len(neighbors),
            "feasible_neighbor_count": len(feasible_neighbors),
            "feasible_neighbor_ratio": (
                float(len(feasible_neighbors) / len(neighbors)) if neighbors else 0.0
            ),
            "median_neighbor_roi_lower_95_percent": (
                float(statistics.median(lower_bounds)) if lower_bounds else -100.0
            ),
            "minimum_neighbor_roi_lower_95_percent": (
                float(min(lower_bounds)) if lower_bounds else -100.0
            ),
        }
