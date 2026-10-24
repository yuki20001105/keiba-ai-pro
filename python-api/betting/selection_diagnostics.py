"""Observational calibration of frozen, genuinely selected application tickets.

This is a read-only diagnostic, not a selector, model validation certificate,
holdout evaluation, or permission to purchase. Probabilities and odds come only
from the owner-scoped server snapshot and the frozen selection, never refreshed
predictions or the outcome payload. Unsettled/incomplete observations stay out
of the denominator; missing observations are never replaced by zero.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
import hashlib
import json
import math
from typing import Any

import numpy as np

from .value_policy import BET_TYPES, ORDERED_TYPES, _instant, _post_time, verified_prediction_input

SCHEMA = "recorded-selection-calibration-v1"
BOOTSTRAP_REPETITIONS = 2000
BOOTSTRAP_SEED = 20261012
ODDS_BANDS = (("1_3", "1–3", 1., 3.), ("3_5", "3–5", 3., 5.),
              ("5_10", "5–10", 5., 10.), ("10_20", "10–20", 10., 20.),
              ("20_30", "20–30", 20., 30.), ("30_plus", "30+", 30., None))
STATUSES = ("settled", "pending", "payout_pending", "review", "skipped")


def _number(value: Any) -> float | None:
    # Accept only actual stored JSON numbers, not string coercions or booleans.
    if type(value) not in (int, float):
        return None
    return float(value) if math.isfinite(value) else None


def _encoded(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _ticket_key(ticket: Any) -> tuple[str, tuple[int, ...]] | None:
    if not isinstance(ticket, dict):
        return None
    kind, numbers = ticket.get("bet_type"), ticket.get("horse_numbers")
    if (not isinstance(kind, str) or kind not in BET_TYPES or not isinstance(numbers, list) or len(numbers) != BET_TYPES[kind]
            or any(type(number) is not int or not 1 <= number <= 99 for number in numbers)
            or len(set(numbers)) != len(numbers)):
        return None
    canonical = numbers if kind in ORDERED_TYPES else sorted(numbers)
    if canonical != numbers or ticket.get("combination") != "-".join(map(str, canonical)):
        return None
    return kind, tuple(numbers)


def _statistics(observations: list[dict], *, repetitions: int, seed: int, identity: tuple) -> dict:
    count = len(observations)
    dates = sorted({item["race_date"] for item in observations})
    result = {
        "ticket_count": count, "race_count": len({item["race_id"] for item in observations}),
        "date_count": len(dates), "mean_probability": None, "hit_rate": None,
        "bias_predicted_minus_actual": None, "mean_probability_ci95": None,
        "hit_rate_ci95": None, "bias_ci95": None, "bootstrap_valid_samples": 0,
        "interval_status": "no_observations" if not count else "insufficient_distinct_dates",
    }
    if not count:
        return result
    probability = sum(item["probability"] for item in observations) / count
    hit_rate = sum(item["hit"] for item in observations) / count
    result.update(mean_probability=probability, hit_rate=hit_rate,
                  bias_predicted_minus_actual=probability - hit_rate)
    # One observed date cannot establish between-date uncertainty. This is not
    # a purchase/validation threshold; the point estimates are still reported.
    if len(dates) < 2:
        return result
    date_index = {day: index for index, day in enumerate(dates)}
    totals = np.zeros((len(dates), 3), dtype=np.float64)
    for item in observations:
        totals[date_index[item["race_date"]]] += (1, item["probability"], item["hit"])
    digest = hashlib.sha256(_encoded([seed, *identity]).encode("utf-8")).digest()
    rng = np.random.default_rng(int.from_bytes(digest[:8], "big"))
    samples = []
    # Bound temporary allocation for long histories. A draw samples entire
    # dates, keeping all selected tickets/races and predicted+actual pairs.
    for offset in range(0, repetitions, 128):
        size = min(128, repetitions - offset)
        chosen = rng.integers(0, len(dates), size=(size, len(dates)))
        drawn = totals[chosen].sum(axis=1)
        p, y = drawn[:, 1] / drawn[:, 0], drawn[:, 2] / drawn[:, 0]
        samples.append(np.column_stack((p, y, p - y)))
    draws = np.concatenate(samples, axis=0)
    for index, field in enumerate(("mean_probability_ci95", "hit_rate_ci95", "bias_ci95")):
        result[field] = np.quantile(draws[:, index], (.025, .975)).tolist()
    result.update(bootstrap_valid_samples=len(draws), interval_status="descriptive_interval")
    return result


def summarize_selection_diagnostics(
    rows: list[dict], snapshots: dict[str, dict], *, mode: str,
    date_from: str | None = None, date_to: str | None = None,
    repetitions: int = BOOTSTRAP_REPETITIONS, seed: int = BOOTSTRAP_SEED,
) -> dict:
    """Summarize all filtered ledger rows; snapshots must be server-owned rows.

    ``rows`` and ``snapshots`` are internal database reads, not HTTP inputs.
    Only settled tickets with coherent official outcomes and frozen pre-race
    evidence are eligible. Counts explain every excluded record/ticket.
    """
    if mode not in {"paper", "actual"} or type(repetitions) is not int or repetitions <= 0 or type(seed) is not int:
        raise ValueError("selection-diagnostics-invalid-parameters")
    statuses, exclusions, ticket_exclusions = Counter(), Counter(), Counter()
    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    eligible_record_ids, eligible_tickets = set(), []
    for row in rows:
        status = row.get("status")
        statuses[status if status in STATUSES else "other"] += 1
        ticket_count = 0

        def exclude(reason: str, count: int | None = None) -> None:
            exclusions[reason] += 1
            ticket_exclusions[reason] += ticket_count if count is None else count

        try:
            payload, tickets = json.loads(row["payload_json"]), json.loads(row["tickets_json"])
            if not isinstance(payload, dict) or not isinstance(tickets, list):
                raise ValueError("invalid frozen selection")
            ticket_count = len(tickets)
        except (ValueError, TypeError, KeyError):
            exclude("invalid_record")
            continue
        if not tickets:
            exclude("no_selection")
            continue
        if status != "settled":
            exclude(status if status in {"pending", "payout_pending", "review"} else "non_settled")
            continue
        rec, info = payload.get("recommendation"), payload.get("race_info")
        if (not isinstance(rec, dict) or not isinstance(info, dict)
                or not isinstance(rec.get("decision"), str) or rec["decision"] not in {"buy", "priority"}):
            exclude("reference_only")
            continue
        if rec.get("probability_basis") != "calibrated":
            exclude("probability_unverified")
            continue
        if rec.get("input_verified") is not True or not verified_prediction_input(payload.get("input_diagnostics")):
            exclude("input_unverified")
            continue
        snapshot = snapshots.get(row.get("snapshot_id"))
        try:
            snapshot_payload = json.loads(snapshot["payload_json"])
            snapshot_hash = hashlib.sha256(_encoded(snapshot_payload).encode("utf-8")).hexdigest()
            comparable = {key: value for key, value in payload.items() if key != "_performance_selection"}
            if (snapshot.get("owner") != row.get("owner") or snapshot_hash != snapshot.get("payload_sha256")
                    or comparable != snapshot_payload):
                raise ValueError("snapshot binding mismatch")
        except (ValueError, TypeError, KeyError, AttributeError):
            exclude("snapshot_unverified")
            continue
        model = payload.get("model_id") or rec.get("model_id")
        policy = rec.get("policy_version")
        if not isinstance(model, str) or not model.strip() or not isinstance(policy, str) or not policy.strip():
            exclude("group_identity_missing")
            continue
        selected, evaluated, expiry = (_instant(row.get("created_at")), _instant(rec.get("evaluated_at")),
                                        _instant(rec.get("valid_until")))
        post = _post_time({**info, "date": info.get("date") or info.get("race_date")})
        try:
            day = date.fromisoformat(str(row.get("race_date"))).isoformat()
            info_day = str(info.get("date") or info.get("race_date") or "").replace("/", "").replace("-", "")
            valid_identity = str(info.get("race_id") or "") == row.get("race_id") and info_day == day.replace("-", "")
        except ValueError:
            valid_identity = False
            day = ""
        if (not valid_identity or selected is None or evaluated is None or expiry is None or post is None
                or not evaluated <= selected < min(expiry, evaluated + timedelta(minutes=5), post - timedelta(minutes=1))):
            exclude("timing_unverified")
            continue
        offers = rec.get("tickets")
        offer_map = {_ticket_key(item): item for item in offers} if isinstance(offers, list) else {}
        selected_keys = [_ticket_key(item) for item in tickets]
        if None in selected_keys or len(set(selected_keys)) != len(selected_keys):
            exclude("invalid_ticket")
            continue
        if None in offer_map or not isinstance(offers, list) or len(offer_map) != len(offers):
            exclude("frozen_ticket_mismatch")
            continue
        try:
            outcome = json.loads(row["result_json"])
            outcome_tickets = outcome.get("tickets")
            if not isinstance(outcome_tickets, list):
                raise ValueError("outcome missing")
            outcome_keys = [_ticket_key(item) for item in outcome_tickets]
            outcome_map = dict(zip(outcome_keys, outcome_tickets))
            if None in outcome_keys or len(outcome_map) != len(outcome_tickets) or set(outcome_map) != set(selected_keys):
                raise ValueError("outcome key mismatch")
        except (ValueError, TypeError, KeyError, AttributeError):
            exclude("outcome_missing_or_mismatched")
            continue
        accepted = []
        for ticket, key in zip(tickets, selected_keys):
            offer, actual = offer_map.get(key), outcome_map[key]
            stake = ticket.get("stake")
            if (not isinstance(offer, dict) or type(stake) is not int or stake < 100 or stake % 100
                    or type(offer.get("stake")) is not int or stake > offer["stake"]
                    or {k: v for k, v in ticket.items() if k != "stake"} != {k: v for k, v in offer.items() if k != "stake"}):
                reason = "frozen_ticket_mismatch"
            else:
                p, odds, returned = _number(ticket.get("probability")), _number(ticket.get("odds")), _number(actual.get("actual_return"))
                if p is None or not 0 <= p <= 1:
                    reason = "invalid_probability"
                elif odds is None or odds < 1:
                    reason = "invalid_odds"
                elif (type(actual.get("is_hit")) is not bool or actual.get("status") != "settled"
                      or type(actual.get("stake")) is not int or actual["stake"] != stake
                      or returned is None or returned < 0 or actual["is_hit"] != (returned > 0)):
                    reason = "outcome_missing_or_mismatched"
                else:
                    accepted.append({"race_id": row["race_id"], "race_date": day, "probability": p,
                                     "odds": odds, "hit": int(actual["is_hit"]), "bet_type": key[0]})
                    continue
            # Ticket reasons are counted separately: a partially usable record
            # is not mislabeled wholly excluded or silently made complete.
            ticket_exclusions[reason] += 1
        if not accepted:
            exclusions["no_eligible_ticket"] += 1
        else:
            eligible_record_ids.add(row.get("id"))
            for item in accepted:
                groups[(model, policy, item["bet_type"])].append(item)
                eligible_tickets.append(item)
    output_groups = []
    for identity in sorted(groups):
        observations = groups[identity]
        result = {"model_id": identity[0], "policy_version": identity[1], "bet_type": identity[2],
                  **_statistics(observations, repetitions=repetitions, seed=seed, identity=identity)}
        bands = []
        for band, label, lower, upper in ODDS_BANDS:
            subset = [item for item in observations if lower <= item["odds"] and (upper is None or item["odds"] < upper)]
            bands.append({"band": band, "label": label, "lower": lower, "upper": upper,
                          **_statistics(subset, repetitions=repetitions, seed=seed, identity=(*identity, band))})
        result["odds_bands"] = bands
        output_groups.append(result)
    return {
        "schema": SCHEMA, "label": "観測済み買い目の校正診断", "observational": True,
        "unknown_holdout": False, "purchase_authority": False,
        "scope": {"mode": mode, "date_from": date_from, "date_to": date_to,
                  "record_count": len(rows), "includes_full_filtered_set": True},
        "status_counts": {status: statuses[status] for status in (*STATUSES, "other")},
        "sample": {"eligible_tickets": len(eligible_tickets),
                   "eligible_races": len({item["race_id"] for item in eligible_tickets}),
                   "eligible_dates": len({item["race_date"] for item in eligible_tickets}),
                   "excluded_records": len(rows) - len(eligible_record_ids),
                   "excluded_tickets": sum(ticket_exclusions.values())},
        "excluded_counts": dict(sorted(exclusions.items())),
        "excluded_ticket_counts": dict(sorted(ticket_exclusions.items())),
        "groups": output_groups,
        "bootstrap": {"method": "paired_whole_race_date_percentile", "confidence_level": .95,
                      "repetitions": repetitions, "seed": seed,
                      "interval_scope": "conditional_on_verified_settled_recorded_selection",
                      "simultaneous_intervals": False, "multiple_comparisons_adjusted": False},
        "denominator": "Verified settled recorded tickets only; pending, missing payout and review await settlement.",
    }
