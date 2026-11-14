"""Grouping-only comparison; original frozen sources and production stay unchanged."""
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
from types import CodeType, FunctionType

import joblib
import numpy as np
import pandas as pd

from experiments.common import REPO_ROOT, file_sha256, json_sha256, read_json, write_json
from experiments.orchestrator import driver_lock
from experiments.search import freeze_json
from training import grouped_speed_baseline as grouped, target_comparison as original
from training.grouped_speed_baseline import ARMS, fit_group_baseline, normalization, teacher
from keiba_ai.speed_deviation import raw_speed_mps


SCHEMA = "keiba-coarse-baseline-comparison-v1"
PRESET = original.PRESETS[1]
PROTOCOL = {
    "schema": SCHEMA, "goal": "Select a coarse speed grouping by race ranking without impairing win metrics.",
    "arms": ARMS, "preset": PRESET, "speed": "m/s", "baseline_history": "whole TRAIN only",
    "statistics_weight": "equal horse", "loss_weight": "equal horse",
    "minimum_child_heads": 30, "fallback": "nearest usable parent; original base fallback unchanged",
    "going": {"good": ["良"], "wet": ["稍重", "重", "不良"]},
    "weather": {"nonprecip": ["晴", "曇"], "precip": ["雨", "小雨", "雪", "小雪"]},
    "unknown_conditions": "parent fallback; never assume dry",
    "development_years": [2023, 2024, 2025], "partitions": {
        name: original.PROTOCOL[name] for name in (
            "train_start_year", "development_end", "final_train_end",
            "final_calibration_start", "final_calibration_end", "retrospective_test_year")},
    "maximum_new_fits": 10, "models_evaluated": 14, "reused_controls": 4,
    "maximum_seconds": 3600, "maximum_processes": 2, "threads": 2, "reserve_ram_gib": 4,
    "minimum_rank_improvement": .001, "maximum_top1_degradation": .005,
    "maximum_nll_degradation": .01, "maximum_annual_rank_degradation": .01,
    "maximum_annual_nll_degradation": .02, "minimum_days": 20, "bootstrap_samples": 2000,
    "development_ci": "paired days; Bonferroni 3 alternatives", "seed": 42,
    "test_previously_observed": True, "promotion_eligible": False,
}
_WORK = None


def _module_source_identity(module):
    data = Path(module.__file__).read_bytes()
    actual = {name: value.__code__ for name, value in vars(module).items()
              if isinstance(value, FunctionType) and value.__module__ == module.__name__}
    compiled = compile(data, next(iter(actual.values())).co_filename, "exec", dont_inherit=True)
    expected = {value.co_name: value for value in compiled.co_consts
                if isinstance(value, CodeType) and not value.co_name.startswith("<")}
    if actual != expected:
        raise ValueError(f"loaded-grouping-code-differs-from-source:{module.__name__}")
    return hashlib.sha256(data).hexdigest()


def _source_snapshot():
    return {**original._sources(),
            "training.grouped_speed_baseline": _module_source_identity(grouped),
            "training.baseline_group_comparison": _module_source_identity(sys.modules[__name__]),
            "scripts.run_baseline_group_comparison": file_sha256(
                REPO_ROOT / "scripts" / "run_baseline_group_comparison.py")}


def _sources():
    current = _source_snapshot()
    if current != _IMPORTED_SOURCES:
        raise ValueError("grouping-code-changed-since-import")
    return current


def _case_name(arm, year):
    return f"{arm}-standard-{year}"


def _directory(root, task, arm, year):
    owner = Path(task["parent_root"]) if arm == "current_speed" else root
    return owner / "cases" / _case_name(arm, year)


def _assert_task(root):
    task = read_json(root / "task-ledger.json")
    if (task["protocol"] != PROTOCOL or task["sources"] != _sources()
            or task["protected"] != original._protected()
            or task["diagnostics_sha256"] != file_sha256(root / "diagnostics.json")):
        raise ValueError("grouping-task-code-goal-or-production-changed")
    for name, digest in task["inputs"].items():
        if file_sha256(Path(name)) != digest:
            raise ValueError(f"grouping-input-changed:{name}")
    return task


def prepare(root, parent):
    with driver_lock(original.BASE, root):
        original._settled(root)
        if (root / "task-ledger.json").exists():
            task = _assert_task(root)
            if Path(task["parent_root"]) != parent.resolve():
                raise ValueError("grouping-parent-changed")
            return task
        if root.exists() and any(root.iterdir()):
            raise ValueError("nonempty-unprepared-grouping-root")
        parent = parent.resolve()
        parent_task = original._assert_task(parent)
        sources, protected = _sources(), original._protected()
        if read_json(parent / "run-state.json")["status"] != "completed":
            raise ValueError("grouping-parent-not-completed")
        parent_manifest = read_json(parent / "artifact-manifest.json")
        for name, digest in parent_manifest["files"].items():
            if file_sha256(parent / name) != digest:
                raise ValueError("grouping-parent-artifact-changed")
        inputs = {str(parent / name): digest for name, digest in parent_task["files"].items()}
        for name in ("task-ledger.json", "artifact-manifest.json", "run-state.json"):
            inputs[str(parent / name)] = file_sha256(parent / name)
        for year in [2023, 2024, 2025, 2026]:
            directory = parent / "cases" / _case_name("current_speed", year)
            for name in ("model.research.joblib", "predictions.parquet", "race-metrics.parquet", "result.json"):
                path = directory / name
                inputs[str(path)] = parent_manifest["files"][str(path.relative_to(parent))]
        target, features, _ = original.validate_prepared_inputs(parent, parent_task)
        root.mkdir(parents=True, exist_ok=False)
        dev = target.race_date.le("20250630")
        statistics = {}
        for arm in ARMS:
            baseline = fit_group_baseline(target.loc[dev], arm)
            statistics[arm] = {"base_groups": len(baseline["base"]["groups"]),
                               "additional_groups": [len(level) for level in baseline["levels"]]}
        write_json(root / "diagnostics.json", {
            "development_only": True, "cohort_races": int(target.loc[dev].race_id.nunique()),
            "group_counts": statistics, "features": list(features.columns),
            "same_day_environment_correction": False,
        })
        if sources != _sources() or protected != original._protected():
            raise ValueError("grouping-code-or-production-changed-during-preparation")
        for name, digest in inputs.items():
            if file_sha256(Path(name)) != digest:
                raise ValueError("grouping-parent-changed-during-preparation")
        task = {"schema": SCHEMA, "protocol": PROTOCOL, "parent_root": str(parent),
                "parent_task_sha256": json_sha256(parent_task), "inputs": inputs,
                "sources": sources, "protected": protected,
                "diagnostics_sha256": file_sha256(root / "diagnostics.json")}
        freeze_json(root / "task-ledger.json", task)
        return _assert_task(root)


def rank_comparison(baseline, candidate, *, development):
    first = baseline.sort_values(["race_id", "race_date"]).reset_index(drop=True)
    second = candidate.sort_values(["race_id", "race_date"]).reset_index(drop=True)
    if not first[["race_id", "race_date"]].equals(second[["race_id", "race_date"]]) or first.race_id.duplicated().any():
        raise ValueError("grouping-comparison-cohort-changed")
    delta = second.spearman - first.spearman
    days = pd.DataFrame({"day": first.race_date, "delta": delta}).groupby("day").delta.agg(["sum", "count"])
    rng = np.random.default_rng(42)
    samples = []
    for _ in range(2000):
        draw = days.iloc[rng.integers(0, len(days), len(days))]
        samples.append(float(draw["sum"].sum() / draw["count"].sum()))
    alpha = .05 / 3 if development else .05
    interval = [float(v) for v in np.quantile(samples, [alpha / 2, 1 - alpha / 2])]
    top1, nll = float((second.top1 - first.top1).mean()), float((second.nll - first.nll).mean())
    annual = {}
    for year, positions in first.groupby(first.race_date.astype(str).str[:4]).indices.items():
        annual[year] = {"spearman_difference": float(delta.iloc[positions].mean()),
                        "top1_difference": float((second.top1 - first.top1).iloc[positions].mean()),
                        "nll_difference": float((second.nll - first.nll).iloc[positions].mean())}
    passes = (len(days) >= 20 and delta.mean() >= .001 and interval[0] > 0
              and top1 >= -.005 and nll <= .01
              and all(row["spearman_difference"] >= -.01 and row["top1_difference"] >= -.005
                      and row["nll_difference"] <= .02 for row in annual.values()))
    return {"spearman_difference": float(delta.mean()), "spearman_difference_ci": interval,
            "top1_difference": top1, "nll_difference": nll, "annual": annual,
            "paired_races": len(first), "paired_days": len(days), "passes": bool(passes),
            "development_family_correction": development}


def _combined(root, task, arm, years):
    return pd.concat([pd.read_parquet(_directory(root, task, arm, year) / "race-metrics.parquet")
                      for year in years], ignore_index=True)


def _limits(deadline):
    if time.time() >= deadline:
        raise TimeoutError("grouping-budget-expired")
    import psutil
    available = psutil.virtual_memory().available
    if available < 4 * 1024**3:
        raise RuntimeError("grouping-ram-reserve-exhausted")
    return available


def _parameters():
    return {"objective": "regression", "verbosity": -1, "num_threads": 2, "device_type": "cpu",
            "deterministic": True, "force_col_wise": True, "seed": 42, "max_bin": 63,
            "learning_rate": .05, "num_leaves": 31, "min_data_in_leaf": 30, "lambda_l2": 1.,
            "lambdarank_truncation_level": 3, "lambdarank_norm": True}


def _speed_errors(target, scores, baseline, races):
    mean, std = normalization(target, baseline)
    errors = pd.Series((np.asarray(scores) * std.to_numpy() + mean.to_numpy()
                        - raw_speed_mps(target).to_numpy()) ** 2, index=target.index)
    races = races.copy()
    races["squared_speed_error"] = races.race_id.map(errors.groupby(target.race_id).mean())
    return races


def _worker(root, tasks, results):
    global _WORK
    try:
        root = Path(root)
        task = read_json(root / "task-ledger.json")
        if _sources() != task["sources"]:
            raise ValueError("grouping-worker-source-not-approved")
        parent = Path(task["parent_root"])
        target, features = (pd.read_parquet(parent / name) for name in ("target.parquet", "features.parquet"))
        _WORK = root, task, target, features
        while True:
            case = tasks.get()
            if case is None:
                return
            results.put(("result", _fit_case(case)))
    except BaseException as error:
        results.put(("error", f"{type(error).__name__}:{error}"))


def _fit_case(case):
    import lightgbm as lgb
    root, task, target, features = _WORK
    arm, year, deadline = case
    name, started = _case_name(arm, year), time.monotonic()
    state = root / "worker-states" / f"{name}.json"
    write_json(state, {"status": "running", "pid": os.getpid(), "case": name})
    try:
        _limits(deadline)
        train, cal, evaluation = original.partition_masks(target, year)
        encoder, encoded, _ = original.optimized._fitted_fixed_encoder(features.loc[train])
        baseline = fit_group_baseline(target.loc[train], arm)
        labels = teacher(target.loc[train], baseline).to_numpy()
        if not np.isfinite(labels).all():
            raise ValueError("nonfinite-grouping-teacher")
        dataset = lgb.Dataset(encoded, label=labels,
                              categorical_feature=list(original.optimized.CATEGORICAL_COLUMNS))
        def bounded(environment):
            _limits(deadline)
        model = lgb.train(_parameters(), dataset, num_boost_round=200, callbacks=[bounded])
        cal_scores = model.predict(encoder.transform(features.loc[cal]), raw_score=True, num_threads=2)
        calibration = original.calibrate(target.loc[cal], cal_scores)
        scores = model.predict(encoder.transform(features.loc[evaluation]), raw_score=True, num_threads=2)
        probabilities = original.race_probabilities(scores, target.loc[evaluation].race_id,
                                                     calibration["temperature"])
        metrics, races = original.race_metrics(target.loc[evaluation], scores, probabilities)
        races = _speed_errors(target.loc[evaluation], scores, baseline, races)
        metrics["rmse_speed_mps_race_balanced"] = float(races.squared_speed_error.mean() ** .5)
        directory = _directory(root, task, arm, year)
        directory.mkdir(parents=True, exist_ok=False)
        joblib.dump({"schema": SCHEMA, "arm": arm, "preset": PRESET, "evaluation_year": year,
                     "model": model, "encoder": encoder, "baseline": baseline, "calibration": calibration,
                     "teacher_digest": original._teacher_digest(labels),
                     "feature_columns": list(features.columns), "research_only": True,
                     "promotion_eligible": False, "purchase_eligible": False},
                    directory / "model.research.joblib")
        saved = target.loc[evaluation, ["race_id", "race_date", "horse_number", "finish"]].copy()
        saved["score"], saved["probability"] = scores, probabilities
        saved.to_parquet(directory / "predictions.parquet", index=False)
        races.to_parquet(directory / "race-metrics.parquet", index=False)
        result = {"name": name, "arm": arm, "evaluation_year": year, "preset": PRESET,
                  "metrics": metrics, "calibration": calibration, "seconds": time.monotonic() - started}
        freeze_json(directory / "result.json", result)
        if _sources() != task["sources"]:
            raise ValueError("grouping-source-changed-during-fit")
        write_json(state, {"status": "completed", "pid": os.getpid(), "case": name})
        return result
    except BaseException as error:
        write_json(state, {"status": "failed", "pid": os.getpid(), "case": name, "error": str(error)})
        raise


def _execute(root, cases, deadline):
    import psutil
    context = multiprocessing.get_context("spawn")
    tasks, results = context.Queue(), context.Queue()
    workers = [context.Process(target=_worker, args=(str(root), tasks, results)) for _ in range(2)]
    pending, completed, inflight = iter(cases), [], 0
    available, peak = _limits(deadline), 0
    succeeded = False
    try:
        for worker in workers:
            worker.start()
        freeze_json(root / "worker-leases" / f"{len(list((root / 'worker-leases').glob('*.json'))) + 1}.json",
                    {"pids": [worker.pid for worker in workers], "parent_pid": os.getpid()})
        for _ in workers:
            case = next(pending, None)
            if case is not None:
                tasks.put(case)
                inflight += 1
        while inflight:
            available = min(available, _limits(deadline))
            rss = 0
            for worker in workers:
                try:
                    rss += psutil.Process(worker.pid).memory_info().rss
                except psutil.NoSuchProcess:
                    pass
            peak = max(peak, rss)
            try:
                kind, value = results.get(timeout=.25)
            except queue.Empty:
                if any(worker.exitcode is not None for worker in workers):
                    raise RuntimeError("grouping-worker-exited-without-result")
                continue
            if kind == "error":
                raise RuntimeError(value)
            if value["name"] in {row["name"] for row in completed}:
                raise ValueError("duplicate-grouping-result")
            _limits(deadline)
            completed.append(value)
            inflight -= 1
            freeze_json(root / "progress" / f"{len(list((root / 'progress').glob('*.json'))) + 1:03d}.json", value)
            print(f"{value['name']} rho={value['metrics']['spearman']:.6f}", flush=True)
            case = next(pending, None)
            if case is not None:
                tasks.put(case)
                inflight += 1
        for _ in workers:
            tasks.put(None)
        while any(worker.is_alive() for worker in workers):
            _limits(deadline)
            for worker in workers:
                worker.join(timeout=.1)
        if any(worker.exitcode != 0 for worker in workers):
            raise RuntimeError("grouping-worker-shutdown-failed")
        if {row["name"] for row in completed} != {_case_name(arm, year) for arm, year, _ in cases}:
            raise ValueError("grouping-worker-case-set-changed")
        succeeded = True
        return completed
    finally:
        original.terminate_owned_workers([worker for worker in workers if worker.pid is not None])
        for path in (root / "worker-states").glob("*.json"):
            state = read_json(path)
            if state["status"] == "running":
                write_json(path, {**state, "status": "unknown", "reason": "owned-worker-stopped"})
        freeze_json(root / "resource-monitor" / f"{len(list((root / 'resource-monitor').glob('*.json'))) + 1}.json",
                    {"completed": succeeded, "minimum_available_ram_bytes": available,
                     "peak_combined_worker_rss_bytes": peak})
        for channel in (tasks, results):
            channel.cancel_join_thread()
            channel.close()


def _control_metrics(root, task, target, years=(2023, 2024, 2025), deadline=None):
    for year in years:
        if deadline is not None:
            _limits(deadline)
        directory = _directory(root, task, "current_speed", year)
        saved = pd.read_parquet(directory / "predictions.parquet")
        _, _, mask = original.partition_masks(target, year)
        bundle = joblib.load(directory / "model.research.joblib")
        baseline = {"base": bundle["baseline"], "levels": []}
        races = _speed_errors(target.loc[mask], saved.score.to_numpy(), baseline,
                              pd.read_parquet(directory / "race-metrics.parquet"))
        races.to_parquet(root / f"control-{year}.parquet", index=False)
        if deadline is not None:
            _limits(deadline)


def _all_metrics(root, task, arm, years):
    if arm == "current_speed":
        return pd.concat([pd.read_parquet(root / f"control-{year}.parquet") for year in years], ignore_index=True)
    return _combined(root, task, arm, years)


def _selection(root, task, deadline=None):
    if deadline is not None:
        _limits(deadline)
    combined = {arm: _all_metrics(root, task, arm, [2023, 2024, 2025]) for arm in ARMS}
    summaries = [{"arm": arm, "metrics": {name: float(combined[arm][name].mean()) for name in
                                        ("spearman", "top1", "top3_recall", "nll", "brier")}}
                 for arm in ARMS]
    for row in summaries:
        row["metrics"]["rmse_speed_mps_race_balanced"] = float(
            combined[row["arm"]].squared_speed_error.mean() ** .5)
    challenger = max(summaries[1:], key=lambda row: (row["metrics"]["spearman"], -ARMS.index(row["arm"])))
    result = {"summaries": summaries, "challenger": challenger["arm"], "uses_test": False,
            "comparisons": {arm: rank_comparison(combined["current_speed"], combined[arm], development=True)
                            for arm in ARMS[1:]}}
    if deadline is not None:
        _limits(deadline)
    return result


def _report(root, result):
    table = []
    for row in result["selection"]["summaries"]:
        m = row["metrics"]
        table.append(f"<tr><td>{html.escape(row['arm'])}</td><td>{m['spearman']:.6f}</td>"
                     f"<td>{m['top1']:.2%}</td><td>{m['nll']:.6f}</td>"
                     f"<td>{m['rmse_speed_mps_race_balanced']:.4f}</td></tr>")
    (root / "report.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Coarse baseline groups</title>'
        '<style>body{font:16px sans-serif;max-width:1000px;margin:32px auto}td,th{padding:10px}</style>'
        '<h1>Coarse speed-baseline comparison</h1><p>Grouping only; same m/s formula, TRAIN data, '
        '46 inputs and fixed training parameters. 2026 is already observed. No production promotion.</p>'
        f"<p>Selected: <b>{html.escape(result['selected_grouping'])}</b></p>"
        '<table><tr><th>Grouping</th><th>Spearman</th><th>Top1</th><th>NLL</th><th>RMSE m/s</th></tr>'
        + "".join(table) + '</table>', encoding="utf-8")


def run(root):
    with driver_lock(original.BASE, root):
        original._settled(root)
        task = _assert_task(root)
        original.check_approval(task, read_json(root / "approval.json"))
        if (root / "results.json").exists():
            verify(root)
            return read_json(root / "results.json")
        if (root / "run-state.json").exists():
            raise ValueError("grouping-run-already-started-no-retry")
        import psutil
        if (psutil.cpu_count(logical=False) or 1) < 4:
            raise RuntimeError("insufficient-cores-for-two-grouping-workers")
        started, wall_started = time.monotonic(), time.time()
        deadline = wall_started + 3600
        _limits(deadline)
        freeze_json(root / "run-budget.json", {"started_at": wall_started, "deadline": deadline,
                                             "new_fits": 10, "workers": 2, "threads": 2})
        write_json(root / "run-state.json", {"status": "running", "pid": os.getpid(),
                                            "started_at": wall_started})
        for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
            os.environ[name] = "2"
        try:
            target = pd.read_parquet(Path(task["parent_root"]) / "target.parquet")
            _control_metrics(root, task, target, deadline=deadline)
            rows = _execute(root, [(arm, year, deadline) for arm in ARMS[1:] for year in [2023, 2024, 2025]], deadline)
            selection = _selection(root, task, deadline)
            freeze_json(root / "selection.json", selection)
            _limits(deadline)
            _control_metrics(root, task, target, years=[2026], deadline=deadline)
            final = _execute(root, [(selection["challenger"], 2026, deadline)], deadline)
            comparison = rank_comparison(_all_metrics(root, task, "current_speed", [2026]),
                                         _all_metrics(root, task, selection["challenger"], [2026]),
                                         development=False)
            _limits(deadline)
            selected = selection["challenger"] if (
                selection["comparisons"][selection["challenger"]]["passes"] and comparison["passes"]
            ) else "current_speed"
            result = {"schema": SCHEMA, "selection": selection, "selected_grouping": selected,
                      "final_comparison": comparison, "final": final, "new_fits": len(rows) + len(final),
                      "models_evaluated": 14, "selection_sha256": file_sha256(root / "selection.json"),
                      "seconds": time.monotonic() - started, "promotion_eligible": False}
            _assert_task(root)
            _limits(deadline)
            freeze_json(root / "results.json", result)
            _report(root, result)
            _limits(deadline)
            files = {
                str(path.relative_to(root)): file_sha256(path) for path in root.rglob("*")
                if path.is_file() and path.name != "artifact-manifest.json"}
            _limits(deadline)
            write_json(root / "run-state.json", {"status": "completed", "pid": os.getpid(),
                                                "started_at": wall_started, "finished_at": time.time()})
            files["run-state.json"] = file_sha256(root / "run-state.json")
            freeze_json(root / "artifact-manifest.json", {"files": files})
            _limits(deadline)
            return result
        except BaseException as error:
            write_json(root / "run-state.json", {"status": "failed", "pid": os.getpid(), "error": str(error)})
            raise


def _validate_fitted_contract(bundle, row, canonical, arm, year):
    expected_schema = original.SCHEMA if arm == "current_speed" else SCHEMA
    if (bundle["schema"] != expected_schema or bundle["arm"] != arm
            or bundle["preset"] != PRESET or bundle["evaluation_year"] != year
            or row["arm"] != arm or row["preset"] != PRESET or row["evaluation_year"] != year
            or row["name"] != _case_name(arm, year)
            or vars(bundle["encoder"]) != vars(canonical)
            or bundle["model"].feature_name() != canonical.feature_columns
            or bundle["model"].params.get("num_iterations") != 200
            or any(bundle["model"].params.get(key) != value for key, value in _parameters().items())):
        raise ValueError("grouping-fitted-model-contract-changed")


def verify(root):
    task = _assert_task(root)
    original.check_approval(task, read_json(root / "approval.json"))
    manifest = read_json(root / "artifact-manifest.json")
    for name, digest in manifest["files"].items():
        if file_sha256(root / name) != digest:
            raise ValueError("grouping-artifact-changed")
    parent = Path(task["parent_root"])
    parent_task = read_json(parent / "task-ledger.json")
    target, features, _ = original.validate_prepared_inputs(parent, parent_task)
    selection = read_json(root / "selection.json")
    if selection["challenger"] not in ARMS[1:]:
        raise ValueError("grouping-selection-unknown-challenger")
    expected = [(arm, year) for arm in ARMS for year in [2023, 2024, 2025]]
    expected += [("current_speed", 2026), (selection["challenger"], 2026)]
    if {p.parent.name for p in (root / "cases").glob("*/model.research.joblib")} != {
        _case_name(arm, year) for arm, year in expected if arm != "current_speed"
    }:
        raise ValueError("grouping-model-case-set-changed")
    state, budget = read_json(root / "run-state.json"), read_json(root / "run-budget.json")
    if (state["status"] != "completed" or budget["new_fits"] != 10
            or budget["workers"] != 2 or budget["threads"] != 2
            or budget["deadline"] - budget["started_at"] != 3600
            or state["started_at"] != budget["started_at"]
            or not budget["started_at"] <= state["finished_at"] <= budget["deadline"]):
        raise ValueError("grouping-completion-budget-evidence-invalid")
    leases = [read_json(p) for p in (root / "worker-leases").glob("*.json")]
    monitors = [read_json(p) for p in (root / "resource-monitor").glob("*.json")]
    if (len(leases) != 2 or any(len(row["pids"]) != 2 for row in leases) or len(monitors) != 2
            or any(row["completed"] is not True or row["minimum_available_ram_bytes"] < 4 * 1024**3
                   for row in monitors)):
        raise ValueError("grouping-worker-resource-evidence-invalid")
    owned_pids = {pid for row in leases for pid in row["pids"]}
    worker_states = [read_json(p) for p in (root / "worker-states").glob("*.json")]
    names = {_case_name(arm, year) for arm, year in expected if arm != "current_speed"}
    if (len(worker_states) != 10 or {row["case"] for row in worker_states} != names
            or any(row["status"] != "completed" or row["pid"] not in owned_pids for row in worker_states)):
        raise ValueError("grouping-worker-completion-evidence-invalid")
    canonical = {}
    for arm, year in expected:
        directory = _directory(root, task, arm, year)
        bundle, row = joblib.load(directory / "model.research.joblib"), read_json(directory / "result.json")
        train, cal, evaluation = original.partition_masks(target, year)
        if year not in canonical:
            canonical[year], _, _ = original.optimized._fitted_fixed_encoder(features.loc[train])
        _validate_fitted_contract(bundle, row, canonical[year], arm, year)
        baseline = fit_group_baseline(target.loc[train], arm)
        labels = teacher(target.loc[train], baseline).to_numpy()
        stored = {"base": bundle["baseline"], "levels": []} if arm == "current_speed" else bundle["baseline"]
        if (bundle["arm"] != arm or bundle["preset"] != PRESET or bundle["evaluation_year"] != year
                or bundle["research_only"] is not True or bundle["promotion_eligible"] is not False
                or bundle["purchase_eligible"] is not False or row["name"] != _case_name(arm, year)
                or bundle["teacher_digest"] != original._teacher_digest(labels)
                or stored["base"] != baseline["base"] or stored["levels"] != baseline["levels"]
                or bundle["feature_columns"] != list(original.optimized.FEATURE_COLUMNS)
                or any(bundle["model"].params.get(key) != value for key, value in _parameters().items())):
            raise ValueError("grouping-teacher-model-or-configuration-changed")
        encoder = canonical[year]
        for mask in (train, cal, evaluation):
            pd.testing.assert_frame_equal(encoder.transform(features.loc[mask]),
                                          bundle["encoder"].transform(features.loc[mask]))
        scores = bundle["model"].predict(encoder.transform(features.loc[evaluation]), raw_score=True, num_threads=2)
        cal_scores = bundle["model"].predict(encoder.transform(features.loc[cal]), raw_score=True, num_threads=2)
        calibration = original.calibrate(target.loc[cal], cal_scores)
        if calibration != bundle["calibration"] or calibration != row["calibration"]:
            raise ValueError("grouping-calibration-not-recomputed")
        probabilities = original.race_probabilities(scores, target.loc[evaluation].race_id,
                                                     calibration["temperature"])
        original.validate_prediction_table(target.loc[evaluation],
                                           pd.read_parquet(directory / "predictions.parquet"), scores, probabilities)
        metrics, races = original.race_metrics(target.loc[evaluation], scores, probabilities)
        races = _speed_errors(target.loc[evaluation], scores, baseline, races)
        saved = (root / f"control-{year}.parquet") if arm == "current_speed" else directory / "race-metrics.parquet"
        pd.testing.assert_frame_equal(races, pd.read_parquet(saved))
        if arm != "current_speed":
            metrics["rmse_speed_mps_race_balanced"] = float(races.squared_speed_error.mean() ** .5)
        if metrics != row["metrics"]:
            raise ValueError("grouping-metrics-not-recomputed")
    recomputed = _selection(root, task)
    if selection != recomputed:
        raise ValueError("grouping-selection-not-recomputed")
    comparison = rank_comparison(_all_metrics(root, task, "current_speed", [2026]),
                                 _all_metrics(root, task, selection["challenger"], [2026]), development=False)
    selected = selection["challenger"] if selection["comparisons"][selection["challenger"]]["passes"] and comparison["passes"] else "current_speed"
    result = read_json(root / "results.json")
    final_rows = [read_json(_directory(root, task, selection["challenger"], 2026) / "result.json")]
    if (result["selection"] != selection or result["selected_grouping"] != selected
            or result["final_comparison"] != comparison or result["new_fits"] != 10
            or result["selection_sha256"] != file_sha256(root / "selection.json")
            or result["promotion_eligible"] is not False or result["models_evaluated"] != 14
            or result["final"] != final_rows):
        raise ValueError("grouping-selection-or-decision-not-recomputed")
    return {"verified": True, "models_reloaded": 14, "selected_grouping": selected,
            "artifacts": len(manifest["files"]), "promotion_eligible": False,
            "task_sha256": json_sha256(task)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "approve", "run", "verify"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--parent", type=Path)
    parser.add_argument("--actor")
    parser.add_argument("--task-sha256")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if root.parent != original.BASE.resolve() or not root.name.startswith("target-baseline-"):
        parser.error("root must be an isolated target-baseline-* directory")
    if args.action == "prepare":
        if args.parent is None:
            parser.error("prepare requires --parent")
        task = prepare(root, args.parent)
        answer = {"status": "awaiting_approval", "task_sha256": json_sha256(task), "protocol": PROTOCOL}
    elif args.action == "approve":
        task = _assert_task(root)
        receipt = {"actor": args.actor, "task_sha256": args.task_sha256,
                   "approved_at": datetime.now(timezone.utc).isoformat(),
                   "authority": "explicit delegated local research; not independent human review"}
        original.check_approval(task, receipt)
        freeze_json(root / "approval.json", receipt)
        answer = receipt
    elif args.action == "run":
        answer = run(root)
    else:
        answer = verify(root)
    import json
    print(json.dumps(answer, ensure_ascii=False, indent=2), flush=True)
    return 0


_IMPORTED_SOURCES = _source_snapshot()
