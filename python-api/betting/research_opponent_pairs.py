"""Bounded, reference-only market-axis partner and ticket comparisons."""
from __future__ import annotations

from itertools import combinations, permutations
import math
from numbers import Integral, Real

import numpy as np
import pandas as pd

SCHEMA = "market-axis-opponent-pairs-v1"
KINDS = ("ワイド", "三連複", "三連単")
INITIAL_BUDGET = 1_000_000
PER_RACE_LIMIT = 1500
SEED = 20261103


def opponent_orders(field: list[dict]) -> tuple[int, list[int], list[int]]:
    if not 5 <= len(field) <= 18:
        raise ValueError("complete_five_to_eighteen_runner_field_required")
    for row in field:
        n, p, odds = row["horse_number"], row["probability"], row["odds"]
        if (isinstance(n, bool) or not isinstance(n, Integral) or not 1 <= n <= 18
                or isinstance(p, bool) or not isinstance(p, Real) or not math.isfinite(p)
                or not 0 <= p <= 1 or isinstance(odds, bool) or not isinstance(odds, Real)
                or not math.isfinite(odds) or odds < 1):
            raise ValueError("invalid_reference_market")
    if (len({r["horse_number"] for r in field}) != len(field)
            or not math.isclose(sum(r["probability"] for r in field), 1, abs_tol=1e-8)):
        raise ValueError("distinct_normalized_complete_runners_required")
    axis = min(field, key=lambda r: (r["odds"], r["horse_number"]))["horse_number"]
    ai = [row for row in sorted(field, key=lambda r: (-r["probability"], r["horse_number"]))
          if row["horse_number"] != axis][:5]
    hole = sorted(ai, key=lambda r: (-r["probability"] * r["odds"], r["horse_number"]))
    return int(axis), [int(r["horse_number"]) for r in ai], [int(r["horse_number"]) for r in hole]


def registry() -> dict[str, dict]:
    result = {}
    for ranking in ("ai", "hole"):
        for pair in combinations(range(1, 6), 2):
            for wide in range(4):
                for trio in (False, True):
                    for mask in range(8):
                        positions = [pos for pos in (1, 2, 3) if mask & (1 << (pos - 1))]
                        points = wide.bit_count() + int(trio) + 2 * len(positions)
                        if not 1 <= points <= 6:
                            continue
                        label = "".join(map(str, positions)) if positions else "0"
                        name = f"{ranking}__r{pair[0]}_{pair[1]}__w{wide}_t{int(trio)}_f{label}"
                        result[name] = {
                            "ranking": ranking, "pair": list(pair), "wide_mask": wide,
                            "trio": trio, "axis_positions": positions, "nominal_points": points,
                        }
    return result


def tickets_from_orders(axis: int, orders: dict[str, list[int]], definition: dict) -> dict | None:
    ranks, wide, positions = definition["pair"], definition["wide_mask"], definition["axis_positions"]
    if (definition["ranking"] not in orders or len(ranks) != 2 or not 1 <= ranks[0] < ranks[1] <= 5
            or wide not in range(4) or len(positions) != len(set(positions))
            or any(pos not in (1, 2, 3) for pos in positions)):
        raise ValueError("invalid_recipe")
    total = int(wide).bit_count() + int(definition["trio"]) + 2 * len(positions)
    if not 1 <= total <= 6:
        raise ValueError("one_to_six_points_required")
    needed = set(ranks) if definition["trio"] or positions else set()
    needed.update(ranks[i] for i in range(2) if wide & (1 << i))
    order = orders[definition["ranking"]]
    if any(rank > len(order) for rank in needed):
        return None
    pair = {rank: order[rank - 1] for rank in needed}
    legs = {}
    if wide:
        legs["ワイド"] = sorted(tuple(sorted((axis, pair[ranks[i]])))
                                 for i in range(2) if wide & (1 << i))
    if definition["trio"]:
        legs["三連複"] = [tuple(sorted((axis, pair[ranks[0]], pair[ranks[1]])))]
    if positions:
        tickets = []
        for position in positions:
            for other in permutations([pair[ranks[0]], pair[ranks[1]]]):
                ticket = list(other)
                ticket.insert(position - 1, axis)
                tickets.append(tuple(ticket))
        legs["三連単"] = sorted(tickets)
    if (sum(map(len, legs.values())) != total
            or any(len(set(t)) != len(t) or axis not in t for tickets in legs.values() for t in tickets)):
        raise ValueError("invalid_axis_book")
    return legs


def select_tickets(field: list[dict], definition: dict) -> dict | None:
    axis, ai, hole = opponent_orders(field)
    return tickets_from_orders(axis, {"ai": ai, "hole": hole}, definition)


def stake_per_point(legs: dict) -> int:
    points = sum(map(len, legs.values()))
    if not 1 <= points <= 6:
        raise ValueError("one_to_six_points_required")
    return 100 * (PER_RACE_LIMIT // (100 * points))


def settle(legs: dict | None, dividends: dict) -> dict:
    kinds, keys, stakes, payouts = [], [], [], []
    if legs:
        stake = stake_per_point(legs)
        for kind, tickets in legs.items():
            if kind not in dividends:
                raise ValueError("verified_official_market_required")
            for ticket in tickets:
                key = "-".join(map(str, ticket))
                payout = dividends[kind].get(key, 0)
                if isinstance(payout, bool) or not isinstance(payout, Integral) or payout < 0:
                    raise ValueError("invalid_official_dividend")
                kinds.append(kind)
                keys.append(key)
                stakes.append(stake)
                payouts.append(int(payout))
    return {
        "ticket_count": len(keys), "cost_yen": sum(stakes),
        "return_yen": sum(s * p // 100 for s, p in zip(stakes, payouts)),
        "hit_ticket_count": sum(p > 0 for p in payouts),
        "bet_types": kinds, "combinations": keys, "stakes_yen": stakes,
        "official_payouts_per100_yen": payouts,
    }


def choose_past_recipe(
    rows: pd.DataFrame, cutoff: str, *, conservative: bool = False,
    min_bets: int = 150, min_dates: int = 20, min_hits: int = 10,
) -> dict:
    if not rows.empty and rows.race_date.max() >= cutoff:
        raise ValueError("future_recipe_results_rejected")
    candidates = []
    for method, part in rows.groupby("method", sort=True):
        bought = part.loc[part.cost_yen.gt(0)]
        if (len(bought) < min_bets or bought.race_date.nunique() < min_dates
                or bought.hit_ticket_count.gt(0).sum() < min_hits):
            continue
        candidates.append({
            "method": method, "bet_races": len(bought), "bet_dates": bought.race_date.nunique(),
            "hit_races": int(bought.hit_ticket_count.gt(0).sum()),
            "mean_points": float(bought.ticket_count.mean()),
            "selection_roi_percent": float(100 * part.return_yen.sum() / part.cost_yen.sum()),
        })
    if not candidates:
        return {"method": None, "score_percent": None, "candidates": []}
    table = pd.DataFrame(candidates).set_index("method")
    if conservative:
        selected = rows.loc[rows.method.isin(table.index)]
        daily = selected.groupby(["race_date", "method"])[["cost_yen", "return_yen"]].sum()
        cost = daily.cost_yen.unstack("method").reindex(columns=table.index).sort_index()
        paid = daily.return_yen.unstack("method").reindex(index=cost.index, columns=cost.columns)
        weights = np.random.default_rng(SEED).multinomial(
            len(cost), np.full(len(cost), 1 / len(cost)), size=500,
        )
        sampled_cost = weights @ cost.to_numpy()
        sampled_paid = weights @ paid.to_numpy()
        values = np.divide(100 * sampled_paid, sampled_cost, out=np.full_like(sampled_paid, np.nan, dtype=float),
                           where=sampled_cost > 0)
        table["score_percent"] = np.nanpercentile(values, 10, axis=0)
    else:
        table["score_percent"] = table.selection_roi_percent
    ordered = table.reset_index().sort_values(
        ["score_percent", "mean_points", "method"], ascending=[False, True, True],
    )
    best = ordered.iloc[0]
    return {
        "method": str(best.method) if best.score_percent > 100 else None,
        "score_percent": float(best.score_percent), "candidates": ordered.to_dict("records"),
    }


def summarize(rows: pd.DataFrame, *, bootstrap_samples: int = 0) -> dict:
    cost, paid = int(rows.cost_yen.sum()), int(rows.return_yen.sum())
    bought = rows.loc[rows.cost_yen.gt(0)]
    if cost > INITIAL_BUDGET or (rows.cost_yen < 0).any() or (rows.return_yen < 0).any():
        raise ValueError("nonnegative_cash_within_initial_budget_required")
    daily = rows.groupby("race_date", sort=True)[["cost_yen", "return_yen"]].sum()
    net = (daily.return_yen - daily.cost_yen).cumsum().to_numpy()
    peak = np.maximum.accumulate(np.r_[0, net])[1:]
    interval = [None, None]
    if bootstrap_samples and cost:
        weights = np.random.default_rng(SEED).multinomial(
            len(daily), np.full(len(daily), 1 / len(daily)), size=bootstrap_samples,
        )
        sampled_cost, sampled_paid = weights @ daily.cost_yen.to_numpy(), weights @ daily.return_yen.to_numpy()
        valid = sampled_cost > 0
        if valid.any():
            interval = np.percentile(100 * sampled_paid[valid] / sampled_cost[valid], [2.5, 97.5]).tolist()
    return {
        "cohort_races": len(rows), "bet_races": len(bought),
        "hit_races": int(bought.hit_ticket_count.gt(0).sum()),
        "hit_rate_percent": float(100 * bought.hit_ticket_count.gt(0).mean()) if len(bought) else None,
        "ticket_count": int(rows.ticket_count.sum()),
        "max_points": int(bought.ticket_count.max()) if len(bought) else 0,
        "cost_yen": cost, "return_yen": paid, "net_yen": paid - cost,
        "gross_return_rate_percent": 100 * paid / cost if cost else None,
        "reserve_yen": INITIAL_BUDGET - cost, "ending_cash_yen": INITIAL_BUDGET - cost + paid,
        "daily_max_drawdown_yen": int((peak - net).max()) if len(net) else 0,
        "highest_payout_zero_roi_percent": 100 * (paid - int(rows.return_yen.max())) / cost if cost else None,
        "top_payout_share_percent": 100 * int(rows.return_yen.max()) / paid if paid else None,
        "date_bootstrap_roi95_percent": interval,
    }
