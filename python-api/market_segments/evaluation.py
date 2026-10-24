"""Fixed whole-race segment comparisons, with shared day-cluster uncertainty.

Every specified cell is reported, including underpowered/non-improving cells.
The simultaneous intervals are centered, standardized bootstrap max-|Z| bands,
not a claim of independent validation or correction for earlier H04 selection.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from market_edge.evaluation import (prediction_metrics, maximum_ev, calibration,
                                   betting_summary, payout_for)


def race_losses(frame):
    ids = frame.race_id
    y = frame.finish.eq(1).to_numpy(float)
    p = frame.probability.to_numpy(float)
    return pd.DataFrame({"race_id": ids, "race_date": frame.race_date,
        "nll": -y * np.log(np.maximum(p, np.finfo(float).tiny)), "brier": (p - y) ** 2}).groupby("race_id", sort=True).agg(
            race_date=("race_date", "first"), nll=("nll", "sum"), brier=("brier", "sum"))


def compare_day_bootstrap(delta, dates, *, samples=2000, seed=42, all_days=None, draws=None):
    """Paired whole-day resampling, weighted by races, including zero-member days."""
    delta, dates = np.asarray(delta, dtype=float), np.asarray(dates)
    if (delta.ndim != 1 or dates.ndim != 1 or len(delta) != len(dates)
            or not len(delta) or not np.isfinite(delta).all() or pd.isna(dates).any()):
        raise ValueError("finite-paired-race-differences-required")
    if isinstance(samples, (bool, np.bool_)) or not isinstance(samples, (int, np.integer)) or samples < 2:
        raise ValueError("at-least-two-bootstrap-samples-required")
    days = sorted(set(dates)) if all_days is None else list(all_days)
    if (not days or pd.isna(days).any() or pd.Index(days).has_duplicates
            or not set(dates).issubset(days)):
        raise ValueError("unique-global-days-must-cover-all-paired-dates")
    daily = pd.DataFrame({"delta": np.asarray(delta), "date": np.asarray(dates)}).groupby("date").agg(total=("delta", "sum"), count=("delta", "size")).reindex(days, fill_value=0)
    if draws is None:
        draws = np.random.default_rng(seed).integers(0, len(days), size=(samples, len(days)))
    draws = np.asarray(draws)
    if (draws.shape != (samples, len(days)) or not np.issubdtype(draws.dtype, np.integer)
            or np.any(draws < 0) or np.any(draws >= len(days))):
        raise ValueError("shared-draws-must-be-valid-whole-day-indices")
    count = daily["count"].to_numpy()[draws].sum(axis=1)
    total = daily.total.to_numpy()[draws].sum(axis=1)
    boot = np.divide(total, count, out=np.full(len(draws), np.nan), where=count > 0)
    finite = boot[np.isfinite(boot)]
    if len(finite) < 2:
        raise ValueError("insufficient-nonempty-day-resamples")
    return {"point": float(np.mean(delta)), "ci95_low": float(np.quantile(finite, .025)),
            "ci95_high": float(np.quantile(finite, .975)), "bootstrap_samples": len(draws),
            "usable_samples": len(finite), "days": int(pd.Series(dates).nunique())}, boot


def simultaneous_bands(records, bootstrap_columns):
    """One fixed family; no post-hoc favorable-segment selection."""
    if len(records) != len(bootstrap_columns):
        raise ValueError("every-family-record-requires-one-bootstrap-column")
    if not records:
        return {"method": "centered_standardized_day_bootstrap_max_abs_Z", "comparisons": 0, "critical": None}
    columns = [np.asarray(column, dtype=float) for column in bootstrap_columns]
    if (any(column.ndim != 1 or len(column) < 2 for column in columns)
            or len({len(column) for column in columns}) != 1):
        raise ValueError("aligned-bootstrap-samples-required-for-entire-family")
    matrix = np.column_stack(columns)
    if not np.isfinite(matrix).all():
        raise ValueError("incomplete-bootstrap-family; no selective dropping")
    points = np.array([r["point"] for r in records], dtype=float)
    if not np.isfinite(points).all():
        raise ValueError("finite-family-points-required")
    se = matrix.std(axis=0, ddof=1)
    standardized = np.divide(abs(matrix - points), se, out=np.zeros_like(matrix), where=se > 0)
    critical = float(np.quantile(standardized.max(axis=1), .95))
    for i, record in enumerate(records):
        record["family_ci95_low"] = float(points[i] - critical * se[i])
        record["family_ci95_high"] = float(points[i] + critical * se[i])
        record["family_size"] = len(records)
    return {"method": "centered_standardized_day_bootstrap_max_abs_Z", "comparisons": len(records),
            "critical": critical, "scope": "all eligible validation model/segment/NLL+Brier comparisons including ALL",
            "limitation": "exploratory multiplicity adjustment; not correction for original H04 selection or an independent holdout"}


def _same_cohort(left, right):
    keys = ["race_id", "horse_number"]
    l = left.sort_values(keys).reset_index(drop=True)
    r = right.sort_values(keys).reset_index(drop=True)
    names = keys + ["race_date", "phase", "finish", "odds", "expected_runner_count", "tansho_payout"]
    if "payout_confirmed_race" in left or "payout_confirmed_race" in right:
        names.append("payout_confirmed_race")
    for name in names:
        if name not in l or name not in r or not l[name].equals(r[name]):
            raise ValueError("same-cohort-required:" + name)


def payout_concentration(selected):
    """Descriptive fixed-stake sensitivity, never a rule-selection criterion."""
    result = {"maximum_payout_yen": None, "top1_share_of_total_payout": None,
        "top3_share_of_total_payout": None, "top3_share_of_positive_ticket_profit": None,
        "roi_excluding_largest_ticket": None, "roi_excluding_top3_tickets": None}
    payouts = payout_for(selected)
    if selected.empty or payouts.isna().any():
        return result
    ranked = payouts.sort_values(ascending=False)
    total = float(ranked.sum())
    result["maximum_payout_yen"] = float(ranked.iloc[0])
    for count, key in [(1, "roi_excluding_largest_ticket"), (3, "roi_excluding_top3_tickets")]:
        # ROI percent = 100 * payout / (100 yen per remaining ticket).
        result[key] = float(ranked.iloc[count:].sum() / (len(ranked) - count)) if len(ranked) > count else None
    if total:
        result["top1_share_of_total_payout"] = float(ranked.iloc[0] / total)
        result["top3_share_of_total_payout"] = float(ranked.head(3).sum() / total)
    positive = (ranked - 100).clip(lower=0)
    if positive.sum():
        result["top3_share_of_positive_ticket_profit"] = float(positive.head(3).sum() / positive.sum())
    return result


def evaluate(models, race_flags, segments, *, minimum_races=300, minimum_runners=3000, samples=2000, seed=42):
    """Models map name to complete scored rows for Q4-2021 and 2022 only."""
    for value, minimum in [(minimum_races, 1), (minimum_runners, 1), (samples, 2)]:
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)) or value < minimum:
            raise ValueError("positive-fixed-counts-and-at-least-two-resamples-required")
    segments = list(segments)
    if len(set(segments)) != len(segments) or any(not isinstance(s, str) or not s or s == "ALL" for s in segments):
        raise ValueError("unique-named-segments-required-with-ALL-reserved")
    if "A_Market" not in models or race_flags.race_id.duplicated().any():
        raise ValueError("market-baseline-and-unique-race-flags-required")
    reference = models["A_Market"]
    if set(reference.phase) != {"development", "validation"}:
        raise ValueError("only-frozen-evaluation-periods-allowed")
    dates = reference.race_date.astype(str)
    if (not dates[reference.phase.eq("development")].between("20211001", "20211231").all()
            or not dates[reference.phase.eq("validation")].between("20220101", "20221231").all()):
        raise ValueError("frozen-evaluation-date-boundary-violated")
    for frame in models.values():
        _same_cohort(reference, frame)
        prediction_metrics(frame)
    flags = race_flags.set_index("race_id")
    if not set(reference.race_id) <= set(flags.index) or set(segments) - set(flags.columns):
        raise ValueError("all-evaluation-races-and-segments-required")
    if any(not flags[s].dropna().map(lambda value: isinstance(value, (bool, np.bool_))).all() for s in segments):
        raise ValueError("segment-flags-must-be-boolean-or-unknown")
    metrics, uncertainty, family_records, boot_columns = [], [], [], []
    val = reference.loc[reference.phase.eq("validation")]
    days = sorted(val.race_date.unique())
    draws = np.random.default_rng(seed).integers(0, len(days), size=(samples, len(days)))
    for period in ["development", "validation"]:
        market = reference.loc[reference.phase.eq(period)].copy()
        market_loss = race_losses(market)
        for segment in ["ALL", *segments]:
            selected_ids = set(market.race_id) if segment == "ALL" else set(flags.index[flags[segment].fillna(False).astype(bool)]) & set(market.race_id)
            unknown = 0 if segment == "ALL" else int(flags.reindex(market.race_id.unique())[segment].isna().sum())
            for name, all_rows in models.items():
                part = all_rows.loc[all_rows.phase.eq(period) & all_rows.race_id.isin(selected_ids)].copy()
                record = {"period": period, "segment": segment, "model": name, "races": len(selected_ids),
                    "horses": len(part), "unknown_segment_races": unknown,
                    "minimum_sample_pass": len(selected_ids) >= minimum_races and len(part) >= minimum_runners}
                if part.empty:
                    record["status"] = "no_evaluable_races"
                    metrics.append(record)
                    continue
                metric = prediction_metrics(part)
                record.update({k: v for k, v in metric.items() if k != "reliability"})
                maximum = maximum_ev(part)
                for key, value in calibration(maximum).items():
                    record["max_ev_" + key] = value
                top1 = part.sort_values(["race_id", "probability", "horse_number"], ascending=[True, False, True]).drop_duplicates("race_id")
                for label, selected in [("top1", top1), ("fixed_EV_gt_1_10", maximum.loc[maximum.selection_ev.gt(1.10)])]:
                    money = betting_summary(selected, race_count=len(selected_ids))
                    for key in ["bets", "roi_percent", "stake_yen", "payout_yen", "unknown_payouts",
                                "predicted_win_rate", "actual_win_rate", "calibration_ratio_actual_over_predicted"]:
                        record[label + "_" + key] = money[key]
                    for key, value in payout_concentration(selected).items():
                        record[label + "_" + key] = value
                record["status"] = "exploratory" if record["minimum_sample_pass"] else "underpowered"
                metrics.append(record)
                if name == "A_Market":
                    continue
                loss = race_losses(part)
                for metric_name in ["nll", "brier"]:
                    delta = loss[metric_name] - market_loss.reindex(loss.index)[metric_name]
                    base = {"period": period, "segment": segment, "model": name, "metric": metric_name,
                            "races": len(loss), "horses": len(part), "minimum_sample_pass": record["minimum_sample_pass"],
                            "unit": "whole race_date paired resampling, including no-member days"}
                    ci, boot = compare_day_bootstrap(delta.to_numpy(), loss.race_date.to_numpy(), samples=samples, seed=seed,
                        all_days=days if period == "validation" else sorted(market.race_date.unique()),
                        draws=draws if period == "validation" else None)
                    base.update(ci)
                    for suffix, lo, hi in [("H1", "20220101", "20220630"), ("H2", "20220701", "20221231")]:
                        half = loss.race_date.between(lo, hi)
                        chunk = delta.loc[half]
                        base["validation_" + suffix + "_delta"] = float(chunk.mean()) if len(chunk) else None
                        base["validation_" + suffix + "_races"] = len(chunk)
                        base["validation_" + suffix + "_days"] = int(loss.loc[half, "race_date"].nunique())
                    uncertainty.append(base)
                    if period == "validation" and record["minimum_sample_pass"]:
                        family_records.append(base)
                        boot_columns.append(boot)
    family = simultaneous_bands(family_records, boot_columns)
    candidates = []
    for segment in segments:
        for name in models:
            if name == "A_Market":
                continue
            val_rows = [r for r in uncertainty if r["period"] == "validation" and r["segment"] == segment and r["model"] == name]
            dev_rows = [r for r in uncertainty if r["period"] == "development" and r["segment"] == segment and r["model"] == name]
            val_sample_ok = len(val_rows) == 2 and all(r["minimum_sample_pass"] for r in val_rows)
            dev_sample_ok = len(dev_rows) == 2 and all(r["minimum_sample_pass"] for r in dev_rows)
            sample_ok = val_sample_ok and dev_sample_ok
            stable = sample_ok and all(r["point"] < 0 for r in dev_rows + val_rows)
            half_stable = stable and all(r["validation_H1_delta"] is not None and r["validation_H1_delta"] < 0 and r["validation_H2_delta"] is not None and r["validation_H2_delta"] < 0 for r in val_rows)
            significant = half_stable and all(r.get("family_ci95_high", float("inf")) < 0 for r in val_rows)
            candidates.append({"segment": segment, "model": name, "sample_pass": sample_ok,
                "development_sample_pass": dev_sample_ok, "validation_sample_pass": val_sample_ok,
                "development_and_validation_direction_consistent": stable, "validation_half_years_consistent": half_stable,
                "simultaneous_intervals_both_below_zero": significant,
                "candidate": "PROBABILITY_IMPROVEMENT_LEAD" if significant else "INCONCLUSIVE" if sample_ok else "UNDERPOWERED",
                "candidate_scope": "exploratory probability improvement only",
                "independent_validation": False, "selection_calibration_must_review": True,
                "payout_concentration_must_review": True,
                "production_adoption": False})
    return pd.DataFrame(metrics), pd.DataFrame(uncertainty), pd.DataFrame(candidates), family
