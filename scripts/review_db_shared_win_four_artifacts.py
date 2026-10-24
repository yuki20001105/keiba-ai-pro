"""Read-only artifact review. Never fits, calibrates, acquires, or activates.

Outputs a new review receipt only when --output is supplied. Existing studies,
numeric evaluation sources, source configuration and models are never modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "reports/generated"
REVIEW_ROOT = GENERATED / "db-shared-win-four-review"
CANDIDATES = ("expanding_binary", "rolling5_binary", "expanding_race_nll", "rolling5_race_nll")
PERIODS = ("DEVELOPMENT_2016_2024", "SEEN_DIAGNOSTIC_2025_2026", "ALL_RETROSPECTIVE")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def fingerprint(directory):
    hashes = {str(path.relative_to(directory)).replace("\\", "/"): digest(path)
              for path in sorted(directory.rglob("*")) if path.is_file()}
    return {"files": hashes, "file_count": len(hashes),
            "fingerprint_sha256": hashlib.sha256(canonical(hashes).encode()).hexdigest()}


def expected_periods(year, candidate):
    if type(year) is not int or year not in range(2016, 2027) or candidate not in CANDIDATES:
        raise ValueError("review-fixed-candidate-and-year-required")
    first = 2013 if candidate.startswith("expanding_") else max(2013, year - 6)
    return {"TRAIN": (f"{first}0101", f"{year-2}1231"),
            "CAL": (f"{year-1}0101", f"{year-1}1231"),
            "TEST": (f"{year}0101", f"{year}1231")}


def review_worker(study, result):
    job = result["job_id"]
    request = read(study / f"{job}-request.json")
    mismatches = []
    expected = {"schema": "db-shared-win-four-worker-v1", "status": "completed",
        "request_sha256": hashlib.sha256(canonical(request).encode()).hexdigest(),
        "source_hashes": request["expected_source_hashes"], "feature_count": 50,
        **{name: request[name] for name in ("job_id", "candidate_id", "fold_year", "frame_sha256",
            "protocol_sha256", "phase1_receipt_sha256", "feature_columns", "categorical_columns")}}
    for name, value in expected.items():
        if result.get(name) != value:
            mismatches.append("result_binding:" + name)
    native = expected_periods(request["fold_year"], request["candidate_id"])
    json_expected = {name: list(bounds) for name, bounds in native.items()}
    if result.get("periods") != json_expected:
        mismatches.append("period_values")
    if set(result.get("partitions", {})) != {"TRAIN", "CAL", "TEST"}:
        mismatches.append("partition_keys")
    for name, receipt in result.get("partitions", {}).items():
        lower, upper = json_expected[name]
        if not lower <= receipt["date_from"] <= receipt["date_to"] <= upper:
            mismatches.append("partition_dates:" + name)
    runtime = result.get("runtime", {})
    if runtime.get("device") != "cpu" or runtime.get("native_threads") != 2 or runtime.get("rounds") != 150:
        mismatches.append("runtime")
    verified = {}
    for name, receipt in result["artifacts"].items():
        path = Path(receipt["path"]).resolve(strict=True)
        if not path.is_relative_to(study) or path.is_symlink() or digest(path) != receipt["sha256"]:
            mismatches.append("artifact_hash:" + name)
        else:
            verified[name] = receipt["sha256"]
    for split, errors in result.get("reload_parity", {}).items():
        if any(type(value) not in (int, float) or value != 0 for value in errors.values()):
            mismatches.append("reload_parity:" + split)
    if mismatches:
        raise ValueError("review-worker-invalid:" + job + ":" + ",".join(mismatches))
    return {"job_id": job, "worker_sha256": digest(study / f"{job}.json"),
        "json_period_type": "list", "native_expected_period_type": "tuple",
        "native_equality": result["periods"] == native,
        "normalized_period_equality": True, "other_validator_bindings_match": True,
        "artifact_hashes_verified": verified, "partitions": result["partitions"],
        "calibration_converged": result["calibration_audit"]["converged"],
        "calibration_at_bound": result["calibration_audit"]["at_bound"]}


def compact_calibration(record):
    keys = ("count", "wins", "mean_probability", "observed_win_rate",
            "bias_predicted_minus_actual", "bias_ci95", "bootstrap_samples_with_observations")
    return {name: record[name] for name in keys}


def compact_results(result):
    evaluation = result["evaluation"]
    precision, calibration, roi = {}, {}, {}
    for period in PERIODS:
        precision[period], calibration[period] = {}, {}
        for method in ("market_reference", *CANDIDATES):
            metric = evaluation["prediction_precision"][period][method]
            if metric["physical_speed_rmse"] is not None:
                raise ValueError("review-probability-rmse-is-not-speed-rmse")
            if not math.isclose(metric["rmse_win_indicator"] ** 2,
                                metric["brier"] * metric["race_count"] / metric["runner_count"],
                                rel_tol=1e-10, abs_tol=1e-12):
                raise ValueError("review-brier-rmse-cohort-invariant")
            precision[period][method] = {name: metric[name] for name in (
                "race_count", "runner_count", "date_from", "date_to", "nll", "brier",
                "spearman", "top1", "rmse_win_indicator", "comparison_vs_reference")}
            precision[period][method]["odds_band_calibration"] = metric["odds_band_calibration"]
            calibration[period][method] = {}
            for rule in ("top1", "top1_ev105"):
                selected = evaluation["win_probability_calibration"][period][method][rule]
                calibration[period][method][rule] = {name: selected[name] for name in (
                    "selected_bet_count", "selected_race_count", "selection_race_coverage", "interval_scope")}
                calibration[period][method][rule].update(overall=compact_calibration(selected["overall"]),
                    odds_band_calibration=selected["odds_band_calibration"])
        row = evaluation["separate_fixed_rule_roi"][period]
        comparison = row["bootstrap"]
        expected_policies = {"market_favorite"} | {candidate + ":" + rule
            for candidate in CANDIDATES for rule in ("top1", "top1_ev105")}
        if (comparison["family_size"] != 9 or len(comparison["candidates"]) != 9
                or {item["policy_id"] for item in comparison["candidates"]} != expected_policies
                or set(row["summaries"]) != expected_policies):
            raise ValueError("review-exact-nine-policy-family-required")
        policies = []
        for policy in comparison["candidates"]:
            summary = row["summaries"][policy["policy_id"]]
            if summary["stake"] != summary["bets"] * 100:
                raise ValueError("review-fixed-100-yen-required")
            expected_roi = summary["payout"] * 100 / summary["stake"] if summary["stake"] else None
            if (expected_roi is None) != (summary["roi_percent"] is None) or (
                    expected_roi is not None and not math.isclose(expected_roi, summary["roi_percent"], rel_tol=1e-12)):
                raise ValueError("review-roi-payout-stake-invariant")
            simultaneous = policy["roi_ci_simultaneous"]
            policies.append({"policy_id": policy["policy_id"], **{name: summary[name] for name in (
                "bets", "hits", "stake", "payout", "roi_percent", "race_coverage", "max_drawdown_jpy", "yearly")},
                **{name: policy[name] for name in ("roi_ci95", "roi_ci_simultaneous",
                    "paired_profit_difference_ci_simultaneous", "bootstrap_samples_with_stake")},
                "simultaneous_interval_strictly_above_100_percent": simultaneous[0] > 100. if simultaneous else None,
                "simultaneous_interval_covers_100_percent": simultaneous[0] <= 100. <= simultaneous[1] if simultaneous else None,
                "live_profit_edge_established": False})
        roi[period] = {name: row[name] for name in ("prediction_races", "roi_races",
            "unavailable_return_excluded_only_from_roi", "policy_selection_performed", "historical_proxy_only")}
        roi[period].update(family_size=9, policies=policies)
    calibration_folds = result.get("worker_results", [])
    return {"precision": precision, "calibration": calibration, "separate_roi": roi,
        "yearly_precision": evaluation["yearly_precision"], "data_audit": result["data_audit"],
        "runtime": result["runtime"],
        "calibrator_diagnostics": {"fold_count": len(calibration_folds),
            "at_bound_folds": [item["job_id"] for item in calibration_folds if item["calibration_audit"]["at_bound"]],
            "all_converged": all(item["calibration_audit"]["converged"] for item in calibration_folds)},
        "limitations": ["Known 2025-26 diagnostic, not a new independent holdout; 2026 ends at frozen-cache date.",
            "Reference is uncalibrated reciprocal market; candidate calibration can explain some delta.",
            "Winner-indicator RMSE is not physical speed RMSE and is monotone with Brier on the same cohort.",
            "Odds/selection calibration CIs are exploratory and unadjusted; ROI uses within-period nine-family intervals.",
            "Historical final odds are hindsight proxies; no executable pre-start-price or live profit claim."]}


def point_metrics(frame):
    """Independent race-level formula; does not call a study evaluator."""
    import numpy as np
    groups = frame.groupby("race_id", sort=False)
    winner = frame.finish.eq(1)
    p = frame.probability
    squared = (p - winner) ** 2
    pr = groups.probability.rank(ascending=False, method="average")
    actual = groups.finish.rank(ascending=True, method="average")
    n = groups.size().astype(float)
    sx, sy = pr.groupby(frame.race_id, sort=False).sum(), actual.groupby(frame.race_id, sort=False).sum()
    sxx = (pr * pr).groupby(frame.race_id, sort=False).sum()
    syy = (actual * actual).groupby(frame.race_id, sort=False).sum()
    sxy = (pr * actual).groupby(frame.race_id, sort=False).sum()
    denominator = np.sqrt(np.maximum(0., n * sxx - sx ** 2) * np.maximum(0., n * syy - sy ** 2))
    rho = np.divide(n * sxy - sx * sy, denominator, out=np.zeros(len(n)), where=denominator > 0)
    selected = frame.loc[groups.probability.idxmax()]
    return {"race_count": int(len(n)), "runner_count": len(frame),
        "nll": float(-np.log(p.loc[winner]).mean()),
        "brier": float(squared.groupby(frame.race_id, sort=False).sum().mean()),
        "spearman": float(rho.mean()), "top1": float(selected.finish.eq(1).mean()),
        "rmse_win_indicator": float(np.sqrt(squared.mean()))}


def point_calibration(frame):
    count = len(frame)
    wins = int(frame.finish.eq(1).sum())
    p = float(frame.probability.mean()) if count else None
    rate = wins / count if count else None
    return {"count": count, "wins": wins, "mean_probability": p,
        "observed_win_rate": rate, "bias_predicted_minus_actual": p - rate if count else None}


def select_fixed(frame, rule):
    chosen = frame.loc[frame.groupby("race_id", sort=False).probability.idxmax()].copy()
    if rule == "top1_ev105":
        chosen = chosen.loc[chosen.probability * chosen.odds >= 1.05]
    elif rule != "top1":
        raise ValueError("review-fixed-two-rules-only")
    return chosen


def read_return_mapping(frame, snapshot):
    """Read actual sole-winner return keys from the immutable snapshot only."""
    import numpy as np
    ids = sorted(frame.race_id.unique())
    connection = sqlite3.connect(Path(snapshot).as_uri() + "?mode=ro", uri=True)
    rows = {}
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        for start in range(0, len(ids), 500):
            batch = ids[start:start+500]
            placeholders = ",".join("?" for _ in batch)
            for race, key, amount in connection.execute(
                "SELECT race_id,combinations,payout FROM return_tables_ultimate "
                f"WHERE bet_type='単勝' AND race_id IN ({placeholders})", batch):
                rows.setdefault(str(race), []).append((key, amount))
    finally:
        connection.close()
    winners = frame.loc[frame.finish.eq(1)].set_index("race_id").horse_number.to_dict()
    valid = {}
    for race, values in rows.items():
        if len(values) != 1:
            continue
        key, amount = values[0]
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            continue
        token = str(key)
        if (not re.fullmatch(r"[1-9]\d?", token) or not 1 <= int(token) <= 18
                or not np.isfinite(amount) or amount < 100 or amount % 10 != 0
                or winners.get(race) != int(token)):
            continue
        valid[race] = amount
    return valid


def point_roi(cohort, chosen, payout_map):
    """No odds substitution; include every eligible date in drawdown."""
    import numpy as np
    winning = chosen.loc[chosen.finish.eq(1)]
    if not set(winning.race_id) <= payout_map.keys():
        raise ValueError("review-winning-payout-unavailable")
    stake = len(chosen) * 100
    payout = float(winning.race_id.map(payout_map).sum())
    profit_by_date = winning.race_id.map(payout_map).groupby(winning.race_date).sum().subtract(
        chosen.groupby("race_date").size() * 100, fill_value=0)
    dates = sorted(cohort.race_date.unique())
    cumulative = profit_by_date.reindex(dates, fill_value=0).cumsum().to_numpy(float)
    peaks = np.maximum.accumulate(np.r_[0., cumulative])
    drawdown = float(np.max(peaks[1:] - cumulative)) if len(cumulative) else 0.
    races = int(cohort.race_id.nunique())
    return {"bets": len(chosen), "hits": len(winning), "stake": stake, "payout": payout,
        "profit": payout - stake, "roi_percent": 100 * payout / stake if stake else None,
        "race_coverage": chosen.race_id.nunique() / races,
        "profit_per_eligible_race": (payout - stake) / races, "max_drawdown_jpy": drawdown}


def verify_point_values(study, result):
    """Audit saved TEST probabilities/returns, never refit or rerun a campaign."""
    import numpy as np
    import pandas as pd
    protocol = read(study / "protocol.json")["protocol"]
    snapshot = ROOT / "keiba/data/model_retrain_snapshots" / (protocol["snapshot_sha256"] + ".db")
    if digest(snapshot) != protocol["snapshot_sha256"]:
        raise ValueError("review-immutable-payout-snapshot-hash")
    columns = ["race_id", "race_date", "horse_number", "finish", "odds", "expected_runner_count",
               "venue", "surface", "normalized_market_probability", "race_payout_complete", "tansho_payout"]
    full = pd.read_parquet(study / "shared50.parquet", columns=columns)
    payout_map = read_return_mapping(full, snapshot)
    audit = result["data_audit"]["payout_audit"]
    if (audit["input_races"] != full.race_id.nunique() or audit["confirmed_payout_races"] != len(payout_map)
            or not full.race_payout_complete.equals(full.race_id.isin(payout_map))):
        raise ValueError("review-independent-payout-coverage-mismatch")
    winners = full.loc[full.finish.eq(1) & full.race_payout_complete]
    if not np.array_equal(winners.tansho_payout.to_numpy(float), winners.race_id.map(payout_map).to_numpy(float)):
        raise ValueError("review-independent-payout-values-mismatch")
    reference = full.loc[full.race_date.ge("20160101")].sort_values(
        ["race_date", "race_id", "horse_number"], kind="stable").reset_index(drop=True)
    methods = {"market_reference": reference.assign(probability=reference.normalized_market_probability)}
    for candidate in CANDIDATES:
        files = [Path(worker["artifacts"]["predictions"]["path"]) for worker in result["worker_results"]
                 if worker["candidate_id"] == candidate]
        output = pd.concat([pd.read_parquet(path) for path in files], ignore_index=True).sort_values(
            ["race_date", "race_id", "horse_number"], kind="stable").reset_index(drop=True)
        for name in columns[:8]:
            if not reference[name].equals(output[name]):
                raise ValueError("review-independent-prediction-cohort-mismatch:" + candidate + ":" + name)
        probability = output.probability.to_numpy(float)
        if (not np.isfinite(probability).all() or ((probability <= 0) | (probability >= 1)).any()
                or not np.allclose(output.groupby("race_id").probability.sum(), 1., rtol=0, atol=1e-8)):
            raise ValueError("review-valid-whole-race-probabilities-required")
        methods[candidate] = reference.assign(probability=probability)
    checks = []
    def compare(actual, expected, label):
        for name, value in actual.items():
            other = expected[name]
            if value is None or other is None:
                if value is not other:
                    raise ValueError("review-null-point-mismatch:" + label + ":" + name)
                continue
            if not math.isclose(float(value), float(other), rel_tol=1e-10, abs_tol=1e-12):
                raise ValueError("review-independent-point-mismatch:" + label + ":" + name)
            checks.append(abs(float(value) - float(other)))
    ranges = ((PERIODS[0], "20160101", "20241231"), (PERIODS[1], "20250101", "20261231"),
              (PERIODS[2], "20160101", "20261231"))
    edges = (1., 3., 5., 10., 20., 30., float("inf"))
    labels = ("1-<3", "3-<5", "5-<10", "10-<20", "20-<30", "30+")
    for period, lower, upper in ranges:
        eligible = methods["market_reference"].loc[reference.race_date.between(lower, upper)]
        roi_cohort = eligible.loc[eligible.race_payout_complete]
        roi_record = result["evaluation"]["separate_fixed_rule_roi"][period]
        reference_metrics = point_metrics(eligible)
        compare({"prediction_races": eligible.race_id.nunique(), "roi_races": roi_cohort.race_id.nunique(),
            "unavailable_return_excluded_only_from_roi": eligible.race_id.nunique() - roi_cohort.race_id.nunique()}, roi_record, period)
        compare(point_roi(roi_cohort, select_fixed(roi_cohort, "top1"), payout_map),
                roi_record["summaries"]["market_favorite"], period + ":market_favorite")
        for method, frame in methods.items():
            part = frame.loc[frame.race_date.between(lower, upper)]
            metric = result["evaluation"]["prediction_precision"][period][method]
            measured = point_metrics(part)
            compare(measured, metric, period + ":" + method)
            compare({"delta_nll": measured["nll"] - reference_metrics["nll"],
                "delta_brier": measured["brier"] - reference_metrics["brier"]},
                metric["comparison_vs_reference"], period + ":" + method + ":matched_market_delta")
            for index, label in enumerate(labels):
                band = part.loc[part.odds.ge(edges[index]) & part.odds.lt(edges[index + 1])]
                reported = metric["odds_band_calibration"][index]
                if reported["odds_band"] != label:
                    raise ValueError("review-fixed-odds-band-label")
                compare(point_calibration(band), reported, period + ":" + method + ":" + label)
            for rule in ("top1", "top1_ev105"):
                chosen = select_fixed(part, rule)
                record = result["evaluation"]["win_probability_calibration"][period][method][rule]
                compare(point_calibration(chosen), record["overall"], period + ":" + method + ":" + rule)
                if record["selected_bet_count"] != len(chosen):
                    raise ValueError("review-selection-cohort-count")
                for index, label in enumerate(labels):
                    band = chosen.loc[chosen.odds.ge(edges[index]) & chosen.odds.lt(edges[index + 1])]
                    compare(point_calibration(band), record["odds_band_calibration"][index],
                            period + ":" + method + ":" + rule + ":" + label)
                if method != "market_reference":
                    complete = part.loc[part.race_payout_complete]
                    compare(point_roi(complete, select_fixed(complete, rule), payout_map),
                            roi_record["summaries"][method + ":" + rule], period + ":" + method + ":" + rule)
    for year in range(2016, 2027):
        for method, frame in methods.items():
            part = frame.loc[frame.race_date.str[:4].eq(str(year))]
            measured = point_metrics(part)
            compare({"races": measured["race_count"], "runners": measured["runner_count"],
                **{name: measured[name] for name in ("nll", "brier", "spearman", "top1")}},
                result["evaluation"]["yearly_precision"][str(year)][method], str(year) + ":" + method)
    return {"status": "PASS", "independently_recomputed_point_fields": len(checks),
        "max_absolute_point_difference": max(checks), "actual_payout_races_verified": len(payout_map),
        "snapshot_read_only": True, "fixed_odds_and_selection_bands_verified": True,
        "roi_rules_verified": 9, "bootstrap_intervals_recomputed": False,
        "no_new_rule_or_calibration_fit": True, "no_outcome_driven_selection": True}


def compare_old_2016(old_study, new_study):
    """Verify deterministic numeric equality, allowing only provenance/timings to differ."""
    import joblib
    import pandas as pd
    old_study = Path(old_study).resolve(strict=True)
    if not old_study.is_relative_to(GENERATED.resolve()):
        raise ValueError("review-old-generated-study-required")
    before = fingerprint(old_study)
    fields = ("feature_columns", "categorical_columns", "feature_count", "periods", "partitions",
        "partition_input_hashes", "train_labels_sha256", "train_market_offset_sha256",
        "train_group_sizes_sha256", "encoder_sha256", "encoded_train_sha256", "encoder_diagnostics",
        "train_categories", "model_text_sha256", "reload_parity", "phase1_receipt_sha256", "frame_sha256")
    checked = []
    for candidate in CANDIDATES:
        job = "2016-" + candidate
        old, new = read(old_study / (job + ".json")), read(new_study / (job + ".json"))
        review_worker(old_study, old)
        review_worker(new_study, new)
        if any(old[name] != new[name] for name in fields):
            raise ValueError("review-v1-v2-numeric-binding-changed:" + candidate)
        for audit, excluded in (("calibration_audit", {"cal_seconds"}),
                                ("fit_audit", {"fit_seconds", "encoding_seconds"})):
            left = {name: value for name, value in old[audit].items() if name not in excluded}
            right = {name: value for name, value in new[audit].items() if name not in excluded}
            if left != right:
                raise ValueError("review-v1-v2-numeric-audit-changed:" + candidate + ":" + audit)
        parquet = {}
        for name in ("predictions", "calibration_predictions"):
            left = pd.read_parquet(old["artifacts"][name]["path"])
            right = pd.read_parquet(new["artifacts"][name]["path"])
            if not left.equals(right):
                raise ValueError("review-v1-v2-parquet-semantic-difference:" + candidate + ":" + name)
            parquet[name] = {"exact_semantic_equality": True, "rows": len(left),
                            "file_bytes_equal": old["artifacts"][name]["sha256"] == new["artifacts"][name]["sha256"]}
        # Only our pinned worker-generated artifacts, already SHA verified above,
        # are deserialized. Never load arbitrary downloaded/unverified joblib.
        left = joblib.load(old["artifacts"]["model"]["path"])
        right = joblib.load(new["artifacts"]["model"]["path"])
        for name in ("model_text", "model_text_sha256", "conditioned_calibration", "train_input_sha256",
                     "encoded_train_sha256", "train_labels_sha256", "train_market_offset_sha256"):
            if left[name] != right[name]:
                raise ValueError("review-v1-v2-serialized-model-numeric-difference:" + candidate + ":" + name)
        for name in ("columns", "categorical_columns", "categories", "means", "standard_deviations", "diagnostics"):
            if getattr(left["encoder"], name) != getattr(right["encoder"], name):
                raise ValueError("review-v1-v2-serialized-encoder-difference:" + candidate + ":" + name)
        checked.append({"candidate_id": candidate, "numeric_binding_fields_equal": list(fields),
            "model_text_sha256": new["model_text_sha256"], "serialized_model_text_and_encoder_exact": True,
            "calibration_coefficients_and_audit_exact_excluding_runtime": True,
            "parquet": parquet, "model_file_bytes_may_differ_due_to_source_provenance": True})
    if fingerprint(old_study) != before:
        raise ValueError("review-original-study-changed-during-comparison")
    return {"status": "PASS", "old_study_fingerprint_sha256": before["fingerprint_sha256"],
        "new_fits_claim_checked_separately_by_technical_amendment": True,
        "old_prediction_or_model_adoption_performed_by_review": False, "candidates": checked}


def evaluation_only_receipt(aggregate, worker_directory):
    """Require affirmative saved-worker/no-fit evidence, not absent-field defaults."""
    runtime = aggregate.get("runtime", {})
    if (runtime.get("mode") != "saved44-evaluation-only"
            or runtime.get("new_fits_started") != 0
            or runtime.get("fit_wall_seconds_this_run") != 0
            or runtime.get("saved_v2_artifacts_reused") is not True
            or runtime.get("validated_saved_worker_count") != 44
            or Path(aggregate.get("worker_study_path", "")).resolve() != worker_directory):
        raise ValueError("review-evaluation-only-saved44-no-fit-receipt-required")
    return {"mode": runtime["mode"], "new_fits_started": runtime["new_fits_started"],
            "fit_wall_seconds_this_run": runtime["fit_wall_seconds_this_run"],
            "saved_v2_artifacts_reused": runtime["saved_v2_artifacts_reused"],
            "validated_saved_worker_count": runtime["validated_saved_worker_count"],
            "worker_study_path": aggregate["worker_study_path"]}


def uncertainty_scope_audit(aggregate, protocol):
    """Check reported bootstrap scope and interval structure, never rerun resampling."""
    def interval(value, label):
        if value is not None and (not isinstance(value, list) or len(value) != 2
                or any(type(item) not in (int, float) or not math.isfinite(item) for item in value)
                or value[0] > value[1]):
            raise ValueError("review-invalid-reported-interval:" + label)

    def calibration_record(record, label, repetitions):
        if not 0 <= record["bootstrap_samples_with_observations"] <= repetitions:
            raise ValueError("review-calibration-bootstrap-observation-count:" + label)
        for name in ("mean_probability_ci95", "observed_win_rate_ci95", "bias_ci95"):
            interval(record[name], label + ":" + name)
        if record["count"] == 0 and (record["bootstrap_samples_with_observations"] != 0
                or any(record[name] is not None for name in ("mean_probability", "observed_win_rate",
                    "bias_predicted_minus_actual", "mean_probability_ci95", "observed_win_rate_ci95", "bias_ci95"))):
            raise ValueError("review-empty-calibration-must-stay-unestimated:" + label)

    methods = ("market_reference", *CANDIDATES)
    labels = ["1-<3", "3-<5", "5-<10", "10-<20", "20-<30", "30+"]
    probability_repetitions = protocol["probability_bootstrap_repetitions"]
    roi_repetitions = protocol["roi_bootstrap_repetitions"]
    calibration_records = 0
    for period in PERIODS:
        for method in methods:
            metric = aggregate["evaluation"]["prediction_precision"][period][method]
            selected = aggregate["evaluation"]["win_probability_calibration"][period][method]
            for scope, record in (("all", metric), *((rule, selected[rule]) for rule in ("top1", "top1_ev105"))):
                expected = {"method": "whole-date-cluster-year-stratified-bootstrap", "confidence": .95,
                    "repetitions": probability_repetitions, "seed": protocol["bootstrap_seed"],
                    "eligible_no_bet_dates_included": True, "interval_scope": "secondary-exploratory-unadjusted",
                    "fits_calibrator": False, "changes_policies": False, "eligible_for_ev": False,
                    "odds_band_boundaries": protocol["odds_bands"], "odds_bands": labels}
                if any(record.get(name) != value for name, value in expected.items()):
                    raise ValueError("review-probability-bootstrap-scope:" + period + ":" + method + ":" + scope)
                if [band["odds_band"] for band in record["odds_band_calibration"]] != labels:
                    raise ValueError("review-reported-fixed-band-order")
                for band in record["odds_band_calibration"]:
                    calibration_record(band, period + ":" + method + ":" + scope + ":" + band["odds_band"], probability_repetitions)
                    calibration_records += 1
                if scope != "all":
                    calibration_record(record["overall"], period + ":" + method + ":" + scope, probability_repetitions)
                    calibration_records += 1
            for name in ("nll", "brier", "spearman", "top1"):
                interval(metric[name + "_ci95"], period + ":" + method + ":" + name)
            for name in ("delta_nll_ci95", "delta_brier_ci95"):
                interval(metric["comparison_vs_reference"][name], period + ":" + method + ":" + name)
        bootstrap = aggregate["evaluation"]["separate_fixed_rule_roi"][period]["bootstrap"]
        expected = {"method": "paired-whole-date-cluster-year-stratified-bootstrap",
            "repetitions": roi_repetitions, "seed": protocol["bootstrap_seed"], "family_size": 9,
            "reference_id": "market_favorite", "all_cohort_dates_included": True, "common_candidate_weights": True,
            "simultaneous_confidence": 1. - .05 / 9}
        if any(bootstrap.get(name) != value for name, value in expected.items()):
            raise ValueError("review-roi-bootstrap-scope:" + period)
        for record in bootstrap["candidates"]:
            if not 0 <= record["bootstrap_samples_with_stake"] <= roi_repetitions:
                raise ValueError("review-roi-bootstrap-stake-count")
            for ordinary, simultaneous in (("roi_ci95", "roi_ci_simultaneous"),
                    ("paired_profit_difference_ci95", "paired_profit_difference_ci_simultaneous")):
                interval(record[ordinary], period + ":" + record["policy_id"] + ":" + ordinary)
                interval(record[simultaneous], period + ":" + record["policy_id"] + ":" + simultaneous)
                small, large = record[ordinary], record[simultaneous]
                if (small is None) != (large is None) or (small is not None and (large[0] > small[0] or large[1] < small[1])):
                    raise ValueError("review-simultaneous-interval-must-contain-ordinary")
    return {"status": "PASS", "reported_intervals_recomputed": False,
        "probability_repetitions": probability_repetitions, "roi_repetitions": roi_repetitions,
        "calibration_records_checked": calibration_records, "roi_family_size_per_period": 9,
        "period_count": 3, "roi_coverage_guarantee_scope": "within each period only; not across periods or prior research",
        "calibration_interval_scope": "secondary exploratory, unadjusted", "eligible_no_bet_dates_retained": True}


def review(study, verify_values=False, compare_v1=None, workers_study=None):
    study = Path(study).resolve(strict=True)
    if not study.is_dir() or not study.is_relative_to(GENERATED.resolve()) or study.is_symlink():
        raise ValueError("review-generated-study-directory-required")
    before = fingerprint(study)
    worker_directory = Path(workers_study).resolve(strict=True) if workers_study else study
    if (not worker_directory.is_dir() or not worker_directory.is_relative_to(GENERATED.resolve())
            or worker_directory.is_symlink()):
        raise ValueError("review-generated-worker-directory-required")
    worker_before = before if worker_directory == study else fingerprint(worker_directory)
    receipt = read(worker_directory / "protocol.json")
    pins = receipt["source_hashes"]
    for relative, expected_hash in pins.items():
        path = (ROOT / relative).resolve(strict=True)
        if not path.is_relative_to(ROOT.resolve()) or digest(path) != expected_hash:
            raise ValueError("review-frozen-source-pin-mismatch:" + relative)
    shared_hash = digest(worker_directory / "shared50.parquet")
    workers = [read(path) for path in sorted(worker_directory.glob("*.json"))
               if path.stem[:4].isdigit() and path.stem[5:] in CANDIDATES]
    for worker in workers:
        request = read(worker_directory / (worker["job_id"] + "-request.json"))
        if (request["expected_source_hashes"] != pins or worker["frame_sha256"] != shared_hash
                or worker["protocol_sha256"] != receipt["protocol_sha256"]):
            raise ValueError("review-worker-frozen-study-binding")
    checked = [review_worker(worker_directory, worker) for worker in workers]
    result = {"schema": "db-shared-win-four-artifact-review-v1", "study": str(study),
              "study_fingerprint": before, "worker_reviews": checked,
              "worker_count": len(checked), "fit_started": False, "evaluation_campaign_started": False,
              "network_requests": 0, "source_modified": False, "model_activated": False}
    result["worker_study"] = str(worker_directory)
    result["worker_study_fingerprint"] = worker_before
    full = study / "results.json"
    if full.exists():
        if len(checked) != 44 or {(item["candidate_id"], item["fold_year"]) for item in workers} != {
                (candidate, year) for candidate in CANDIDATES for year in range(2016, 2027)}:
            raise ValueError("review-complete44-workers-required")
        manifest = read(study / "artifact_manifest.json")["files"]
        if manifest != {name: value for name, value in before["files"].items() if name != "artifact_manifest.json"}:
            raise ValueError("review-sealed-full-artifact-manifest-required")
        aggregate = read(full)
        aggregation_pins = aggregate.get("aggregation_source_hashes", aggregate.get("source_hashes", {}))
        if not isinstance(aggregation_pins, dict) or not aggregation_pins:
            raise ValueError("review-aggregation-source-pins-required")
        for relative, expected_hash in aggregation_pins.items():
            path = (ROOT / relative).resolve(strict=True)
            if not path.is_relative_to(ROOT.resolve()) or digest(path) != expected_hash:
                raise ValueError("review-aggregation-source-pin-mismatch:" + relative)
        declared_worker_pins = aggregate.get("worker_source_hashes", aggregate.get("source_hashes"))
        if declared_worker_pins != pins:
            raise ValueError("review-worker-source-lineage-required")
        if (aggregate.get("status") != "completed" or aggregate.get("phase1_pass") is not True
                or any(aggregate.get(name) is not False for name in
                       ("actual_purchase", "production_changed", "forward_collection_changed"))
                or aggregate.get("preservation_before") != aggregate.get("preservation_after")):
            raise ValueError("review-full-research-and-preservation-contract-required")
        if worker_directory != study:
            result["evaluation_only_receipt"] = evaluation_only_receipt(aggregate, worker_directory)
        if sorted(aggregate["worker_results"], key=lambda item: item["job_id"]) != sorted(workers, key=lambda item: item["job_id"]):
            raise ValueError("review-full-result-worker-readback-binding")
        result.update(status="completed", results_sha256=digest(full), compact=compact_results(aggregate))
        result["reported_uncertainty_scope_audit"] = uncertainty_scope_audit(aggregate, receipt["protocol"])
        result["full_study_metadata"] = {name: aggregate.get(name) for name in ("schema",
            "post_outcome_technical_amendment", "new_preregistration_claim", "all44_fits_new",
            "failed_run_artifacts_reused", "technical_amendment_sha256")}
        result["full_study_metadata"].update(aggregation_source_pin_count=len(aggregation_pins),
            worker_source_pin_count=len(pins), separate_saved_worker_study=worker_directory != study)
        if verify_values:
            result["independent_point_value_audit"] = verify_point_values(worker_directory, aggregate)
        if compare_v1:
            result["v1_v2_2016_semantic_comparison"] = compare_old_2016(compare_v1, worker_directory)
    else:
        result.update(status="partial_artifact_review_only", full_evaluation_available=False)
    if fingerprint(study) != before:
        raise ValueError("review-study-mutated-during-read")
    if fingerprint(worker_directory) != worker_before:
        raise ValueError("review-saved-workers-mutated-during-read")
    result["study_read_only_verified"] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", required=True)
    parser.add_argument("--output")
    parser.add_argument("--verify-values", action="store_true", help="Completed studies only: audit saved point values and immutable payouts")
    parser.add_argument("--compare-v1", help="Completed v2 only: compare the original four 2016 worker models and probabilities")
    parser.add_argument("--workers-study", help="v3 evaluation-only: sealed saved-v2 worker directory, never refit")
    args = parser.parse_args()
    result = review(args.study, args.verify_values, args.compare_v1, args.workers_study)
    if args.output:
        destination = Path(args.output).resolve()
        if destination.suffix != ".json" or not destination.is_relative_to(REVIEW_ROOT.resolve()) or destination.exists():
            raise ValueError("review-new-exclusive-review-json-required")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        print(json.dumps({"status": result["status"], "worker_count": result["worker_count"],
            "output": str(destination), "sha256": digest(destination)}))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
