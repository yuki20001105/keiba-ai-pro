"""Fixed rank-only examples; never a priced, calibrated or executable offer."""
from __future__ import annotations

import hashlib
import json
import math

SCHEMA = "rank-reference-bets-v1"


def build_reference_bets(predictions: list[dict], *, runner_count: int) -> dict:
    result = {
        "schema": SCHEMA, "basis": "ranking_only", "purchase_eligible": False,
        "monetary_amounts_assigned": False, "tickets": [],
    }
    # No incomplete race, duplicate horse or non-finite score may acquire even
    # a reference selection. No odds, outcomes or apparent EV enter this rule.
    if type(runner_count) is not int or not 2 <= runner_count <= 18 or len(predictions) != runner_count:
        return result
    ordered = []
    for row in predictions:
        if not isinstance(row, dict):
            return result
        rank, number, score = row.get("predicted_rank"), row.get("horse_number"), row.get("p_raw")
        if (type(rank) is not int or type(number) is not int or not 1 <= number <= 18
                or isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score)):
            return result
        ordered.append((rank, number, float(score)))
    ordered.sort()
    if ([r for r, _, _ in ordered] != list(range(1, runner_count + 1))
            or len({n for _, n, _ in ordered}) != runner_count
            or any(a[2] < b[2] for a, b in zip(ordered, ordered[1:]))):
        return result
    numbers = [n for _, n, _ in ordered[:3]]
    examples = [("単勝", numbers[:1]), ("ワイド", sorted(numbers[:2])),
                ("馬連", sorted(numbers[:2])), ("馬単", numbers[:2])]
    if len(numbers) == 3:
        examples += [("三連複", sorted(numbers)), ("三連単", numbers)]
    result["tickets"] = [{"bet_type": kind, "horse_numbers": members,
                          "combination": "-".join(map(str, members))} for kind, members in examples]
    result["ranking_sha256"] = hashlib.sha256(json.dumps(ordered, separators=(",", ":")).encode()).hexdigest()
    return result
