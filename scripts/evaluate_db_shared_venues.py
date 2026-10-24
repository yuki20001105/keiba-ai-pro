"""Isolated retrospective walk-forward comparison of fixed DB venue models.

No network, publication, activation, forward-ledger writes, calibration or bets.
Only the pinned checkpointed SQLite snapshot is read. The fixed47 feature
formula retains travelling horses' prior history across every venue. Models
are fitted in memory and discarded; only new research reports are saved.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import uuid

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "reports/generated/venue-model-evaluation"
PROTOCOL_PATH = ROOT / "config/research/venue-model-evaluation-20261011.json"
SNAPSHOT_SHA256 = "8880c290e31ae614f2c837dd00bc773f07e24ef6919e611a3f645927bc679a7e"
SNAPSHOT_PATH = ROOT / "keiba/data/model_retrain_snapshots" / (SNAPSHOT_SHA256 + ".db")
VENUES = {"01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
          "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉"}
STRATEGIES = ("global", "venue_only", "blend50")
DEFAULT_PROTOCOL = {
    "schema": "db-shared-venue-walk-forward-protocol-v1",
    "purpose": "Compare venue-specific training against venue-aware global training without deployment changes",
    "date_to": "20260913",
    "training_start_year": 2013, "first_test_year": 2016, "last_test_year": 2026,
    "rolling_years": 5,
    "windows": ["expanding", "rolling5"], "strategies": list(STRATEGIES),
    "specialist_weight": 0.5, "minimum_specialist_races": 500,
    "minimum_specialist_runners": 2500, "minimum_specialist_dates": 40,
    "workers": 4, "threads_per_fit": 2,
    "bootstrap_repetitions": 10000, "seed": 20261011,
    "feature_pipeline": "local-db-shared-v1", "feature_count": 47,
    "preset": "standard", "num_boost_round": 200,
    "common_train_only_encoder": True, "common_train_only_speed_baseline": True,
    "historical_features_all_venues": True, "current_and_same_day_results_excluded": True,
    "test_year_daily_prior_history_updates": True,
    "bootstrap_cluster": "race_date", "bootstrap_stratify": "test_year",
    "primary_metrics": ["race_macro_spearman", "runner_pooled_rmse_speed_mps"],
    "overall_comparison_family_size": 8, "overall_simultaneous_ci_level": 0.99375,
    "venue_ci_level": 0.95, "venue_results_exploratory": True,
    "historically_seen_periods_not_fresh_forward_validation": True,
    "roi_evaluation": False, "probability_calibration_evaluation": False,
    "publish_models": False, "activate_models": False, "modify_forward_collection": False,
    "commit_push": False, "real_purchase": False,
}


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def object_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item"):
        value = value.item()
    return None if isinstance(value, float) and not math.isfinite(value) else value


def _write_json(path, value):
    Path(path).write_text(json.dumps(_json_safe(value), ensure_ascii=False, indent=2,
                                    allow_nan=False) + "\n", encoding="utf-8")


def _modules():
    for directory in (ROOT / "python-api", ROOT / "keiba"):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    from training import db_shared_features, db_shared_fit
    return db_shared_features, db_shared_fit


def validate_protocol(value):
    # This study has one precommitted design. A differently specified study must
    # get its own script/version, not acquire a tunable CLI after TEST is seen.
    if value != DEFAULT_PROTOCOL:
        raise ValueError("venue-study-protocol-must-equal-fixed-v1")
    return json.loads(json.dumps(value))


def build_folds(training_start_year=2013, first_test_year=2016, last_test_year=2026,
                date_to="20260913", rolling_years=5):
    values = (training_start_year, first_test_year, last_test_year, rolling_years)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        raise ValueError("venue-fold-integer-years-required")
    if (training_start_year < 2013 or first_test_year - training_start_year < 3
            or first_test_year > last_test_year or rolling_years < 3
            or not re.fullmatch(r"\d{8}", str(date_to))):
        raise ValueError("venue-fold-boundaries-invalid")
    parsed = datetime.strptime(str(date_to), "%Y%m%d")
    if parsed.year != last_test_year:
        raise ValueError("venue-fold-last-year-date-mismatch")
    folds = []
    for window in ("expanding", "rolling5"):
        for year in range(first_test_year, last_test_year + 1):
            lower = training_start_year if window == "expanding" else max(training_start_year, year - rolling_years)
            test_to = f"{year}1231" if year < last_test_year else str(date_to)
            folds.append({"window": window, "year": year,
                          "train_from": f"{lower}0101", "train_to": f"{year - 1}1231",
                          "test_from": f"{year}0101", "test_to": test_to,
                          "test_year": year, "train_date_from": f"{lower}0101",
                          "train_date_to": f"{year - 1}1231", "test_date_from": f"{year}0101",
                          "test_date_to": test_to,
                          "prior_calendar_years": year - lower,
                          "partial_test_year": test_to != f"{year}1231"})
    return folds


def specialist_eligibility(frame, minimum_races=500, minimum_dates=40, minimum_runners=2500):
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1
           for value in (minimum_races, minimum_dates, minimum_runners)):
        raise ValueError("venue-specialist-minimums-invalid")
    if not {"race_id", "race_date"}.issubset(frame.columns):
        raise ValueError("venue-specialist-training-keys-required")
    races, dates = int(frame.race_id.nunique()), int(frame.race_date.nunique())
    reasons = []
    if races < minimum_races:
        reasons.append("insufficient_complete_training_races")
    if dates < minimum_dates:
        reasons.append("insufficient_training_dates")
    if len(frame) < minimum_runners:
        reasons.append("insufficient_training_runners")
    return {"available": not reasons, "reason_codes": reasons,
            "training_race_count": races, "training_date_count": dates,
            "training_runner_count": len(frame), "minimum_races": minimum_races,
            "minimum_dates": minimum_dates, "minimum_runners": minimum_runners}


def race_metric_records(target, raw_scores, baseline):
    """One row per complete race; additive physical errors permit exact pooling."""
    _, fit = _modules()
    scores = np.asarray(raw_scores, dtype=float)
    required = {"race_id", "race_date", "horse_number", "finish", "venue", "surface"}
    if (target.empty or not required.issubset(target.columns)
            or scores.shape != (len(target),) or not np.isfinite(scores).all()):
        raise ValueError("venue-race-metrics-aligned-finite-scores-required")
    frame = target[list(required)].copy().reset_index(drop=True)
    frame["score"] = scores
    for column in ("race_date", "venue", "surface"):
        if frame.groupby("race_id")[column].nunique(dropna=False).ne(1).any():
            raise ValueError("venue-race-metrics-inconsistent-race")
    if frame.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("venue-race-metrics-duplicate-runner")
    predicted_speed = fit._inverse_speed(scores, target, baseline)
    truth_speed = fit.raw_speed_mps(target).to_numpy()
    error = predicted_speed - truth_speed
    if not np.isfinite(error).all():
        raise ValueError("venue-race-metrics-physical-errors-invalid")
    frame["squared_error"] = error ** 2
    frame["absolute_error"] = np.abs(error)
    first = frame.groupby("race_id").score.rank(method="average")
    second = (-pd.to_numeric(frame.finish)).groupby(frame.race_id).rank(method="average")
    first -= first.groupby(frame.race_id).transform("mean")
    second -= second.groupby(frame.race_id).transform("mean")
    numerator = (first * second).groupby(frame.race_id).sum()
    denominator = np.sqrt((first ** 2).groupby(frame.race_id).sum() *
                          (second ** 2).groupby(frame.race_id).sum())
    correlations = (numerator / denominator.where(denominator.gt(0))).fillna(0)
    top = frame.sort_values(["race_id", "score", "horse_number"],
                            ascending=[True, False, True], kind="stable").groupby("race_id").head(1)
    result = frame.groupby("race_id", sort=True).agg(
        race_date=("race_date", "first"), venue=("venue", "first"), surface=("surface", "first"),
        runner_count=("horse_number", "size"), squared_error_sum=("squared_error", "sum"),
        absolute_error_sum=("absolute_error", "sum"))
    result["rank_correlation"] = correlations
    result["constant_score_or_result"] = denominator.eq(0)
    result["top1_correct"] = top.set_index("race_id").finish.eq(1).astype(int)
    return result.reset_index()


def _paired_frame(records):
    keys = ["window", "year", "race_id"]
    if records.duplicated(keys + ["strategy"]).any():
        raise ValueError("venue-paired-duplicate-strategy-race")
    expected = records.loc[records.strategy.eq("global")].copy()
    if expected.empty:
        raise ValueError("venue-paired-global-baseline-required")
    baseline = expected[keys + ["race_date", "venue", "surface", "runner_count",
                                "rank_correlation", "squared_error_sum"]].rename(columns={
        "rank_correlation": "global_rank_correlation",
        "squared_error_sum": "global_squared_error_sum",
        "runner_count": "global_runner_count", "race_date": "global_race_date",
        "venue": "global_venue", "surface": "global_surface"})
    paired = records.merge(baseline, on=keys, how="outer", validate="many_to_one", indicator=True)
    if (paired._merge.ne("both").any() or not paired.runner_count.eq(paired.global_runner_count).all()
            or not paired.race_date.eq(paired.global_race_date).all()
            or not paired.venue.eq(paired.global_venue).all()
            or not paired.surface.eq(paired.global_surface).all()):
        raise ValueError("venue-paired-cohort-or-metadata-mismatch")
    for strategy in STRATEGIES:
        actual = paired.loc[paired.strategy.eq(strategy), keys]
        if len(actual) != len(expected) or set(map(tuple, actual.to_numpy())) != set(map(tuple, expected[keys].to_numpy())):
            raise ValueError("venue-paired-all-strategies-must-cover-exact-same-races")
    return paired.drop(columns="_merge")


def date_cluster_bootstrap(paired, repetitions=2000, seed=20261011, confidence=0.95,
                           stratify_by_year=True):
    """Paired deltas; whole dates resampled within years, never individual horses."""
    if (isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions < 100
            or not 0 < confidence < 1):
        raise ValueError("venue-bootstrap-repetitions-or-confidence-invalid")
    required = {"race_date", "year", "runner_count", "rank_correlation",
                "global_rank_correlation", "squared_error_sum", "global_squared_error_sum"}
    if paired.empty or not required.issubset(paired.columns):
        raise ValueError("venue-bootstrap-paired-records-required")
    numeric = paired[list(required - {"race_date"})].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(numeric.to_numpy(dtype=float)).all() or paired.runner_count.le(0).any():
        raise ValueError("venue-bootstrap-numeric-records-invalid")
    if paired.race_date.astype(str).str[:4].astype(int).ne(paired.year.astype(int)).any():
        raise ValueError("venue-bootstrap-date-year-mismatch")
    groups = []
    group_keys = ["year", "race_date"] if stratify_by_year else ["race_date"]
    dates = paired.assign(delta_rho=paired.rank_correlation - paired.global_rank_correlation).groupby(group_keys).agg(
        delta_sum=("delta_rho", "sum"), race_count=("delta_rho", "size"),
        sse=("squared_error_sum", "sum"), global_sse=("global_squared_error_sum", "sum"),
        runner_count=("runner_count", "sum")).reset_index()
    if len(dates) < 2:
        return {"confidence": confidence, "repetitions": repetitions, "date_count": len(dates),
                "status": "insufficient_dates", "delta_rank_correlation_ci": None,
                "delta_rmse_speed_mps_ci": None, "rank_correlation_delta_ci": None,
                "rmse_speed_mps_delta_ci": None}
    if stratify_by_year:
        for _, stratum in dates.groupby("year", sort=True):
            groups.append(stratum[["delta_sum", "race_count", "sse", "global_sse", "runner_count"]].to_numpy(float))
    else:
        groups = [dates[["delta_sum", "race_count", "sse", "global_sse", "runner_count"]].to_numpy(float)]
    rng = np.random.default_rng(seed)
    values = np.zeros((repetitions, 5), dtype=float)
    for start in range(0, repetitions, 64):
        count = min(64, repetitions - start)
        for stratum in groups:
            sampled = rng.integers(0, len(stratum), size=(count, len(stratum)))
            values[start:start + count] += stratum[sampled].sum(axis=1)
    delta_rho = values[:, 0] / values[:, 1]
    delta_rmse = np.sqrt(values[:, 2] / values[:, 4]) - np.sqrt(values[:, 3] / values[:, 4])
    alpha = (1 - confidence) / 2
    interval = lambda vector: [float(np.quantile(vector, alpha)), float(np.quantile(vector, 1 - alpha))]
    rho_delta = float((paired.rank_correlation - paired.global_rank_correlation).mean())
    rmse_delta = float(np.sqrt(paired.squared_error_sum.sum() / paired.runner_count.sum()) -
                       np.sqrt(paired.global_squared_error_sum.sum() / paired.runner_count.sum()))
    rho_interval, rmse_interval = interval(delta_rho), interval(delta_rmse)
    return {"confidence": confidence, "repetitions": repetitions, "date_count": len(dates),
            "status": "computed", "resampling": "paired whole race dates; TEST-year strata" if stratify_by_year else "paired whole race dates",
            "expected_resamples_in_each_tail": repetitions * alpha,
            "delta_rank_correlation_ci": rho_interval,
            "delta_rmse_speed_mps_ci": rmse_interval,
            "rank_correlation_delta": rho_delta, "rmse_speed_mps_delta": rmse_delta,
            "rank_correlation_delta_ci": rho_interval, "rmse_speed_mps_delta_ci": rmse_interval,
            "year_strata": len(groups), "singleton_year_strata": sum(len(group) == 1 for group in groups),
            "positive_rmse_delta_is_worse": True}


def _group_summary(paired):
    runners = int(paired.runner_count.sum())
    rho = float(paired.rank_correlation.mean())
    rmse = float(np.sqrt(paired.squared_error_sum.sum() / runners))
    base_rho = float(paired.global_rank_correlation.mean())
    base_rmse = float(np.sqrt(paired.global_squared_error_sum.sum() / runners))
    return {"race_count": len(paired), "runner_count": runners,
            "date_count": int(paired.race_date.nunique()), "rank_correlation": rho,
            "rmse_speed_mps": rmse, "mae_speed_mps": float(paired.absolute_error_sum.sum() / runners),
            "top1_win_rate": float(paired.top1_correct.mean()),
            "global_rank_correlation": base_rho, "global_rmse_speed_mps": base_rmse,
            "delta_rank_correlation_vs_global": rho - base_rho,
            "delta_rmse_speed_mps_vs_global": rmse - base_rmse,
            "fallback_race_count": int(paired.fallback_to_global.sum()),
            "constant_score_or_result_races": int(paired.constant_score_or_result.sum()),
            "date_from": str(paired.race_date.min()), "date_to": str(paired.race_date.max()),
            "aggregation": "race-macro Spearman; runner-weighted physical RMSE m/s"}


def paired_summaries(records, include_year=False, repetitions=10000, seed=20261011):
    paired = _paired_frame(records)
    summaries = []
    dimensions = (("overall", []), ("venue", ["venue"]), ("surface", ["surface"]))
    for scope, dims in dimensions:
        keys = ["window", "strategy"] + (["year"] if include_year else []) + dims
        for values, group in paired.groupby(keys, sort=True, dropna=False):
            if not isinstance(values, tuple):
                values = (values,)
            labels = dict(zip(keys, values))
            row = {"scope": scope, **labels, **_group_summary(group)}
            if not include_year and labels["strategy"] != "global":
                # Stable group-specific seed prevents output-order dependence.
                sub_seed = seed ^ int(object_hash({"scope": scope, **_json_safe(labels)})[:8], 16)
                row["paired_bootstrap_95"] = date_cluster_bootstrap(group, repetitions, sub_seed)
                if scope == "overall":
                    row["paired_bootstrap_bonferroni_99_375"] = date_cluster_bootstrap(
                        group, repetitions, sub_seed, confidence=0.99375)
                    row["adjustment_family"] = "8 overall deltas: 2 windows x 2 strategy contrasts x 2 primary metrics"
                else:
                    row["exploratory_segment_not_adoption_evidence"] = True
            summaries.append(row)
    return summaries


def preservation_receipt():
    pointer = ROOT / "python-api/models/.active_model.json"
    active = json.loads(pointer.read_text(encoding="utf-8"))["model_id"]
    if not isinstance(active, str) or not re.fullmatch(r"model_[A-Za-z0-9_]+", active):
        raise ValueError("venue-study-active-pointer-invalid")
    artifact = pointer.parent / (active + ".joblib")
    frozen = ROOT / "reports/generated/forward-runtime/h04-live-20260920-v2/protocol.json"
    protocol = json.loads(frozen.read_text(encoding="utf-8"))
    pins = protocol["protocol"]["source_manifest"]
    pin_results = {name: {"expected": expected, "actual": file_hash(ROOT / name)} for name, expected in pins.items()}
    if len(pin_results) != 35 or any(row["expected"] != row["actual"] for row in pin_results.values()):
        raise ValueError("venue-study-forward-source-pins-mismatch")
    processes = []
    try:
        import psutil
        for process in psutil.process_iter(["pid", "cmdline", "create_time"]):
            try:
                if any(Path(str(item)).name.casefold() == "run_forward_collector.py"
                       for item in process.info.get("cmdline") or []):
                    processes.append({"pid": process.pid, "created_at_epoch": process.info["create_time"]})
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass
    except ImportError:
        pass
    return {"active_model_id": active, "active_pointer_sha256": file_hash(pointer),
            "active_artifact_sha256": file_hash(artifact), "forward_protocol_sha256": file_hash(frozen),
            "forward_source_pins": pin_results, "forward_processes": sorted(processes, key=lambda row: row["pid"])}


def _resource_allocation(encoded_bytes):
    workers, native = 1, 2
    resources = {"physical_cores": None, "logical_cores": os.cpu_count(), "available_memory_bytes": None}
    try:
        import psutil
        physical = psutil.cpu_count(logical=False) or 1
        available = int(psutil.virtual_memory().available)
        per_fit = max(512 * 1024**2, encoded_bytes * 6)
        memory_workers = max(1, (available - 2 * 1024**3) // per_fit)
        workers = int(max(1, min(4, physical // 2, memory_workers)))
        native = min(2, physical)
        resources = {"physical_cores": physical, "logical_cores": psutil.cpu_count(),
                     "available_memory_bytes": available, "estimated_bytes_per_fit": per_fit}
    except (ImportError, OSError, AttributeError):
        pass
    return {"workers": workers, "threads_per_fit": native,
            "total_native_thread_budget": min(8, workers * native), "resources": resources}


def _history_diagnostics(target, features, scores, baseline, window, year, strategy):
    """Horse-level physical error; rank comparison needs whole complete races."""
    _, fit = _modules()
    counts = pd.to_numeric(features.safe_prior_starts, errors="coerce").to_numpy()
    squared = (fit._inverse_speed(scores, target, baseline) - fit.raw_speed_mps(target).to_numpy()) ** 2
    rows = []
    for label, mask in (("history_le1", np.isfinite(counts) & (counts <= 1)),
                        ("history_gt1", np.isfinite(counts) & (counts > 1)),
                        ("history_unknown", ~np.isfinite(counts))):
        subset = target.loc[mask]
        rows.append({"window": window, "year": year, "strategy": strategy, "history_group": label,
                     "runner_count": len(subset), "race_count_with_any_runner": int(subset.race_id.nunique()),
                     "squared_error_sum": float(squared[mask].sum()),
                     "rmse_speed_mps": float(np.sqrt(squared[mask].mean())) if mask.any() else None,
                     "rank_correlation": None,
                     "rank_note": "rank correlation omitted for incomplete horse-selected race subgroups"})
    return rows


def evaluate_fold(target, features, fold, fit, protocol, notify=None):
    train_mask = target.race_date.between(fold["train_from"], fold["train_to"]).to_numpy()
    test_mask = target.race_date.between(fold["test_from"], fold["test_to"]).to_numpy()
    if not train_mask.any() or not test_mask.any():
        raise ValueError("venue-study-fold-has-empty-train-or-test")
    training = target.loc[train_mask].reset_index(drop=True)
    testing = target.loc[test_mask].reset_index(drop=True)
    train_raw = features.loc[train_mask].reset_index(drop=True)
    test_raw = features.loc[test_mask].reset_index(drop=True)
    if str(training.race_date.max()) >= str(testing.race_date.min()):
        raise ValueError("venue-study-train-test-time-overlap")
    clock = time.perf_counter()
    encoder, encoded_train, encoder_audit = fit._fitted_fixed_encoder(train_raw)
    encoded_test = encoder.transform(test_raw)
    baseline = fit.fit_speed_deviation_baseline(training)
    labels = fit.apply_speed_deviation_baseline(training, baseline).to_numpy()
    preset = dict(next(value for value in fit.PRESETS if value["id"] == protocol["preset"]))
    if preset != {"id": "standard", "num_boost_round": 200, "num_leaves": 31,
                  "min_data_in_leaf": 30, "lambda_l2": 1.0}:
        raise ValueError("venue-study-standard-preset-changed")
    allocation = _resource_allocation(int(encoded_train.memory_usage(deep=True).sum()))
    prep_seconds = time.perf_counter() - clock
    clock = time.perf_counter()
    global_model = fit._train_booster(encoded_train, labels, preset,
                                     n_threads=allocation["total_native_thread_budget"])
    global_scores = fit._finite_predictions(global_model, encoded_test, allocation["total_native_thread_budget"])
    global_seconds = time.perf_counter() - clock
    global_model_text_sha256 = hashlib.sha256(global_model.model_to_string().encode()).hexdigest()
    del global_model
    gc.collect()
    specialist_scores = global_scores.copy()
    available = np.zeros(len(testing), dtype=bool)
    coverage_rows = []
    jobs = []
    for code, venue in VENUES.items():
        train_positions = np.flatnonzero(training.venue.eq(venue).to_numpy())
        test_positions = np.flatnonzero(testing.venue.eq(venue).to_numpy())
        eligibility = specialist_eligibility(training.iloc[train_positions], protocol["minimum_specialist_races"],
                                             protocol["minimum_specialist_dates"], protocol["minimum_specialist_runners"])
        receipt = {"window": fold["window"], "year": fold["year"], "venue_code": code, "venue": venue,
                   **eligibility, "test_race_count": int(testing.iloc[test_positions].race_id.nunique()),
                   "test_runner_count": len(test_positions), "test_date_count": int(testing.iloc[test_positions].race_date.nunique()),
                   "partial_test_year": fold["partial_test_year"], "fallback_to_global": not eligibility["available"],
                   "evaluation_status": "AVAILABLE" if len(test_positions) else "N/A_NO_TEST_RACES"}
        coverage_rows.append(receipt)
        if len(test_positions) and eligibility["available"]:
            jobs.append((venue, train_positions, test_positions, receipt))
    def specialist(job):
        venue, train_positions, test_positions, receipt = job
        begin = time.perf_counter()
        model = fit._train_booster(encoded_train.iloc[train_positions], labels[train_positions], preset,
                                  n_threads=allocation["threads_per_fit"])
        predictions = fit._finite_predictions(model, encoded_test.iloc[test_positions], allocation["threads_per_fit"])
        digest = hashlib.sha256(model.model_to_string().encode()).hexdigest()
        del model
        return test_positions, predictions, {**receipt, "fit_predict_seconds": time.perf_counter() - begin,
                                             "model_text_sha256": digest}
    clock = time.perf_counter()
    fitted_receipts = []
    with ThreadPoolExecutor(max_workers=allocation["workers"], thread_name_prefix="venue-research") as executor:
        futures = [executor.submit(specialist, job) for job in jobs]
        for future in as_completed(futures):
            positions, scores, receipt = future.result()
            specialist_scores[positions] = scores
            available[positions] = True
            fitted_receipts.append(receipt)
            if notify:
                notify(f"{fold['window']} {fold['year']} {receipt['venue']} specialist fitted")
    specialist_seconds = time.perf_counter() - clock
    score_sets = {"global": global_scores, "venue_only": specialist_scores,
                  "blend50": (global_scores + specialist_scores) * 0.5}
    records, history = [], []
    for strategy, scores in score_sets.items():
        row = race_metric_records(testing, scores, baseline)
        row["window"], row["year"], row["strategy"] = fold["window"], fold["year"], strategy
        row["partial_test_year"] = fold["partial_test_year"]
        runner_flags = testing.assign(_available=available).groupby("race_id")._available.all()
        row["specialist_available"] = row.race_id.map(runner_flags)
        row["fallback_to_global"] = False
        if strategy != "global":
            row["fallback_to_global"] = ~row.specialist_available
        records.append(row)
        history.extend(_history_diagnostics(testing, test_raw, scores, baseline, fold["window"], fold["year"], strategy))
    receipt = {**fold, "train": fit._partition_receipt(training), "test": fit._partition_receipt(testing),
               "encoded_train_sha256": fit._frame_digest(encoded_train),
               "encoded_test_sha256": fit._frame_digest(encoded_test),
               "baseline": baseline, "baseline_sha256": object_hash(baseline), "encoder_audit": encoder_audit,
               "train_label_sha256": hashlib.sha256(labels.tobytes()).hexdigest(),
               "global_model_text_sha256": global_model_text_sha256,
               "score_sha256": {name: hashlib.sha256(values.tobytes()).hexdigest() for name, values in score_sets.items()},
               "resource_allocation": allocation, "prepare_fold_seconds": prep_seconds,
               "global_fit_predict_seconds": global_seconds,
               "specialists_wall_seconds": specialist_seconds,
               "specialist_receipts": sorted(fitted_receipts, key=lambda row: row["venue"]),
               "test_used_for_selection_or_fit": False, "artifact_published": False}
    return pd.concat(records, ignore_index=True), coverage_rows, history, receipt


def run_study(snapshot=SNAPSHOT_PATH, *, expected_sha256=SNAPSHOT_SHA256, protocol=None, execute=False):
    protocol = validate_protocol(protocol or json.loads(PROTOCOL_PATH.read_text(encoding="utf-8")))
    protocol_file_sha = file_hash(PROTOCOL_PATH)
    snapshot = Path(snapshot).resolve(strict=True)
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(snapshot) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError("venue-study-needs-checkpointed-immutable-snapshot")
    digest = file_hash(snapshot)
    if digest != expected_sha256.lower() or digest != SNAPSHOT_SHA256:
        raise ValueError("venue-study-snapshot-sha256-mismatch")
    api_features, fit = _modules()
    if list(api_features.FEATURE_COLUMNS) != list(fit.FEATURE_COLUMNS) or len(fit.FEATURE_COLUMNS) != 47:
        raise ValueError("venue-study-fixed47-contract-mismatch")
    sources = fit.source_hashes()
    script_sha = file_hash(__file__)
    preservation_before = preservation_receipt()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
    output = OUTPUT_ROOT.resolve() / stamp
    if not output.is_relative_to(OUTPUT_ROOT.resolve()):
        raise ValueError("venue-study-output-outside-new-research-root")
    output.mkdir(parents=True, exist_ok=False)
    _write_json(output / "protocol.json", protocol)
    report = {"schema": "db-shared-venue-walk-forward-result-v1", "status": "running" if execute else "dry_run",
              "started_at": datetime.now(timezone.utc).isoformat(), "snapshot": str(snapshot),
              "snapshot_sha256": digest, "protocol_sha256": object_hash(protocol),
              "protocol_source_file": str(PROTOCOL_PATH), "protocol_source_file_sha256": protocol_file_sha,
              "source_hashes": sources, "script_sha256": script_sha,
              "preservation_before": preservation_before, "protocol": protocol,
              "feature_columns": list(fit.FEATURE_COLUMNS), "folds": [],
              "calibration_performed": False, "roi_evaluated": False, "active_model_changed": False,
              "artifact_published": False, "forward_collector_written": False,
              "known_retrospective_data_not_unseen_validation": True}
    _write_json(output / "results.json", report)
    print(json.dumps({"output": str(output), "protocol_sha256": report["protocol_sha256"],
                      "status": report["status"]}), flush=True)
    if not execute:
        report["planned_folds"] = build_folds()
        _write_json(output / "results.json", report)
        return output, report
    started = time.perf_counter()
    all_records, all_coverage, all_history = [], [], []
    def checkpoint():
        _write_json(output / "results.json", report)
        if all_records:
            pd.concat(all_records, ignore_index=True).to_csv(output / "race_metrics.csv", index=False, encoding="utf-8-sig")
        pd.DataFrame(all_coverage).to_csv(output / "venue_fold_coverage.csv", index=False, encoding="utf-8-sig")
        pd.DataFrame(all_history).to_csv(output / "history_diagnostics.csv", index=False, encoding="utf-8-sig")
    try:
        clock = time.perf_counter()
        target, history, coverage = api_features.load_db_frames(snapshot, f"{protocol['training_start_year']}0101", protocol["date_to"])
        load_seconds = time.perf_counter() - clock
        clock = time.perf_counter()
        raw = api_features.build_training_features(target, history)
        del history
        feature_seconds = time.perf_counter() - clock
        target, raw = fit._prepare(target, raw)
        report["preparation"] = {"load_seconds": load_seconds, "feature_generation_seconds": feature_seconds,
                                 "raw47_sha256": fit._frame_digest(raw), "target_sha256": fit._frame_digest(target),
                                 "source_coverage": coverage, "point_in_time_audit": raw.attrs.get("point_in_time_audit"),
                                 "features_prepared_once_across_all_venues": True,
                                 "target_period": fit._partition_receipt(target)}
        inventory = target.assign(year=target.race_date.astype(str).str[:4].astype(int)).groupby(["year", "venue"]).agg(
            runner_count=("race_id", "size"), race_count=("race_id", "nunique"), date_count=("race_date", "nunique"),
            date_from=("race_date", "min"), date_to=("race_date", "max")).reset_index()
        inventory.to_csv(output / "eligible_year_venue_inventory.csv", index=False, encoding="utf-8-sig")
        checkpoint()
        from threadpoolctl import threadpool_limits
        for fold in build_folds():
            print(json.dumps({"stage": "fold_started", **fold}), flush=True)
            with threadpool_limits(limits=protocol["workers"] * protocol["threads_per_fit"]):
                records, coverage_rows, history_rows, receipt = evaluate_fold(
                    target, raw, fold, fit, protocol, notify=lambda value: print(value, flush=True))
            all_records.append(records)
            all_coverage.extend(coverage_rows)
            all_history.extend(history_rows)
            report["folds"].append(receipt)
            checkpoint()
            print(json.dumps({"stage": "fold_completed", "window": fold["window"], "year": fold["year"],
                              "completed_folds": len(report["folds"])}), flush=True)
            gc.collect()
        records = pd.concat(all_records, ignore_index=True)
        _paired_frame(records)
        report["yearly_summaries"] = paired_summaries(records, include_year=True)
        print("paired TEST-year-stratified date-cluster bootstrap started", flush=True)
        report["pooled_summaries"] = paired_summaries(records, repetitions=protocol["bootstrap_repetitions"], seed=protocol["seed"])
        pd.DataFrame(report["yearly_summaries"]).to_csv(output / "yearly_summary.csv", index=False, encoding="utf-8-sig")
        pd.json_normalize(report["pooled_summaries"]).to_csv(output / "pooled_summary.csv", index=False, encoding="utf-8-sig")
        report["preservation_after"] = preservation_receipt()
        report["preservation_unchanged"] = report["preservation_after"] == preservation_before
        report["source_hashes_after"] = fit.source_hashes()
        report["script_sha256_after"] = file_hash(__file__)
        report["protocol_source_file_sha256_after"] = file_hash(PROTOCOL_PATH)
        report["snapshot_sha256_after"] = file_hash(snapshot)
        report["source_unchanged"] = (sources == report["source_hashes_after"]
                                      and script_sha == report["script_sha256_after"]
                                      and protocol_file_sha == report["protocol_source_file_sha256_after"])
        report["snapshot_unchanged"] = digest == report["snapshot_sha256_after"]
        if not all(report[name] for name in ("preservation_unchanged", "source_unchanged", "snapshot_unchanged")):
            raise ValueError("venue-study-preservation-or-source-changed-during-research")
        report["status"] = "completed"
        report["elapsed_seconds"] = time.perf_counter() - started
        report["completed_at"] = datetime.now(timezone.utc).isoformat()
        checkpoint()
        files = {str(path.relative_to(output)): file_hash(path) for path in output.iterdir() if path.is_file()}
        _write_json(output / "artifact_manifest.json", {"schema": "venue-study-artifact-manifest-v1", "files": files,
                                                       "protocol_sha256": report["protocol_sha256"], "snapshot_sha256": digest})
        print(json.dumps({"status": "completed", "output": str(output), "elapsed_seconds": report["elapsed_seconds"]}), flush=True)
        return output, report
    except BaseException as error:
        report["status"] = "failed"
        report["error"] = f"{type(error).__name__}: {error}"
        report["elapsed_seconds"] = time.perf_counter() - started
        checkpoint()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT_PATH)
    parser.add_argument("--snapshot-sha256", default=SNAPSHOT_SHA256)
    parser.add_argument("--protocol", type=Path, help="optional JSON; must match the locked v1 protocol")
    parser.add_argument("--execute", action="store_true", help="execute fixed fits; omitted means protocol-only dry run")
    args = parser.parse_args()
    protocol = json.loads(args.protocol.read_text(encoding="utf-8")) if args.protocol else None
    run_study(args.snapshot, expected_sha256=args.snapshot_sha256, protocol=protocol, execute=args.execute)


if __name__ == "__main__":
    main()
