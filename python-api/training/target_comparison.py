"""Fixed-goal target comparison, isolated from serving and model registration."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
import multiprocessing
import os
from pathlib import Path
import queue
import sys
import time

import joblib
import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar

from experiments.common import (
    REPO_ROOT, active_pointer_hash, file_sha256, json_sha256, read_json,
    source_manifest, write_json,
)
from experiments.goal_loop import BASE, _other_experiments_settled
from experiments.orchestrator import driver_lock
from experiments.search import freeze_json
from training import db_shared_features as shared, db_shared_optimized as optimized
from training.performance_targets import rolling_condition_target, race_relative_speed_target


SCHEMA = "keiba-target-goal-loop-v1"
ARMS = ["current_speed", "balanced_speed", "condition_speed", "race_relative", "win_binary", "rank_top3"]
PRESETS = [
    {"id": "compact", "num_leaves": 15, "min_data_in_leaf": 60, "num_boost_round": 120},
    {"id": "standard", "num_leaves": 31, "min_data_in_leaf": 30, "num_boost_round": 200},
]
PROTOCOL = {
    "schema": SCHEMA, "goal": "Improve calibrated winner NLL without sacrificing race ranking.",
    "arms": ARMS, "presets": PRESETS, "warmup_year": 2016, "train_start_year": 2017,
    "development_years": [2023, 2024, 2025], "development_end": "20250630",
    "final_train_end": "20250630", "final_calibration_start": "20250701",
    "final_calibration_end": "20251231", "retrospective_test_year": 2026,
    "test_previously_observed": True, "maximum_fits": 40, "maximum_seconds": 3600,
    "maximum_processes": 2, "threads_per_process": 2, "reserve_ram_gib": 4,
    "minimum_nll_improvement": .005, "maximum_top1_degradation": .005,
    "maximum_rank_degradation": .01, "minimum_paired_days": 20,
    "bootstrap_samples": 2000, "seed": 42, "promotion_eligible": False,
    "balanced_loss_scale": "mean1; LambdaRank uses internal query normalization",
}
_WORK = None


def partition_masks(frame, evaluation_year):
    dates = frame.race_date.astype(str)
    years = dates.str[:4].astype(int)
    if evaluation_year == 2026:
        return (dates.between("20170101", "20250630").to_numpy(),
                dates.between("20250701", "20251231").to_numpy(),
                years.eq(2026).to_numpy())
    return (
        years.between(2017, evaluation_year - 2).to_numpy(),
        years.eq(evaluation_year - 1).to_numpy(),
        (years.eq(evaluation_year) & dates.le("20250630")).to_numpy(),
    )


def training_weights(frame, arm):
    if arm in {"current_speed", "rank_top3"}:
        return None
    weights = 1/frame.groupby("race_id").race_id.transform("size")
    return (weights/weights.mean()).to_numpy()


def common_cohort_mask(frame, condition):
    available = pd.Series(np.isfinite(np.asarray(condition)), index=frame.index)
    available = available.groupby(frame.race_id).transform("all")
    single = frame.finish.eq(1).groupby(frame.race_id).transform("sum").eq(1)
    return (available & single).to_numpy()


def race_probabilities(scores, race_ids, temperature):
    scores = np.asarray(scores, dtype=float)
    ids = np.asarray(race_ids)
    if (scores.ndim != 1 or scores.shape != ids.shape or not len(scores)
            or not np.isfinite(scores).all() or not np.isfinite(temperature) or temperature <= 0
            or pd.isna(ids).any()):
        raise ValueError("invalid-race-probability-input")
    codes, unique = pd.factorize(ids, sort=False)
    if np.min(np.bincount(codes)) < 2:
        raise ValueError("complete-race-required")
    logits = scores / temperature
    maxima = np.full(len(unique), -np.inf)
    np.maximum.at(maxima, codes, logits)
    numerators = np.maximum(np.exp(logits - maxima[codes]), 1e-15)
    return numerators / np.bincount(codes, weights=numerators)[codes]


def calibrate(frame, scores):
    winners = frame.finish.eq(1).to_numpy()
    if not frame.finish.eq(1).groupby(frame.race_id).sum().eq(1).all():
        raise ValueError("single-winner-calibration-required")
    def loss(log_temperature):
        probabilities = race_probabilities(scores, frame.race_id, np.exp(log_temperature))
        return float(-np.log(probabilities[winners]).mean())
    result = minimize_scalar(loss, bounds=(np.log(.02), np.log(10)), method="bounded",
                             options={"xatol": 1e-7, "maxiter": 100})
    if not result.success or not np.isfinite(result.fun):
        raise ValueError("temperature-calibration-failed")
    temperature = float(np.exp(result.x))
    return {
        "temperature": temperature, "calibration_races": int(frame.race_id.nunique()),
        "calibration_days": int(frame.race_date.nunique()),
        "date_from": str(frame.race_date.min()), "date_to": str(frame.race_date.max()),
        "calibration_nll": float(result.fun), "fit_uses_test": False,
        "boundary_reached": temperature <= .02001 or temperature >= 9.999,
    }


def race_metrics(frame, scores, probabilities):
    data = frame[["race_id", "race_date", "horse_number", "finish"]].copy().reset_index(drop=True)
    data["score"], data["p"] = np.asarray(scores), np.asarray(probabilities)
    if (not np.isfinite(data[["score", "p"]]).all().all() or not data.p.between(0, 1).all()
            or not np.allclose(data.groupby("race_id").p.sum(), 1, atol=1e-8)):
        raise ValueError("invalid-evaluation-probabilities")
    data["winner"] = data.finish.eq(1).astype(float)
    if not data.groupby("race_id").winner.sum().eq(1).all():
        raise ValueError("single-winner-evaluation-required")
    a = data.groupby("race_id").score.rank(method="average")
    b = (-data.finish).groupby(data.race_id).rank(method="average")
    a -= a.groupby(data.race_id).transform("mean")
    b -= b.groupby(data.race_id).transform("mean")
    denom = np.sqrt((a*a).groupby(data.race_id).sum() * (b*b).groupby(data.race_id).sum())
    rho = ((a*b).groupby(data.race_id).sum() / denom.where(denom.gt(0))).fillna(0)
    ordered = data.sort_values(["race_id", "score", "horse_number"],
                               ascending=[True, False, True], kind="stable")
    top = ordered.groupby("race_id").head(1).set_index("race_id")
    top3 = ordered.groupby("race_id").head(3).assign(hit=lambda f: f.finish.le(3))
    races = data.groupby("race_id").race_date.first().to_frame()
    races["nll"] = -np.log(data.loc[data.winner.eq(1)].set_index("race_id").p.clip(lower=1e-15))
    races["brier"] = ((data.p-data.winner)**2).groupby(data.race_id).sum()
    races["spearman"], races["top1"] = rho, top.winner
    races["top3_recall"] = (
        top3.groupby("race_id").hit.sum() / data.finish.le(3).groupby(data.race_id).sum()
    )
    metrics = {name: float(races[name].mean()) for name in
               ("nll", "brier", "spearman", "top1", "top3_recall")}
    metrics.update(races=len(races), runners=len(data))
    return metrics, races.reset_index()


def paired_comparison(baseline, candidate):
    columns = ["race_id", "race_date"]
    first = baseline.sort_values(columns).reset_index(drop=True)
    second = candidate.sort_values(columns).reset_index(drop=True)
    if not first[columns].equals(second[columns]) or first.race_id.duplicated().any():
        raise ValueError("paired-comparison-cohort-changed")
    delta = first.nll - second.nll
    day = pd.DataFrame({"day": first.race_date, "delta": delta}).groupby("day").delta.agg(["sum", "count"])
    rng = np.random.default_rng(42)
    samples = []
    for _ in range(PROTOCOL["bootstrap_samples"]):
        draw = day.iloc[rng.integers(0, len(day), size=len(day))]
        samples.append(float(draw["sum"].sum() / draw["count"].sum()))
    interval = [float(v) for v in np.quantile(samples, [.025, .975])]
    top1 = float(second.top1.mean() - first.top1.mean())
    rank = float(second.spearman.mean() - first.spearman.mean())
    annual = {}
    for year, positions in first.groupby(first.race_date.str[:4]).indices.items():
        annual[year] = {
            "nll_improvement": float(delta.iloc[positions].mean()),
            "top1_difference": float((second.top1-first.top1).iloc[positions].mean()),
            "spearman_difference": float((second.spearman-first.spearman).iloc[positions].mean()),
        }
    passes = (
        len(day) >= 20 and delta.mean() >= .005 and interval[0] > 0
        and top1 >= -.005 and rank >= -.01
        and all(row["top1_difference"] >= -.005 and row["spearman_difference"] >= -.01
                for row in annual.values())
    )
    return {
        "nll_improvement": float(delta.mean()), "nll_improvement_95ci": interval,
        "top1_difference": top1, "spearman_difference": rank, "annual": annual,
        "paired_races": len(first), "paired_days": len(day), "passes": bool(passes),
        "interval_scope": "exploratory paired-day CI; development selection not multiplicity-corrected",
    }


def check_approval(task, approval):
    if not approval.get("actor") or approval.get("task_sha256") != json_sha256(task):
        raise ValueError("approval-not-bound-to-exact-target-task")


def _source_snapshot():
    return {
        **source_manifest(), **optimized.feature_source_hashes(),
        "training.target_comparison": file_sha256(Path(__file__)),
        "training.performance_targets": file_sha256(Path(__file__).with_name("performance_targets.py")),
        "scripts.run_target_goal_loop": file_sha256(REPO_ROOT / "scripts" / "run_target_goal_loop.py"),
        "training.local_retrain": file_sha256(REPO_ROOT / "python-api" / "training" / "local_retrain.py"),
        "training.db_shared_fit": file_sha256(REPO_ROOT / "python-api" / "training" / "db_shared_fit.py"),
    }


def _sources():
    current = _source_snapshot()
    if current != _IMPORTED_SOURCES:
        raise ValueError("target-code-changed-since-import-restart-required")
    return current


def _protected():
    policies = ["model_acceptance_contract.v1.json", "phase3n_staking_payout_policy.v1.json"]
    return {"active_pointer": active_pointer_hash(), "policies": {
        name: file_sha256(REPO_ROOT / "config" / name) for name in policies},
        "models": {path.name: {"bytes": path.stat().st_size, "sha256": file_sha256(path)}
                   for path in sorted((REPO_ROOT / "python-api" / "models").glob("*.joblib"))}}


def _settled(root):
    _other_experiments_settled(root)
    for state in BASE.glob("target-*/run-state.json"):
        if state.parent != root and read_json(state).get("status") not in {"completed"}:
            raise RuntimeError(f"unresolved-target-run:{state.parent.name}")


def _assert_task(root):
    task = read_json(root / "task-ledger.json")
    if task["protocol"] != PROTOCOL or task["sources"] != _sources() or task["protected"] != _protected():
        raise ValueError("target-task-code-goal-or-production-changed")
    for name, digest in task["files"].items():
        path = root / name
        if file_sha256(path) != digest:
            raise ValueError(f"target-task-input-changed:{name}")
    return task


def rebuild_inputs(snapshot):
    target, history, coverage = shared.load_db_frames(snapshot, "20160101", "20261031")
    raw = shared.build_training_features(target, history)
    audit = {name: raw.attrs.get(name) for name in
             ("current_outcomes_consumed", "same_day_or_future_history_used", "point_in_time_audit")}
    if audit["current_outcomes_consumed"] is not False or audit["same_day_or_future_history_used"] is not False:
        raise ValueError("point-in-time-feature-proof-required")
    target, raw = optimized.legacy._prepare(target, raw)
    features = optimized.select_features(raw)
    del history, raw
    condition, target_audit = rolling_condition_target(target)
    relative = race_relative_speed_target(target)
    keep = common_cohort_mask(target, condition)
    labels = pd.DataFrame({"condition_speed": condition, "race_relative": relative}).loc[keep].reset_index(drop=True)
    return (target.loc[keep].reset_index(drop=True), features.loc[keep].reset_index(drop=True),
            labels, coverage, {"feature_audit": audit, "condition": target_audit})


def prepare(root, source):
    from training.local_retrain import create_sqlite_snapshot
    started = time.monotonic()
    with driver_lock(BASE, root):
        _settled(root)
        if (root / "task-ledger.json").exists():
            return _assert_task(root)
        if root.exists() and any(root.iterdir()):
            raise ValueError("nonempty-unprepared-target-directory")
        root.mkdir(parents=True, exist_ok=True)
        sources, protected = _sources(), _protected()
        print("Creating consistent read-only source snapshot", flush=True)
        snapshot = create_sqlite_snapshot(source.resolve(), (root / "snapshots").resolve())
        print("Building shared point-in-time raw46 once", flush=True)
        target, features, labels, coverage, audit = rebuild_inputs(snapshot.path)
        dev = target.race_date.le("20250630")
        diagnostics = {}
        for name, values in labels.loc[dev].items():
            diagnostics[name] = {str(key): float(value) for key, value in values.describe().items()}
        excluded = coverage["target"]["eligible_races"] - target.race_id.nunique()
        write_json(root / "diagnostics.json", {
            "development_only": True, "targets": diagnostics, "condition": audit["condition"],
            "feature_missing_rate_development": float(features.loc[dev].isna().to_numpy().mean()),
            "whole_races_excluded_for_common_cohort": int(excluded), "feature_audit": audit["feature_audit"],
        })
        target.to_parquet(root / "target.parquet", index=False)
        features.to_parquet(root / "features.parquet", index=False)
        labels.to_parquet(root / "labels.parquet", index=False)
        write_json(root / "coverage.json", coverage)
        files = {str(path.relative_to(root)): file_sha256(path) for path in
                 [snapshot.path, *(root / name for name in
                   ("target.parquet", "features.parquet", "labels.parquet", "coverage.json", "diagnostics.json"))]}
        years = target.groupby(target.race_date.str[:4]).agg(
            runners=("race_id", "size"), races=("race_id", "nunique"), first=("race_date", "min"),
            last=("race_date", "max"),
        ).to_dict("index")
        task = {"schema": SCHEMA, "protocol": PROTOCOL, "sources": sources, "protected": protected,
                "files": files, "cohort_by_year": years, "feature_columns": list(features.columns),
                "snapshot_sha256": snapshot.sha256, "preparation_seconds": time.monotonic()-started}
        if sources != _sources() or protected != _protected():
            raise ValueError("target-input-preparation-code-or-production-changed")
        freeze_json(root / "task-ledger.json", task)
        return task


def _initialize(root):
    global _WORK
    if _sources() != read_json(Path(root) / "task-ledger.json")["sources"]:
        raise ValueError("worker-loaded-code-not-bound-to-approved-task")
    _WORK = (
        Path(root), pd.read_parquet(Path(root) / "target.parquet"),
        pd.read_parquet(Path(root) / "features.parquet"), pd.read_parquet(Path(root) / "labels.parquet"),
    )


def _teacher(target, labels, arm, train):
    baseline = None
    if arm in {"current_speed", "balanced_speed"}:
        baseline = optimized.fit_speed_deviation_baseline(target.loc[train])
        values = optimized.apply_speed_deviation_baseline(target.loc[train], baseline).to_numpy()
    elif arm in {"condition_speed", "race_relative"}:
        values = labels.loc[train, arm].to_numpy()
    elif arm == "win_binary":
        values = target.loc[train].finish.eq(1).astype(int).to_numpy()
    elif arm == "rank_top3":
        values = (4-target.loc[train].finish).clip(lower=0).to_numpy(dtype=int)
    else:
        raise ValueError("unknown-target-arm")
    return values, baseline


def _teacher_digest(values):
    return hashlib.sha256(np.asarray(values, dtype=np.float64).tobytes()).hexdigest()


def check_run_limits(deadline):
    import psutil
    if time.time() >= deadline:
        raise TimeoutError("fixed-target-comparison-budget-expired")
    available = psutil.virtual_memory().available
    if available < PROTOCOL["reserve_ram_gib"] * 1024**3:
        raise RuntimeError("research-ram-reserve-exhausted")
    return available


def terminate_owned_workers(processes):
    for process in processes:
        if process.is_alive():
            process.terminate()
    for process in processes:
        process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        if process.is_alive():
            raise RuntimeError(f"owned-worker-could-not-be-stopped:{process.pid}")


def _worker_main(root, tasks, results):
    try:
        _initialize(root)
        while True:
            case = tasks.get()
            if case is None:
                return
            results.put(("result", _fit_case(case)))
    except BaseException as error:
        results.put(("error", {"pid": os.getpid(), "error": type(error).__name__ + ":" + str(error)}))


def _execute_cases(root, cases, deadline):
    import psutil
    context = multiprocessing.get_context("spawn")
    tasks, results = context.Queue(), context.Queue()
    processes = [context.Process(target=_worker_main, args=(str(root), tasks, results)) for _ in range(2)]
    min_available, peak_rss = check_run_limits(deadline), 0
    completed, pending = {}, iter(cases)
    inflight = 0
    succeeded = False
    try:
        for process in processes:
            process.start()
        freeze_json(root / "worker-leases" / f"{len(list((root / 'worker-leases').glob('*.json')))+1:03d}.json",
                    {"pids": [process.pid for process in processes], "parent_pid": os.getpid()})
        for _ in processes:
            case = next(pending, None)
            if case is not None:
                check_run_limits(deadline)
                tasks.put(case)
                inflight += 1
        while inflight:
            min_available = min(min_available, check_run_limits(deadline))
            rss = 0
            for process in processes:
                try:
                    rss += psutil.Process(process.pid).memory_info().rss
                except psutil.NoSuchProcess:
                    pass
            peak_rss = max(peak_rss, rss)
            try:
                kind, value = results.get(timeout=.25)
            except queue.Empty:
                if any(process.exitcode is not None for process in processes):
                    raise RuntimeError("target-worker-exited-without-a-result")
                continue
            if kind == "error":
                raise RuntimeError(f"target-worker-failed:{value}")
            check_run_limits(deadline)
            if value["name"] in completed:
                raise ValueError("duplicate-worker-result")
            completed[value["name"]] = value
            inflight -= 1
            sequence = len(list((root / "progress").glob("*.json"))) + 1
            freeze_json(root / "progress" / f"{sequence:03d}.json", value)
            print(f"{sequence}/38 {value['name']} NLL={value['metrics']['nll']:.6f}", flush=True)
            case = next(pending, None)
            if case is not None:
                check_run_limits(deadline)
                tasks.put(case)
                inflight += 1
        for _ in processes:
            tasks.put(None)
        while any(process.is_alive() for process in processes):
            check_run_limits(deadline)
            for process in processes:
                process.join(timeout=.1)
        if any(process.exitcode != 0 for process in processes):
            raise RuntimeError("target-worker-shutdown-failed")
        succeeded = True
        names = [f"{arm}-{preset['id']}-{year}" for arm, preset, year, _ in cases]
        if set(names) != set(completed):
            raise ValueError("worker-case-set-changed")
        return [completed[name] for name in names]
    finally:
        terminate_owned_workers([process for process in processes if process.pid is not None])
        for state in (root / "worker-states").glob("*.json"):
            value = read_json(state)
            if value["status"] == "running" and value["pid"] in [p.pid for p in processes]:
                write_json(state, {**value, "status": "unknown", "reason": "owned-worker-stopped-without-completion"})
        freeze_json(root / "resource-monitor" / f"{len(list((root / 'resource-monitor').glob('*.json')))+1:03d}.json",
                    {"completed": succeeded, "minimum_available_ram_bytes": min_available,
                     "peak_combined_worker_rss_bytes": peak_rss})
        for channel in (tasks, results):
            channel.cancel_join_thread()
            channel.close()


def _fit_case(case):
    import lightgbm as lgb
    root, target, features, labels = _WORK
    arm, preset, year, deadline = case
    name = f"{arm}-{preset['id']}-{year}"
    state = root / "worker-states" / f"{name}.json"
    started = time.monotonic()
    write_json(state, {"status": "running", "pid": os.getpid(), "case": name})
    try:
        check_run_limits(deadline)
        train, cal, evaluation = partition_masks(target, year)
        if not all(mask.any() for mask in (train, cal, evaluation)):
            raise ValueError(f"empty-date-partition:{name}")
        encoder, encoded, _ = optimized._fitted_fixed_encoder(features.loc[train])
        teacher, baseline = _teacher(target, labels, arm, train)
        weights = training_weights(target.loc[train], arm)
        groups = target.loc[train].groupby("race_id", sort=False).size().to_numpy() if arm == "rank_top3" else None
        params = {
            "objective": "binary" if arm == "win_binary" else "lambdarank" if arm == "rank_top3" else "regression",
            "verbosity": -1, "num_threads": 2, "device_type": "cpu", "deterministic": True,
            "force_col_wise": True, "seed": 42, "max_bin": 63, "learning_rate": .05,
            "num_leaves": preset["num_leaves"], "min_data_in_leaf": preset["min_data_in_leaf"],
            "lambda_l2": 1., "lambdarank_truncation_level": 3, "lambdarank_norm": True,
        }
        dataset = lgb.Dataset(encoded, label=teacher, weight=weights, group=groups,
                              categorical_feature=list(optimized.CATEGORICAL_COLUMNS))
        def bounded(environment):
            if time.time() >= deadline:
                raise TimeoutError("fixed-target-comparison-budget-expired")
        model = lgb.train(params, dataset, num_boost_round=preset["num_boost_round"], callbacks=[bounded])
        cal_scores = model.predict(encoder.transform(features.loc[cal]), raw_score=True, num_threads=2)
        calibration = calibrate(target.loc[cal], cal_scores)
        scores = model.predict(encoder.transform(features.loc[evaluation]), raw_score=True, num_threads=2)
        probabilities = race_probabilities(scores, target.loc[evaluation].race_id, calibration["temperature"])
        metrics, races = race_metrics(target.loc[evaluation], scores, probabilities)
        directory = root / "cases" / name
        directory.mkdir(parents=True, exist_ok=False)
        bundle = {
            "schema": SCHEMA, "model": model, "encoder": encoder, "arm": arm, "preset": preset,
            "evaluation_year": year, "calibration": calibration, "baseline": baseline,
            "feature_columns": list(optimized.FEATURE_COLUMNS), "research_only": True,
            "teacher_digest": _teacher_digest(teacher),
            "promotion_eligible": False, "purchase_eligible": False,
            "training_date_from": str(target.loc[train].race_date.min()),
            "training_date_to": str(target.loc[train].race_date.max()),
        }
        joblib.dump(bundle, directory / "model.research.joblib")
        predictions = target.loc[evaluation, ["race_id", "race_date", "horse_number", "finish"]].copy()
        predictions["score"], predictions["probability"] = scores, probabilities
        predictions.to_parquet(directory / "predictions.parquet", index=False)
        races.to_parquet(directory / "race-metrics.parquet", index=False)
        result = {"name": name, "arm": arm, "preset": preset, "evaluation_year": year,
                  "metrics": metrics, "calibration": calibration, "seconds": time.monotonic()-started}
        freeze_json(directory / "result.json", result)
        if _sources() != read_json(root / "task-ledger.json")["sources"]:
            raise ValueError("worker-code-changed-during-fit")
        write_json(state, {"status": "completed", "pid": os.getpid(), "case": name})
        return result
    except BaseException as error:
        write_json(state, {"status": "failed", "pid": os.getpid(), "case": name,
                           "error": type(error).__name__ + ":" + str(error)})
        raise


def _combined(root, rows):
    return pd.concat([pd.read_parquet(root / "cases" / row["name"] / "race-metrics.parquet")
                      for row in rows], ignore_index=True)


def select_development(root, rows):
    expected = {f"{arm}-{preset['id']}-{year}" for arm in ARMS for preset in PRESETS for year in [2023, 2024, 2025]}
    if len(rows) != 36 or {row["name"] for row in rows} != expected:
        raise ValueError("development-case-set-changed")
    summaries = []
    for arm in ARMS:
        candidates = []
        for preset in PRESETS:
            subset = sorted([row for row in rows if row["arm"] == arm and row["preset"] == preset],
                            key=lambda row: row["evaluation_year"])
            if len(subset) != 3:
                raise ValueError("development-case-identity-changed")
            metrics = _combined(root, subset)
            candidates.append({"arm": arm, "preset": preset, "cases": subset,
                               "metrics": {name: float(metrics[name].mean()) for name in
                                           ("nll", "brier", "top1", "spearman", "top3_recall")}})
        summaries.append(min(candidates, key=lambda row: (row["metrics"]["nll"], row["preset"]["id"])))
    baseline = next(row for row in summaries if row["arm"] == "current_speed")
    challenger = min((row for row in summaries if row["arm"] != "current_speed"),
                     key=lambda row: (row["metrics"]["nll"], row["arm"]))
    comparison = paired_comparison(_combined(root, baseline["cases"]), _combined(root, challenger["cases"]))
    return summaries, {"baseline": baseline, "challenger": challenger,
                       "development_comparison": comparison, "uses_test": False}


def validate_selection_and_decision(root, rows, selection, result):
    summaries, recomputed = select_development(root, rows)
    if (selection != recomputed or result["development_summary"] != summaries
            or result["development_comparison"] != recomputed["development_comparison"]):
        raise ValueError("development-selection-not-independently-reproduced")
    decision = "research_candidate_supported" if (
        recomputed["development_comparison"]["passes"] and result["final_comparison"]["passes"]
    ) else "retain_current_target"
    if result["decision"] != decision:
        raise ValueError("target-decision-not-independently-reproduced")


def validate_prediction_table(target, saved, scores, probabilities):
    keys = ["race_id", "race_date", "horse_number", "finish"]
    if saved.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("duplicate-saved-prediction-identity")
    pd.testing.assert_frame_equal(saved[keys].reset_index(drop=True), target[keys].reset_index(drop=True))
    np.testing.assert_allclose(saved.score, scores, rtol=0, atol=0)
    np.testing.assert_allclose(saved.probability, probabilities, rtol=0, atol=0)


def validate_prepared_inputs(root, task):
    target = pd.read_parquet(root / "target.parquet")
    features = pd.read_parquet(root / "features.parquet")
    labels = pd.read_parquet(root / "labels.parquet")
    snapshots = [root / name for name in task["files"] if name.endswith(".db")]
    if len(snapshots) != 1:
        raise ValueError("single-immutable-source-snapshot-required")
    rebuilt_target, rebuilt_features, rebuilt_labels, _, _ = rebuild_inputs(snapshots[0])
    pd.testing.assert_frame_equal(target, rebuilt_target)
    pd.testing.assert_frame_equal(features, rebuilt_features)
    pd.testing.assert_frame_equal(labels, rebuilt_labels)
    return target, features, labels


def _report(root, result):
    rows = result["development_summary"]
    bars = []
    table = []
    for index, row in enumerate(rows):
        label = html.escape(row["arm"])
        nll = row["metrics"]["nll"]
        bars.append(f'<text x="5" y="{index*36+22}">{label}</text><rect x="160" y="{index*36+8}" '
                    f'width="{max(0, nll-1)*220:.1f}" height="19" fill="#2563eb"/>'
                    f'<text x="650" y="{index*36+22}">{nll:.5f}</text>')
        m = row["metrics"]
        table.append(f"<tr><td>{label}</td><td>{row['preset']['id']}</td><td>{nll:.6f}</td>"
                     f"<td>{m['brier']:.6f}</td><td>{m['top1']:.2%}</td><td>{m['spearman']:.6f}</td>"
                     f"<td>{m['top3_recall']:.2%}</td></tr>")
    page = (
        '<!doctype html><meta charset="utf-8"><title>KeibaAI target comparison</title>'
        '<style>body{max-width:1000px;margin:32px auto;font:16px sans-serif;padding:0 16px}'
        'td,th{padding:8px;text-align:right}td:first-child{text-align:left}</style>'
        '<h1>KeibaAI target comparison (research only)</h1>'
        '<p>Same raw46 inputs; development 2023-June2025, preceding-year CAL per fold. '
        'Final CAL July-December2025 is reserved from target selection. '
        '2026 is an already-observed retrospective comparison, not unknown future evidence.</p>'
        f'<p><b>Decision: {html.escape(result["decision"])}</b>. Production remains unchanged.</p>'
        '<table><tr><th>Target</th><th>Preset</th><th>NLL</th><th>Brier</th><th>Top1</th>'
        '<th>Spearman</th><th>Top3 recall</th></tr>' + "".join(table) + '</table>'
        '<h2>Development NLL (smaller is better)</h2><svg viewBox="0 0 760 235" '
        'role="img" aria-label="Development NLL by target">' + "".join(bars) + '</svg>'
        '<h2>Locked challenger vs baseline</h2><pre>'
        + html.escape(__import__("json").dumps(result["final_comparison"], indent=2)) + '</pre>'
        '<p>Clocks and finishes are teachers/evaluation only. No odds, popularity, payouts, external '
        'speed figures, purchase activation or guaranteed returns. Target-scale RMSEs are not comparable.</p>'
    )
    (root / "report.html").write_text(page, encoding="utf-8")


def run(root):
    started = time.monotonic()
    with driver_lock(BASE, root):
        _settled(root)
        task = _assert_task(root)
        check_approval(task, read_json(root / "approval.json"))
        if (root / "results.json").exists():
            verify(root)
            return {"reused": True, **read_json(root / "results.json")}
        if (root / "run-state.json").exists():
            raise RuntimeError("target-run-already-started-no-automatic-retry")
        import psutil
        available = psutil.virtual_memory().available
        if available < 6 * 1024**3 or (psutil.cpu_count(logical=False) or 1) < 4:
            raise RuntimeError("insufficient-headroom-for-two-research-workers")
        deadline = time.time() + 3600
        freeze_json(root / "run-budget.json", {"deadline": deadline, "maximum_fits": 40,
                                             "workers": 2, "threads": 2, "available_ram_bytes": available})
        write_json(root / "run-state.json", {"status": "running", "pid": os.getpid(), "started_at": time.time()})
        for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
            os.environ[name] = "2"
        rows = []
        try:
            plan = [(arm, preset, year, deadline) for arm in ARMS for preset in PRESETS for year in [2023, 2024, 2025]]
            rows = _execute_cases(root, plan, deadline)
            summaries, selection = select_development(root, rows)
            baseline, challenger = selection["baseline"], selection["challenger"]
            dev_comparison = selection["development_comparison"]
            freeze_json(root / "selection.json", selection)
            check_run_limits(deadline)
            final = _execute_cases(root, [(row["arm"], row["preset"], 2026, deadline)
                                          for row in (baseline, challenger)], deadline)
            check_run_limits(deadline)
            final_comparison = paired_comparison(_combined(root, final[:1]), _combined(root, final[1:]))
            result = {
                "schema": SCHEMA, "development_summary": summaries, "final": final,
                "development_comparison": dev_comparison, "final_comparison": final_comparison,
                "selection_sha256": file_sha256(root / "selection.json"),
                "decision": "research_candidate_supported" if dev_comparison["passes"] and final_comparison["passes"]
                            else "retain_current_target",
                "fit_count": len(rows)+len(final), "elapsed_seconds": time.monotonic()-started,
                "promotion_eligible": False, "production_unchanged": task["protected"] == _protected(),
            }
            if _sources() != task["sources"] or not result["production_unchanged"]:
                raise ValueError("code-or-production-changed-during-target-run")
            for row in final:
                bundle = joblib.load(root / "cases" / row["name"] / "model.research.joblib")
                importance = pd.DataFrame({"feature": bundle["feature_columns"],
                                           "gain": bundle["model"].feature_importance(importance_type="gain")})
                importance.sort_values("gain", ascending=False).to_csv(
                    root / f"{row['arm']}-feature-gain.csv", index=False)
            freeze_json(root / "results.json", result)
            _report(root, result)
            write_json(root / "run-state.json", {"status": "completed", "pid": os.getpid(),
                                                "finished_at": time.time()})
            files = {str(path.relative_to(root)): file_sha256(path) for path in root.rglob("*")
                     if path.is_file() and path.name != "artifact-manifest.json"}
            freeze_json(root / "artifact-manifest.json", {"schema": SCHEMA, "files": files})
            check_run_limits(deadline)
            return result
        except BaseException as error:
            write_json(root / "run-state.json", {"status": "failed", "pid": os.getpid(),
                                                "error": type(error).__name__ + ":" + str(error)})
            raise


def verify(root):
    task = _assert_task(root)
    check_approval(task, read_json(root / "approval.json"))
    manifest = read_json(root / "artifact-manifest.json")
    for name, expected in manifest["files"].items():
        if file_sha256(root / name) != expected:
            raise ValueError(f"target-artifact-changed:{name}")
    result, selection = read_json(root / "results.json"), read_json(root / "selection.json")
    if result["selection_sha256"] != file_sha256(root / "selection.json") or selection["uses_test"] is not False:
        raise ValueError("target-selection-binding-invalid")
    if result["fit_count"] != 38 or result["promotion_eligible"] is not False:
        raise ValueError("target-fit-budget-or-promotion-invalid")
    target, features, labels = validate_prepared_inputs(root, task)
    verified = 0
    case_rows = []
    for path in sorted((root / "cases").glob("*/model.research.joblib")):
        bundle = joblib.load(path)
        row = read_json(path.parent / "result.json")
        expected_name = f"{bundle['arm']}-{bundle['preset']['id']}-{bundle['evaluation_year']}"
        if (bundle["research_only"] is not True or bundle["promotion_eligible"] is not False
                or bundle["purchase_eligible"] is not False or bundle["schema"] != SCHEMA
                or expected_name != path.parent.name or row["name"] != expected_name
                or row["arm"] != bundle["arm"] or row["preset"] != bundle["preset"]
                or row["evaluation_year"] != bundle["evaluation_year"]
                or bundle["arm"] not in ARMS or bundle["preset"] not in PRESETS
                or bundle["feature_columns"] != list(optimized.FEATURE_COLUMNS)
                or bundle["encoder"].feature_columns != list(optimized.FEATURE_COLUMNS)):
            raise ValueError("model-not-research-restricted")
        train, cal, evaluation = partition_masks(target, bundle["evaluation_year"])
        teacher, baseline = _teacher(target, labels, bundle["arm"], train)
        if bundle["teacher_digest"] != _teacher_digest(teacher) or bundle["baseline"] != baseline:
            raise ValueError("training-teacher-or-baseline-not-recomputed")
        for column in optimized.CATEGORICAL_COLUMNS:
            expected_categories = sorted(features.loc[train, column].astype("string").str.strip()
                                         .replace("", pd.NA).dropna().unique().tolist())
            if bundle["encoder"].categories[column] != expected_categories:
                raise ValueError("encoder-categories-not-train-only")
        scores = bundle["model"].predict(bundle["encoder"].transform(features.loc[evaluation]),
                                         raw_score=True, num_threads=2)
        probabilities = race_probabilities(scores, target.loc[evaluation].race_id,
                                           bundle["calibration"]["temperature"])
        saved = pd.read_parquet(path.parent / "predictions.parquet")
        validate_prediction_table(target.loc[evaluation], saved, scores, probabilities)
        cal_scores = bundle["model"].predict(bundle["encoder"].transform(features.loc[cal]),
                                             raw_score=True, num_threads=2)
        if calibrate(target.loc[cal], cal_scores) != bundle["calibration"]:
            raise ValueError("calibration-not-recomputed-from-cal-only")
        summary, races = race_metrics(target.loc[evaluation], scores, probabilities)
        if summary != row["metrics"] or bundle["calibration"] != row["calibration"]:
            raise ValueError("target-model-metrics-not-reproduced")
        pd.testing.assert_frame_equal(races, pd.read_parquet(path.parent / "race-metrics.parquet"))
        verified += 1
        case_rows.append(row)
    first, second = result["final"]
    development_rows = [row for row in case_rows if row["evaluation_year"] in [2023, 2024, 2025]]
    _, independently_selected = select_development(root, development_rows)
    expected_final = [
        f"{independently_selected[key]['arm']}-{independently_selected[key]['preset']['id']}-2026"
        for key in ("baseline", "challenger")
    ]
    actual_final = [row for row in case_rows if row["evaluation_year"] == 2026]
    if ({row["name"] for row in actual_final} != set(expected_final)
            or [row["name"] for row in result["final"]] != expected_final
            or any(row not in case_rows for row in result["final"])):
        raise ValueError("final-models-not-bound-to-locked-development-selection")
    comparison = paired_comparison(_combined(root, [first]), _combined(root, [second]))
    if comparison != result["final_comparison"] or verified != 38:
        raise ValueError("target-decision-or-worker-count-not-recomputed")
    validate_selection_and_decision(root, development_rows, selection, result)
    return {"verified": True, "models_reloaded": verified, "artifact_count": len(manifest["files"]),
            "decision": result["decision"], "promotion_eligible": False, "task_sha256": json_sha256(task)}


_IMPORTED_SOURCES = _source_snapshot()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "approve", "run", "verify"])
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--actor")
    parser.add_argument("--task-sha256")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if root.parent != BASE.resolve() or not root.name.startswith("target-"):
        parser.error("--root must be a target-* child of the model-experiments directory")
    try:
        if args.action == "prepare":
            if args.source is None:
                parser.error("prepare requires --source")
            task = prepare(root, args.source)
            answer = {"status": "awaiting_approval", "task_sha256": json_sha256(task),
                      "cohort_by_year": task["cohort_by_year"], "protocol": task["protocol"]}
        elif args.action == "approve":
            task = _assert_task(root)
            receipt = {"actor": args.actor, "task_sha256": args.task_sha256,
                       "approved_at": datetime.now(timezone.utc).isoformat(),
                       "authority": "operator_or_explicitly_delegated_cli; not independent human review"}
            check_approval(task, receipt)
            freeze_json(root / "approval.json", receipt)
            answer = {"status": "approved", **receipt}
        elif args.action == "run":
            answer = run(root)
        else:
            answer = verify(root)
        print(__import__("json").dumps(answer, ensure_ascii=False, indent=2), flush=True)
        return 0
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return 1
