"""Predeclared, outcome-conditioned diagnostics; never a selection/policy API.

No training, file writes, acquisition, promotion or access to 2023+ observations.
Rank diagnostics use ordinal horse-number tie breaking without altering the
older residual model's fractional-rank input columns.
"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd


PROBABILITY_EDGES = [-np.inf, -.10, -.05, -.02, 0., .02, .05, .10, np.inf]
PROBABILITY_LABELS = ["<=-10pt", "(-10,-5]pt", "(-5,-2]pt", "(-2,0]pt",
                      "(0,2]pt", "(2,5]pt", "(5,10]pt", ">10pt"]
RANK_EDGES = [-np.inf, -4.5, -2.5, -.5, .5, 2.5, 4.5, np.inf]
RANK_LABELS = ["<=-5", "-4..-3", "-2..-1", "0", "+1..+2", "+3..+4", ">=+5"]
REQUIRED = {"race_id", "race_date", "horse_number", "expected_runner_count", "finish", "odds",
            "normalized_market_probability", "h04_score", "h04_probability", "tansho_payout"}
FORBIDDEN_COMPARISON = {"finish", "is_win", "tansho_payout", "time_seconds", "probability",
                        "case_A", "case_B", "case_C", "case_D", "case_tied"}


def diagnostic_definition():
    """JSON-safe constants for freezing *before* inspecting diagnostic results."""
    return {
        "schema": "market-segment-diagnostics-v1", "maximum_date": "20221231",
        "validation": "2022 is research-known, not independent confirmation",
        "cohort": "complete competition-min final ranks, exactly one winner, 2..18 runners; nonwinning dead heats allowed",
        "probability_divergence": "h04_probability - normalized_market_probability",
        "probability_edges": ["-inf", -.10, -.05, -.02, 0., .02, .05, .10, "+inf"],
        "probability_labels": PROBABILITY_LABELS.copy(), "probability_right_closed": True,
        "rank_divergence": "market_rank_ordinal - h04_rank_ordinal",
        "ordinal_rank": "descending probability/score, exact ties ascending horse_number",
        "rank_edges": ["-inf", -4.5, -2.5, -.5, .5, 2.5, 4.5, "+inf"],
        "rank_labels": RANK_LABELS.copy(), "rank_right_closed": True,
        "case_A": "N>=6, market rank<=3, finish>ceil(N/2)",
        "case_B": "N>=6, market rank>=ceil(0.75*N), finish<=3",
        "case_C": "abs(rank divergence)>=5 and absolute H04 rank error<market error; truth uses average rank for nonwinner ties",
        "case_D": "abs(rank divergence)>=5 and absolute market rank error<H04 error; truth uses average rank for nonwinner ties",
        "case_tied": "abs(rank divergence)>=5 and absolute rank errors equal",
        "case_overlap": "A/B may overlap C/D; not a partition except C/D/tied within divergent horses",
        "feature_comparison": "all predeclared numeric columns; C-minus-D SMD using sqrt((varC+varD)/2), ddof=1",
        "comparison_limit": "outcome-conditioned descriptive association, not causal or deployable feature selection",
        "roi": "100 yen on every included horse; multi-ticket diagnostic reference, not betting policy",
        "payout": "confirmed single-winner official payout per 100 yen; unknown race settlement makes whole ROI null",
        "odds_asof": "historical source cutoff unverified; not T-5 or executable locked odds",
    }


def _validate(frame):
    if frame.empty or not REQUIRED.issubset(frame):
        raise ValueError("complete-historical-diagnostic-cohort-required")
    if frame[["race_id", "horse_number"]].isna().any().any() or frame.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("missing-or-duplicate-runner-identity")
    dates = frame.race_date.astype(str)
    if not dates.str.fullmatch(r"\d{8}").all() or dates.gt("20221231").any():
        raise ValueError("only-pre-2023-historical-diagnostics-permitted")
    pd.to_datetime(dates, format="%Y%m%d", errors="raise")
    groups = frame.groupby("race_id", sort=False)
    counts = groups.race_id.transform("size")
    if (not counts.between(2, 18).all() or not frame.expected_runner_count.eq(counts).all()
            or groups.race_date.nunique().ne(1).any()):
        raise ValueError("incomplete-or-inconsistent-race")
    for col in ("horse_number", "finish", "odds", "normalized_market_probability", "h04_score", "h04_probability"):
        if not np.isfinite(frame[col].to_numpy(float)).all():
            raise ValueError("nonfinite-diagnostic-input:" + col)
    if (not frame.horse_number.between(1, 18).all() or not frame.horse_number.eq(np.floor(frame.horse_number)).all()
            or not frame.finish.between(1, counts).all() or not frame.finish.eq(np.floor(frame.finish)).all()
            or not frame.odds.ge(1).all()):
        raise ValueError("invalid-runner-finish-or-odds")
    if (not frame.finish.eq(1).groupby(frame.race_id).sum().eq(1).all()
            or not frame.finish.groupby(frame.race_id).rank(method="min").eq(frame.finish).all()):
        raise ValueError("single-winner-valid-competition-min-final-ranks-required")
    for col in ("normalized_market_probability", "h04_probability"):
        if not frame[col].between(0, 1).all() or not np.allclose(groups[col].sum(), 1., atol=1e-7, rtol=0):
            raise ValueError("invalid-race-probability-simplex:" + col)


def add_divergence_columns(frame):
    _validate(frame)
    value = frame.copy().reset_index(drop=True)
    for score, column in [("normalized_market_probability", "market_rank_ordinal"), ("h04_score", "h04_rank_ordinal")]:
        ordered = value.sort_values(["race_id", score, "horse_number"], ascending=[True, False, True], kind="stable")
        ranks = ordered.groupby("race_id", sort=False).cumcount() + 1
        value[column] = ranks.reindex(value.index).astype(int)
    value["probability_divergence"] = value.h04_probability - value.normalized_market_probability
    value["rank_divergence"] = value.market_rank_ordinal - value.h04_rank_ordinal
    value["probability_bucket"] = pd.cut(value.probability_divergence, PROBABILITY_EDGES, labels=PROBABILITY_LABELS, right=True)
    value["rank_bucket"] = pd.cut(value.rank_divergence, RANK_EDGES, labels=RANK_LABELS, right=True)
    return value


def _payouts(full):
    """Unknown official race settlement remains unknown, including losers."""
    payout = pd.to_numeric(full.tansho_payout, errors="coerce")
    won = full.finish.eq(1)
    valid = np.isfinite(payout) & payout.ge(100) & payout.mod(10).eq(0)
    confirmed = (valid & won).groupby(full.race_id).transform("any")
    if "payout_confirmed_race" in full:
        confirmed &= full.payout_confirmed_race.eq(True).groupby(full.race_id).transform("all")
    return pd.Series(np.where(won, payout, 0.), index=full.index).where(confirmed, np.nan)


def _summary(part, payout):
    n = len(part)
    market = float(part.normalized_market_probability.mean()) if n else None
    h04 = float(part.h04_probability.mean()) if n else None
    actual = float(part.finish.eq(1).mean()) if n else None
    money = payout.reindex(part.index)
    known = money.notna()
    known_n = int(known.sum())
    return {
        "horses": n, "races": int(part.race_id.nunique()),
        "market_predicted_win_rate": market, "h04_predicted_win_rate": h04,
        "actual_win_rate": actual,
        "market_calibration_actual_over_predicted": actual / market if market else None,
        "h04_calibration_actual_over_predicted": actual / h04 if h04 else None,
        "average_odds": float(part.odds.mean()) if n else None,
        "hypothetical_stake_yen": 100 * n, "payout_unknown_horses": n - known_n,
        "hypothetical_payout_yen": float(money.sum()) if n and known_n == n else None,
        "hypothetical_roi_percent": float(money.sum() / n) if n and known_n == n else None,
        "known_payout_horses": known_n,
        "known_subset_roi_percent": float(money[known].sum() / known_n) if known_n else None,
        "roi_use": "multi_ticket_100_yen_per_horse_reference_not_policy",
    }


def divergence_analysis(frame):
    """All 15 fixed buckets, including empty buckets, in predefined order."""
    value = add_divergence_columns(frame)
    payouts = _payouts(value)
    rows = []
    for kind, column, labels in [("probability", "probability_bucket", PROBABILITY_LABELS),
                                  ("rank", "rank_bucket", RANK_LABELS)]:
        for label in labels:
            rows.append({"divergence_type": kind, "bucket": label,
                         **_summary(value.loc[value[column].eq(label)], payouts)})
    return pd.DataFrame(rows)


def extract_cases(frame):
    """Outcome-conditioned case flags; never predictive segment membership."""
    value = add_divergence_columns(frame)
    n = value.expected_runner_count
    value["case_A"] = n.ge(6) & value.market_rank_ordinal.le(3) & value.finish.gt(np.ceil(n / 2))
    value["case_B"] = n.ge(6) & value.market_rank_ordinal.ge(np.ceil(.75 * n)) & value.finish.le(3)
    value["actual_average_rank"] = value.finish.groupby(value.race_id).rank(method="average")
    value["h04_absolute_rank_error"] = abs(value.h04_rank_ordinal - value.actual_average_rank)
    value["market_absolute_rank_error"] = abs(value.market_rank_ordinal - value.actual_average_rank)
    divergent = value.rank_divergence.abs().ge(5)
    value["case_C"] = divergent & value.h04_absolute_rank_error.lt(value.market_absolute_rank_error)
    value["case_D"] = divergent & value.market_absolute_rank_error.lt(value.h04_absolute_rank_error)
    value["case_tied"] = divergent & value.market_absolute_rank_error.eq(value.h04_absolute_rank_error)
    return value


def _feature_rows(value, columns):
    if len(set(columns)) != len(columns) or any(c in FORBIDDEN_COMPARISON or c.startswith("case_") for c in columns):
        raise ValueError("unique-predeclared-nonoutcome-feature-list-required")
    rows = []
    for column in columns:
        record = {"feature": column, "comparison": "C_minus_D", "interpretation": "descriptive_not_causal_not_feature_selection"}
        samples = {}
        for case in ("C", "D"):
            part = value.loc[value["case_" + case]]
            numeric = pd.to_numeric(part[column], errors="coerce") if column in part else pd.Series(np.nan, index=part.index)
            finite = numeric.loc[np.isfinite(numeric)]
            samples[case] = finite
            record.update({case + "_horses": len(part), case + "_races": int(part.race_id.nunique()),
                           case + "_observed": len(finite), case + "_missing": len(part) - len(finite),
                           case + "_coverage": len(finite) / len(part) if len(part) else None,
                           case + "_mean": float(finite.mean()) if len(finite) else None,
                           case + "_std": float(finite.std(ddof=1)) if len(finite) >= 2 else None})
        c, d = samples["C"], samples["D"]
        record["standardized_mean_difference"] = None
        if column not in value:
            record["status"] = "unavailable_column"
        elif min(len(c), len(d)) < 2:
            record["status"] = "insufficient_observed_values"
        else:
            denominator = math.sqrt((c.var(ddof=1) + d.var(ddof=1)) / 2)
            if denominator > 0:
                record["standardized_mean_difference"] = float((c.mean() - d.mean()) / denominator)
                record["status"] = "descriptive_only"
            elif c.mean() == d.mean():
                record["standardized_mean_difference"] = 0.
                record["status"] = "equal_constant_values"
            else:
                record["status"] = "zero_variance_unequal_means"
        rows.append(record)
    return rows


def case_feature_comparison(frame, feature_columns):
    return pd.DataFrame(_feature_rows(extract_cases(frame), list(feature_columns)))


def case_analysis(frame, feature_columns):
    """JSON-safe summaries; all requested columns retained, never sorted by SMD."""
    value = extract_cases(frame)
    payouts = _payouts(value)
    cases = [{"case": case, **_summary(value.loc[value["case_" + case]], payouts)} for case in ("A", "B", "C", "D", "tied")]
    return {"definition": diagnostic_definition(), "case_summary": cases,
            "feature_comparison": _feature_rows(value, list(feature_columns)),
            "cohort": {"horses": len(value), "races": int(value.race_id.nunique()),
                       "date_from": str(value.race_date.min()), "date_to": str(value.race_date.max())}}
