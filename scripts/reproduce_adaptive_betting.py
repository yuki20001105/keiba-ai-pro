"""Fit portable upset/event models and validate frozen three-recipe switching."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import sys

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, log_loss
from threadpoolctl import threadpool_limits

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "python-api"), str(REPO / "scripts")]
from betting import adaptive_strategy as ai
from betting.research_opponent_pairs import select_tickets, settle, summarize
from evaluate_market_axis_pairs import SOURCES, _sources, sha, write_json


def prepare_kit(study_root: Path, output: Path) -> None:
    if output.exists():
        raise FileExistsError("unused_kit_directory_required")
    sources = _sources(study_root)
    output.mkdir(parents=True)
    files = {}
    for key, path in sources.items():
        folder, name, digest = SOURCES[key]
        target = output / folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        if sha(target) != digest:
            raise ValueError("portable_copy_hash_mismatch")
        files[str(Path(folder) / name)] = digest
    write_json(output / "kit-manifest.json", {"schema": ai.SCHEMA, "files": files})


def risk_summary(rows: pd.DataFrame) -> dict:
    result = summarize(rows, bootstrap_samples=1000)
    bought = rows.loc[rows.cost_yen.gt(0)]
    daily = rows.groupby("race_date", sort=True)[["cost_yen", "return_yen"]].sum()
    run, longest = 0, 0
    for net in (daily.return_yen - daily.cost_yen).loc[daily.cost_yen.gt(0)]:
        run = run + 1 if net < 0 else 0
        longest = max(longest, run)
    monthly = rows.assign(month=rows.race_date.str[:6]).groupby("month")[["cost_yen", "return_yen"]].sum()
    return {
        **result, "profitable_races": int(bought.return_yen.gt(bought.cost_yen).sum()),
        "profitable_race_rate_percent": float(100 * bought.return_yen.gt(bought.cost_yen).mean()) if len(bought) else None,
        "max_consecutive_negative_betting_dates": longest,
        "negative_months": int(monthly.return_yen.lt(monthly.cost_yen).sum()),
        "monthly": monthly.assign(net_yen=monthly.return_yen - monthly.cost_yen).reset_index().to_dict("records"),
    }


def assess_switch(rows: pd.DataFrame, reference: pd.DataFrame) -> dict:
    if (rows.race_id.duplicated().any() or reference.race_id.duplicated().any()
            or set(rows.race_id) != set(reference.race_id)):
        raise ValueError("same_distinct_cohort_required")
    joined = rows.merge(reference, on="race_id", suffixes=("", "_ref"), validate="one_to_one")
    if not joined.race_date.eq(joined.race_date_ref).all():
        raise ValueError("same_cohort_dates_required")
    current, control = risk_summary(rows), risk_summary(reference)
    daily = joined.assign(
        difference=(joined.return_yen - joined.cost_yen) - (joined.return_yen_ref - joined.cost_yen_ref),
    ).groupby("race_date").difference.sum().sort_index()
    weights = np.random.default_rng(20261103).multinomial(
        len(daily), np.full(len(daily), 1 / len(daily)), size=1000,
    )
    gain_interval = np.percentile(weights @ daily.to_numpy(), [2.5, 97.5]).tolist()
    roi_lower = current["date_bootstrap_roi95_percent"][0]
    return {
        "historical_criteria": {
            "at_least_50_purchases": current["bet_races"] >= 50,
            "at_least_10_profitable_races": current["profitable_races"] >= 10,
            "roi95_lower_above_100": roi_lower is not None and roi_lower > 100,
            "paired_profit_gain95_lower_above_zero": gain_interval[0] > 0,
            "daily_drawdown_at_least_20_percent_lower":
                current["daily_max_drawdown_yen"] <= .8 * control["daily_max_drawdown_yen"],
        },
        "paired_profit_gain95_yen": gain_interval,
        "reference": "candidate1_selected_on2025_only",
        "live_effectiveness_confirmed": False, "purchase_eligible": False,
        "blocking_data_requirements": [
            "historical_input_odds_not_proven_prestart",
            "2026_test_previously_seen",
            "base_probability_validation_not_approved",
        ],
    }


def classification_metrics(frame: pd.DataFrame) -> dict:
    y = frame.true_upset.to_numpy(int)
    p = frame[["p0", "p1", "p2"]].to_numpy()
    predicted = p.argmax(axis=1)
    matrix = confusion_matrix(y, predicted, labels=[0, 1, 2])
    return {
        "races": len(frame), "accuracy": float(accuracy_score(y, predicted)),
        "macro_f1": float(f1_score(y, predicted, average="macro", zero_division=0)),
        "logloss": float(log_loss(y, p, labels=[0, 1, 2])),
        "past_prior_logloss": float(log_loss(y, frame[["prior_p0", "prior_p1", "prior_p2"]], labels=[0, 1, 2])),
        "confusion_true_rows": matrix.tolist(),
        "large_recall": float(matrix[2, 2] / matrix[2].sum()),
        "large_precision": float(matrix[2, 2] / matrix[:, 2].sum()) if matrix[:, 2].sum() else None,
        "event_brier": {target: float(np.mean((frame[f"p_{target}"] - frame[f"true_{target}"]) ** 2))
                        for target in ai.TARGETS if target != "upset"},
    }


def replay(study_root: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError("unused_output_directory_required")
    source = _sources(study_root)
    raw = pd.read_parquet(source["horses"])
    official = json.loads(source["wide_trio"].read_text(encoding="utf-8"))
    trifecta = json.loads(source["trifecta"].read_text(encoding="utf-8"))
    fields, records = {}, []
    for rid, part in raw.groupby("race_id", sort=True):
        x = ai.race_features(part, partners=True)
        y = ai.race_outcomes(part, official[rid]["三連複"], official[rid]["ワイド"])
        if part.race_date.nunique() != 1:
            raise ValueError("same_race_date_required")
        records.append({"race_id": rid, "race_date": part.race_date.iloc[0], **x, **y,
                        "numeric_priority": ai.numeric_priority(part)})
        fields[rid] = part
    data = pd.DataFrame(records)
    if len(data) != 6458 or data.numeric_priority.sum() != 630:
        raise ValueError("original_priority_scope_mismatch")
    quarters = pd.to_datetime(data.race_date, format="%Y%m%d").dt.to_period("Q").astype(str)
    forecasts, bundles, folds = [], {}, []
    for quarter in sorted(quarters[quarters.ge("2025Q1")].unique()):
        cutoff = pd.Period(quarter, freq="Q").start_time.strftime("%Y%m%d")
        train, test = data.loc[data.race_date.lt(cutoff)], data.loc[quarters.eq(quarter)]
        bundle = ai.fit_bundle(train, cutoff)
        prediction = ai.predict_frame(bundle, test, ai.BASE_MODEL_SHA)
        forecast = test[["race_id", "race_date", *ai.TARGETS, "rank_sum"]].rename(
            columns={name: f"true_{name}" for name in ai.TARGETS},
        ).copy()
        for name in prediction:
            forecast[name if name.startswith("p") else f"p_{name}"] = prediction[name]
        prior = (np.bincount(train.upset, minlength=3) + 1) / (len(train) + 3)
        for i in range(3):
            forecast[f"prior_p{i}"] = prior[i]
        forecast["quarter"], forecast["fit_through"] = quarter, bundle["fitted_through"]
        forecasts.append(forecast)
        bundles[quarter] = bundle
        folds.append({"quarter": quarter, "cutoff": cutoff, "fit_through": bundle["fitted_through"],
                      "training_races": len(train), "prediction_races": len(test)})
    forecast = pd.concat(forecasts, ignore_index=True)
    forecast["forecast_class"] = forecast[["p0", "p1", "p2"]].to_numpy().argmax(axis=1)
    legacy = pd.read_parquet(source["forecasts"])
    legacy = legacy.loc[legacy.family.eq("ai_selected")]
    compared = forecast.merge(legacy, on="race_id", suffixes=("", "_old"), validate="one_to_one")
    maximum_error = max(float(np.max(np.abs(compared[name] - compared[f"{name}_old"])))
                        for name in ("p0", "p1", "p2", "p_axis"))
    if len(compared) != 5473 or maximum_error > 1e-10:
        raise ValueError(f"historical_forecast_parity_failed:{maximum_error}")
    expected_priority = pd.read_parquet(source["priority"])
    expected_priority = expected_priority.loc[
        expected_priority.scenario.eq("stored_win_market_hindsight") & expected_priority.reference_priority
    ]
    priority = data.loc[data.numeric_priority]
    if set(priority.race_id) != set(expected_priority.race_id):
        raise ValueError("original_numeric_priority_id_mismatch")
    cohort = priority[["race_id", "race_date"]].merge(
        forecast.drop(columns="race_date"), on="race_id", how="left", validate="one_to_one",
    )
    cohort["gate_passed"] = cohort.race_date.ge("20250101") & cohort.p_axis.ge(.65)
    if len(cohort) != 630 or cohort.gate_passed.sum() != 355:
        raise ValueError("original_axis_guard_mismatch")
    by_race = cohort.set_index("race_id")
    fixed_rows, payoff_rows = [], []
    for row in cohort.itertuples():
        dividends = {**official[row.race_id], "三連単": trifecta[row.race_id]}
        if any(not dividends.get(kind) for kind in ("ワイド", "三連複", "三連単")):
            raise ValueError("verified_official_markets_required")
        component = {}
        for action, definition in ai.RECIPE_DEFINITIONS.items():
            legs = select_tickets(fields[row.race_id].to_dict("records"), definition) if row.gate_passed else None
            result = settle(legs, dividends)
            fixed_rows.append({
                "race_id": row.race_id, "race_date": row.race_date, "method": action,
                "selected_action": action if row.gate_passed else "skip",
                "fit_through": row.fit_through if row.race_date >= "20250101" else None, **result,
            })
            if row.gate_passed:
                for kind, payout in zip(result["bet_types"], result["official_payouts_per100_yen"]):
                    target = ("f1" if action == "candidate1" else "wide" if kind == "ワイド"
                              else "trio" if kind == "三連複" else "f2")
                    if (target == "f1" or (action == "candidate2" and target in ("wide", "f2"))
                            or (action == "candidate3" and target == "trio")):
                        component[target] = component.get(target, 0) + int(payout)
        if row.gate_passed and row.race_date < "20260101":
            payoff_rows.append({"race_id": row.race_id, "race_date": row.race_date,
                                "forecast_class": int(row.forecast_class),
                                **{f"payout_{key}": component[key] for key in ai.COMPONENTS}})
    fixed = pd.DataFrame(fixed_rows)
    gold = pd.read_parquet(source["old_books"])
    gold = gold.loc[gold.method.eq("ai_selected__axis_guard")]
    baseline = fixed.loc[fixed.method.eq("candidate3") & fixed.race_date.ge("20250101")].set_index("race_id")
    if len(baseline) != 552 or len(gold) != 552:
        raise ValueError("historical_baseline_scope_mismatch")
    keys = ("bet_types", "combinations", "stakes_yen", "official_payouts_per100_yen")
    for row in gold.itertuples():
        new = baseline.loc[row.race_id]
        if sorted(zip(*(getattr(row, key) for key in keys))) != sorted(zip(*(new[key] for key in keys))):
            raise ValueError("historical_four_point_book_mismatch")
    if (int(baseline.cost_yen.sum()), int(baseline.return_yen.sum())) != (426000, 679710):
        raise ValueError("historical_four_point_money_mismatch")
    history = pd.DataFrame(payoff_rows)
    if len(history) != 205 or history.race_date.max() >= "20260101":
        raise ValueError("2025_only_calibration_required")
    policies = {name: ai.fit_severity(history, conservative=name == "lower10") for name in ("mean", "lower10")}
    lookups = {(row.race_id, row.method): row._asdict() for row in fixed.itertuples(index=False)}
    decisions = []
    for mode, policy in policies.items():
        for row in cohort.itertuples():
            choice = {"action": "skip", "historical_payoff_scores": {}}
            if row.race_date >= "20260101" and row.gate_passed:
                probabilities = {name: getattr(row, f"p_{name}") for name in ai.COMPONENTS}
                choice = ai.choose_reference(probabilities, policy, int(row.forecast_class), row.race_date)
            if choice["action"] != "skip":
                selected = lookups[(row.race_id, choice["action"])]
                result = {key: selected[key] for key in (
                    "ticket_count", "cost_yen", "return_yen", "hit_ticket_count", *keys)}
            else:
                result = settle(None, {})
            decisions.append({
                "race_id": row.race_id, "race_date": row.race_date, "method": f"ai_{mode}",
                "selected_action": choice["action"], "policy_fitted_through": policy["fitted_through"],
                "forecast_fitted_through": row.fit_through if row.race_date >= "20250101" else None,
                "forecast_class": int(row.forecast_class) if row.race_date >= "20250101" else -1,
                "historical_payoff_scores_json": json.dumps(choice["historical_payoff_scores"], sort_keys=True),
                **result,
            })
    decisions = pd.DataFrame(decisions)
    all_rows = pd.concat([fixed, decisions], ignore_index=True)
    testing = all_rows.loc[all_rows.race_date.ge("20260101")]
    reference = testing.loc[testing.method.eq("candidate1")]
    comparisons = {method: risk_summary(part) for method, part in testing.groupby("method", sort=True)}
    verdicts = {mode: assess_switch(testing.loc[testing.method.eq(f"ai_{mode}")], reference)
                for mode in policies}
    latest = ai.fit_bundle(data, (pd.to_datetime(data.race_date.max()) + pd.Timedelta(days=1)).strftime("%Y%m%d"))
    report = {
        "schema": ai.SCHEMA, "reference_only": True, "purchase_eligible": False,
        "classification_all_OOT": classification_metrics(forecast),
        "classification_2026": classification_metrics(forecast.loc[forecast.race_date.ge("20260101")]),
        "2026_cohort_races": len(reference), "2026_fixed_gate_passes": int(by_race.loc[reference.race_id, "gate_passed"].sum()),
        "comparisons_2026": comparisons, "effectiveness": verdicts,
        "action_counts_2026": {
            mode: decisions.loc[decisions.method.eq(f"ai_{mode}") & decisions.race_date.ge("20260101"),
                                "selected_action"].value_counts().to_dict() for mode in policies},
        "historical_forecast_max_abs_error": maximum_error,
        "historical_baseline_all552_books_match": True,
        "historical_baseline_cost_yen": 426000, "historical_baseline_return_yen": 679710,
        "adoption_verdict": "not_ready_for_live",
    }
    protocol = {
        "schema": ai.SCHEMA, "reference_only": True, "purchase_eligible": False,
        "sources": {name: {"study": folder, "file": filename, "sha256": digest}
                    for name, (folder, filename, digest) in SOURCES.items()},
        "feature_source_sha256": ai.source_sha(), "runner_sha256": sha(Path(__file__)),
        "ticket_source_sha256": sha(REPO / "python-api" / "betting" / "research_opponent_pairs.py"),
        "base_model_sha256": ai.BASE_MODEL_SHA,
        "features_upset_and_axis": ai.NUMERIC + ai.CATEGORICAL,
        "features_event_models": ai.NUMERIC + ai.PARTNERS + ai.CATEGORICAL,
        "training": "Expanding quarterly, all training dates strictly before decisions;2024warmup.",
        "classifier": {"type": "logistic", "C": 1, "max_iter": 1500, "seed": 42,
                       "constant_binary_fallback": "Laplace prior when training class is constant"},
        "policy_calibration": "2025only,205 original gated cases; no2026 outcomes in severity estimates.",
        "severity": {"contexts": "predictedcalm/medium/large", "minimum_context_races": 40,
                     "minimum_context_dates": 12, "minimum_component_hits": 3,
                     "bootstrap_draws": 500, "seed": 20261103, "missing_support_credit": 0},
        "minimum_historical_payoff_score": 1.10,
        "primary_policy": "lower10", "alternative_policy": "mean", "no_hyperparameter_search": True,
        "funding": "1m reserve,2x700/3x500/4x300yen; no compounding or borrowing",
        "decision_cohort": "630 including78warmup and2025calibration skips;2026forwardcomparison236rows/150gate.",
        "effectiveness_criteria": [">=50buys", ">=10profitraces", "ROI95lower>100",
                                   "pairedprofitgain95lower>0 vs2025-selectedC1", "DD<=80%ofC1"],
        "live_blockers": verdicts["lower10"]["blocking_data_requirements"],
        "environment": {"python": platform.python_version(),
                        **{name: importlib.metadata.version(name) for name in
                           ("numpy", "pandas", "scikit-learn", "scipy", "threadpoolctl", "pyarrow", "joblib")}},
    }
    output.mkdir(parents=True)
    (output / "models").mkdir()
    write_json(output / "protocol.json", protocol)
    write_json(output / "report.json", report)
    write_json(output / "folds.json", folds)
    write_json(output / "latest_model.json", latest)
    for quarter, bundle in bundles.items():
        write_json(output / "models" / f"{quarter}.json", bundle)
    for mode, policy in policies.items():
        write_json(output / f"policy_{mode}.json", policy)
    data.to_parquet(output / "features_and_targets.parquet", index=False)
    forecast.to_parquet(output / "forecasts.parquet", index=False)
    history.to_parquet(output / "policy_calibration_2025.parquet", index=False)
    all_rows.to_parquet(output / "decision_ledger.parquet", index=False)
    pd.DataFrame([{"method": method, **value} for method, value in comparisons.items()]).drop(
        columns="monthly").to_csv(output / "summary_2026.csv", index=False, encoding="utf-8-sig")
    write_json(output / "artifact-manifest.json", {
        "schema": ai.SCHEMA, "files": {
            str(path.relative_to(output)): sha(path) for path in sorted(output.rglob("*")) if path.is_file()
        },
    })
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-root", type=Path, default=REPO / "reports" / "generated" / "race-selection")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare-kit", type=Path)
    group.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.prepare_kit is not None:
        prepare_kit(args.study_root, args.prepare_kit)
        print("Portable frozen-input kit prepared.")
    else:
        with threadpool_limits(limits=1):
            report = replay(args.study_root, args.output)
        print(json.dumps({key: report[key] for key in (
            "adoption_verdict", "classification_all_OOT", "comparisons_2026", "effectiveness",
            "action_counts_2026", "historical_forecast_max_abs_error")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
