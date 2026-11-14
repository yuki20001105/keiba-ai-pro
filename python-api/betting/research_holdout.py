"""Research-only official settlement, including dead heats and unresolved races."""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations

from betting.performance import _official_key
from betting.research_market_axis import STAKE_YEN, _finite


def verify_holdout_dividends(runners: list[dict], rows: list[dict]) -> dict:
    numbers = [row["horse_number"] for row in runners]
    if (not 3 <= len(numbers) <= 18 or len(set(numbers)) != len(numbers)
            or any(type(n) is not int or not 1 <= n <= 18 for n in numbers)):
        raise ValueError("complete_distinct_result_field_required")
    groups = defaultdict(list)
    for row in runners:
        finish = _finite(row["finish"])
        if finish < 1 or not finish.is_integer():
            raise ValueError("positive_integer_finish_required")
        if finish <= 3:
            groups[int(finish)].append(row["horse_number"])
    position = 1
    for rank, horses in sorted(groups.items()):
        if rank != position:
            raise ValueError("inconsistent_top_three_competition_ranks")
        position += len(horses)
    if position < 4:
        raise ValueError("complete_top_three_required")
    mandatory, trios = [], set()
    for _, horses in sorted(groups.items()):
        slots = 3 - len(mandatory)
        if len(horses) >= slots:
            trios = {tuple(sorted(mandatory + list(t))) for t in combinations(horses, slots)}
            break
        mandatory.extend(horses)
    # Third-place dead-heat horses cannot form a winning wide with each other.
    expected = {
        "三連複": trios,
        "ワイド": {pair for trio in trios for pair in combinations(trio, 2)},
    }
    found = {kind: {} for kind in expected}
    for row in rows:
        kind = {"3連複": "三連複"}.get(row["bet_type"], row["bet_type"])
        if kind not in expected:
            continue
        key, payout = _official_key(kind, row["combinations"]), _finite(row["payout"])
        if key not in expected[kind] or payout <= 0 or not payout.is_integer():
            raise ValueError("official_payout_key_or_value_mismatch")
        if key in found[kind] and found[kind][key] != int(payout):
            raise ValueError("conflicting_official_payout")
        found[kind][key] = int(payout)
    if any(set(found[kind]) != keys for kind, keys in expected.items()):
        raise ValueError("missing_official_payout")
    return found


def settle_holdout_tickets(tickets: list[tuple[int, ...]], payouts: dict | None) -> dict:
    if (len(set(tickets)) != len(tickets)
            or any(t != tuple(sorted(set(t))) or len(t) not in (2, 3) for t in tickets)):
        raise ValueError("duplicate_or_noncanonical_ticket")
    result = {"ticket_count": len(tickets), "cost_yen": len(tickets) * STAKE_YEN}
    if payouts is None:
        return {**result, "return_yen": None, "hit_ticket_count": None}
    if not payouts or (tickets and any(len(key) != len(tickets[0]) for key in payouts)):
        raise ValueError("verified_complete_dividends_required")
    returns = [payouts.get(ticket, 0) for ticket in tickets]
    return {**result, "return_yen": sum(returns), "hit_ticket_count": sum(value > 0 for value in returns)}
