"""Finite retrospective win-ticket rule search AFTER a model is frozen.

No model fitting, probability calibration, network access, or ticket purchase is
performed. Score-gap thresholds and rule selection use development years only.
All market values are retrospective proxies, not verified pre-bet observations.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .common import REPO_ROOT


SCHEMA = "fixed-model-retrospective-win-rule-search-v1"
STAKE_YEN = 100
MARKET_POLICY = "retrospective_market_proxy"
ODDS_RANGES = ((1.0, 3.0), (3.0, 5.0), (5.0, 10.0),
               (10.0, 20.0), (20.0, 50.0), (50.0, None))
RANK_SETS = ((1,), (2,), (3,), (2, 3))
GAP_QUANTILES = (0.25, 0.5, 0.75)
_REQUIRED = ("race_date", "race_id", "horse_number", "prediction", "finish",
             "odds", "popularity", "tansho_payout")
_TRAIN_END_COLUMNS = ("fold_train_date_to", "training_date_to", "train_date_to")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _write_exclusive(path: Path, text: str) -> None:
    # Logical immutability: never replace an earlier run or an already frozen rule.
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


def _dates(values: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(values.dtype):
        result = values.dt.strftime("%Y%m%d")
    else:
        result = values.astype("string").str.strip().str.replace("-", "", regex=False).str.replace("/", "", regex=False)
    if result.isna().any() or not result.str.fullmatch(r"\d{8}").all():
        raise ValueError("Dates must be complete YYYYMMDD or ISO calendar dates")
    pd.to_datetime(result, format="%Y%m%d", errors="raise")
    return result.astype(str)


def _frame_hash(frame: pd.DataFrame) -> str:
    """Bounded-memory fingerprint of normalized, canonically ordered input."""
    columns = list(_REQUIRED) + [name for name in _TRAIN_END_COLUMNS if name in frame]
    columns += [name for name in ("expected_runner_count", "num_horses") if name in frame]
    ordered = frame.sort_values(["race_date", "race_id", "horse_number"], kind="stable")
    digest = hashlib.sha256(_json(columns).encode())
    for start in range(0, len(ordered), 4096):
        digest.update(ordered.iloc[start:start + 4096][columns].to_csv(
            index=False, header=False, na_rep="<missing>", float_format="%.17g", lineterminator="\n",
        ).encode("utf-8"))
    return digest.hexdigest()


def _normalize(predictions: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not isinstance(predictions, pd.DataFrame) or predictions.empty:
        raise ValueError("Nonempty fixed-model OOF predictions are required")
    if predictions.columns.duplicated().any() or not set(_REQUIRED).issubset(predictions.columns):
        raise ValueError(f"Unique required columns are needed: {_REQUIRED}")
    columns = list(_REQUIRED) + [column for column in _TRAIN_END_COLUMNS if column in predictions]
    columns += [column for column in ("expected_runner_count", "num_horses") if column in predictions]
    frame = predictions[columns].copy().reset_index(drop=True)
    frame["race_date"] = _dates(frame["race_date"])
    race_ids = frame["race_id"].astype("string").str.strip()
    if race_ids.isna().any() or race_ids.eq("").any():
        raise ValueError("Every runner needs a race_id")
    frame["race_id"] = race_ids.astype(str)
    for column in ("horse_number", "prediction", "finish", "odds", "popularity", "tansho_payout"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype(float)
    for column in ("horse_number", "prediction", "finish"):
        if not np.isfinite(frame[column]).all():
            raise ValueError(f"Nonfinite required result/prediction: {column}")
    if (not frame["horse_number"].between(1, 18).all()
            or not frame["horse_number"].eq(np.floor(frame["horse_number"])).all()
            or frame.duplicated(["race_id", "horse_number"]).any()):
        raise ValueError("Runner numbers must be distinct positive integers per race")
    frame["horse_number"] = frame["horse_number"].astype(int)
    counts = frame.groupby("race_id", sort=False)["horse_number"].transform("size")
    if counts.lt(2).any() or frame.groupby("race_id")["race_date"].nunique().ne(1).any():
        raise ValueError("Race cohorts must have at least two runners and one consistent date")
    if (not frame["finish"].ge(1).all() or not frame["finish"].le(counts).all()
            or not frame["finish"].eq(np.floor(frame["finish"])).all()
            or frame.assign(_winner=frame["finish"].eq(1)).groupby("race_id")["_winner"].sum().lt(1).any()):
        raise ValueError("Every race needs complete confirmed finish positions and a winner")
    for column in ("expected_runner_count", "num_horses"):
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
            if not frame[column].eq(counts).all():
                raise ValueError("Incomplete runner cohort; do not silently drop horses")
    checked = []
    for column in _TRAIN_END_COLUMNS:
        if column in frame:
            frame[column] = _dates(frame[column])
            if frame[column].ge(frame["race_date"]).any():
                raise ValueError("OOF training must end strictly before each prediction date")
            checked.append(column)
    for column in ("odds", "popularity"):
        frame.loc[~np.isfinite(frame[column]) | frame[column].le(0), column] = np.nan
    frame.loc[frame["popularity"].ne(np.floor(frame["popularity"])), "popularity"] = np.nan
    frame = frame.sort_values(["race_date", "race_id", "prediction", "horse_number"],
                              ascending=[True, True, False, True], kind="stable").reset_index(drop=True)
    frame["_rank"] = frame.groupby("race_id", sort=False).cumcount() + 1
    first_two = frame.loc[frame["_rank"].le(2)].pivot(index="race_id", columns="_rank", values="prediction")
    frame["_gap"] = frame["race_id"].map(first_two[1] - first_two[2])
    winning = frame.loc[frame["finish"].eq(1)].copy()
    winning["_valid"] = np.isfinite(winning["tansho_payout"]) & winning["tansho_payout"].gt(0)
    verified = winning.groupby("race_id")["_valid"].all()
    frame["_payout_verified"] = frame["race_id"].map(verified).astype(bool)
    frame["_return_yen"] = np.where(frame["_payout_verified"],
                                     np.where(frame["finish"].eq(1), frame["tansho_payout"], 0.0), np.nan)
    frame["_year"] = frame["race_date"].str[:4].astype(int)
    return frame, {
        "runner_rows": len(frame), "race_count": frame["race_id"].nunique(),
        "checked_oof_boundary_columns": checked,
        "oof_status": "date_boundary_checked" if checked else "caller_attested_not_independently_verified",
        "cohort_size_status": "checked_declared_runner_count" if any(c in frame for c in ("expected_runner_count", "num_horses")) else "observed_cohort_only_no_independent_size_proof",
        "market_missing_rows": {column: int(frame[column].isna().sum()) for column in ("odds", "popularity")},
        "race_payout_missing_count": int((~verified).sum()),
        "normalization_drops_runner_rows": False,
    }


def _candidates(development: pd.DataFrame) -> list[dict[str, Any]]:
    gaps = development.drop_duplicates("race_id")["_gap"]
    thresholds = {str(q): float(gaps.quantile(q)) for q in GAP_QUANTILES}
    rules = []
    for ranks in RANK_SETS:
        label = "rank" + "_".join(map(str, ranks))
        common = {"ranks": list(ranks), "odds_min": None, "odds_max_exclusive": None,
                  "gap_quantile": None, "gap_min": None, "popularity_min": None}
        rules.append({"rule_id": "baseline_top1" if ranks == (1,) else label,
                      **common, "complexity": 0 if ranks == (1,) else 1})
        for index, (lower, upper) in enumerate(ODDS_RANGES, 1):
            rules.append({"rule_id": f"{label}_odds{index}", **common,
                          "odds_min": lower, "odds_max_exclusive": upper, "complexity": 2})
        for q in GAP_QUANTILES:
            rules.append({"rule_id": f"{label}_gap{int(q * 100)}", **common,
                          "gap_quantile": q, "gap_min": thresholds[str(q)], "complexity": 2})
        for popularity in (2, 4):
            rules.append({"rule_id": f"{label}_pop{popularity}", **common,
                          "popularity_min": popularity, "complexity": 2})
        rules.append({"rule_id": f"{label}_gap50_pop2", **common, "gap_quantile": 0.5,
                      "gap_min": thresholds["0.5"], "popularity_min": 2, "complexity": 3})
    assert len(rules) == 52
    return rules


def _pick(frame: pd.DataFrame, rule: dict[str, Any]) -> pd.DataFrame:
    keep = frame["_rank"].isin(rule["ranks"])
    if rule["odds_min"] is not None:
        keep &= frame["odds"].ge(rule["odds_min"])
    if rule["odds_max_exclusive"] is not None:
        keep &= frame["odds"].lt(rule["odds_max_exclusive"])
    if rule["gap_min"] is not None:
        keep &= frame["_gap"].ge(rule["gap_min"])
    if rule["popularity_min"] is not None:
        keep &= frame["popularity"].ge(rule["popularity_min"])
    return frame.loc[keep]


def _drawdown(bets: pd.DataFrame) -> float:
    if bets.empty:
        return 0.0
    daily = bets.groupby("race_date", sort=True)["_return_yen"].agg(["sum", "size"])
    equity = np.r_[0.0, (daily["sum"] - STAKE_YEN * daily["size"]).cumsum().to_numpy()]
    return float(np.max(np.maximum.accumulate(equity) - equity))


def _totals(bets: pd.DataFrame, eligible_races: int) -> dict[str, Any]:
    count = len(bets)
    races = int(bets["race_id"].nunique())
    verified = bets.loc[bets["_payout_verified"]]
    subset_stake = STAKE_YEN * len(verified)
    subset_payout = float(verified["_return_yen"].sum())
    complete = len(verified) == count
    turnover = STAKE_YEN * count
    gross = subset_payout if complete else None
    net = gross - turnover if gross is not None else None
    gross_rate = gross / turnover * 100 if turnover and gross is not None else None
    return {
        "bet_count": count, "bet_race_count": races, "eligible_race_count": int(eligible_races),
        "race_coverage": races / eligible_races if eligible_races else None,
        "bet_date_count": int(bets["race_date"].nunique()), "stake_per_ticket_yen": STAKE_YEN,
        "turnover_yen": turnover, "gross_return_yen": gross, "net_profit_yen": net,
        "gross_return_rate_percent": gross_rate,
        "net_roi_percent": gross_rate - 100 if gross_rate is not None else None,
        "observed_gross_return_at_least_100_percent": gross_rate >= 100 if gross_rate is not None else None,
        "win_bet_count": int(bets["finish"].eq(1).sum()),
        "payout_complete_for_bet_races": complete, "payout_missing_bets": count - len(verified),
        "payout_missing_races": int(bets.loc[~bets["_payout_verified"], "race_id"].nunique()),
        "max_drawdown_yen": _drawdown(bets) if complete else None,
        "drawdown_basis": "date_daily_settled_profit; no intraday ordering or financing assumptions",
        "verified_subset": {
            "bet_count": len(verified), "race_count": int(verified["race_id"].nunique()),
            "turnover_yen": subset_stake, "gross_return_yen": subset_payout,
            "net_profit_yen": subset_payout - subset_stake,
            "gross_return_rate_percent": subset_payout / subset_stake * 100 if subset_stake else None,
            "net_roi_percent": (subset_payout / subset_stake - 1) * 100 if subset_stake else None,
            "max_drawdown_yen": _drawdown(verified), "not_full_rule_performance": not complete,
        },
    }


def _bootstrap_plan(frame: pd.DataFrame, samples: int, seed: int) -> tuple[list[str], np.ndarray]:
    days = sorted(frame["race_date"].unique())
    if len(days) < 2:
        return days, np.empty((0, len(days)))
    rng = np.random.default_rng(seed)
    return days, rng.multinomial(len(days), np.full(len(days), 1 / len(days)), size=samples)


def _confidence(bets: pd.DataFrame, days: list[str], weights: np.ndarray, *, complete: bool) -> dict[str, Any]:
    names = ("net_roi_percent", "net_profit_yen")
    null = {name: {"lower": None, "upper": None, "valid_replicates": 0} for name in names}
    if not complete or bets.empty or not weights.size:
        return null
    daily = bets.groupby("race_date")["_return_yen"].agg(["sum", "size"]).reindex(days, fill_value=0)
    stakes = STAKE_YEN * daily["size"].to_numpy(dtype=float)
    profits = daily["sum"].to_numpy(dtype=float) - stakes
    sampled_stakes = weights @ stakes
    sampled_profit = weights @ profits
    valid = sampled_stakes > 0
    values = {"net_roi_percent": sampled_profit[valid] / sampled_stakes[valid] * 100,
              "net_profit_yen": sampled_profit}
    return {name: {"lower": float(np.quantile(value, 0.025)) if len(value) else None,
                   "upper": float(np.quantile(value, 0.975)) if len(value) else None,
                   "valid_replicates": len(value)} for name, value in values.items()}


def _evaluate_rule(frame: pd.DataFrame, rule: dict[str, Any], years: tuple[int, ...],
                   bootstrap: tuple[list[str], np.ndarray]) -> dict[str, Any]:
    bets = _pick(frame, rule)
    metrics = _totals(bets, int(frame["race_id"].nunique()))
    days, weights = bootstrap
    metrics["by_year"] = {str(year): _totals(bets.loc[bets["_year"].eq(year)],
        int(frame.loc[frame["_year"].eq(year), "race_id"].nunique())) for year in years}
    metrics["date_bootstrap_95_ci"] = {
        "unit": "race_date", "date_count": len(days), "requested_replicates": len(weights),
        "resamples_all_cohort_dates_including_no_bet_dates": True,
        "selection_adjusted": False,
        "metrics": _confidence(bets, days, weights, complete=metrics["payout_complete_for_bet_races"]),
        "verified_subset_metrics": _confidence(bets.loc[bets["_payout_verified"]], days, weights, complete=True),
    }
    return metrics


def _eligibility(metrics: dict[str, Any], *, min_bets: int, min_races: int,
                 min_bets_per_year: int, min_dates: int, max_annual_net_roi_spread_pp: float) -> list[str]:
    reasons = []
    for key, minimum in (("bet_count", min_bets), ("bet_race_count", min_races), ("bet_date_count", min_dates)):
        if metrics[key] < minimum:
            reasons.append(f"insufficient_{key}")
    if not metrics["payout_complete_for_bet_races"]:
        reasons.append("unverified_winner_payout")
    annual = list(metrics["by_year"].values())
    if any(year["bet_count"] < min_bets_per_year for year in annual):
        reasons.append("insufficient_annual_bets")
    annual_roi = [year["net_roi_percent"] for year in annual]
    if any(value is None for value in annual_roi):
        reasons.append("annual_roi_unavailable")
    elif max(annual_roi) - min(annual_roi) > max_annual_net_roi_spread_pp:
        reasons.append("unstable_annual_roi_spread")
    if metrics["date_bootstrap_95_ci"]["metrics"]["net_roi_percent"]["lower"] is None:
        reasons.append("date_bootstrap_ci_unavailable")
    return reasons


def evaluate_betting_search(
    predictions: pd.DataFrame, output_dir: str | Path, *, model_id: str,
    payout_source: str = "official_confirmed", development_years: tuple[int, ...] = (2021, 2022),
    confirmation_years: tuple[int, ...] = (2023, 2024, 2025),
    retrospective_years: tuple[int, ...] = (2026,), min_bets: int = 200,
    min_races: int = 200, min_bets_per_year: int = 30, min_dates: int = 20,
    bootstrap_samples: int = 500, seed: int = 42,
    max_annual_net_roi_spread_pp: float = 100.0,
    prediction_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate exactly 52 preregistered rules against ONE fixed model's OOF.

    ``tansho_payout`` must contain official per-100-yen payouts on every winning
    runner (including dead heats); losing-runner NaN is normal. The caller owns
    that provenance assertion: odds are NEVER substituted for payouts. Pass the
    model contract/hash and OOF fold definitions in ``prediction_provenance``.
    Optional train-end columns are checked against prediction dates, but this
    function cannot independently prove model provenance from scores alone.

    Years must be ordered, nonoverlapping, nonempty tuples. All thresholds,
    minimum sample sizes, and annual stability limits are fixed before search.
    Gap cutoffs use only development races. Selection prioritizes the lower 95%
    date-bootstrap net-ROI bound, then the worst year, then annual stability;
    maximum gross return alone never selects a rule. If no nonbaseline rule has
    a strictly higher lower bound than the eligible baseline, keep the baseline.
    If nothing is eligible, report the baseline with selection declined.

    ``output_dir`` must not already exist. A frozen JSON rule and SHA-256 are
    exclusively written before confirmation/retrospective rule evaluation. No
    tuning or retries on those known-reused periods are performed. Repeating
    this entire search until a profitable result appears is not valid evidence.
    """
    if not isinstance(model_id, str) or not model_id.strip():
        raise ValueError("A fixed model_id is required")
    if payout_source != "official_confirmed":
        raise ValueError("Only official confirmed win payouts are supported; never implied odds payouts")
    periods = [tuple(values) for values in (development_years, confirmation_years, retrospective_years)]
    for years in periods:
        if (not years or any(type(year) is not int or not 1900 <= year <= 2200 for year in years)
                or list(years) != sorted(set(years))):
            raise ValueError("Period years must be nonempty sorted unique integer tuples")
    if not max(periods[0]) < min(periods[1]) or not max(periods[1]) < min(periods[2]):
        raise ValueError("Development, confirmation, and retrospective years must be ordered and disjoint")
    for name, value in (("min_bets", min_bets), ("min_races", min_races),
                        ("min_bets_per_year", min_bets_per_year), ("min_dates", min_dates)):
        if type(value) is not int or value < (2 if name == "min_dates" else 1):
            raise ValueError(f"{name} must be a positive integer (min_dates >= 2)")
    if type(bootstrap_samples) is not int or not 100 <= bootstrap_samples <= 5000:
        raise ValueError("Use 100-5000 bootstrap samples; a CI is required for selection")
    if type(seed) is not int or seed < 0:
        raise ValueError("seed must be a nonnegative integer")
    if (isinstance(max_annual_net_roi_spread_pp, bool) or not isinstance(max_annual_net_roi_spread_pp, (int, float))
            or not math.isfinite(max_annual_net_roi_spread_pp) or max_annual_net_roi_spread_pp < 0):
        raise ValueError("Annual ROI spread limit must be a finite nonnegative number")
    if prediction_provenance is not None and not isinstance(prediction_provenance, dict):
        raise ValueError("prediction_provenance must be a JSON object")
    provenance = json.loads(_json(prediction_provenance or {}))
    implementation = {"source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      "numpy_version": np.__version__, "pandas_version": pd.__version__}
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError("Choose a new output directory; frozen searches cannot be overwritten")
    for protected in (REPO_ROOT / "python-api/models", REPO_ROOT / "keiba/data"):
        if output == protected or output.is_relative_to(protected):
            raise ValueError("Betting search output must be isolated from models and source data")
    frame, audit = _normalize(predictions)
    development = frame.loc[frame["_year"].isin(periods[0])]
    if development.empty:
        raise ValueError("Development OOF predictions are required before searching rules")
    rules = _candidates(development)
    policy = {
        "minimum_bets": min_bets, "minimum_races": min_races, "minimum_bets_per_development_year": min_bets_per_year,
        "minimum_bet_dates": min_dates, "maximum_annual_net_roi_spread_pp": max_annual_net_roi_spread_pp,
        "ranking": ["date_bootstrap_net_roi_lower95_desc", "worst_annual_net_roi_desc",
                    "annual_net_roi_spread_asc", "rule_complexity_asc", "rule_id_asc"],
        "baseline_id": "baseline_top1", "baseline_lower_ci_strict_improvement_required_when_eligible": True,
        "stake_yen_per_ticket": STAKE_YEN, "candidate_count": len(rules), "maximum_candidates": 60,
        "bootstrap_samples": bootstrap_samples, "seed": seed,
        "market_policy": MARKET_POLICY, "payout_source_attestation": payout_source,
        "development_years": list(periods[0]), "confirmation_years": list(periods[1]),
        "known_reused_retrospective_years": list(periods[2]),
        "unseen_period_claimed": False, "confirmation_reselection": False,
    }
    bootstrap = _bootstrap_plan(development, bootstrap_samples, seed)
    evaluations = []
    for rule in rules:
        metrics = _evaluate_rule(development, rule, periods[0], bootstrap)
        reasons = _eligibility(metrics, min_bets=min_bets, min_races=min_races,
            min_bets_per_year=min_bets_per_year, min_dates=min_dates,
            max_annual_net_roi_spread_pp=max_annual_net_roi_spread_pp)
        evaluations.append({"rule": rule, "metrics": metrics, "eligible": not reasons, "rejection_reasons": reasons})
    baseline = evaluations[0]
    eligible = [candidate for candidate in evaluations if candidate["eligible"]]

    def selection_key(candidate):
        metrics = candidate["metrics"]
        annual = [row["net_roi_percent"] for row in metrics["by_year"].values()]
        return (-metrics["date_bootstrap_95_ci"]["metrics"]["net_roi_percent"]["lower"],
                -min(annual), max(annual) - min(annual), candidate["rule"]["complexity"], candidate["rule"]["rule_id"])

    selected = min(eligible, key=selection_key) if eligible else baseline
    reason = "highest_eligible_lower95_ci" if eligible else "selection_declined_no_eligible_rule"
    if baseline["eligible"] and selected is not baseline:
        candidate_lower = selected["metrics"]["date_bootstrap_95_ci"]["metrics"]["net_roi_percent"]["lower"]
        baseline_lower = baseline["metrics"]["date_bootstrap_95_ci"]["metrics"]["net_roi_percent"]["lower"]
        if candidate_lower <= baseline_lower:
            selected, reason = baseline, "baseline_retained_no_strict_lower_ci_improvement"
    frozen = {
        "schema": SCHEMA, "model_id": model_id, "prediction_provenance": provenance,
        "implementation": implementation,
        "development_predictions_sha256": _frame_hash(development), "policy": policy,
        "candidate_catalog_sha256": _sha(rules), "selected_rule": selected["rule"],
        "baseline_rule": baseline["rule"], "selection_reason": reason,
        "selection_eligible": selected["eligible"], "fit_scope": "development_only",
        "probability_model": False, "automatic_purchase": False,
    }
    frozen_text = _json(frozen)
    frozen_sha = hashlib.sha256(frozen_text.encode("utf-8")).hexdigest()
    output.mkdir(parents=True, exist_ok=False)
    _write_exclusive(output / "frozen_rule.json", frozen_text)
    _write_exclusive(output / "frozen_rule.sha256", frozen_sha + "\n")
    # No confirmation/retrospective rule evaluation occurs above this boundary.
    phases = {}
    for index, (phase, years) in enumerate(zip(("confirmation", "known_reused_retrospective"), periods[1:]), 1):
        subset = frame.loc[frame["_year"].isin(years)]
        plan = _bootstrap_plan(subset, bootstrap_samples, seed + index)
        phases[phase] = {"years": list(years), "unseen": False, "rule_reselected": False,
            "predictions_sha256": _frame_hash(subset),
            "baseline": _evaluate_rule(subset, baseline["rule"], years, plan),
            "selected": _evaluate_rule(subset, selected["rule"], years, plan)}
    annual_dev = selected["metrics"]["by_year"].values()
    report = {
        "schema": SCHEMA, "model_id": model_id, "market_policy": MARKET_POLICY,
        "implementation": implementation,
        "source_predictions_sha256": _frame_hash(frame), "input_audit": audit,
        "prediction_provenance": provenance, "frozen_rule_sha256": frozen_sha,
        "frozen_rule_path": str(output / "frozen_rule.json"),
        "policy": policy, "development_search": evaluations,
        "selection": {"rule": selected["rule"], "reason": reason, "eligible": selected["eligible"],
                      "development_all_years_observed_gross_return_at_least_100_percent": all(
                          row["gross_return_rate_percent"] is not None and row["gross_return_rate_percent"] >= 100
                          for row in annual_dev)},
        **phases, "bet_types": ["win"], "stake_yen_per_ticket": STAKE_YEN,
        "score_is_probability": False, "expected_value_estimated": False,
        "automatic_purchase": False, "production_rule_activated": False,
        "profitability_guaranteed": False, "live_trading_reproduction": False,
        "definitions": {
            "score_order": "higher prediction first; ties use smaller horse_number; no probability interpretation",
            "turnover_yen": "100 yen multiplied by selected ticket count; rank2_3 may buy two tickets per race",
            "gross_return_rate_percent": "official payout divided by turnover times 100; 100 percent is break-even",
            "net_roi_percent": "(official payout minus turnover) divided by turnover times 100; zero is break-even",
            "race_coverage": "races with at least one selected ticket divided by all eligible OOF races in the phase",
            "net_profit_ci": "same-count resampling of observed calendar-date profit blocks, not a forward profit forecast",
            "full_rule_missingness": "any selected race with an unverified winner payout nulls full-rule monetary metrics, even if the selected ticket lost",
            "market_usage": "recorded odds/popularity filter tickets only; this function never feeds them into a model",
        },
        "limitations": [
            "Market odds/popularity have no verified as-of timestamp: retrospective_market_proxy only.",
            "OOF/model and official payout provenance are caller attestations; scores alone cannot prove them.",
            "Confirmation and 2026 are known-reused periods, not unseen final tests.",
            "The 95% date-bootstrap intervals are not corrected for selecting among 52 rules.",
            "Unknown winner payout makes full-rule ROI/profit/drawdown/CI null; verified subset is separate.",
            "No odds substitution, probabilities, expected values, bet execution, or model retraining.",
            "Observed 100% gross return is not a guarantee; do not repeat selection until a target is reached.",
        ],
    }
    _write_exclusive(output / "betting_search_report.json", _json(report))
    return report
