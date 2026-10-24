"""Outcome-only diagnostics for isolated direct-win research.

These functions do not fit, select, publish, or purchase anything. Calibration
means observed alignment of already frozen probabilities with outcomes; it is
not a second calibration fit. All uncertainty intervals are exploratory 95%
date-cluster bootstraps, stratified by year. Eligible dates with no selected bets
are retained. Fixed odds bands are left-inclusive and right-exclusive.
"""
from __future__ import annotations

from datetime import datetime
from numbers import Integral, Real
import re

import numpy as np
import pandas as pd


ODDS_EDGES = (1., 3., 5., 10., 20., 30., np.inf)
ODDS_LABELS = ("1-<3", "3-<5", "5-<10", "10-<20", "20-<30", "30+")
_KEYS = ["race_id", "horse_number"]
_REQUIRED = ["race_id", "race_date", "horse_number", "expected_runner_count",
             "finish", "odds", "probability"]


def _numeric(series, name):
    if pd.api.types.is_bool_dtype(series.dtype):
        raise ValueError(f"win-diagnostics-invalid-{name}")
    if not pd.api.types.is_numeric_dtype(series.dtype):
        if not series.map(lambda value: isinstance(value, Real)
                          and not isinstance(value, (bool, np.bool_))).all():
            raise ValueError(f"win-diagnostics-invalid-{name}")
    try:
        values = series.to_numpy(dtype=float)
    except (TypeError, ValueError) as error:
        raise ValueError(f"win-diagnostics-invalid-{name}") from error
    if not np.isfinite(values).all():
        raise ValueError(f"win-diagnostics-invalid-{name}")
    return values


def _validate(frame):
    if (not isinstance(frame, pd.DataFrame) or frame.empty
            or frame.columns.duplicated().any() or not set(_REQUIRED) <= set(frame)):
        raise ValueError("win-diagnostics-nonempty-complete-frame-required")
    # Physically restrict the view; unknown columns, payouts and supplied
    # prediction ranks never reach a diagnostic calculation.
    optional = [name for name in ("venue", "surface") if name in frame]
    value = frame[_REQUIRED + optional].copy()
    for name in ("race_id", "race_date", *optional):
        if not value[name].map(lambda item: isinstance(item, str) and bool(item.strip())).all():
            raise ValueError(f"win-diagnostics-invalid-{name}")
    for day in value.race_date.unique():
        if not re.fullmatch(r"\d{8}", day):
            raise ValueError("win-diagnostics-invalid-race_date")
        try:
            parsed = datetime.strptime(day, "%Y%m%d")
        except ValueError as error:
            raise ValueError("win-diagnostics-invalid-race_date") from error
        if parsed.strftime("%Y%m%d") != day:
            raise ValueError("win-diagnostics-invalid-race_date")
    for name in ("horse_number", "expected_runner_count", "finish"):
        values = _numeric(value[name], name)
        if not ((values >= 1) & (values <= 18) & (values == np.floor(values))).all():
            raise ValueError(f"win-diagnostics-invalid-{name}")
        value[name] = values.astype(np.int64)
    value["odds"] = _numeric(value.odds, "odds")
    value["probability"] = _numeric(value.probability, "probability")
    if (value.odds < 1.).any():
        raise ValueError("win-diagnostics-invalid-odds")
    if not value.probability.between(0., 1., inclusive="neither").all():
        raise ValueError("win-diagnostics-invalid-probability")
    if value.duplicated(_KEYS).any():
        raise ValueError("win-diagnostics-duplicate-runner")
    value = value.sort_values(["race_date", *_KEYS], kind="mergesort").reset_index(drop=True)
    groups = value.groupby("race_id", sort=False, observed=True)
    metadata = ["race_date", "expected_runner_count", *optional]
    if groups[metadata].nunique(dropna=False).ne(1).any().any():
        raise ValueError("win-diagnostics-conflicting-race-metadata")
    counts = groups.size()
    if not counts.between(2, 18).all() or not value.expected_runner_count.eq(value.race_id.map(counts)).all():
        raise ValueError("win-diagnostics-whole-race-required")
    if value.finish.gt(value.expected_runner_count).any():
        raise ValueError("win-diagnostics-invalid-finish")
    if value.finish.eq(1).groupby(value.race_id, sort=False).sum().ne(1).any():
        raise ValueError("win-diagnostics-single-winner-required")
    if not np.allclose(groups.probability.sum().to_numpy(), 1., atol=1e-8, rtol=0.):
        raise ValueError("win-diagnostics-race-normalized-probability-required")
    return value


def _arguments(repetitions, seed):
    if (not isinstance(repetitions, Integral) or isinstance(repetitions, bool)
            or repetitions < 2):
        raise ValueError("win-diagnostics-bootstrap-repetitions-required")
    if not isinstance(seed, Integral) or isinstance(seed, bool) or seed < 0:
        raise ValueError("win-diagnostics-bootstrap-seed-required")


def _receipt(frame):
    return {"runner_count": int(len(frame)), "race_count": int(frame.race_id.nunique()),
            "date_count": int(frame.race_date.nunique()), "date_from": frame.race_date.min(),
            "date_to": frame.race_date.max()}


def _race_values(frame):
    ids, races = pd.factorize(frame.race_id, sort=False)
    count = len(races)
    probability = frame.probability.to_numpy(float)
    winner = frame.finish.eq(1).to_numpy()
    n = np.bincount(ids, minlength=count).astype(float)
    nll = np.bincount(ids, weights=np.where(winner, -np.log(probability), 0.), minlength=count)
    brier = np.bincount(ids, weights=(probability - winner) ** 2, minlength=count)
    predicted_rank = frame.groupby("race_id", sort=False).probability.rank(ascending=False, method="average").to_numpy()
    finish_rank = frame.groupby("race_id", sort=False).finish.rank(ascending=True, method="average").to_numpy()
    centered_p = predicted_rank - np.bincount(ids, weights=predicted_rank)[ids] / n[ids]
    centered_f = finish_rank - np.bincount(ids, weights=finish_rank)[ids] / n[ids]
    covariance = np.bincount(ids, weights=centered_p * centered_f, minlength=count)
    denominator = np.sqrt(np.bincount(ids, weights=centered_p ** 2, minlength=count)
                          * np.bincount(ids, weights=centered_f ** 2, minlength=count))
    rho = np.divide(covariance, denominator, out=np.zeros(count), where=denominator > 0)
    # Frame is stably horse-number sorted within race, so equal probabilities
    # use the smallest horse number without consulting a result.
    maximum = np.full(count, -np.inf)
    np.maximum.at(maximum, ids, probability)
    first = np.full(count, len(frame), dtype=np.int64)
    positions = np.flatnonzero(probability == maximum[ids])
    np.minimum.at(first, ids[positions], positions)
    race_dates = frame.groupby("race_id", sort=False).race_date.first().reindex(races).to_numpy()
    return {"race_id": races.to_numpy(), "race_date": race_dates, "nll": nll,
            "brier": brier, "spearman": rho, "top1": winner[first].astype(float),
            "constant_rank_races": int(np.count_nonzero(denominator == 0))}


def _day_aggregate(dates, row_dates, columns):
    lookup = pd.Index(dates)
    positions = lookup.get_indexer(row_dates)
    if (positions < 0).any():
        raise ValueError("win-diagnostics-date-not-in-cohort")
    result = np.zeros((len(dates), len(columns)), dtype=float)
    for index, values in enumerate(columns):
        result[:, index] = np.bincount(positions, weights=np.asarray(values, float), minlength=len(dates))
    return result


def _bootstrap(dates, values, repetitions, seed):
    """At most 200 x number-of-eligible-dates weights are allocated at once."""
    years = np.asarray([int(day[:4]) for day in dates])
    year_positions = [np.flatnonzero(years == year) for year in np.unique(years)]
    rng = np.random.default_rng(int(seed))
    output = np.empty((repetitions, values.shape[1]), dtype=float)
    for start in range(0, repetitions, 200):
        batch = min(200, repetitions - start)
        weights = np.zeros((batch, len(dates)), dtype=float)
        for positions in year_positions:
            weights[:, positions] = rng.multinomial(
                len(positions), np.full(len(positions), 1. / len(positions)), size=batch)
        output[start:start + batch] = weights @ values
    return output


def _interval(values):
    values = np.asarray(values, float)
    finite = values[np.isfinite(values)]
    return [float(item) for item in np.quantile(finite, [.025, .975])] if len(finite) else None


def _divide(left, right):
    return np.divide(left, right, out=np.full(np.asarray(left).shape, np.nan), where=right > 0)


def _band_columns(frame):
    bands = np.searchsorted(np.asarray(ODDS_EDGES[1:-1]), frame.odds.to_numpy(float), side="right")
    probability = frame.probability.to_numpy(float)
    hit = frame.finish.eq(1).to_numpy(float)
    columns = []
    for band in range(len(ODDS_LABELS)):
        selected = bands == band
        columns.extend((selected.astype(float), probability * selected, hit * selected))
    return columns


def _calibration_record(count, p_sum, hit_sum, samples, *, label=None):
    p = float(p_sum / count) if count else None
    observed = float(hit_sum / count) if count else None
    sample_p = _divide(samples[:, 1], samples[:, 0])
    sample_observed = _divide(samples[:, 2], samples[:, 0])
    record = {"count": int(count), "wins": int(hit_sum), "mean_probability": p,
              "observed_win_rate": observed, "bias_predicted_minus_actual": p - observed if count else None,
              "mean_probability_ci95": _interval(sample_p), "observed_win_rate_ci95": _interval(sample_observed),
              "bias_ci95": _interval(sample_p - sample_observed),
              "bootstrap_samples_with_observations": int(np.count_nonzero(samples[:, 0] > 0))}
    if label is not None:
        record["odds_band"] = label
    return record


def _band_report(point, samples):
    return [_calibration_record(*point[3 * band:3 * band + 3], samples[:, 3 * band:3 * band + 3], label=label)
            for band, label in enumerate(ODDS_LABELS)]


def _uncertainty(repetitions, seed):
    return {"method": "whole-date-cluster-year-stratified-bootstrap", "repetitions": int(repetitions),
            "seed": int(seed), "confidence": .95, "eligible_no_bet_dates_included": True,
            "interval_scope": "secondary-exploratory-unadjusted", "fits_calibrator": False,
            "changes_policies": False, "eligible_for_ev": False,
            "odds_bands": list(ODDS_LABELS), "odds_band_boundaries": [1, 3, 5, 10, 20, 30, None],
            "odds_band_boundary_semantics": "left-inclusive, right-exclusive; final upper edge is infinity"}


def analyze_probability(frame, reference_frame=None, repetitions=2000, seed=20261012):
    """Evaluate frozen probabilities; paired comparisons require exact cohorts."""
    _arguments(repetitions, seed)
    work = _validate(frame)
    metrics = _race_values(work)
    dates = np.asarray(sorted(work.race_date.unique()))
    main = _day_aggregate(dates, metrics["race_date"],
                          [metrics[name] for name in ("nll", "brier", "spearman", "top1")]
                          + [np.ones(len(metrics["race_id"]))])
    reference = None
    if reference_frame is not None:
        reference = _validate(reference_frame)
        check = [name for name in work if name != "probability"]
        if set(check) != set(reference.columns) - {"probability"} or not work[check].equals(reference[check]):
            raise ValueError("win-diagnostics-matched-reference-cohort-required")
        ref_metrics = _race_values(reference)
        main = np.column_stack((main, _day_aggregate(dates, ref_metrics["race_date"],
                                                    [ref_metrics["nll"], ref_metrics["brier"]])))
    main_size = main.shape[1]
    bands = _day_aggregate(dates, work.race_date.to_numpy(), _band_columns(work))
    values = np.column_stack((main, bands))
    point = values.sum(axis=0)
    boot = _bootstrap(dates, values, int(repetitions), int(seed))
    result = {**_receipt(work), **_uncertainty(repetitions, seed),
              "nll": float(point[0] / point[4]), "brier": float(point[1] / point[4]),
              "spearman": float(point[2] / point[4]), "top1": float(point[3] / point[4]),
              "metric_scope": "race-average-single-winner-probability",
              "spearman_constant_race_policy": "zero, retained in common cohort",
              "constant_rank_races": metrics["constant_rank_races"],
              "odds_band_calibration": _band_report(point[main_size:], boot[:, main_size:]),
              "comparison_vs_reference": None}
    for index, name in enumerate(("nll", "brier", "spearman", "top1")):
        result[name + "_ci95"] = _interval(_divide(boot[:, index], boot[:, 4]))
    if reference is not None:
        result["comparison_vs_reference"] = {
            "matched_race_count": result["race_count"], "matched_runner_count": result["runner_count"],
            "exact_same_outcomes_odds_dates_metadata": True,
            "delta_convention": "current minus reference; negative NLL/Brier is improvement",
            "delta_nll": float((point[0] - point[5]) / point[4]),
            "delta_brier": float((point[1] - point[6]) / point[4]),
            "delta_nll_ci95": _interval(_divide(boot[:, 0] - boot[:, 5], boot[:, 4])),
            "delta_brier_ci95": _interval(_divide(boot[:, 1] - boot[:, 6], boot[:, 4])),
            "reference_nll": float(point[5] / point[4]), "reference_brier": float(point[6] / point[4]),
            "interval_scope": "secondary-exploratory-unadjusted"}
    return result


def selection_calibration(frame, bets, repetitions=2000, seed=20261012):
    """Diagnose existing selected keys; never select from labels or copied odds."""
    _arguments(repetitions, seed)
    work = _validate(frame)
    if (not isinstance(bets, pd.DataFrame) or bets.columns.duplicated().any()
            or not set(_KEYS) <= set(bets)):
        raise ValueError("win-diagnostics-selected-keys-required")
    keys = bets[_KEYS + (["race_date"] if "race_date" in bets else [])].copy()
    if not keys.race_id.map(lambda value: isinstance(value, str) and bool(value.strip())).to_numpy(dtype=bool).all():
        raise ValueError("win-diagnostics-invalid-selected-race_id")
    numbers = _numeric(keys.horse_number, "selected-horse_number")
    if not ((numbers >= 1) & (numbers <= 18) & (numbers == np.floor(numbers))).all():
        raise ValueError("win-diagnostics-invalid-selected-horse_number")
    keys["horse_number"] = numbers.astype(np.int64)
    if keys.duplicated(_KEYS).any():
        raise ValueError("win-diagnostics-duplicate-selected-key")
    selected = keys.merge(work, on=_KEYS, how="left", validate="one_to_one", indicator=True,
                          suffixes=("_ticket", ""))
    if not selected._merge.eq("both").all():
        raise ValueError("win-diagnostics-selected-key-outside-cohort")
    if "race_date_ticket" in selected and not selected.race_date_ticket.eq(selected.race_date).all():
        raise ValueError("win-diagnostics-selected-date-mismatch")
    selected = selected.sort_values(["race_date", *_KEYS], kind="mergesort").reset_index(drop=True)
    dates = np.asarray(sorted(work.race_date.unique()))
    columns = [np.ones(len(selected)), selected.probability.to_numpy(float), selected.finish.eq(1).to_numpy(float)]
    columns += _band_columns(selected)
    values = _day_aggregate(dates, selected.race_date.to_numpy(), columns)
    point = values.sum(axis=0)
    boot = _bootstrap(dates, values, int(repetitions), int(seed))
    return {**_receipt(work), **_uncertainty(repetitions, seed),
            "selected_bet_count": int(len(selected)), "selected_race_count": int(selected.race_id.nunique()),
            "selected_date_count": int(selected.race_date.nunique()),
            "selection_race_coverage": float(selected.race_id.nunique() / work.race_id.nunique()),
            "selection_date_coverage": float(selected.race_date.nunique() / len(dates)),
            "selection_source": "frozen-ticket-keys-only; independent cohort supplies outcomes/probability/odds",
            "metric_scope": "selected-tickets; no optimization or fitting",
            "overall": _calibration_record(*point[:3], boot[:, :3]),
            "odds_band_calibration": _band_report(point[3:], boot[:, 3:])}


