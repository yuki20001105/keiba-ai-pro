"""Research-only market-axis tickets using single-win odds and AI top five.

Single-win odds determine the axis, not the opponent order or joint EV.
No live selection or purchase is performed.
Unknown dividends must be rejected before any losing ticket is assigned zero.
"""
from __future__ import annotations

from datetime import timedelta
from itertools import combinations
import math

from betting.performance import _official_key
from forward_runtime.schedule import aware, sha256

STAKE_YEN = 100
METHODS = {
    "ai_wide12": {"bet_type": "ワイド", "rule": "AI probability ranks 1 and 2", "max_points": 1},
    "ai_wide_axis5": {"bet_type": "ワイド", "rule": "AI rank 1 axis; ranks 2-5 opponents", "max_points": 4},
    "ai_trio_axis5": {"bet_type": "三連複", "rule": "AI rank 1 axis; ranks 2-5 opponents", "max_points": 6},
    "market_wide_ai2": {"bet_type": "ワイド", "rule": "Market axis; first2 after excluding axis from AI top five", "opponent_count": 2, "max_points": 2},
    "market_wide_ai3": {"bet_type": "ワイド", "rule": "Market axis; first3 after excluding axis from AI top five", "opponent_count": 3, "max_points": 3},
    "market_wide_ai4": {"bet_type": "ワイド", "rule": "Market axis; first4 after excluding axis from AI top five", "opponent_count": 4, "max_points": 4},
    "market_trio_ai2": {"bet_type": "三連複", "rule": "Market axis; pairs from first2 non-axis AI top-five horses", "opponent_count": 2, "max_points": 1},
    "market_trio_ai3": {"bet_type": "三連複", "rule": "Market axis; pairs from first3 non-axis AI top-five horses", "opponent_count": 3, "max_points": 3},
    "market_trio_ai4": {"bet_type": "三連複", "rule": "Market axis; pairs from first4 non-axis AI top-five horses", "opponent_count": 4, "max_points": 6},
    "market_wide_ai5": {"bet_type": "ワイド", "rule": "Single-win favorite axis plus each AI top-five horse, excluding self", "max_points": 5},
    "market_trio_ai5": {"bet_type": "三連複", "rule": "Single-win favorite axis plus every pair from AI top five, excluding self", "max_points": 10},
}


def _finite(value) -> float:
    if isinstance(value, bool):
        raise ValueError("finite_number_required")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("finite_number_required")
    return result


def select_tickets(runners: list[dict], method: str) -> list[tuple[int, ...]]:
    if method not in METHODS:
        raise ValueError("unknown_research_method")
    if not 5 <= len(runners) <= 18:
        raise ValueError("complete_five_or_more_runner_field_required")
    field = []
    for row in runners:
        n = row["horse_number"]
        p, odds = _finite(row["probability"]), _finite(row["odds"])
        if type(n) is not int or not 1 <= n <= 18 or not 0 <= p <= 1 or odds < 1:
            raise ValueError("invalid_runner_probability_or_odds")
        field.append({"n": n, "p": p, "odds": odds})
    if len({r["n"] for r in field}) != len(field) or abs(sum(r["p"] for r in field) - 1) > 1e-5:
        raise ValueError("complete_distinct_normalized_probabilities_required")
    ranked = sorted(field, key=lambda r: (-r["p"], r["n"]))
    market = min(field, key=lambda r: (r["odds"], r["n"]))
    axis = ranked[0] if method.startswith("ai_") else market
    opponents = [r for r in ranked[:5] if r["n"] != axis["n"]]
    if method == "ai_wide12":
        opponents = opponents[:1]
    else:
        opponents = opponents[:METHODS[method].get("opponent_count", 5)]
    if METHODS[method]["bet_type"] == "ワイド":
        tickets = [(axis["n"], r["n"]) for r in opponents]
    else:
        tickets = [(axis["n"], a["n"], b["n"]) for a, b in combinations(opponents, 2)]
    return sorted(tuple(sorted(t)) for t in tickets)


def verify_dividends(runners: list[dict], rows: list[dict]) -> dict:
    finishes = [_finite(r["finish"]) for r in runners]
    numbers = [r["horse_number"] for r in runners]
    if (len(set(numbers)) != len(numbers)
            or sorted(finishes) != list(range(1, len(runners) + 1))):
        raise ValueError("unique_complete_finish_required")
    top = [n for _, n in sorted(zip(finishes, numbers))[:3]]
    expected = {"ワイド": {tuple(sorted(t)) for t in combinations(top, 2)}, "三連複": {tuple(sorted(top))}}
    found = {kind: {} for kind in expected}
    for row in rows:
        kind = {"3連複": "三連複"}.get(row["bet_type"], row["bet_type"])
        if kind not in expected:
            continue
        key = _official_key(kind, row["combinations"])
        payout = _finite(row["payout"])
        if key not in expected[kind] or payout <= 0 or not payout.is_integer():
            raise ValueError("official_payout_key_or_value_mismatch")
        if key in found[kind] and found[kind][key] != int(payout):
            raise ValueError("conflicting_official_payout")
        found[kind][key] = int(payout)
    if any(set(found[kind]) != keys for kind, keys in expected.items()):
        raise ValueError("missing_official_payout")
    return found


def settle_tickets(tickets: list[tuple[int, ...]], payouts: dict) -> dict:
    if not payouts or len(payouts) not in (1, 3):
        raise ValueError("verified_complete_dividends_required")
    if len(set(tickets)) != len(tickets) or any(len(set(t)) != len(t) for t in tickets):
        raise ValueError("duplicate_ticket_or_runner")
    returns = [payouts.get(t, 0) for t in tickets]
    return {
        "ticket_count": len(tickets), "cost_yen": len(tickets) * STAKE_YEN,
        "return_yen": sum(returns), "hit_ticket_count": sum(value > 0 for value in returns),
    }


def capture_odds(envelope: dict, race_id: str) -> dict[int, float]:
    snapshot = envelope.get("snapshot")
    if not isinstance(snapshot, dict) or envelope.get("snapshot_sha256") != sha256(snapshot):
        raise ValueError("capture_content_hash_mismatch")
    if (snapshot.get("schema") != "forward-race-snapshot-v2" or snapshot.get("race_id") != race_id
            or snapshot.get("capture_kind") != "direct_fetch" or snapshot.get("paper_only") is not True
            or snapshot.get("automatic_purchase") is not False):
        raise ValueError("direct_prestart_capture_required")
    post, cutoff = aware(snapshot["post_time"]), aware(snapshot["cutoff_at"])
    source, received, generated, predicted, stored = (
        aware(snapshot[key]) for key in (
            "source_at", "received_at", "features_generated_at", "prediction_at", "stored_at",
        )
    )
    if (not source <= received <= generated <= predicted <= stored <= cutoff <= post - timedelta(minutes=1)
            or stored - source > timedelta(minutes=5)):
        raise ValueError("stale_or_late_prestart_capture")
    result = {}
    for row in snapshot["runners"]:
        n, odds = row["horse_number"], _finite(row["odds"])
        if type(n) is not int or not 1 <= n <= 18 or n in result or odds < 1:
            raise ValueError("incomplete_capture_odds")
        result[n] = odds
    if len(result) != snapshot["expected_runner_count"] or not 5 <= len(result) <= 18:
        raise ValueError("incomplete_capture_field")
    return result
