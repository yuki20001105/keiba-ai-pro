"""Exact Plackett-Luce top-three distributions for 2..18 fixed-score runners.

No Monte Carlo, model fitting, odds, purchases, storage, or application imports.
Indices are the caller's runner order, NOT horse numbers. Unordered arrays store
each event once: i<j for pairs and i<j<k for triples; other cells are zero.
Top-k membership is mathematical, not a claim about ticket availability, JRA
place rules, dead-heat settlement, or historical pre-race availability.

The optional scalar calibration uses only declared 2021-H1 OOF H04 scores and
confirmed finishes. It never alters H04 or Track A's temperature. PL models a
strict ranking and assumes Luce's choice axiom; its probabilities require an
independent empirical calibration check, not merely sum-to-one checks.
Reference: https://hturner.github.io/PlackettLuce/articles/Overview.html
"""
from __future__ import annotations

import hashlib
from itertools import permutations
import math

import numpy as np
import pandas as pd
from scipy.special import logsumexp, softmax


TEMPERATURE_BOUNDS = (0.01, 100.0)
FIT_FROM, FIT_TO = "20210101", "20210630"
FIT_COLUMNS = ["race_id", "race_date", "horse_number", "expected_runner_count",
               "h04_score", "finish", "fold_train_date_to"]


class CalibrationRejectedError(ValueError):
    """Rejected scalar calibration; receipt retains the reason and audit counts."""

    def __init__(self, reason, receipt):
        self.receipt = {**receipt, "accepted": False, "rejection_reason": reason}
        super().__init__(reason)


def _vector(values):
    array = np.asarray(values, dtype=float)
    if array.ndim != 1 or not 2 <= len(array) <= 18 or not np.isfinite(array).all():
        raise ValueError("two-to-eighteen-finite-runner-values-required")
    return array


def _positive_scalar(value):
    if isinstance(value, (bool, np.bool_)) or not np.isscalar(value) or not math.isfinite(float(value)) or float(value) <= 0:
        raise ValueError("finite-positive-temperature-required")
    return float(value)


def _from_log_weights(log_weights):
    n = len(log_weights)
    win = softmax(log_weights)
    pair = win[:, None] * softmax(np.where(np.eye(n, dtype=bool), -np.inf, log_weights[None, :]), axis=1)
    triple = np.zeros((n, n, n))
    i, j, k = np.indices(triple.shape)
    if n >= 3:
        # Recompute remaining-set denominators, never subtract almost-equal sums.
        third = softmax(np.where((k != i) & (k != j), log_weights[None, None, :], -np.inf), axis=2)
        triple = pair[:, :, None] * third
    unordered_pair = np.triu(pair + pair.T, 1)
    unordered_triple = np.where((i < j) & (j < k),
        sum(triple.transpose(order) for order in permutations(range(3))), 0.)
    top2 = win + pair.sum(axis=0)
    top3 = top2 + triple.sum(axis=(0, 1))
    # The three disjoint position-pairs: (1,2), (1,3), and (2,3).
    first_third, second_third = triple.sum(axis=1), triple.sum(axis=0)
    wide = unordered_pair + np.triu(first_third + first_third.T + second_third + second_third.T, 1)
    result = {"win": win, "top2": np.clip(top2, 0., 1.), "top3": np.clip(top3, 0., 1.),
              "ordered_pair": pair, "unordered_pair": unordered_pair,
              "wide": np.clip(wide, 0., 1.), "ordered_triple": triple, "unordered_triple": unordered_triple}
    audit_distribution(result)
    return result


def plackett_luce(weights):
    """Exact rank probabilities from finite strictly positive relative weights."""
    weights = _vector(weights)
    if np.any(weights <= 0):
        raise ValueError("strictly-positive-weights-required")
    # Taking logs BEFORE normalization preserves tiny remaining weights even
    # when a dominant horse makes its initial win probability round to one.
    return _from_log_weights(np.log(weights))


def scores_to_distribution(scores, temperature):
    """Higher score means better; w_i is proportional to exp(score_i / T)."""
    scores, temperature = _vector(scores), _positive_scalar(temperature)
    with np.errstate(over="ignore", invalid="ignore"):
        logits = (scores - scores.max()) / temperature
    if not np.isfinite(logits).all():
        raise ValueError("score-range-exceeds-finite-temperature-arithmetic")
    return _from_log_weights(logits)


def audit_distribution(distribution, *, tolerance=2e-12):
    """Fail closed on malformed marginals/events; return normalization totals."""
    if not math.isfinite(tolerance) or not 0 < tolerance <= 1e-8:
        raise ValueError("small-positive-audit-tolerance-required")
    n = len(_vector(distribution["win"]))
    shapes = {"win": (n,), "top2": (n,), "top3": (n,), "ordered_pair": (n, n),
              "unordered_pair": (n, n), "wide": (n, n),
              "ordered_triple": (n, n, n), "unordered_triple": (n, n, n)}
    for name, shape in shapes.items():
        values = np.asarray(distribution[name])
        if values.shape != shape or not np.isfinite(values).all() or np.any(values < 0) or np.any(values > 1 + tolerance):
            raise ValueError("invalid-probability-array:" + name)
    i, j, k = np.indices((n, n, n))
    pair, triple = distribution["ordered_pair"], distribution["ordered_triple"]
    if (np.diag(pair).any() or triple[(i == j) | (i == k) | (j == k)].any()
            or np.tril(distribution["unordered_pair"]).any() or np.tril(distribution["wide"]).any()
            or distribution["unordered_triple"][~((i < j) & (j < k))].any()):
        raise ValueError("same-runner-or-duplicate-unordered-event")
    expected = {"win": 1., "top2": 2., "top3": float(min(3, n)), "ordered_pair": 1.,
                "unordered_pair": 1., "wide": float(math.comb(min(3, n), 2)),
                "ordered_triple": float(n >= 3), "unordered_triple": float(n >= 3)}
    sums = {name: float(np.sum(distribution[name])) for name in shapes}
    if any(not math.isclose(sums[name], total, rel_tol=0., abs_tol=tolerance) for name, total in expected.items()):
        raise ValueError("rank-event-probability-normalization-failed")
    if (not np.allclose(distribution["win"], pair.sum(axis=1), rtol=0., atol=tolerance)
            or not np.allclose(distribution["top2"], pair.sum(axis=0) + pair.sum(axis=1), rtol=0., atol=tolerance)
            or not np.allclose(distribution["top3"], distribution["top2"] + triple.sum(axis=(0, 1)), rtol=0., atol=tolerance)
            or (n >= 3 and not np.allclose(pair, triple.sum(axis=2), rtol=0., atol=tolerance))):
        raise ValueError("rank-marginal-consistency-failed")
    unordered_pair = np.triu(pair + pair.T, 1)
    unordered_triple = np.where((i < j) & (j < k), sum(triple.transpose(order) for order in permutations(range(3))), 0.)
    a, b = triple.sum(axis=1), triple.sum(axis=0)
    for name, expected_values in [("unordered_pair", unordered_pair), ("unordered_triple", unordered_triple),
                                  ("wide", unordered_pair + np.triu(a + a.T + b + b.T, 1))]:
        if not np.allclose(distribution[name], expected_values, rtol=0., atol=tolerance):
            raise ValueError("unordered-event-consistency-failed:" + name)
    return {"runners": n, "exact_enumeration": True, "monte_carlo_samples": 0, "probability_sums": sums}


def ordered_top3_nll(scores, finish_order, temperature):
    """NLL of a distinct ordered top min(3,N) sequence of zero-based indices."""
    values = _vector(scores)
    temperature = _positive_scalar(temperature)
    order = np.asarray(finish_order)
    if (order.shape != (min(3, len(values)),) or not np.issubdtype(order.dtype, np.integer)
            or len(set(order)) != len(order) or np.any(order < 0) or np.any(order >= len(values))):
        raise ValueError("distinct-complete-top-three-indices-required")
    remaining, loss = np.ones(len(values), dtype=bool), 0.
    for index in order:
        with np.errstate(over="ignore", invalid="ignore"):
            shifted = (values[remaining] - values[remaining].max()) / temperature
            chosen = (values[index] - values[remaining].max()) / temperature
        if not np.isfinite(shifted).all() or not math.isfinite(chosen):
            raise ValueError("nonfinite-ordered-likelihood")
        loss += float(logsumexp(shifted) - chosen)
        remaining[index] = False
    return loss


def _calibration_choices(frame):
    if not isinstance(frame, pd.DataFrame) or frame.empty or frame.columns.duplicated().any() or not set(FIT_COLUMNS) <= set(frame):
        raise ValueError("complete-OOF-H04-calibration-columns-required")
    work = frame[FIT_COLUMNS].copy()
    for column in ["race_date", "fold_train_date_to"]:
        text = work[column].astype("string")
        if text.isna().any() or not text.str.fullmatch(r"\d{8}").all():
            raise ValueError("canonical-calibration-dates-required")
        pd.to_datetime(text, format="%Y%m%d", errors="raise")
        work[column] = text.astype(str)
    if not work.race_date.between(FIT_FROM, FIT_TO).all() or not work.fold_train_date_to.le("20201231").all():
        raise ValueError("calibration-is-restricted-to-2021-H1-prior-year-OOF")
    if work.race_id.isna().any() or work.race_id.astype(str).str.strip().eq("").any():
        raise ValueError("race-identities-required")
    work["race_id"] = work.race_id.astype(str)
    for column in ["horse_number", "h04_score", "expected_runner_count"]:
        work[column] = pd.to_numeric(work[column], errors="raise")
        if not np.isfinite(work[column]).all():
            raise ValueError("finite-calibration-identities-scores-counts-required")
    if (not work.horse_number.between(1, 18).all() or not work.horse_number.eq(np.floor(work.horse_number)).all()
            or work.duplicated(["race_id", "horse_number"]).any()):
        raise ValueError("unique-integer-runner-identities-required")
    work["finish"] = pd.to_numeric(work.finish, errors="coerce")
    work = work.sort_values(["race_date", "race_id", "horse_number"], kind="stable").reset_index(drop=True)
    choices, observed, included_ids, exclusions = [], [], [], []
    for race_id, group in work.groupby("race_id", sort=False):
        if group.race_date.nunique() != 1 or group.fold_train_date_to.nunique() != 1:
            raise ValueError("inconsistent-race-date-or-OOF-provenance")
        finish = group.finish.to_numpy(float)
        reason = None
        if not 3 <= len(group) <= 18 or not group.expected_runner_count.eq(len(group)).all():
            reason = "incomplete-or-less-than-three-runner-race"
        elif (not np.isfinite(finish).all() or not ((finish >= 1) & (finish <= len(group)) & (finish == np.floor(finish))).all()):
            reason = "unconfirmed-or-invalid-finish"
        elif not np.array_equal(group.finish.rank(method="min").to_numpy(), finish):
            reason = "invalid-competition-ranking"
        elif len(np.unique(finish[finish <= 3])) != int(np.sum(finish <= 3)):
            reason = "top-three-dead-heat"
        if reason:
            exclusions.append({"race_id": race_id, "runners": len(group), "reason": reason})
            continue
        scores = group.h04_score.to_numpy(float)
        remaining = np.ones(len(group), dtype=bool)
        for index in np.argsort(finish, kind="stable")[:3]:
            row = np.full(18, -np.inf)
            centered = scores[remaining] - scores[remaining].max()
            if not np.isfinite(centered).all():
                raise ValueError("calibration-score-range-exceeds-finite-arithmetic")
            row[:remaining.sum()] = centered
            observed.append(float(scores[index] - scores[remaining].max()))
            choices.append(row)
            remaining[index] = False
        included_ids.append(race_id)
    audit = {"input_races": int(work.race_id.nunique()), "input_runners": len(work),
             "fit_races": len(included_ids), "fit_runners": int(work.race_id.isin(included_ids).sum()),
             "exclusions": exclusions, "excluded_races": len(exclusions),
             "dead_heat_policy": "strict top-three order; whole race excluded if top-three tied",
             "fit_input_sha256": hashlib.sha256(work.to_csv(index=False, lineterminator="\n", float_format="%.17g").encode()).hexdigest()}
    return np.asarray(choices), np.asarray(observed), audit


def fit_temperature(frame, *, temperature_bounds=TEMPERATURE_BOUNDS, minimum_races=1):
    """Fit ONE bounded inverse-temperature by convex top-three order NLL.

    All supplied rows must be 2021-H1 OOF. No odds or payout column is read.
    The outer caller must bind the source-model/artifact hashes and preregister
    the bounds/minimum before invoking this function on real data. A solution
    within 1% of either log-temperature boundary is rejected, not promoted.
    """
    if len(temperature_bounds) != 2:
        raise ValueError("two-temperature-bounds-required")
    low_t, high_t = map(_positive_scalar, temperature_bounds)
    if not low_t < high_t or not isinstance(minimum_races, int) or isinstance(minimum_races, bool) or minimum_races < 1:
        raise ValueError("ordered-positive-bounds-and-minimum-races-required")
    choices, observed, audit = _calibration_choices(frame)
    receipt = {"schema": "fixed-H04-PL-top3-temperature-v1", "accepted": False, **audit,
        "temperature_bounds": [low_t, high_t], "minimum_races": minimum_races,
        "fit_period": [FIT_FROM, FIT_TO], "objective": "race-mean ordered top-three Plackett-Luce NLL",
        "score_direction": "higher_is_better", "model_refit": False, "odds_used": False,
        "boundary_log_range_fraction": .01, "iterations": 64}
    if audit["fit_races"] < minimum_races:
        raise CalibrationRejectedError("insufficient-eligible-calibration-races", receipt)
    mask = np.isfinite(choices)
    finite_scores = np.where(mask, choices, 0.)

    def stats(beta):
        with np.errstate(over="ignore", invalid="ignore"):
            logits = choices * beta
        if not np.isfinite(logits[mask]).all():
            raise ValueError("nonfinite-calibration-likelihood")
        derivative = float((np.sum(softmax(logits, axis=1) * finite_scores) - observed.sum()) / audit["fit_races"])
        nll = float(np.sum(logsumexp(logits, axis=1) - beta * observed) / audit["fit_races"])
        if not math.isfinite(derivative) or not math.isfinite(nll):
            raise ValueError("nonfinite-calibration-likelihood")
        return derivative, nll

    if not np.any(finite_scores):
        raise CalibrationRejectedError("constant-scores-temperature-unidentifiable", receipt)
    low, high = 1 / high_t, 1 / low_t
    if stats(low)[0] >= 0:
        beta = low
    elif stats(high)[0] <= 0:
        beta = high
    else:
        for _ in range(64):
            middle = (low + high) / 2
            if stats(middle)[0] < 0:
                low = middle
            else:
                high = middle
        beta = (low + high) / 2
    temperature = 1 / beta
    boundary_distance = min(math.log(temperature / low_t), math.log(high_t / temperature)) / math.log(high_t / low_t)
    receipt.update(temperature=float(temperature), inverse_temperature=float(beta),
                   fit_nll=stats(beta)[1], reference_t1_nll=stats(1.)[1],
                   distance_to_boundary_log_fraction=float(boundary_distance))
    if boundary_distance <= .01:
        raise CalibrationRejectedError("temperature-within-one-percent-log-boundary", receipt)
    return {**receipt, "accepted": True}
