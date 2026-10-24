"""Predeclared, selection-aware historical diagnostics; no live betting API."""
from __future__ import annotations

import math
import numpy as np
import pandas as pd

THRESHOLDS = [1., 1.02, 1.05, 1.10, 1.15, 1.20, 1.30]
COVERAGES = [1., .75, .5, .3, .2, .1, .05, .02, .01]
ODDS_EDGES = [0, 3, 5, 10, 20, 50, np.inf]
ODDS_LABELS = ["<3", "3-5", "5-10", "10-20", "20-50", "50+"]


def odds_band(frame):
    return pd.cut(frame.odds, ODDS_EDGES, labels=ODDS_LABELS, right=False).astype(str)


def validate(frame):
    required = {"race_id", "race_date", "horse_number", "finish", "odds", "probability", "expected_runner_count", "tansho_payout"}
    if frame.empty or not required.issubset(frame):
        raise ValueError("Complete historical cohort required")
    if frame.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("Duplicate runner")
    grouped = frame.groupby("race_id")
    counts = grouped.race_id.transform("size")
    if not frame.expected_runner_count.eq(counts).all() or not counts.between(2, 18).all():
        raise ValueError("Incomplete race")
    if grouped.race_date.nunique().ne(1).any():
        raise ValueError("Conflicting date")
    for column in ["finish", "odds", "probability"]:
        if not np.isfinite(frame[column].to_numpy(float)).all():
            raise ValueError("Nonfinite input")
    if not (frame.odds.ge(1).all() and frame.probability.between(0, 1).all()):
        raise ValueError("Invalid odds/probability")
    if not np.allclose(grouped.probability.sum(), 1, rtol=0, atol=1e-7):
        raise ValueError("Probability sums")
    if not (frame.finish.between(1, counts).all() and frame.finish.eq(np.floor(frame.finish)).all()):
        raise ValueError("Unconfirmed outcome")
    if not frame.finish.eq(1).groupby(frame.race_id).sum().eq(1).all():
        raise ValueError("Exactly one winner required")


def calibration(frame):
    n = len(frame)
    predicted = float(frame.probability.mean()) if n else None
    observed = float(frame.finish.eq(1).mean()) if n else None
    ratio = observed / predicted if n and predicted else None
    return {"horses": n, "races": int(frame.race_id.nunique()), "predicted_win_rate": predicted,
            "actual_win_rate": observed, "calibration_ratio_actual_over_predicted": ratio,
            "absolute_log_ratio": abs(math.log(ratio)) if ratio and ratio > 0 else None}


def maximum_ev(frame, *, conservative=False):
    probability = "p_lower" if conservative else "probability"
    value = frame.assign(selection_ev=frame[probability] * frame.odds)
    # Rounded tie handling is deliberately NOT used: only exact EV ties.
    return value.sort_values(["race_id", "selection_ev", "horse_number"], ascending=[True, False, True], kind="stable").drop_duplicates("race_id").copy()


def selection_calibration(frame):
    work = frame.assign(ev=frame.probability * frame.odds)
    n = max(1, math.ceil(len(work) * .1))
    top = work.sort_values(["ev", "race_id", "horse_number"], ascending=[False, True, True], kind="stable").head(n)
    maxima = maximum_ev(frame)
    lower_maxima = maximum_ev(frame, conservative=True)
    return {"all_horses": calibration(frame), "ev_top_10_percent_horses": calibration(top),
            "maximum_ev_per_race": calibration(maxima),
            "ordinary_buy": calibration(maxima.loc[maxima.selection_ev.gt(1.10)]),
            "conservative_buy": calibration(lower_maxima.loc[lower_maxima.selection_ev.gt(1.10)])}


def prediction_metrics(frame):
    validate(frame)
    ids = frame.race_id
    y = frame.finish.eq(1).to_numpy(float)
    p = frame.probability.to_numpy(float)
    ranks = frame.probability.groupby(ids).rank(method="average")
    truth = -frame.finish.groupby(ids).rank(method="average")
    a, b = ranks - ranks.groupby(ids).transform("mean"), truth - truth.groupby(ids).transform("mean")
    numerator = (a * b).groupby(ids).sum()
    denominator = np.sqrt((a * a).groupby(ids).sum() * (b * b).groupby(ids).sum())
    rho = numerator / denominator.replace(0, np.nan)
    best = frame.sort_values(["race_id", "probability", "horse_number"], ascending=[True, False, True]).drop_duplicates("race_id")
    bins = np.minimum((p * 10).astype(int), 9)
    reliability = []
    ece = 0.
    for i in range(10):
        part = frame.loc[bins == i]
        record = calibration(part)
        if len(part):
            ece += len(part) / len(frame) * abs(record["predicted_win_rate"] - record["actual_win_rate"])
        reliability.append({"bin_low": i / 10, "bin_high": (i + 1) / 10, **record})
    return {"races": int(ids.nunique()), "horses": len(frame),
            "nll": float(pd.Series(-y * np.log(np.maximum(p, np.finfo(float).tiny)), index=frame.index).groupby(ids).sum().mean()),
            "brier": float(pd.Series((p - y) ** 2, index=frame.index).groupby(ids).sum().mean()),
            "spearman": float(rho.mean()), "constant_races": int(rho.isna().sum()),
            "top1_win_rate": float(best.finish.eq(1).mean()), "ece": ece, "reliability": reliability}


def fit_conservative(development):
    if development.empty or not development.race_date.astype(str).between("20211001", "20211231").all():
        raise ValueError("Conservative calibration may fit only 2021 Q4 development data")
    selected = maximum_ev(development)
    selected["odds_band"] = odds_band(selected)
    bands = {}
    z = 1.959963984540054
    for name in ODDS_LABELS:
        part = selected.loc[selected.odds_band.eq(name)]
        n = len(part)
        wins = int(part.finish.eq(1).sum())
        mean = float(part.probability.mean()) if n else None
        if n >= 30:
            observed = wins / n
            lower = (observed + z*z/(2*n) - z * math.sqrt(observed*(1-observed)/n + z*z/(4*n*n))) / (1+z*z/n)
            factor = min(1., max(0., lower / mean)) if mean else 0.
        else:
            lower, factor = 0., 0.
        bands[name] = {"n": n, "wins": wins, "predicted_mean": mean, "wilson_lower": lower, "factor": factor}
    return {"method": "2021 Q4 max-EV selection odds-band Wilson lower stress; not individual uncertainty",
            "minimum_band_sample": 30, "fit_date_from": str(development.race_date.min()),
            "fit_date_to": str(development.race_date.max()), "bands": bands}


def apply_conservative(frame, artifact):
    value = frame.copy()
    factors = odds_band(value).map({name: v["factor"] for name, v in artifact["bands"].items()})
    if factors.isna().any():
        raise ValueError("Unknown conservative band")
    value["p_lower"] = value.probability * factors
    return value


def payout_for(selected):
    result = pd.Series(0., index=selected.index)
    won = selected.finish.eq(1)
    payout = pd.to_numeric(selected.tansho_payout, errors="coerce")
    valid = payout.ge(100) & np.isfinite(payout) & payout.mod(10).eq(0)
    result.loc[won] = payout.loc[won].where(valid.loc[won], np.nan)
    if "payout_confirmed_race" in selected:
        result.loc[~selected.payout_confirmed_race.eq(True)] = np.nan
    return result


def betting_summary(selected, *, race_count):
    n = len(selected)
    payouts = payout_for(selected)
    unknown = int(payouts.isna().sum())
    total = float(payouts.sum()) if not unknown else None
    stake = n * 100
    return {"bets": n, "race_count": race_count, "bet_rate": n / race_count if race_count else None,
            "stake_yen": stake, "unknown_payouts": unknown, "payout_yen": total,
            "roi_percent": total / stake * 100 if stake and total is not None else None,
            "profit_yen": total - stake if total is not None else None,
            "hit_rate": float(selected.finish.eq(1).mean()) if n else None,
            **{k: v for k, v in calibration(selected).items() if k not in {"races", "horses"}}}


def coverage_cutoffs(development, *, conservative=False):
    maxima = maximum_ev(development, conservative=conservative)
    # No outcome enters this cutoff calculation. >= gives at least intended coverage on ties.
    return {str(c): float(np.quantile(maxima.selection_ev, 1-c)) for c in COVERAGES}


def policy_tables(frame, cutoffs):
    threshold, coverage, bands = [], [], []
    races = frame.race_id.nunique()
    for kind in ["ordinary", "conservative"]:
        maxima = maximum_ev(frame, conservative=kind == "conservative")
        for t in THRESHOLDS:
            threshold.append({"kind": kind, "threshold": t, **betting_summary(maxima.loc[maxima.selection_ev.gt(t)], race_count=races)})
        for c, cutoff in cutoffs[kind].items():
            coverage.append({"kind": kind, "nominal_development_coverage": float(c), "frozen_cutoff": cutoff,
                             **betting_summary(maxima.loc[maxima.selection_ev.ge(cutoff)], race_count=races)})
        selected = maxima.loc[maxima.selection_ev.gt(1.10)].copy()
        selected["odds_band"] = odds_band(selected)
        horse_bands = odds_band(frame)
        for name in ODDS_LABELS:
            horses = frame.loc[horse_bands.eq(name)]
            bands.append({"kind": kind, "odds_band": name, "horses": len(horses),
                          "all_horse_predicted_win_rate": calibration(horses)["predicted_win_rate"],
                          "all_horse_actual_win_rate": calibration(horses)["actual_win_rate"],
                          **betting_summary(selected.loc[selected.odds_band.eq(name)], race_count=races)})
    return threshold, coverage, bands


def robustness(frame, *, conservative=False, samples=2000, seed=42):
    maxima = maximum_ev(frame, conservative=conservative)
    selected = maxima.loc[maxima.selection_ev.gt(1.10)].copy()
    summary = betting_summary(selected, race_count=frame.race_id.nunique())
    result = {**summary, "unit": "whole race_date including no-bet days", "samples": samples,
              "selection_adjusted": False, "roi_ci95": None, "maximum_payout_yen": None,
              "roi_excluding_largest_ticket": None, "roi_excluding_top3_tickets": None,
              "top3_share_of_total_payout": None, "top3_share_of_positive_ticket_profit": None}
    if not len(selected) or summary["unknown_payouts"]:
        return result
    selected["payout"] = payout_for(selected)
    days = sorted(frame.race_date.unique())
    by_day = selected.groupby("race_date").agg(payout=("payout", "sum"), bets=("payout", "size")).reindex(days, fill_value=0)
    payouts, bets = by_day.payout.to_numpy(), by_day.bets.to_numpy()
    rng = np.random.default_rng(seed)
    estimates = []
    for _ in range(samples):
        idx = rng.integers(0, len(days), len(days))
        count = bets[idx].sum()
        if count:
            estimates.append(float(payouts[idx].sum() / count))
    result["roi_ci95"] = [float(v) for v in np.quantile(estimates, [.025, .975])] if estimates else None
    ranked = selected.payout.sort_values(ascending=False)
    total = ranked.sum()
    result["maximum_payout_yen"] = float(ranked.iloc[0])
    for k, key in [(1, "roi_excluding_largest_ticket"), (3, "roi_excluding_top3_tickets")]:
        result[key] = float(ranked.iloc[k:].sum() / (len(ranked)-k)) if len(ranked) > k else None
    result["top3_share_of_total_payout"] = float(ranked.head(3).sum() / total) if total else None
    positive = (ranked - 100).clip(lower=0)
    result["top3_share_of_positive_ticket_profit"] = float(positive.head(3).sum() / positive.sum()) if positive.sum() else None
    result["payout_removal_definition"] = "Remove the largest 1 or 3 payout tickets AND their 100 yen stakes"
    return result
