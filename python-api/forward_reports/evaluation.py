"""Read-only, predeclared prospective evaluation; no fitting or selection.

This module is intentionally outside the frozen collector source packages.
It consumes a consistent RuntimeLedger.snapshot(), never opens its database,
loads a model, fetches results, mutates a decision, or writes an artifact.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import math
from pathlib import Path
import sys

from forward_runtime.schedule import DATE_FROM, DATE_TO, JST, ForwardError, aware, sha256
from forward_runtime.settlement import (aggregate_forward, settle_win_decision,
                                        validate_capture_and_decision)


DEFINITIONS = {
    "cohort": "Eligible, verified, completely settled prospective races, regardless of paper bet or skip; cancelled/refunded races excluded from prediction metrics.",
    "rank_correlation": "Race-equal mean Spearman(score, -official finish); average ranks for ties; constant vectors excluded and counted.",
    "normalized_rank_rmse": "Runner-weighted sqrt(mean((stored H04 score - (field_size - finish)/(field_size - 1))^2)); not speed RMSE, probability error, or a re-ranked score.",
    "incomplete_finish": "A single missing/DNF/disqualified/invalid rank excludes the entire race from Spearman and RMSE; confirmed categorical winner metrics remain possible.",
    "nll": "Race-equal categorical cross-entropy -sum(y*log(p)); y=1/k on each of k confirmed dead-heat winners and 0 otherwise. Zero p uses fixed float64 minimum normal value for finite reporting; clipped values counted.",
    "brier": "Race-equal sum over all runners of (p-y)^2, using the same uniform dead-heat winner distribution; not divided by field size.",
    "top1_win_rate": "Highest stored H04 score, ties broken by smaller horse number; success if this horse belongs to the confirmed winner set (including dead heats).",
    "calibration": "Ten fixed probability bins [0,.1),...,[.9,1], runner-weighted counts, mean p and mean categorical target y; dead heats contribute 1/k, not multiple one-hot wins. Actual winning-horse counts are also separate.",
    "selection": "No fitting, recalibration, tuning, promotion, threshold selection or optional stopping is performed.",
    "betting": "Paper decisions, coverage, official payouts, pending balances and ROI are reported separately by the existing frozen aggregate_forward function.",
    "evidence": "Existing runtime hash/artifact/protocol/time/cohort guards are replayed. Raw receipt objects and executable model inference are not independently reconstructed from this snapshot-only input.",
}


def evaluation_definition():
    """Freeze this returned definition separately before inspecting outcomes."""
    return {"schema": "forward-prediction-evaluation-v1", "definitions": dict(DEFINITIONS),
            "evaluation_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "probability_bin_edges": [n / 10 for n in range(11)], "definition_uses_future_outcomes": False}


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _mean(values):
    return math.fsum(values) / len(values) if values else None


def _ranks(values):
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.] * len(values)
    start = 0
    while start < len(order):
        stop = start + 1
        while stop < len(order) and values[order[stop]] == values[order[start]]:
            stop += 1
        rank = (start + 1 + stop) / 2
        for i in order[start:stop]:
            ranks[i] = rank
        start = stop
    return ranks


def _spearman(scores, finish):
    x, y = _ranks(scores), _ranks([-f for f in finish])
    x = [v - _mean(x) for v in x]
    y = [v - _mean(y) for v in y]
    denominator = math.sqrt(math.fsum(v*v for v in x) * math.fsum(v*v for v in y))
    return max(-1., min(1., math.fsum(a*b for a, b in zip(x, y)) / denominator)) if denominator else None


def _finishes(official, numbers):
    raw = official.get("ranks")
    if not isinstance(raw, dict):
        return None, "official_ranks_missing"
    normalized = {}
    for key, value in raw.items():
        if type(key) is int:
            number = key
        elif isinstance(key, str) and key.isdecimal() and str(int(key)) == key:
            number = int(key)
        else:
            return None, "official_rank_identity_invalid"
        if number in normalized or number not in numbers:
            return None, "official_rank_identity_invalid"
        normalized[number] = value
    if set(normalized) != set(numbers):
        return None, "incomplete_runner_ranks_dnf_or_disqualification"
    finish = [normalized[n] for n in numbers]
    if any(type(f) is not int or not 1 <= f <= len(numbers) for f in finish):
        return None, "invalid_finish_rank"
    if {n for n, f in zip(numbers, finish) if f == 1} != set(official["winners"]):
        return None, "finish_winner_mismatch"
    ordered = sorted(finish)
    for i, value in enumerate(ordered):
        if (i == 0 or value != ordered[i-1]) and value != i+1:
            return None, "non_competition_rank_sequence"
    return finish, None


def evaluate_forward(snapshot, *, as_of):
    """Return prediction and paper-betting reports from one read-only snapshot.

    Hash/identity/protocol corruption fails the whole report. Normal absence of
    observation, final results, or all-runner ranks produces explicit counts
    and null metrics, never fabricated zero accuracy or retrospective fallback.
    """
    contract = snapshot["contract"]
    if sha256(contract) != snapshot.get("contract_sha256"):
        raise ForwardError("evaluation-contract-hash-mismatch")
    betting = aggregate_forward(snapshot["days"], snapshot["races"], as_of=as_of, contract=contract)
    last = min(aware(as_of).astimezone(JST).strftime("%Y%m%d"), DATE_TO)
    rows = [r for r in snapshot["races"] if DATE_FROM <= r["race_date"] <= last]
    reasons, rank_reasons, probability_reasons, top_reasons = Counter(), Counter(), Counter(), Counter()
    correlations, nlls, briers, top_hits = [], [], [], []
    error_sum, error_count, rmse_races, probability_runners, eligible_settled = 0., 0, 0, 0, 0
    dead_heats, clipped, probability_race_ids = 0, 0, []
    bins = [{"lower": n/10, "upper": (n+1)/10, "upper_inclusive": n == 9,
             "runner_count": 0, "race_ids": set(), "probability_sum": 0., "target_sum": 0., "winning_horse_count": 0}
            for n in range(10)]
    for row in rows:
        envelope, settled = row.get("snapshot"), row.get("settlement")
        if not envelope:
            reasons["no_capture:" + row["status"]] += 1
            continue
        capture = envelope["snapshot"]
        if not row.get("eligible"):
            reasons["capture_not_eligible"] += 1
            continue
        # Reuse the collector's frozen cutoff, model/rule bindings and policy
        # checks using its actual stored time, not today's evaluation time.
        _, verified = validate_capture_and_decision(row, envelope, row["decision"], contract, now=capture["stored_at"])
        if not verified:
            raise ForwardError("eligible-projection-disagrees-with-capture")
        if not settled or settled.get("status") != "settled":
            reasons["final_official_result_pending"] += 1
            continue
        rebuilt = settle_win_decision(row, settled["official"], now=as_of)
        if rebuilt != settled:
            raise ForwardError("evaluation-settlement-replay-mismatch")
        official = settled["official"]
        if official["status"] != "final" or official.get("refunded_numbers"):
            reasons["cancelled_or_refunded_race"] += 1
            continue
        eligible_settled += 1
        runners = capture["runners"]
        numbers = [r["horse_number"] for r in runners]
        scores = [r.get("score") for r in runners]
        winners = set(official["winners"])
        probabilities = [r["win_probability"] for r in runners]
        # The reused capture guard requires complete finite probabilities. Do
        # not silently normalize or replace them from stored indicative odds.
        if any(not _finite(p) or not 0 <= p <= 1 for p in probabilities) or abs(math.fsum(probabilities)-1) > 1e-7:
            probability_reasons["invalid_saved_probabilities"] += 1
        else:
            targets = [1/len(winners) if n in winners else 0. for n in numbers]
            nlls.append(-math.fsum(y * math.log(max(p, sys.float_info.min)) for p, y in zip(probabilities, targets) if y))
            clipped += sum(p == 0 and y > 0 for p, y in zip(probabilities, targets))
            briers.append(math.fsum((p-y)**2 for p, y in zip(probabilities, targets)))
            probability_runners += len(runners)
            probability_race_ids.append(row["race_id"])
            dead_heats += len(winners) > 1
            for n, p, y in zip(numbers, probabilities, targets):
                b = bins[min(9, int(p*10))]
                b["runner_count"] += 1; b["race_ids"].add(row["race_id"])
                b["probability_sum"] += p; b["target_sum"] += y
                b["winning_horse_count"] += n in winners
        if any(not _finite(s) for s in scores):
            rank_reasons["missing_or_nonfinite_h04_score"] += 1
            top_reasons["missing_or_nonfinite_h04_score"] += 1
            continue
        top = min(range(len(numbers)), key=lambda i: (-scores[i], numbers[i]))
        top_hits.append(int(numbers[top] in winners))
        finish, reason = _finishes(official, numbers)
        if finish is None:
            rank_reasons[reason] += 1
            continue
        correlation = _spearman(scores, finish)
        if correlation is None:
            rank_reasons["constant_score_or_finish_spearman_undefined"] += 1
        else:
            correlations.append(correlation)
        error_sum += math.fsum((s - (len(numbers)-f)/(len(numbers)-1))**2 for s, f in zip(scores, finish))
        error_count += len(numbers); rmse_races += 1
    calibration = []
    for b in bins:
        count = b["runner_count"]
        calibration.append({k: b[k] for k in ("lower", "upper", "upper_inclusive", "runner_count", "winning_horse_count")}
                           | {"race_count": len(b["race_ids"]), "mean_probability": b["probability_sum"]/count if count else None,
                              "mean_categorical_winner_target": b["target_sum"]/count if count else None,
                              "categorical_winner_target_sum": b["target_sum"]})
    definition = evaluation_definition()
    return {"schema": definition["schema"], "mode": "prospective_observed_only", "as_of": betting["as_of"],
            "protocol_sha256": contract["protocol_sha256"], "artifact_hashes": contract["artifact_hashes"],
            "evaluation_source_sha256": definition["evaluation_source_sha256"], "analysis_definition": definition,
            "prediction": {"scheduled_races": len(rows), "eligible_settled_races": eligible_settled,
                "excluded_races": dict(reasons), "rank_correlation": _mean(correlations), "rank_correlation_races": len(correlations),
                "normalized_rank_rmse": math.sqrt(error_sum/error_count) if error_count else None,
                "normalized_rank_rmse_races": rmse_races, "normalized_rank_rmse_runners": error_count,
                "rank_metric_unavailable_reasons": dict(rank_reasons), "nll": _mean(nlls), "brier": _mean(briers),
                "probability_races": len(nlls), "probability_runners": probability_runners,
                "probability_metric_unavailable_reasons": dict(probability_reasons), "dead_heat_probability_races": dead_heats,
                "winning_zero_probabilities_clipped": clipped, "top1_win_rate": _mean(top_hits), "top1_races": len(top_hits),
                "top1_unavailable_reasons": dict(top_reasons), "calibration_bins": calibration,
                "evaluated_race_ids_sha256": sha256(sorted(probability_race_ids)), "no_observed_evaluation": not nlls},
            "paper_betting": betting, "model_or_policy_changed": False, "definition_retuned_on_observations": False}
