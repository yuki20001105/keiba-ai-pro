"""Finite, separate 2016..2026 fixed-group contextual feature comparison.

No external acquisition, production publication, activation, H04 mutation or
purchase. All TEST years are retrospective, not unknown future holdouts.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "reports/generated/context-features"


def modules():
    for path in (ROOT / "python-api", ROOT / "keiba", ROOT / "scripts"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from training import db_context_fit as fit, db_context_features as features
    return fit, features


def save(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)


def groups(features):
    fixed = features.FEATURE_GROUPS
    base = list(fixed["baseline46"])
    conditional = base + fixed["condition_aptitude"] + fixed["weight"]
    return {"baseline46": base, "condition_weight60": conditional,
        "condition_weight_gate_style73": conditional + fixed["gate"] + fixed["style_pace"],
        "pedigree53": base + fixed["pedigree"], "full80": list(features.FEATURE_COLUMNS)}


def worker(payload):
    # Avoid multiplying OpenMP and BLAS budgets in each spawned interpreter.
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "2"
    os.environ["LOG_LEVEL"] = "CRITICAL"
    fit, features = modules()
    from threadpoolctl import threadpool_limits
    import numpy as np
    import pandas as pd
    directory = Path(payload["output"])
    target_path, feature_path = Path(payload["target"]), Path(payload["features"])
    if (fit.file_hash(target_path) != payload["target_sha"]
            or fit.file_hash(feature_path) != payload["features_sha"] or fit.source_hashes() != payload["sources"]):
        raise ValueError("context-worker-input-or-formula-drift")
    target, raw = fit.prepare(pd.read_parquet(target_path), pd.read_parquet(feature_path))
    year, name = payload["year"], payload["group"]
    train_end = f"{year-2}1231"
    cal_start, cal_end, test_start = f"{year-1}0101", f"{year-1}1231", f"{year}0101"
    end = min(f"{year}1231", payload["period_end"])
    positions = {"TRAIN": np.flatnonzero(target.race_date.between("20130101", train_end).to_numpy()),
        "CAL": np.flatnonzero(target.race_date.between(cal_start, cal_end).to_numpy()),
        "TEST": np.flatnonzero(target.race_date.between(test_start, end).to_numpy())}
    with threadpool_limits(limits=2):
        bundle, result, output = fit.fit_on_partitions(target, raw, positions, columns=groups(features)[name],
            n_threads=2, preset_parallel=False)
    predicted = output["target"][["race_id", "race_date", "horse_number", "finish", "venue", "surface", "field_condition"]].copy()
    predicted["score"] = output["scores"]
    predicted["physical_speed"] = output["physical_speed"]
    truth = fit.legacy.raw_speed_mps(output["target"])
    predicted["squared_error"] = (output["physical_speed"]-truth.to_numpy())**2
    predicted["probability"] = output["probabilities"] if output["probabilities"] is not None else np.nan
    path = directory / f"{year}-{name}-predictions.parquet"
    predicted.to_parquet(path, index=False)
    # Research bundles are deliberately outside the production publication ledger.
    import joblib
    artifact = directory / f"{year}-{name}-research.joblib"
    joblib.dump(bundle, artifact)
    result.update({"test_year": year, "group": name, "prediction_file": path.name,
        "prediction_sha256": fit.file_hash(path), "research_bundle": artifact.name,
        "research_bundle_sha256": fit.file_hash(artifact), "historical_label": "known retrospective data",
        "source_population": "all eligible JRA flat; strict prior source dates"})
    save(directory / f"{year}-{name}-result.json", result)
    return {"test_year": year, "group": name, "elapsed_seconds": result["elapsed_seconds"]}


def race_metrics(frame):
    import numpy as np
    import pandas as pd
    data = frame.copy()
    rank_a = data.groupby("race_id").score.rank(method="average")
    rank_b = (-data.finish).groupby(data.race_id).rank(method="average")
    rank_a -= rank_a.groupby(data.race_id).transform("mean")
    rank_b -= rank_b.groupby(data.race_id).transform("mean")
    cross = (rank_a*rank_b).groupby(data.race_id).sum()
    denominator = np.sqrt((rank_a**2).groupby(data.race_id).sum()*(rank_b**2).groupby(data.race_id).sum())
    races = data.groupby("race_id", sort=True).agg(race_date=("race_date", "first"), venue=("venue", "first"),
        surface=("surface", "first"), field_condition=("field_condition", "first"),
        squared_error=("squared_error", "sum"), runner_count=("horse_number", "size"))
    races["rank_correlation"] = (cross/denominator).fillna(0)
    picks = data.sort_values(["race_id", "score", "horse_number"], ascending=[True, False, True]).drop_duplicates("race_id").set_index("race_id")
    races["top1"] = picks.finish.eq(1)
    races["selected_probability"] = picks.probability
    return races.reset_index()


def accuracy(races):
    import numpy as np
    return {"race_count": len(races), "runner_count": int(races.runner_count.sum()),
        "rank_correlation": float(races.rank_correlation.mean()),
        "rmse_speed_mps": float(np.sqrt(races.squared_error.sum()/races.runner_count.sum())),
        "top1": float(races.top1.mean())}


def aggregate(directory, protocol):
    fit, features = modules()
    import numpy as np
    import pandas as pd
    frames, results, summaries = {}, {}, {}
    for name in groups(features):
        frames[name] = pd.concat([pd.read_parquet(directory / f"{year}-{name}-predictions.parquet")
            for year in protocol["test_years"]], ignore_index=True)
        results[name] = race_metrics(frames[name])
        summaries[name] = {"accuracy": accuracy(results[name]), "years": [], "segments": []}
        for year in protocol["test_years"]:
            item = json.loads((directory / f"{year}-{name}-result.json").read_text(encoding="utf-8"))
            summaries[name]["years"].append({"year": year, "accuracy": item["evaluation"]["primary"],
                "probability": item["probability_metrics"], "probability_validation": item["probability_validation"],
                "selected_preset": item["selected_preset"], "seconds": item["elapsed_seconds"]})
        for column in ("venue", "surface", "field_condition"):
            for label, segment in results[name].groupby(column, sort=True):
                summaries[name]["segments"].append({"dimension": column, "label": str(label), **accuracy(segment)})
    base = results["baseline46"].set_index("race_id")
    comparisons = {}
    random = np.random.default_rng(protocol["bootstrap_seed"])
    for name in groups(features):
        if name == "baseline46":
            continue
        current = results[name].set_index("race_id")
        if not current.index.equals(base.index) or not current.runner_count.equals(base.runner_count):
            raise ValueError("context-comparison-must-use-identical-whole-races")
        delta = pd.DataFrame({"date": base.race_date,
            "rho": current.rank_correlation-base.rank_correlation,
            "current_se": current.squared_error, "base_se": base.squared_error, "runners": base.runner_count})
        days = delta.groupby("date").agg(rho=("rho", "sum"), races=("rho", "size"),
            current_se=("current_se", "sum"), base_se=("base_se", "sum"), runners=("runners", "sum"))
        array = days.to_numpy(dtype=float)
        sampled = random.integers(0, len(array), size=(protocol["bootstrap_repetitions"], len(array)))
        sums = array[sampled].sum(axis=1)
        rho_samples = sums[:,0]/sums[:,1]
        rmse_samples = np.sqrt(sums[:,2]/sums[:,4])-np.sqrt(sums[:,3]/sums[:,4])
        rank_delta = summaries[name]["accuracy"]["rank_correlation"]-summaries["baseline46"]["accuracy"]["rank_correlation"]
        ratio = summaries[name]["accuracy"]["rmse_speed_mps"]/summaries["baseline46"]["accuracy"]["rmse_speed_mps"]-1
        better_years = sum(c["accuracy"]["rank_correlation"] > b["accuracy"]["rank_correlation"]
            for c,b in zip(summaries[name]["years"], summaries["baseline46"]["years"]))
        rank_ci = np.quantile(rho_samples, [.025,.975]).tolist()
        comparisons[name] = {"rank_delta": rank_delta, "rank_delta_ci95": rank_ci,
            "rmse_relative_change": ratio, "rmse_delta_ci95": np.quantile(rmse_samples,[.025,.975]).tolist(),
            "years_with_better_rank": better_years,
            "next_phase_candidate": bool(rank_ci[0] > 0 and ratio <= .01
                and better_years >= int(np.ceil(len(protocol["test_years"])*2/3))),
            "production_activation": False, "live_probability_or_profitability_verified": False}
    return {"schema": "context-annual-ablation-v1", "summaries": summaries, "vs_baseline46": comparisons,
        "historical_label": "retrospective known data, not future holdout", "roi_evaluated": False,
        "model_activated": False, "profitability_verified": False}


def execute(snapshot_reuse=None):
    os.environ["LOG_LEVEL"] = "CRITICAL"
    fit, features = modules()
    from evaluate_db_shared_venues import preservation_receipt
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")+"-"+uuid.uuid4().hex[:8]
    directory = OUTPUT / run
    directory.mkdir(parents=True, exist_ok=False)
    before = preservation_receipt()
    save(directory / "before.json", before)
    source = ROOT / "keiba/data/keiba_ultimate.db"
    snapshot = directory / "snapshot.sqlite3"
    if snapshot_reuse is not None:
        snapshot = Path(snapshot_reuse).resolve(strict=True)
        if not snapshot.is_relative_to(OUTPUT.resolve()) or snapshot.name != "snapshot.sqlite3" or snapshot.is_symlink():
            raise ValueError("context-explicit-research-snapshot-required")
    else:
        with sqlite3.connect(source.resolve().as_uri()+"?mode=ro", uri=True, timeout=10) as src:
            with sqlite3.connect(snapshot) as dst:
                src.backup(dst, pages=8192, sleep=.01)
    protocol = {"schema": "context-fixed-annual-plan-v1", "requested_period": ["20160101", "20260930"],
        "context_history_start": "20130101", "test_years": list(range(2016,2027)),
        "train": "2013 through TEST year minus 2", "cal": "TEST year minus 1", "test": "whole TEST year through 20260930",
        "groups": groups(features), "presets": fit.PRESETS, "source_hashes": fit.source_hashes(),
        "snapshot_sha256": fit.file_hash(snapshot), "workers": 3, "threads_per_worker": 2,
        "snapshot_path": str(snapshot), "runner_sha256": fit.file_hash(__file__),
        "preset_parallel": False, "bootstrap_repetitions": 1000, "bootstrap_seed": 20261012,
        "adoption_gate": {"rank_delta_ci95_lower_gt": 0, "rmse_relative_worsening_lte": .01,
            "minimum_improving_year_fraction": 2/3}, "network": False, "production_publish": False,
        "historical_label": "previously observed history, not unseen evaluation"}
    save(directory / "protocol.json", protocol)
    print(json.dumps({"stage": "preparation", "directory": str(directory)}), flush=True)
    target, raw, coverage, receipt = fit.prepare_inputs(snapshot, "2013-01", "2026-09", directory / "feature-cache",
        expected_source_hashes=protocol["source_hashes"])
    save(directory / "preparation.json", {"coverage": coverage, "receipt": receipt,
        "missingness": {name: float(raw[name].isna().mean()) for name in raw}})
    target_path, feature_path = directory / "target.parquet", directory / "features.parquet"
    target.to_parquet(target_path, index=False); raw.to_parquet(feature_path, index=False)
    common = {"output": str(directory), "target": str(target_path), "features": str(feature_path),
        "target_sha": fit.file_hash(target_path), "features_sha": fit.file_hash(feature_path),
        "sources": protocol["source_hashes"], "period_end": "20260930"}
    del target, raw
    import gc
    gc.collect()
    jobs = [{**common, "year": year, "group": name} for year in protocol["test_years"] for name in protocol["groups"]]
    print(json.dumps({"stage": "training", "jobs": len(jobs), "workers": 3}), flush=True)
    with ProcessPoolExecutor(max_workers=3, mp_context=multiprocessing.get_context("spawn")) as pool:
        futures = {pool.submit(worker, payload): payload for payload in jobs}
        for index, future in enumerate(as_completed(futures), start=1):
            item = future.result()
            print(json.dumps({"stage": "completed", "done": index, "total": len(jobs), **item}), flush=True)
    result = aggregate(directory, protocol)
    after = preservation_receipt()
    save(directory / "after.json", after)
    result["protected_state_unchanged"] = before == after
    if before != after:
        result["protection_note"] = "External active-model or collector change detected; no activation performed by this research."
    save(directory / "results.json", result)
    manifest = {path.name: fit.file_hash(path) for path in directory.iterdir() if path.is_file()}
    save(directory / "artifact_manifest.json", {"files": manifest})
    print(json.dumps({"stage": "complete", "directory": str(directory), "comparisons": result["vs_baseline46"]}), flush=True)
    return directory


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args()
    if args.run:
        execute(args.snapshot)
    else:
        fit, features = modules()
        print(json.dumps({"groups": {name: len(cols) for name,cols in groups(features).items()}, "test_years": list(range(2016,2027)),
            "source_hashes": fit.source_hashes(), "run_started": False}))
