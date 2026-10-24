"""Fixed Phase-A rank diagnostics, separate from probability fitting and ROI.

No database, production, betting, model training, or network dependencies.
All comparisons use the same complete, no-tie, at-least-eight-runner races.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
import numpy as np
import pandas as pd

from market_segments.evaluation import compare_day_bootstrap, simultaneous_bands

RULES = {
    "win_top1": ("win", [(0,)]),
    "place_top1": ("place", [(0,)]),
    "wide_12": ("wide", [(0, 1)]),
    "wide_13": ("wide", [(0, 2)]),
    "quinella_12": ("quinella", [(0, 1)]),
    "quinella_top3_box": ("quinella", list(combinations(range(3), 2))),
}
PRIMARY = ["top2_recall_at2", "top3_recall_at3", *RULES]


def validate(frame):
    required = {"race_id", "race_date", "horse_number", "finish", "h04_score", "odds", "expected_runner_count", "tansho_payout"}
    if frame.empty or not required <= set(frame) or frame.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("complete unique runner keys required")
    if frame.race_id.isna().any() or frame.race_id.astype(str).str.strip().eq("").any():
        raise ValueError("nonempty race identifiers required")
    dates = frame.race_date.astype(str)
    if not dates.str.fullmatch(r"\d{8}").all() or not dates.between("20210101", "20221231").all():
        raise ValueError("only 2021-2022 permitted")
    pd.to_datetime(dates, format="%Y%m%d", errors="raise")
    for name in ["horse_number", "finish", "h04_score", "odds", "expected_runner_count"]:
        if not np.isfinite(frame[name].to_numpy(float)).all():
            raise ValueError("nonfinite:" + name)
    if not frame.odds.ge(1).all():
        raise ValueError("positive historical odds required")
    for _, part in frame.groupby("race_id", sort=False):
        n = len(part)
        if not (8 <= n <= 18 and part.expected_runner_count.eq(n).all() and part.race_date.nunique() == 1):
            raise ValueError("common Phase-A race count/date mismatch")
        if sorted(part.finish.tolist()) != list(range(1, n + 1)):
            raise ValueError("strictly complete no-tie final ranks required")
        if not (part.horse_number.between(1, 18).all() and part.horse_number.eq(np.floor(part.horse_number)).all()):
            raise ValueError("valid horse numbers required")


def _model_rows(frame, model):
    rows, topn, tickets = [], [], []
    for race_id, original in frame.groupby("race_id", sort=True):
        part = original.sort_values(["h04_score" if model == "H04" else "odds", "horse_number"],
                                    ascending=[model != "H04", True], kind="stable")
        finish, numbers = part.finish.to_numpy(int), part.horse_number.to_numpy(int)
        date = str(part.race_date.iloc[0])
        period = "calibration" if date <= "20210630" else "development" if date <= "20211231" else "validation"
        base = {"race_id": str(race_id), "race_date": date, "period": period, "model": model, "runners": len(part)}
        values = dict(base)
        for n in range(1, 6):
            first = finish[:n]
            record = {**base, "top_n": n,
                "winner_in_topn": int(1 in first), "top2_recall": float((first <= 2).sum() / 2),
                "top3_recall": float((first <= 3).sum() / 3),
                "exacta_pair_in_topn": int({1, 2} <= set(first)),
                "quinella_pair_in_topn": int({1, 2} <= set(first)),
                "trifecta_horses_in_topn": int({1, 2, 3} <= set(first)),
                "ordered_leading_pair_hit": int(tuple(finish[:2]) == (1, 2)) if n >= 2 else 0}
            topn.append(record)
            if n in (2, 3):
                values[f"top{n}_recall_at{n}"] = record[f"top{n}_recall"]
        for rule, (kind, choices) in RULES.items():
            hits = []
            for choice in choices:
                ranks = finish[list(choice)]
                hit = bool(ranks[0] == 1) if kind == "win" else bool(ranks[0] <= 3) if kind == "place" else bool(np.all(ranks <= 3)) if kind == "wide" else set(ranks) == {1, 2}
                hits.append(hit)
                # No non-win monetary accounting: payout-only evidence is not odds.
                paid = float(part.iloc[choice[0]].tansho_payout) if kind == "win" and hit else 0. if kind == "win" else None
                if kind == "win" and hit and (not np.isfinite(paid) or paid < 100 or paid % 10):
                    raise ValueError("win payout not audited")
                tickets.append({**base, "rule": rule, "bet_type": kind,
                    "horses": "-".join(map(str, sorted(numbers[list(choice)]))), "hit": int(hit),
                    "payout_yen": paid, "roi_evaluable": kind == "win", "fixed_unit_yen": 100})
            # Race-level hit probability for rules, including any winning ticket in box.
            values[rule] = int(any(hits))
        rows.append(values)
    return pd.DataFrame(rows), pd.DataFrame(topn), pd.DataFrame(tickets)


def rank_baselines(frame):
    validate(frame)
    # Independent deterministic model branches; no shared mutable frame state.
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda name: _model_rows(frame, name), ["H04", "Market"]))
    return tuple(pd.concat([value[i] for value in results], ignore_index=True) for i in range(3))


def topn_summary(topn):
    metrics = ["winner_in_topn", "top2_recall", "top3_recall", "exacta_pair_in_topn", "quinella_pair_in_topn", "trifecta_horses_in_topn", "ordered_leading_pair_hit"]
    rows = []
    for (period, model, n), part in topn.groupby(["period", "model", "top_n"], sort=True):
        rows.append({"period": period, "model": model, "top_n": int(n), "races": len(part),
                     "runners": int(part.runners.sum()), **{key: float(part[key].mean()) for key in metrics}})
    return pd.DataFrame(rows)


def payout_stats(values):
    values = np.asarray(values, dtype=float)
    n, total = len(values), float(values.sum())
    ranked = np.sort(values)[::-1]
    positive = values[values > 0]
    return {"stake_yen": n * 100, "return_yen": total, "profit_yen": total - n * 100,
        "roi_percent": total / n if n else None,
        "average_hit_payout_yen": float(positive.mean()) if len(positive) else None,
        "median_hit_payout_yen": float(np.median(positive)) if len(positive) else None,
        "max_payout_yen": float(ranked[0]) if n else None,
        "top1_payout_concentration": float(ranked[0] / total) if total else None,
        "top3_payout_concentration": float(ranked[:3].sum() / total) if total else None,
        "max_payout_removed_roi": float(ranked[1:].sum() / (n - 1)) if n > 1 else None,
        "top3_payout_removed_roi": float(ranked[3:].sum() / (n - 3)) if n > 3 else None}


def rule_summary(tickets):
    rows = []
    for (period, model, rule), part in tickets.groupby(["period", "model", "rule"], sort=True):
        evaluable = bool(part.roi_evaluable.all())
        money = payout_stats(part.payout_yen) if evaluable else {key: None for key in payout_stats([])}
        rows.append({"period": period, "model": model, "rule": rule, "bet_type": part.bet_type.iloc[0],
            "races": int(part.race_id.nunique()), "bet_races": int(part.race_id.nunique()), "bet_rate": 1.,
            "bets": len(part), "ticket_count": len(part), "hit_count": int(part.hit.sum()),
            "hit_rate": float(part.hit.mean()), "race_hit_rate": float(part.groupby("race_id").hit.max().mean()),
            "roi_evaluable": evaluable, "status": "historical_win_reference" if evaluable else "prediction_only_no_all_combination_odds",
            **money})
    return pd.DataFrame(rows)


def ranking_gate(races, *, minimum_races=300, minimum_runners=3000, samples=2000, seed=42):
    """Gate before temperature calibration: eight prespecified paired contrasts."""
    if (races.empty or races.race_id.isna().any() or races.race_id.astype(str).str.strip().eq("").any()
            or races.duplicated(["race_id", "model"]).any() or set(races.model) != {"H04", "Market"}
            or not set(races.period) <= {"calibration", "development", "validation"}):
        raise ValueError("unique complete race/model identities required")
    val, development, family, columns = [], {}, [], []
    for period in ["development", "validation"]:
        view = races.loc[races.period.eq(period)]
        h = view.loc[view.model.eq("H04")].set_index("race_id").sort_index()
        m = view.loc[view.model.eq("Market")].set_index("race_id").sort_index()
        if h.empty or not h[["race_date", "runners"]].equals(m[["race_date", "runners"]]):
            raise ValueError("same complete evaluation races required")
        sample_ok = len(h) >= minimum_races and h.runners.sum() >= minimum_runners
        for metric in PRIMARY:
            delta = (h[metric] - m[metric]).to_numpy(float)
            stats, boot = compare_day_bootstrap(delta, h.race_date.to_numpy(), samples=samples, seed=seed)
            row = {"period": period, "metric": metric, "races": len(h), "runners": int(h.runners.sum()),
                "sample_pass": bool(sample_ok), "H04": float(h[metric].mean()), "Market": float(m[metric].mean()), **stats}
            if period == "development":
                development[metric] = row
            else:
                mid = h.race_date.le("20220630").to_numpy()
                row.update(H1_delta=float(delta[mid].mean()) if mid.any() else None,
                           H2_delta=float(delta[~mid].mean()) if (~mid).any() else None,
                           H1_races=int(mid.sum()), H2_races=int((~mid).sum()))
                val.append(row)
                family.append(row)
                columns.append(boot)
    bands = simultaneous_bands(family, columns)
    bands["scope"] = "eight prespecified validation H04-minus-Market rank-recall/fixed-rule race-hit contrasts"
    for row in val:
        d = development[row["metric"]]
        row["development_delta"] = d["point"]
        row["development_sample_pass"] = d["sample_pass"]
        row["lead"] = bool(d["sample_pass"] and row["sample_pass"] and d["point"] > 0 and row["family_ci95_low"] > 0
                           and row["H1_delta"] is not None and row["H2_delta"] is not None
                           and row["H1_delta"] > 0 and row["H2_delta"] > 0)
    lead = [row["metric"] for row in val if row["lead"]]
    nonwin_lead = [metric for metric in lead if metric != "win_top1"]
    return {"gate": "CONTINUE_CALIBRATION" if nonwin_lead else "STOP_NO_STABLE_RANK_INCREMENT",
        "leads": lead, "independent_validation": False, "new_model_training": False,
        "nonwin_leads": nonwin_lead, "calibrator_fitting_allowed": bool(nonwin_lead), "phase_B_allowed": False, "phase_C_allowed": False,
        "stop_scope": "If gate fails, no temperature fitting, probability/EV exploration or Phase B/C. Mathematical distribution code remains synthetic-tested only.",
        "primary_comparisons": val, "development_comparisons": list(development.values()), "multiplicity": bands}


def win_roi_bootstrap(tickets, *, samples=2000, seed=42):
    rows = []
    for (period, model), part in tickets.loc[tickets.rule.eq("win_top1") & tickets.period.isin(["development", "validation"])].groupby(["period", "model"]):
        # One 100-yen ticket per race: mean payout is ROI in percent.
        stats, _ = compare_day_bootstrap(part.payout_yen.to_numpy(float), part.race_date.to_numpy(), samples=samples, seed=seed)
        rows.append({"period": period, "model": model, "bet_type": "win", "rule": "win_top1", **stats,
                     "metric": "ROI_percent", "status": "historical_only_not_executable_T5"})
    return rows
