"""Paper-only settlement and prospective-denominator aggregation.

Never estimates a payout from odds. A missing/partial final proof leaves the
entire ticket pending, including a seemingly losing ticket. Dead heats retain
each winner's separately confirmed payout. Outcome evidence is kept separate
from the pre-result snapshot/decision.
"""
from __future__ import annotations

from collections import Counter
from datetime import timedelta
import math

from .schedule import (DATE_FROM, DATE_TO, JST, ForwardError, aware, require_hash,
                       sha256, stamp, study_days, window_for)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_capture_and_decision(race, envelope, decision, contract, *, now):
    if not isinstance(envelope, dict) or set(envelope) != {"snapshot", "snapshot_sha256"}:
        raise ForwardError("snapshot-envelope-required")
    snapshot = envelope["snapshot"]
    if not isinstance(snapshot, dict) or sha256(snapshot) != envelope["snapshot_sha256"]:
        raise ForwardError("snapshot-hash-mismatch")
    if snapshot.get("race_id") != race["race_id"] or aware(snapshot.get("post_time")) != aware(race["post_time"]):
        raise ForwardError("snapshot-scheduled-race-mismatch")
    if snapshot.get("protocol_sha256") != contract["protocol_sha256"] or snapshot.get("artifact_hashes") != contract["artifact_hashes"]:
        raise ForwardError("snapshot-frozen-artifact-contract-mismatch")
    if snapshot.get("paper_only") is not True or snapshot.get("automatic_purchase") is not False or snapshot.get("capture_kind") != "direct_fetch":
        raise ForwardError("prospective-paper-direct-fetch-required")
    start, cutoff = window_for(race["post_time"])
    source, received, generated, stored = (aware(snapshot.get(k)) for k in ("source_at", "received_at", "features_generated_at", "stored_at"))
    predicted = aware(snapshot["prediction_at"]) if snapshot.get("prediction_at") else stored
    if not cutoff - timedelta(seconds=120) <= source <= received <= generated <= predicted <= stored <= aware(now) <= cutoff or stored < start:
        raise ForwardError("capture-times-outside-fixed-cutoff")
    if snapshot.get("cutoff_at") is not None and aware(snapshot["cutoff_at"]) != cutoff:
        raise ForwardError("snapshot-cutoff-mismatch")
    runners = snapshot.get("runners")
    if not isinstance(runners, list) or not 2 <= len(runners) <= 18:
        raise ForwardError("complete-runner-field-required")
    seen = set()
    for row in runners:
        n = row.get("horse_number") if isinstance(row, dict) else None
        if type(n) is not int or not 1 <= n <= 18 or n in seen:
            raise ForwardError("snapshot-runner-identity-invalid")
        seen.add(n)
    if "expected_runner_count" in snapshot and snapshot["expected_runner_count"] != len(runners):
        raise ForwardError("snapshot-runner-count-mismatch")
    eligible = all(snapshot.get(k) is True for k in ("policy_ready", "inference_verified", "raw_features_recomputed"))
    if eligible:
        probabilities = [r.get("win_probability") for r in runners]
        odds = [r.get("odds") for r in runners]
        if any(not _finite(v) or not 0 <= v <= 1 for v in probabilities) or abs(sum(probabilities) - 1) > 1e-7:
            raise ForwardError("verified-race-probabilities-invalid")
        if any(not _finite(v) or v < 1 for v in odds):
            raise ForwardError("verified-race-odds-incomplete")
    if not isinstance(decision, dict) or decision.get("decision") not in ("paper_bet", "skip"):
        raise ForwardError("paper-decision-required")
    if decision.get("live_bet", False) is not False or decision.get("automatic_purchase", False) is not False:
        raise ForwardError("actual-betting-forbidden")
    if decision["decision"] == "paper_bet":
        if not eligible or snapshot.get("prediction_at") is None:
            raise ForwardError("unverified-capture-cannot-place-paper-ticket")
        if type(decision.get("stake_yen")) is not int or decision["stake_yen"] != 100 or type(decision.get("horse_number")) is not int or decision["horse_number"] not in seen:
            raise ForwardError("single-100-yen-win-ticket-required")
        chosen = min(runners, key=lambda r: (-r["win_probability"] * r["odds"], r["horse_number"]))
        if chosen["horse_number"] != decision["horse_number"] or chosen["win_probability"] * chosen["odds"] * .9 <= 1.10:
            raise ForwardError("ticket-differs-from-frozen-paper-policy")
        for key, expected in (("win_probability", chosen["win_probability"]), ("indicative_odds", chosen["odds"]), ("odds_multiplier", .9)):
            if key in decision and decision[key] != expected:
                raise ForwardError("decision-input-binding-mismatch")
    elif not isinstance(decision.get("reason"), str) or not decision["reason"].strip():
        raise ForwardError("skip-reason-required")
    elif eligible and max(r["win_probability"] * r["odds"] * .9 for r in runners) > 1.10:
        raise ForwardError("eligible-skip-differs-from-frozen-paper-policy")
    return snapshot, eligible


def settle_win_decision(race, official, *, now):
    """Evaluate an immutable paper ticket with explicit complete final evidence.

    official: race_id/status/complete/runner_numbers/winners/win_payouts/
    refunded_numbers/source_url/source_sha256/observed_at/received_at.
    Missing final values are pending, not 0. Malformed attribution is rejected.
    ``win_payouts`` maps string horse numbers to JPY per 100-JPY ticket.
    """
    snapshot = race.get("snapshot")
    decision = race.get("decision")
    if not isinstance(snapshot, dict) or not isinstance(decision, dict):
        raise ForwardError("frozen-snapshot-decision-required")
    capture = snapshot["snapshot"]
    if sha256(capture) != snapshot["snapshot_sha256"] or sha256(decision) != race.get("decision_sha256"):
        raise ForwardError("frozen-record-hash-mismatch")
    if not isinstance(official, dict) or official.get("race_id") != race["race_id"]:
        raise ForwardError("official-race-identity-mismatch")
    require_hash(official.get("source_sha256"))
    if not isinstance(official.get("source_url"), str) or not official["source_url"].startswith("https://"):
        raise ForwardError("official-source-required")
    observed, received, current = aware(official.get("observed_at")), aware(official.get("received_at")), aware(now)
    if not aware(race["post_time"]) <= observed <= received <= current:
        raise ForwardError("official-result-time-invalid")
    if official.get("status") not in ("pending", "final", "cancelled"):
        raise ForwardError("official-status-invalid")
    result = {"race_id": race["race_id"], "mode": "prospective_paper", "snapshot_sha256": snapshot["snapshot_sha256"],
              "decision_sha256": race["decision_sha256"], "protocol_sha256": capture["protocol_sha256"],
              "artifact_hashes": capture["artifact_hashes"], "official": official,
              "official_sha256": sha256(official), "status": "payout_pending", "payout_yen": None,
              "net_yen": None, "settled_at": None, "stake_yen": 100 if decision["decision"] == "paper_bet" else 0,
              "actual_purchase": False, "reason": "official-final-proof-incomplete"}
    if official.get("status") == "pending" or official.get("complete") is not True:
        return result
    expected = {r["horse_number"] for r in capture["runners"]}
    numbers = official.get("runner_numbers")
    if not isinstance(numbers, list) or any(type(n) is not int or not 1 <= n <= 18 for n in numbers) or len(set(numbers)) != len(numbers):
        raise ForwardError("official-runner-list-invalid")
    if set(numbers) != expected:
        result["reason"] = "official-runner-cohort-incomplete"
        return result
    winners, refunded = official.get("winners", []), official.get("refunded_numbers", [])
    for values in (winners, refunded):
        if not isinstance(values, list) or any(type(n) is not int or n not in expected for n in values) or len(set(values)) != len(values):
            raise ForwardError("official-winner-refund-identity-invalid")
    if set(winners) & set(refunded):
        raise ForwardError("winner-refund-conflict")
    payouts = official.get("win_payouts", {})
    if not isinstance(payouts, dict):
        raise ForwardError("official-payout-map-invalid")
    if official["status"] == "cancelled":
        if winners or payouts or set(refunded) != expected:
            result["reason"] = "official-full-refund-not-proven"
            return result
    else:
        if not winners or set(payouts) != {str(n) for n in winners}:
            result["reason"] = "all-winner-payouts-not-confirmed"
            return result
        # Japanese win dividends are paid in whole 10-yen units per 100 yen.
        if any(type(value) is not int or value < 100 or value % 10 for value in payouts.values()):
            raise ForwardError("official-payout-unit-invalid")
    if decision["decision"] == "skip":
        amount = 0
    else:
        number = decision["horse_number"]
        amount = 100 if number in refunded else payouts[str(number)] if number in winners else 0
    result.update(status="settled", payout_yen=amount, net_yen=amount - result["stake_yen"],
                  settled_at=stamp(received), reason="confirmed-official-win-payout" if decision["decision"] == "paper_bet" else "no-ticket")
    return result


def aggregate_forward(days, races, *, as_of, contract):
    """Summarize the fixed prospective universe without dropping failures.

    Future dates remain planned, not treated as observations. Rates over the
    full calendar are null until all elapsed days have complete/no-race proof.
    Any pending paper payout makes full-campaign profit/ROI/drawdown unknown;
    a known-only subset is labelled separately, never silently substituted.
    """
    current = aware(as_of)
    last = min(current.astimezone(JST).strftime("%Y%m%d"), DATE_TO)
    day_map = {r["date"]: r for r in days}
    if len(day_map) != len(days):
        raise ForwardError("duplicate-calendar-denominator")
    included_days = [day_map.get(d, {"date": d, "status": "unknown"}) for d in study_days() if d <= last]
    included = [r for r in races if DATE_FROM <= r["race_date"] <= last]
    if len({r["race_id"] for r in included}) != len(included):
        raise ForwardError("duplicate-race-denominator")
    for row in included:
        snap = row.get("snapshot")
        if snap:
            payload = snap["snapshot"]
            if payload.get("protocol_sha256") != contract["protocol_sha256"] or payload.get("capture_kind") != "direct_fetch" or not payload.get("paper_only"):
                raise ForwardError("retrospective-or-different-protocol-cannot-aggregate")
            if payload.get("artifact_hashes") != contract["artifact_hashes"] or sha256(payload) != snap["snapshot_sha256"]:
                raise ForwardError("aggregate-snapshot-hash-mismatch")
            if payload.get("race_id") != row["race_id"] or aware(payload.get("post_time")) != aware(row["post_time"]):
                raise ForwardError("aggregate-race-identity-mismatch")
            if aware(payload.get("stored_at")) > current:
                raise ForwardError("projection-not-historical-as-of-snapshot")
            if row.get("snapshot_sha256") != snap["snapshot_sha256"] or sha256(row.get("decision")) != row.get("decision_sha256"):
                raise ForwardError("aggregate-frozen-record-hash-mismatch")
        if row.get("settlement") and row["settlement"].get("mode") != "prospective_paper":
            raise ForwardError("retrospective-settlement-cannot-aggregate")
        if row.get("settlement"):
            settled = row["settlement"]
            if (sha256(settled) != row.get("settlement_sha256") or settled.get("protocol_sha256") != contract["protocol_sha256"]
                    or settled.get("artifact_hashes") != contract["artifact_hashes"] or settled.get("snapshot_sha256") != row.get("snapshot_sha256")
                    or settled.get("decision_sha256") != row.get("decision_sha256") or sha256(settled.get("official")) != settled.get("official_sha256")):
                raise ForwardError("aggregate-settlement-hash-or-binding-mismatch")
            if aware(settled["official"]["received_at"]) > current:
                raise ForwardError("projection-not-historical-as-of-settlement")
    counts = Counter(row["status"] for row in included)
    day_counts = Counter(row["status"] for row in included_days)
    proven = len(included_days) > 0 and all(row["status"] in ("complete", "not_held") for row in included_days)
    captured = sum(row.get("snapshot") is not None for row in included)
    eligible = [r for r in included if r.get("snapshot") is not None and bool(r.get("eligible"))]
    tickets = [r for r in included if (r.get("decision") or {}).get("decision") == "paper_bet"]
    known = [r for r in tickets if (r.get("settlement") or {}).get("status") == "settled" and r["settlement"].get("payout_yen") is not None]
    pending = len(tickets) - len(known)
    total_stake = sum(r["decision"]["stake_yen"] for r in tickets)
    known_stake = sum(r["decision"]["stake_yen"] for r in known)
    known_payout = sum(r["settlement"]["payout_yen"] for r in known)
    payout = None if pending else known_payout
    daily = []
    cumulative = 0
    peak = 0
    drawdown = 0
    unknown_seen = False
    for date_str in sorted({d["date"] for d in included_days} | {r["race_date"] for r in included}):
        all_rows = [r for r in included if r["race_date"] == date_str]
        placed = [r for r in tickets if r["race_date"] == date_str]
        resolved = [r for r in known if r["race_date"] == date_str]
        day_pending = len(placed) - len(resolved)
        stake = sum(r["decision"]["stake_yen"] for r in placed)
        day_known = sum(r["settlement"]["payout_yen"] for r in resolved)
        amount = None if day_pending else day_known
        net = None if amount is None else amount - stake
        unknown_seen = unknown_seen or bool(day_pending)
        if not unknown_seen:
            cumulative += net
            peak = max(peak, cumulative)
            drawdown = max(drawdown, peak - cumulative)
        daily.append({"race_date": date_str, "scheduled": len(all_rows), "tickets": len(placed), "stake_yen": stake,
                      "pending_tickets": day_pending, "payout_yen": amount, "net_yen": net,
                      "known_subset_payout_yen": day_known, "cumulative_net_yen": None if unknown_seen else cumulative})
    eligible_dates = len({r["race_date"] for r in eligible})
    complete = [r for r in eligible if (r.get("settlement") or {}).get("status") == "settled"]
    complete_dates = len({r["race_date"] for r in complete})
    scheduled = len(included)
    return {"schema": "forward-runtime-aggregate-v1", "mode": "prospective_paper", "as_of": stamp(current),
            "protocol_sha256": contract["protocol_sha256"], "date_from": DATE_FROM, "date_to": DATE_TO,
            "planned_calendar_days": 90, "elapsed_calendar_days": len(included_days), "day_status_counts": dict(day_counts),
            "race_universe_complete_for_elapsed_days": proven, "scheduled_races_known": scheduled,
            "unknown_day_count": sum(r["status"] not in ("complete", "not_held") for r in included_days),
            "race_status_counts": dict(counts), "captured_races": captured, "eligible_races": len(eligible),
            "eligible_race_days": eligible_dates, "complete_races": len(complete), "complete_race_days": complete_dates,
            "known_schedule_capture_rate": captured / scheduled if scheduled else None,
            "full_calendar_capture_rate": captured / scheduled if proven and scheduled else None,
            "failures": counts["failed"], "missed": counts["missed"], "unknown_captures": counts["unknown"],
            "skipped": sum((r.get("decision") or {}).get("decision") == "skip" or r["status"] == "skipped" for r in included),
            "skip_failure_reasons": dict(Counter(r["reason"] for r in included if r.get("reason"))),
            "paper_tickets": len(tickets), "pending_tickets": pending, "stake_yen": total_stake,
            "payout_yen": payout, "net_yen": payout - total_stake if payout is not None else None,
            "gross_return_percent": payout / total_stake * 100 if payout is not None and total_stake else None,
            "net_roi_percent": (payout - total_stake) / total_stake * 100 if payout is not None and total_stake else None,
            "maximum_end_of_day_drawdown_yen": None if pending else drawdown,
            "known_subset": {"tickets": len(known), "stake_yen": known_stake, "payout_yen": known_payout,
                             "gross_return_percent": known_payout / known_stake * 100 if known_stake else None},
            "daily_curve": daily, "minimum_data_met": len(complete) >= 1000 and complete_dates >= 30,
            "period_extended_to_hit_target": False, "actual_purchases": 0,
            "limitations": ["Calendar/provider completeness is an evidence-backed caller attestation, not inferred from empty HTML.",
                            "Known scheduled races are a lower bound when discovery is partial/unknown.",
                            "Indicative odds are not locked prices; only official final dividends settle paper tickets.",
                            "Drawdown is calculated at day-end, not intraday; pending payouts keep full metrics unknown."]}
