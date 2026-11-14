"""Read an independently verified aggregate study without touching any ledger."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path

POLICY_IDS = frozenset(
    name + "__" + gate
    for name in ("single_ai", "single_favorite", "wide_ai", "wide_axis_h3", "trio_ai", "fixed_two", "trifecta_ai")
    for gate in ("all", "market_top3")
)
_SUM_FIELDS = ("opportunities", "bet_races", "hit_races", "investment", "payout")


def _totals(days):
    data = {key: sum(day[key] for day in days) for key in _SUM_FIELDS}
    data["profit"] = data["payout"] - data["investment"]
    data["roi"] = data["payout"] / data["investment"] * 100 if data["investment"] > 0 else None
    return data


def read_research_performance(path: Path, expected_sha: str, *, policy: str, scope: str,
                              date_from: str | None = None, date_to: str | None = None):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise ValueError("research_report_integrity")
    report = json.loads(raw)
    if (report["schema"] != "betting-research-study-v1"
            or report["independently_verified"] is not True or report["reference_only"] is not True
            or report["purchase_eligible"] is not False or report["feature_count"] != 101
            or len(report["policies"]) != 14 or {row["id"] for row in report["policies"]} != POLICY_IDS):
        raise ValueError("research_report_contract")

    def filtered(days):
        return [day for day in days if (date_from is None or day["date"] >= date_from)
                and (date_to is None or day["date"] <= date_to)]

    policies = [{
        "id": row["id"], "label": row["label"],
        **{phase: _totals(filtered(row["days"][phase])) for phase in ("development", "retrospective")},
        "baseline": _totals(filtered(row["baseline_days"][scope])),
    } for row in report["policies"]]
    selected = next(row for row in report["policies"] if row["id"] == policy)
    days = filtered(selected["days"][scope])
    baseline = {day["date"]: day for day in filtered(selected["baseline_days"][scope])}
    points = []
    accumulated = dict.fromkeys(_SUM_FIELDS, 0)
    baseline_accumulated = dict.fromkeys(_SUM_FIELDS, 0)
    months, baseline_months = defaultdict(list), defaultdict(list)
    for day in days:
        for key in _SUM_FIELDS:
            accumulated[key] += day[key]
            baseline_accumulated[key] += baseline[day["date"]][key]
        points.append({"date": day["date"], **_totals([accumulated]),
                       "baseline_roi": _totals([baseline_accumulated])["roi"]})
        months[day["date"][:7]].append(day)
        baseline_months[day["date"][:7]].append(baseline[day["date"]])
    monthly = [{"month": month, **_totals(values), "baseline_roi": _totals(baseline_months[month])["roi"]}
               for month, values in sorted(months.items())]
    return {
        "schema": "betting-research-performance-v1", "study_id": report["study_id"], "feature_count": 101,
        "reference_only": True, "purchase_eligible": False, "recommended_method": report["recommended_method"],
        "historical_support": report["historical_support"], "periods": report["periods"],
        "scope": scope, "policy": policy, "date_from": date_from, "date_to": date_to,
        "excluded_races": sum(report["exclusions"][scope].values()),
        "policies": policies, "points": points, "monthly": monthly,
    }
