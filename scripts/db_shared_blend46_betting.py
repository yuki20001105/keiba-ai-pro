"""Fixed, retrospective betting-policy and small residual-model research.

This module has no database, network, file-write, publication or activation API.
Results odds are a decision-time hindsight proxy, not executable pre-race
quotes. Settlement uses only saved result-table payouts, never odds times stake.
The two AI candidates use a runner-weighted binary logit-offset residual objective,
sigmoid, then race normalization; they are NOT multinomial training objectives.
Every policy has a fixed 100 JPY stake. No actual purchase is authorized.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import math
from numbers import Integral, Real
import re

import numpy as np
import pandas as pd
from scipy.optimize import minimize, minimize_scalar
from scipy.special import expit, logsumexp


RULES = ("top1", "top1_odds3_20", "top1_ev105", "max_ev105", "max_ev115", "top3_ev105")
FEATURES = ("model_probability", "market_probability", "log_odds", "model_rank",
            "market_rank", "rank_difference", "probability_difference", "field_size",
            "race_top_gap", "race_entropy")
MARKET_FEATURES = ("market_probability", "log_odds", "market_rank", "field_size")
MODEL_SCHEMA = "research-blend46-betting-offset-v1"
OBJECTIVE_DESCRIPTION = "binary logit-offset residual + sigmoid + race normalization; not multinomial training objective"
PERIODS = {"TRAIN": ("20160101", "20201231"), "CAL": ("20210101", "20221231"),
           "SELECT": ("20230101", "20241231"), "TEST": ("20250101", "20260913")}
L2 = .01
TEMPERATURE_BOUNDS = (.5, 2.)
STAKE = 100
SEED = 20261012
_BASE_COLUMNS = ("race_id", "race_date", "horse_number", "odds", "probability",
                 "raw_score", "venue", "surface", "expected_runner_count")


def _number(value):
    return isinstance(value, Real) and not isinstance(value, (bool, np.bool_)) and math.isfinite(float(value))


def _integer(value, minimum=1):
    return _number(value) and float(value).is_integer() and float(value) >= minimum


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
        raise ValueError("betting-valid-yyyymmdd-required")
    try:
        parsed = datetime.strptime(value, "%Y%m%d")
    except ValueError as error:
        raise ValueError("betting-valid-yyyymmdd-required") from error
    if parsed.strftime("%Y%m%d") != value:
        raise ValueError("betting-valid-yyyymmdd-required")
    return value


def validate_cohort(frame, require_results=True):
    """Fail closed on incomplete races; never silently drop a runner or race.

    When require_results=False no outcome or payout column is inspected. This
    is the sole validation path used by feature creation and bet selection.
    """
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError("betting-nonempty-dataframe-required")
    required = set(_BASE_COLUMNS) | ({"finish", "tansho_payout"} if require_results else set())
    if not required <= set(frame.columns) or frame.columns.duplicated().any():
        raise ValueError("betting-required-unique-columns")
    for name in ("race_id", "venue", "surface"):
        if not frame[name].map(lambda x: isinstance(x, str) and bool(x.strip())).all():
            raise ValueError(f"betting-valid-{name}-required")
    for value in frame.race_date.unique():
        _date(value)
    if not frame.horse_number.map(_integer).all() or not frame.expected_runner_count.map(lambda x: _integer(x, 2)).all():
        raise ValueError("betting-valid-runner-keys-required")
    if frame.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("betting-duplicate-runner-key")
    if not frame.odds.map(lambda x: _number(x) and x >= 1).all():
        raise ValueError("betting-valid-odds-required")
    if not frame.probability.map(lambda x: _number(x) and 0 < x < 1).all():
        raise ValueError("betting-valid-probability-required")
    if not frame.raw_score.map(_number).all():
        raise ValueError("betting-valid-score-required")
    if require_results and not frame.finish.map(_integer).all():
        raise ValueError("betting-valid-finish-required")
    if require_results and (frame.finish > frame.expected_runner_count).any():
        raise ValueError("betting-finish-within-runner-count-required")
    grouped = frame.groupby("race_id", sort=False, observed=True)
    for name in ("race_date", "venue", "surface", "expected_runner_count"):
        if grouped[name].nunique(dropna=False).ne(1).any():
            raise ValueError("betting-inconsistent-race-metadata")
    if not grouped.size().eq(grouped.expected_runner_count.first()).all():
        raise ValueError("betting-whole-race-required")
    if not np.allclose(grouped.probability.sum().to_numpy(float), 1., atol=1e-6, rtol=0.):
        raise ValueError("betting-race-normalized-probability-required")
    if require_results:
        winners = frame.finish.eq(1)
        if winners.groupby(frame.race_id, sort=False).sum().ne(1).any():
            raise ValueError("betting-single-winner-required")
        if not frame.loc[winners, "tansho_payout"].map(
                lambda x: _number(x) and x >= STAKE and float(x).is_integer() and int(x) % 10 == 0).all():
            raise ValueError("betting-official-winner-payout-required")
        # Loser payout fields are never a second positive award or inference source.
        losers = frame.loc[~winners, "tansho_payout"]
        if not losers.map(lambda x: pd.isna(x) or (_number(x) and x == 0)).all():
            raise ValueError("betting-loser-positive-payout-forbidden")
    return {"runner_count": int(len(frame)), "race_count": int(frame.race_id.nunique()),
            "date_count": int(frame.race_date.nunique()), "date_from": frame.race_date.min(),
            "date_to": frame.race_date.max()}


def _race_normalize(frame, values):
    values = np.asarray(values, dtype=float)
    if values.shape != (len(frame),) or not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("betting-positive-probability-vector-required")
    groups = pd.factorize(frame.race_id, sort=False)[0]
    totals = np.bincount(groups, weights=values)
    probabilities = values / totals[groups]
    if not np.isfinite(probabilities).all() or (probabilities <= 0).any() or (probabilities >= 1).any():
        raise ValueError("betting-normalization-invalid")
    return probabilities


def market_probabilities(frame):
    validate_cohort(frame, require_results=False)
    return _race_normalize(frame, 1. / frame.odds.to_numpy(float))


def replace_probabilities(frame, probabilities):
    """Replace model probabilities without reading outcomes or changing rows."""
    result = frame.copy()
    vector = np.asarray(probabilities, dtype=float)
    if vector.shape != (len(frame),):
        raise ValueError("betting-probability-vector-length")
    result["probability"] = vector
    validate_cohort(result, require_results=False)
    return result


def _ranks(frame, values):
    # Stable horse-number tie break, independent of the DataFrame's row order.
    order = pd.DataFrame({"race_id": frame.race_id.to_numpy(),
                          "horse_number": frame.horse_number.to_numpy(), "value": values,
                          "position": np.arange(len(frame))}).sort_values(
                              ["race_id", "value", "horse_number"], ascending=[True, False, True], kind="mergesort")
    order["rank"] = order.groupby("race_id", sort=False).cumcount() + 1
    return order.sort_values("position")["rank"].to_numpy(float)


def feature_matrix(frame, market_only=False):
    """The fixed ten columns, all input-only; no missing clock/outcome filling."""
    validate_cohort(frame, require_results=False)
    model = frame.probability.to_numpy(float)
    market = market_probabilities(frame)
    model_rank, market_rank = _ranks(frame, model), _ranks(frame, market)
    gaps = frame.groupby("race_id", sort=False).raw_score.apply(
        lambda x: float(np.partition(x.to_numpy(float), -2)[-1] - np.partition(x.to_numpy(float), -2)[-2]))
    entropy = pd.Series(-model * np.log(model)).groupby(frame.race_id.reset_index(drop=True), sort=False).sum()
    values = np.column_stack((model, market, np.log(frame.odds.to_numpy(float)), model_rank,
                              market_rank, model_rank - market_rank, model - market,
                              frame.expected_runner_count.to_numpy(float), frame.race_id.map(gaps).to_numpy(float),
                              frame.race_id.map(entropy).to_numpy(float)))
    if not np.isfinite(values).all():
        raise ValueError("betting-finite-feature-matrix-required")
    return values[:, [FEATURES.index(name) for name in MARKET_FEATURES]] if market_only else values


def _partition(frame, name):
    receipt = validate_cohort(frame, require_results=True)
    lower, upper = PERIODS[name]
    if not frame.race_date.between(lower, upper).all():
        raise ValueError(f"betting-{name.lower()}-period-required")
    return receipt


def _cohort_hash(frame):
    columns = list(_BASE_COLUMNS) + ["finish", "tansho_payout"]
    ordered = frame[columns].sort_values(["race_id", "horse_number"], kind="mergesort")
    return hashlib.sha256(pd.util.hash_pandas_object(ordered, index=False).to_numpy().tobytes()).hexdigest()


def _validate_artifact(artifact, calibrated=True):
    if not isinstance(artifact, dict) or artifact.get("schema") != MODEL_SCHEMA:
        raise ValueError("betting-research-artifact-required")
    if not isinstance(artifact.get("market_only"), bool):
        raise ValueError("betting-explicit-market-control-required")
    expected_features = MARKET_FEATURES if artifact["market_only"] else FEATURES
    if artifact.get("kind") not in ("logistic", "lightgbm") or tuple(artifact.get("features", ())) != expected_features:
        raise ValueError("betting-fixed-model-kind-features-required")
    if artifact.get("objective_description") != OBJECTIVE_DESCRIPTION or artifact.get("research_only") is not True:
        raise ValueError("betting-research-objective-required")
    if artifact.get("eligible_for_ev") is not False or artifact.get("artifact_published") is not False:
        raise ValueError("betting-no-serving-authority-required")
    if calibrated:
        temperature = artifact.get("temperature")
        if not _number(temperature) or not TEMPERATURE_BOUNDS[0] <= temperature <= TEMPERATURE_BOUNDS[1]:
            raise ValueError("betting-calibrated-temperature-required")


def predict_offset_uncalibrated(artifact, frame):
    _validate_artifact(artifact, calibrated=False)
    features = feature_matrix(frame, market_only=artifact["market_only"])
    feature_names = MARKET_FEATURES if artifact["market_only"] else FEATURES
    market = market_probabilities(frame)
    offset = np.log(market) - np.log1p(-market)
    if artifact["kind"] == "logistic":
        mean, std, beta = (np.asarray(artifact[name], float) for name in ("means", "standard_deviations", "coefficients"))
        if mean.shape != (len(feature_names),) or std.shape != mean.shape or beta.shape != (len(feature_names) + 1,):
            raise ValueError("betting-valid-logistic-artifact-required")
        if not all(np.isfinite(x).all() for x in (mean, std, beta)) or (std <= 0).any():
            raise ValueError("betting-valid-logistic-artifact-required")
        residual = beta[0] + ((features - mean) / std) @ beta[1:]
    else:
        import lightgbm as lgb
        model = lgb.Booster(model_str=artifact["model_text"])
        if model.num_feature() != len(feature_names) or tuple(model.feature_name()) != feature_names:
            raise ValueError("betting-valid-lightgbm-artifact-required")
        residual = model.predict(features, raw_score=True, num_threads=2)
    # Numerical clamp is explicit and fixed before normalization, not data imputation.
    values = np.clip(expit(offset + residual), 1e-12, 1. - 1e-12)
    return _race_normalize(frame, values)


def _temperature_probabilities(frame, probabilities, temperature):
    logp = np.log(probabilities) / float(temperature)
    groups = pd.factorize(frame.race_id, sort=False)[0]
    # Race-wise shift avoids overflow; sorting/index position never changes the cohort.
    maxima = np.full(groups.max() + 1, -np.inf)
    np.maximum.at(maxima, groups, logp)
    return _race_normalize(frame, np.exp(logp - maxima[groups]))


def predict_offset_model(artifact, frame):
    _validate_artifact(artifact, calibrated=True)
    return _temperature_probabilities(frame, predict_offset_uncalibrated(artifact, frame), artifact["temperature"])


def fit_offset_model(train, cal, kind="logistic", market_only=False):
    """TRAIN-only residual fit, CAL-only bounded temperature; TEST is not an API input."""
    if kind not in ("logistic", "lightgbm"):
        raise ValueError("betting-fixed-ai-candidate-required")
    if not isinstance(market_only, bool):
        raise ValueError("betting-explicit-market-control-required")
    train_receipt, cal_receipt = _partition(train, "TRAIN"), _partition(cal, "CAL")
    if train.race_date.max() >= cal.race_date.min() or set(train.race_id) & set(cal.race_id):
        raise ValueError("betting-train-cal-disjoint-chronology-required")
    features = feature_matrix(train, market_only=market_only)
    feature_names = MARKET_FEATURES if market_only else FEATURES
    market = market_probabilities(train)
    offset = np.log(market) - np.log1p(-market)
    target = train.finish.eq(1).to_numpy(float)
    artifact = {"schema": MODEL_SCHEMA, "kind": kind, "features": list(feature_names), "market_only": market_only,
                "objective_description": OBJECTIVE_DESCRIPTION,
                "research_only": True, "artifact_published": False, "eligible_for_ev": False,
                "actual_purchase_authorized": False, "independent_future_validation": False,
                "quote_scope": "historical-results-odds-not-executable-pre-race-quotes",
                "train": train_receipt, "cal": cal_receipt,
                "train_cohort_sha256": _cohort_hash(train), "cal_cohort_sha256": _cohort_hash(cal),
                "temperature_bounds": list(TEMPERATURE_BOUNDS), "sigmoid_clamp": [1e-12, 1. - 1e-12]}
    if kind == "logistic":
        mean, std = features.mean(axis=0), features.std(axis=0)
        std[std == 0] = 1.
        design = np.column_stack((np.ones(len(train)), (features - mean) / std))

        def objective(beta):
            logits = offset + design @ beta
            loss = np.mean(np.logaddexp(0., logits) - target * logits) + .5 * L2 * np.dot(beta[1:], beta[1:])
            gradient = design.T @ (expit(logits) - target) / len(target)
            gradient[1:] += L2 * beta[1:]
            return loss, gradient

        fit = minimize(objective, np.zeros(len(feature_names) + 1), jac=True, method="L-BFGS-B",
                       options={"maxiter": 200, "ftol": 1e-12, "gtol": 1e-7})
        if not fit.success or not np.isfinite(fit.x).all():
            raise ValueError("betting-logistic-fit-not-converged")
        artifact.update(means=mean.tolist(), standard_deviations=std.tolist(), coefficients=fit.x.tolist(),
                        fit_audit={"converged": True, "iterations": int(fit.nit), "l2": L2,
                                   "intercept_penalized": False, "standardization_scope": "TRAIN-only"})
    else:
        import lightgbm as lgb
        params = {"objective": "binary", "learning_rate": .03, "num_leaves": 7,
                  "min_data_in_leaf": 200, "lambda_l2": 10., "num_threads": 2,
                  "device_type": "cpu", "deterministic": True, "force_col_wise": True,
                  "seed": SEED, "verbosity": -1, "feature_fraction": 1., "bagging_fraction": 1.}
        dataset = lgb.Dataset(features, label=target, init_score=offset, feature_name=list(feature_names), free_raw_data=True)
        model = lgb.train(params, dataset, num_boost_round=150)
        artifact.update(model_text=model.model_to_string(),
                        fit_audit={"parameters": params, "rounds": 150, "offset_in_training": True,
                                   "offset_explicitly_added_in_prediction": True})
    raw = predict_offset_uncalibrated(artifact, cal)
    wins = cal.finish.eq(1).to_numpy()

    def cal_loss(temperature):
        probabilities = _temperature_probabilities(cal, raw, temperature)
        return float(-np.log(probabilities[wins]).mean())

    calibrated = minimize_scalar(cal_loss, bounds=TEMPERATURE_BOUNDS, method="bounded",
                                 options={"xatol": 1e-7, "maxiter": 200})
    if not calibrated.success or not _number(calibrated.fun):
        raise ValueError("betting-calibration-not-converged")
    artifact["temperature"] = float(calibrated.x)
    artifact["calibration_audit"] = {"scope": "CAL-only", "converged": True,
                                    "temperature": float(calibrated.x), "race_nll": float(calibrated.fun),
                                    "at_bound": bool(min(abs(calibrated.x - x) for x in TEMPERATURE_BOUNDS) < 1e-5),
                                    "feature_selection": False, "rule_selection": False,
                                    "test_outcomes_consumed": False}
    _validate_artifact(artifact)
    return artifact


def probability_metrics(frame):
    receipt = validate_cohort(frame, require_results=True)
    winners = frame.finish.eq(1).to_numpy()
    residual = (frame.probability.to_numpy(float) - winners.astype(float)) ** 2
    races = frame.assign(_squared=residual).groupby("race_id", sort=False)._squared.sum()
    ranks = _ranks(frame, frame.probability.to_numpy(float))
    selected = frame.loc[ranks == 1]
    confidence = selected.probability.to_numpy(float)
    hit = selected.finish.eq(1).to_numpy(float)
    bins = np.minimum((confidence * 10).astype(int), 9)
    calibration = []
    ece = 0.
    for bin_id in range(10):
        mask = bins == bin_id
        count = int(mask.sum())
        if count:
            average, observed = float(confidence[mask].mean()), float(hit[mask].mean())
            ece += count / len(confidence) * abs(average - observed)
            calibration.append({"bin": bin_id, "count": count, "mean_confidence": average, "hit_rate": observed})
    return {**receipt, "nll": float(-np.log(frame.probability.to_numpy(float)[winners]).mean()),
            "brier": float(races.mean()), "top1": float(hit.mean()), "selection_ece": float(ece),
            "selection_bias": float(confidence.mean() - hit.mean()), "calibration_bins": calibration,
            "metric_scope": "race-average-single-winner-probability", "eligible_for_ev": False}


def select_bets(frame, rule_id):
    """Input-only policy. Outcome/payout columns are neither read nor returned."""
    if rule_id not in RULES:
        raise ValueError("betting-fixed-rule-required")
    validate_cohort(frame, require_results=False)
    work = frame[list(_BASE_COLUMNS)].copy()
    work["model_rank"] = _ranks(frame, frame.probability.to_numpy(float)).astype(int)
    work["ev"] = work.probability * work.odds
    if rule_id == "top1":
        chosen = work.loc[work.model_rank.eq(1)]
    elif rule_id == "top1_odds3_20":
        chosen = work.loc[work.model_rank.eq(1) & work.odds.between(3., 20.)]
    elif rule_id == "top1_ev105":
        chosen = work.loc[work.model_rank.eq(1) & work.ev.ge(1.05)]
    elif rule_id in ("max_ev105", "max_ev115"):
        threshold = 1.05 if rule_id == "max_ev105" else 1.15
        eligible = work.loc[work.odds.le(30.) & work.probability.ge(.02) & work.ev.ge(threshold)]
        chosen = eligible.sort_values(["race_id", "ev", "horse_number"], ascending=[True, False, True],
                                     kind="mergesort").drop_duplicates("race_id", keep="first")
    else:
        chosen = work.loc[work.model_rank.le(3) & work.ev.ge(1.05) & work.odds.le(30.)]
    result = chosen.sort_values(["race_date", "race_id", "horse_number"], kind="mergesort").copy()
    result["stake"] = STAKE
    result["rule_id"] = rule_id
    return result.reset_index(drop=True)


def settle_bets(cohort, bets):
    """Only the official sole-winner payout settles tickets, never odds * 100."""
    validate_cohort(cohort, require_results=True)
    if not isinstance(bets, pd.DataFrame) or not {"race_id", "horse_number", "stake"} <= set(bets):
        raise ValueError("betting-ticket-frame-required")
    if bets.duplicated(["race_id", "horse_number"]).any() or not bets.stake.map(lambda x: _integer(x) and x == STAKE).all():
        raise ValueError("betting-unique-fixed-stake-tickets-required")
    if not bets.horse_number.map(_integer).all():
        raise ValueError("betting-valid-ticket-key-required")
    keys = ["race_id", "horse_number"]
    outcomes = cohort[keys + ["race_date", "finish", "tansho_payout"]].copy()
    # Strip copied metadata/results from tickets, so settlement always uses the independent cohort.
    merged = bets[keys + ["stake"]].merge(outcomes, on=keys, how="left", validate="one_to_one", indicator=True)
    if not merged._merge.eq("both").all():
        raise ValueError("betting-tickets-not-in-cohort")
    merged = merged.drop(columns="_merge")
    merged["hit"] = merged.finish.eq(1)
    merged["payout"] = np.where(merged.hit, merged.tansho_payout, 0.).astype(float)
    merged["profit"] = merged.payout - merged.stake
    return merged


def _date_ledger(cohort, settlement):
    dates = pd.Index(sorted(cohort.race_date.unique()), name="race_date")
    ledger = pd.DataFrame(index=dates)
    ledger["eligible_races"] = cohort.groupby("race_date", sort=False).race_id.nunique().reindex(dates, fill_value=0)
    if settlement.empty:
        for name in ("stake", "payout", "profit", "bets", "hits", "bet_races"):
            ledger[name] = 0.
    else:
        daily = settlement.groupby("race_date", sort=False).agg(
            stake=("stake", "sum"), payout=("payout", "sum"), profit=("profit", "sum"),
            bets=("stake", "size"), hits=("hit", "sum"), bet_races=("race_id", "nunique"))
        for name in daily:
            ledger[name] = daily[name].reindex(dates, fill_value=0)
    ledger["year"] = ledger.index.str[:4].astype(int)
    return ledger.reset_index()


def summarize_policy(cohort, bets):
    receipt = validate_cohort(cohort, require_results=True)
    settlement = settle_bets(cohort, bets)
    ledger = _date_ledger(cohort, settlement)
    stake, payout = float(settlement.stake.sum()), float(settlement.payout.sum())
    bet_count, hit_count = len(settlement), int(settlement.hit.sum())
    profit = payout - stake
    cum = ledger.profit.cumsum().to_numpy(float)
    peaks = np.maximum.accumulate(np.r_[0., cum])
    drawdown = float(np.max(peaks[1:] - cum)) if len(cum) else 0.
    per_year = []
    for year, group in ledger.groupby("year", sort=True):
        year_stake, year_payout = float(group.stake.sum()), float(group.payout.sum())
        per_year.append({"year": int(year), "bets": int(group.bets.sum()), "dates_with_bets": int(group.bets.gt(0).sum()),
                         "stake": year_stake, "payout": year_payout,
                         "roi_percent": 100. * year_payout / year_stake if year_stake else None})
    return {**receipt, "bets": int(bet_count), "hits": hit_count, "stake": stake, "payout": payout,
            "profit": profit, "roi_percent": 100. * payout / stake if stake else None,
            "hit_rate": hit_count / bet_count if bet_count else None,
            "bet_races": int(settlement.race_id.nunique()),
            "race_coverage": settlement.race_id.nunique() / receipt["race_count"],
            "dates_with_bets": int(settlement.race_date.nunique()),
            "date_coverage": settlement.race_date.nunique() / receipt["date_count"],
            "profit_per_eligible_race": profit / receipt["race_count"],
            "max_drawdown_jpy": drawdown, "drawdown_scope": "chronological-daily-fixed-stake-net-profit-start-zero",
            "yearly": per_year, "daily_ledger": ledger.to_dict("records"),
            "settlement_source": "saved_netkeiba_result_return_table", "payout_independently_verified_with_jra": False,
            "actual_purchase": False,
            "quote_scope": "historical-results-odds-not-executable-pre-race-quotes"}


def _interval(values, confidence):
    finite = np.asarray(values, float)
    finite = finite[np.isfinite(finite)]
    if not len(finite):
        return None
    alpha = (1. - confidence) / 2.
    return [float(x) for x in np.quantile(finite, (alpha, 1. - alpha))]


def bootstrap_policy_comparisons(cohort, policies, reference_id, repetitions=5000, seed=SEED, family_size=31):
    """Common date-cluster weights, stratified by year, include all no-bet dates."""
    validate_cohort(cohort, require_results=True)
    if not isinstance(policies, dict) or not policies or reference_id not in policies:
        raise ValueError("betting-policies-reference-required")
    if not isinstance(repetitions, Integral) or isinstance(repetitions, bool) or repetitions < 2:
        raise ValueError("betting-bootstrap-repetitions-required")
    if not isinstance(family_size, Integral) or isinstance(family_size, bool) or family_size < len(policies):
        raise ValueError("betting-candidate-family-too-small")
    if not isinstance(seed, Integral) or isinstance(seed, bool):
        raise ValueError("betting-bootstrap-seed-required")
    ids = list(policies)
    summaries = {key: summarize_policy(cohort, policies[key]) for key in ids}
    ledgers = [pd.DataFrame(summaries[key]["daily_ledger"]) for key in ids]
    dates = ledgers[0].race_date.to_numpy()
    if any(not np.array_equal(item.race_date.to_numpy(), dates) for item in ledgers):
        raise ValueError("betting-identical-bootstrap-cohort-required")
    stakes = np.column_stack([item.stake.to_numpy(float) for item in ledgers])
    payouts = np.column_stack([item.payout.to_numpy(float) for item in ledgers])
    profits = payouts - stakes
    race_counts = ledgers[0].eligible_races.to_numpy(float)
    years = ledgers[0].year.to_numpy()
    rng = np.random.default_rng(int(seed))
    boot_roi = np.full((repetitions, len(ids)), np.nan)
    boot_profit = np.empty_like(boot_roi)
    for start in range(0, repetitions, 250):
        batch_size = min(250, repetitions - start)
        weights = np.zeros((batch_size, len(dates)), dtype=float)
        for year in np.unique(years):
            positions = np.flatnonzero(years == year)
            weights[:, positions] = rng.multinomial(len(positions), np.full(len(positions), 1. / len(positions)), size=batch_size)
        expense = weights @ stakes
        reward = weights @ payouts
        boot_roi[start:start + batch_size] = np.divide(100. * reward, expense, out=np.full_like(expense, np.nan), where=expense > 0)
        boot_profit[start:start + batch_size] = (weights @ profits) / (weights @ race_counts)[:, None]
    simultaneous = 1. - .05 / int(family_size)
    reference_index = ids.index(reference_id)
    candidates = []
    for index, key in enumerate(ids):
        candidates.append({"policy_id": key, "roi_percent": summaries[key]["roi_percent"],
                           "profit_per_eligible_race": summaries[key]["profit_per_eligible_race"],
                           "roi_ci95": _interval(boot_roi[:, index], .95),
                           "roi_ci_simultaneous": _interval(boot_roi[:, index], simultaneous),
                           "profit_difference_per_eligible_race_vs_reference": summaries[key]["profit_per_eligible_race"] - summaries[reference_id]["profit_per_eligible_race"],
                           "paired_profit_difference_ci95": _interval(boot_profit[:, index] - boot_profit[:, reference_index], .95),
                           "paired_profit_difference_ci_simultaneous": _interval(boot_profit[:, index] - boot_profit[:, reference_index], simultaneous),
                           "bootstrap_samples_with_stake": int(np.isfinite(boot_roi[:, index]).sum()),
                           "summary": summaries[key]})
    return {"method": "paired-whole-date-cluster-year-stratified-bootstrap", "seed": int(seed),
            "repetitions": int(repetitions), "family_size": int(family_size),
            "simultaneous_confidence": simultaneous, "reference_id": reference_id,
            "all_cohort_dates_included": True, "common_candidate_weights": True,
            "expected_draws_per_simultaneous_tail": float(repetitions * (1. - simultaneous) / 2.),
            "finite_tail_resolution_warning": "empirical extreme-tail bounds have finite Monte Carlo resolution",
            "candidates": candidates}


def choose_policy_on_select(cohort, policies, bootstrap_result):
    """SELECT-only lower simultaneous ROI bound selection, never TEST retuning."""
    _partition(cohort, "SELECT")
    if not isinstance(bootstrap_result, dict) or bootstrap_result.get("family_size", 0) < len(policies):
        raise ValueError("betting-selection-bootstrap-family-required")
    records = {item["policy_id"]: item for item in bootstrap_result.get("candidates", ())}
    if set(records) != set(policies) or len(records) != len(bootstrap_result.get("candidates", ())):
        raise ValueError("betting-selection-identical-policy-family-required")
    eligible = []
    rejected = []
    for key, bets in policies.items():
        current = summarize_policy(cohort, bets)
        prior = records[key]["summary"]
        # Refuse a CI accidentally calculated on another period or modified tickets.
        for field in ("date_from", "date_to", "runner_count", "race_count", "bets", "stake", "payout", "daily_ledger"):
            if current[field] != prior[field]:
                raise ValueError("betting-selection-bootstrap-receipt-mismatch")
        reasons = []
        if current["bets"] < 200:
            reasons.append("fewer_than_200_bets")
        if current["dates_with_bets"] < 20:
            reasons.append("fewer_than_20_bet_dates")
        yearly = {item["year"]: item for item in current["yearly"]}
        if any(yearly.get(year, {}).get("bets", 0) < 30 for year in (2023, 2024)):
            reasons.append("fewer_than_30_bets_in_each_selection_year")
        interval = records[key]["roi_ci_simultaneous"]
        if interval is None or len(interval) != 2 or not all(_number(x) for x in interval):
            reasons.append("roi_interval_unavailable")
        if reasons:
            rejected.append({"policy_id": key, "reasons": reasons})
        else:
            eligible.append({"policy_id": key, "lower_roi_bound": float(interval[0]),
                             "roi_percent": current["roi_percent"], "bets": current["bets"]})
    eligible.sort(key=lambda item: (-item["lower_roi_bound"], item["policy_id"]))
    best = eligible[0] if eligible else None
    selected = best if best and best["lower_roi_bound"] > 100. else None
    return {"selection_scope": "SELECT-2023-2024-only", "risk_objective": "maximize-lower-Bonferroni-ROI-confidence-bound",
            "selected_policy_id": selected["policy_id"] if selected else None,
            "highest_candidate": best, "eligible_candidates": eligible, "rejected_candidates": rejected,
            "no_bet_comparator": {"profit": 0., "stake": 0., "roi_percent": None},
            "reason": "candidate_lower_bound_above_100" if selected else "no_candidate_demonstrably_above_no_bet",
            "test_outcomes_consumed": False, "future_profit_guaranteed": False}


def payout_sensitivity(cohort, bets):
    settlement = settle_bets(cohort, bets)
    stake = float(settlement.stake.sum())
    winners = settlement.loc[settlement.hit].sort_values(["payout", "race_id", "horse_number"],
                                                       ascending=[False, True, True], kind="mergesort")
    total = float(settlement.payout.sum())
    result = []
    for count in (1, 3):
        removed = float(winners.head(count).payout.sum())
        result.append({"removed_largest_winning_payouts": count, "removed_count": min(count, len(winners)),
                       "removed_payout": removed, "roi_percent": 100. * (total - removed) / stake if stake else None,
                       "profit": total - removed - stake,
                       "stake_unchanged": True, "sensitivity_not_new_strategy": True})
    return result
