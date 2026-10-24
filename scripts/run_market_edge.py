"""Predeclared Track B campaign. Does not import the production application."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import statistics
import sys
import time

for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[name] = "2"
REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "python-api"), str(REPO / "keiba")]
ROOT = REPO / "reports/generated/model-experiments/market-edge-v1"
COLLECTOR = REPO / "reports/generated/forward-runtime/h04-live-20260920-v2"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    encoded = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path = Path(path)
    if path.exists():
        if path.read_text(encoding="utf-8") != encoded:
            raise FileExistsError(f"Immutable research output differs: {path}")
        return
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(encoded)


def job(job_id, model, device, fold, kind="research"):
    from market_edge.compute import JobSpec
    interpreter = REPO / (".local-runtime/gpu-validation/.venv/Scripts/python.exe" if device == "gpu" else "python-api/.venv/Scripts/python.exe")
    output = ROOT / "jobs" / job_id
    return JobSpec(job_id=job_id, model=model, device=device, dataset=str(ROOT / "development.parquet"), fold=fold,
                   command=[str(interpreter), str(REPO / "scripts/run_market_edge_worker.py"), "--data", str(ROOT / "development.parquet"),
                            "--output", str(output), "--model", model, "--device", device, "--fold", fold, "--rounds", "150"],
                   cwd=str(REPO), result_path=str(output / "result.json"), memory_mb=512, cpu_threads=2, kind=kind)


def train():
    from market_edge.compute import run_jobs, ProtectionLimits, CollectorMonitor, ResourceMonitor
    plan = ROOT / "experiment_plan.json"
    provenance = read(ROOT / "provenance.json")
    if sha(ROOT / "development.parquet") != provenance["artifact"]["sha256"]:
        raise ValueError("Prepared development data changed")
    if (ROOT / "job_ledger.sqlite").exists():
        raise FileExistsError("Use existing completed jobs; do not silently rerun research")
    # GPU benchmark can overlap a different CPU model, not its CPU timing counterpart.
    first = [job("logistic-dev", "logistic", "cpu", "development"),
             job("benchmark-gpu-0", "lightgbm", "gpu", "validation", "benchmark"),
             job("logistic-val", "logistic", "cpu", "validation"),
             job("benchmark-gpu-1", "lightgbm", "gpu", "validation", "benchmark"),
             job("market-control-dev", "market_logistic", "cpu", "development"),
             job("market-control-val", "market_logistic", "cpu", "validation")]
    second = [job("benchmark-cpu-0", "lightgbm", "cpu", "validation", "benchmark"),
              job("benchmark-cpu-1", "lightgbm", "cpu", "validation", "benchmark"),
              job("lightgbm-dev", "lightgbm", "cpu", "development")]
    limits = ProtectionLimits()
    snapshot = CollectorMonitor(COLLECTOR).snapshot()
    if not snapshot["healthy"]:
        raise ValueError(f"Forward collector protection: {snapshot}")
    code = list((REPO / "python-api/market_edge").glob("*.py")) + [Path(__file__), REPO / "scripts/run_market_edge_worker.py"]
    freeze = {"frozen_at": datetime.now(timezone.utc).isoformat(), "plan_sha256": sha(plan),
              "dataset_sha256": sha(ROOT / "development.parquet"),
              "source_sha256": {str(p.relative_to(REPO)): sha(p) for p in sorted(code)},
              "active_model_sha256": sha(REPO / "python-api/models/.active_model.json"),
              "track_a_protocol_file_sha256": sha(COLLECTOR / "protocol.json"),
              "track_a_analysis_file_sha256": sha(COLLECTOR / "analysis-definition.json")}
    write(ROOT / "research_freeze.json", freeze)
    for source in code:
        destination = ROOT / "code_snapshot" / source.relative_to(REPO)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    write(ROOT / "compute_plan.json", {"limits": asdict(limits), "jobs": [asdict(j) for j in first+second],
            "initial_resources": ResourceMonitor(ROOT).sample(), "initial_collector": snapshot,
            "priority": "below normal", "workers": "at most one CPU and one GPU; admission adapts to monitored resources",
            "benchmark_timing_note": "GPU fits overlap small CPU logistic work; CPU benchmark is run alone. Two repeats, speed uncertain at subsecond scale."})
    started = time.monotonic()
    run_jobs(ROOT, first, COLLECTOR, plan, freeze["plan_sha256"], limits=limits, max_wait_seconds=120)
    run_jobs(ROOT, second, COLLECTOR, plan, freeze["plan_sha256"], limits=limits, max_wait_seconds=120)
    write(ROOT / "training_completion.json", {"finished_at": datetime.now(timezone.utc).isoformat(),
          "total_elapsed_seconds": time.monotonic()-started, "collector_after": CollectorMonitor(COLLECTOR).snapshot()})
    print("Research fits completed; no evaluation of 2023+ outcomes.", flush=True)


def joined(data, path):
    import pandas as pd
    p = pd.read_parquet(path)
    if len(p) != len(data) or set(map(tuple,p[["race_id","horse_number"]].to_numpy())) != set(map(tuple,data[["race_id","horse_number"]].to_numpy())):
        raise ValueError("Prediction key set differs from full evaluation cohort")
    result = data.merge(p, on=["race_id", "horse_number"], how="left", validate="one_to_one")
    if len(result) != len(data) or result.probability.isna().any():
        raise ValueError("Prediction/cohort join mismatch")
    return result


def evaluate():
    import numpy as np
    import pandas as pd
    from market_edge import evaluation as ev
    from market_edge.compute import CollectorMonitor
    freeze = read(ROOT / "research_freeze.json")
    if sha(ROOT / "experiment_plan.json") != freeze["plan_sha256"] or sha(ROOT / "development.parquet") != freeze["dataset_sha256"]:
        raise ValueError("Research freeze changed")
    for relative, expected in freeze["source_sha256"].items():
        if sha(REPO/relative) != expected:
            raise ValueError("Research source changed after fit: " + relative)
    frame = pd.read_parquet(ROOT / "development.parquet")
    dev = frame.loc[frame.phase.eq("development")].reset_index(drop=True)
    val = frame.loc[frame.phase.eq("validation")].reset_index(drop=True)
    benchmark = []
    for device in ["cpu", "gpu"]:
        for repeat in range(2):
            output = ROOT / "jobs" / f"benchmark-{device}-{repeat}"
            result = read(output / "result.json")
            metrics = ev.prediction_metrics(joined(val, output / "predictions.parquet"))
            benchmark.append({**result, "repeat": repeat, "metric_nll": metrics["nll"], "metric_brier": metrics["brier"],
                              "dataset_sha256": freeze["dataset_sha256"]})
    cpu = statistics.median([r["train_seconds"] for r in benchmark if r["device"] == "cpu"])
    gpu = statistics.median([r["train_seconds"] for r in benchmark if r["device"] == "gpu"])
    gain = cpu/gpu
    chosen_device = "gpu" if gain >= 1.1 else "cpu"
    pa = pd.read_parquet(ROOT / "jobs/benchmark-cpu-0/predictions.parquet")
    pb = pd.read_parquet(ROOT / "jobs/benchmark-gpu-0/predictions.parquet")
    parity = pa.merge(pb,on=["race_id", "horse_number"],validate="one_to_one",suffixes=("_cpu","_gpu"))
    difference = float(abs(parity.probability_cpu-parity.probability_gpu).max())
    if difference > .005:
        chosen_device = "cpu"
    write(ROOT / "cpu_gpu_benchmark.json", {"records": benchmark, "median_cpu_fit_seconds": cpu,
          "median_gpu_fit_seconds": gpu, "fit_speedup_cpu_over_gpu": gain, "chosen_validation_artifact_device": chosen_device,
          "maximum_probability_difference": difference, "parity_tolerance": .005,
          "choice_used_no_outcome_metrics": True, "speedup_is_not_campaign_speedup": True})
    models = {}
    for name, column in [("market", "normalized_market_probability"), ("h04", "h04_probability"),
                         ("h04_fixed_reference", "h04_probability_fixed_reference")]:
        models[name] = {"development": dev.assign(probability=dev[column]), "validation": val.assign(probability=val[column])}
    for name, directory in [("logistic","logistic"),("market_logistic","market-control")]:
        models[name] = {"development": joined(dev, ROOT / "jobs" / (directory+"-dev") / "predictions.parquet"),
                        "validation": joined(val, ROOT / "jobs" / (directory+"-val") / "predictions.parquet")}
    models["lightgbm"] = {"development": joined(dev, ROOT / "jobs/lightgbm-dev/predictions.parquet"),
                         "validation": joined(val, ROOT / "jobs" / f"benchmark-{chosen_device}-0/predictions.parquet")}
    comparison, selection, calibration, policies = {}, {}, {}, {}
    threshold_rows, coverage_rows, band_rows, annual_rows = [], [], [], []
    for name, phases in models.items():
        lower = ev.fit_conservative(phases["development"])
        phases = {phase: ev.apply_conservative(data,lower) for phase,data in phases.items()}
        models[name] = phases
        cuts = {kind: ev.coverage_cutoffs(phases["development"],conservative=kind=="conservative") for kind in ["ordinary","conservative"]}
        policies[name] = {"conservative_artifact": lower, "coverage_cutoffs": cuts}
        comparison[name], selection[name], calibration[name] = {}, {}, {}
        for phase,data in phases.items():
            metrics = ev.prediction_metrics(data)
            reliability = metrics.pop("reliability")
            comparison[name][phase] = metrics
            calibration[name][phase] = reliability
            selection[name][phase] = ev.selection_calibration(data)
            thresholds, coverages, bands = ev.policy_tables(data,cuts)
            metadata = {"model": name, "phase": phase, "year": int(data.race_date.iloc[0][:4])}
            threshold_rows.extend([{**metadata,**r} for r in thresholds])
            coverage_rows.extend([{**metadata,**r} for r in coverages])
            band_rows.extend([{**metadata,**r} for r in bands])
            for kind in ["ordinary","conservative"]:
                annual_rows.append({**metadata,"kind":kind,**next(r for r in thresholds if r["kind"]==kind and r["threshold"]==1.1)})
    selected = min(["logistic","lightgbm"],key=lambda n:(comparison[n]["validation"]["nll"],comparison[n]["validation"]["brier"]))
    gates = {}
    gates["beats_market_nll_both_periods"] = all(comparison[selected][p]["nll"] < comparison["market"][p]["nll"] for p in ["development","validation"])
    gates["validation_brier_no_worse"] = comparison[selected]["validation"]["brier"] <= comparison["market"]["validation"]["brier"]
    gates["h04_increment_vs_market_recalibration"] = comparison[selected]["validation"]["nll"] < comparison["market_logistic"]["validation"]["nll"]
    ratio = selection[selected]["validation"]["maximum_ev_per_race"]["absolute_log_ratio"]
    baseline = selection["h04"]["validation"]["maximum_ev_per_race"]["absolute_log_ratio"]
    gates["max_ev_calibration_improved_vs_h04"] = ratio is not None and baseline is not None and ratio < baseline
    subset = [r for r in coverage_rows if r["model"]==selected and r["phase"]=="validation" and r["kind"]=="ordinary"]
    full = next(r for r in subset if r["nominal_development_coverage"]==1)
    top = next(r for r in subset if r["nominal_development_coverage"]==.1)
    gates["coverage_roi_and_raw_hit_rate_improve"] = bool(top["bets"]>=30 and top["roi_percent"] is not None and full["roi_percent"] is not None and top["roi_percent"]>full["roi_percent"] and top["actual_win_rate"]>full["actual_win_rate"])
    bootstrap = {kind: ev.robustness(models[selected]["validation"],conservative=kind=="conservative") for kind in ["ordinary","conservative"]}
    ordinary = bootstrap["ordinary"]
    gates["not_single_jackpot_success"] = all(not (v["roi_percent"] is not None and v["roi_percent"]>100 and (v["roi_excluding_largest_ticket"] is None or v["roi_excluding_largest_ticket"]<=100)) for v in bootstrap.values())
    health = CollectorMonitor(COLLECTOR).snapshot()
    gates["forward_healthy"] = health["healthy"]
    for path, expected in [(COLLECTOR/"protocol.json",freeze["track_a_protocol_file_sha256"]),
                           (COLLECTOR/"analysis-definition.json",freeze["track_a_analysis_file_sha256"]),
                           (REPO/"python-api/models/.active_model.json",freeze["active_model_sha256"])]:
        if sha(path)!=expected:
            raise ValueError("Protected Track A/active model changed")
    passed = all(gates.values())
    policy = {"mode": "retrospective_proxy_candidate_only", "selected_by": "validation NLL then Brier, not ROI", "selected_model": selected,
              "primary_kind": "conservative", "threshold": 1.10, "stake_yen":100,"max_bets_per_race":1,
              "frozen_threshold_grid_diagnostic_only":ev.THRESHOLDS,"per_model":policies,"gates":gates,
              "further_search_permitted": passed, "later_period_evaluated":False,
              "later_period_reason":"early_stop_failed_gates" if not passed else "needs_next_frozen_phase",
              "production_adoption":False,"real_bets":0}
    write(ROOT/"market_baseline.json",comparison["market"])
    write(ROOT/"model_comparison.json",{"metrics":comparison,"selected_candidate":selected,"gates":gates,
          "all_years_previously_reused":True,"market_odds_time_unverified":True})
    write(ROOT/"calibration_report.json",calibration)
    write(ROOT/"selection_calibration.json",selection)
    write(ROOT/"bootstrap_results.json",{"model":selected,"validation":bootstrap,"limitations":["Not selection-adjusted confidence intervals", "Historical final-odds proxy; not executable ROI", "2021/2022 used in earlier H04 hyperparameter search"]})
    write(ROOT/"policy.json",policy)
    for name, rows in [("threshold_analysis.csv",threshold_rows),("coverage_roi.csv",coverage_rows),
                       ("odds_band_analysis.csv",band_rows),("annual_results.csv",annual_rows)]:
        path=ROOT/name
        if path.exists(): raise FileExistsError(path)
        pd.DataFrame(rows).to_csv(path,index=False,encoding="utf-8-sig")
    with sqlite3.connect(ROOT/"job_ledger.sqlite") as db:
        db.row_factory=sqlite3.Row
        jobs=[dict(r) for r in db.execute("SELECT * FROM jobs")]
    counts={d:sum(j["device"]==d and j["status"]=="completed" for j in jobs) for d in ["cpu","gpu"]}
    point_estimates=[{k:r[k] for k in ["model","kind","threshold","bets","roi_percent"]} for r in threshold_rows if r["phase"]=="validation" and r["model"] in ["logistic","lightgbm"] and r["roi_percent"] is not None and r["roi_percent"]>100]
    result={"status":"early_stopped" if not passed else "development_gates_passed", "candidate":selected,
            "market_edge":"NO" if not gates["beats_market_nll_both_periods"] else "INCONCLUSIVE",
            "roi_100_category":"B" if point_estimates else "C", "diagnostic_grid_over_100":point_estimates,
            "roi_category_scope":"diagnostic threshold grid point estimates; not primary-policy success or selection-adjusted evidence",
            "gates":gates,"jobs":counts,"primary_policy_validation":bootstrap["conservative"],
            "ordinary_policy_validation":bootstrap["ordinary"],"collector_after":health,
            "later_years_evaluated":False,"optuna_trials":0,"production_changes":False}
    write(ROOT/"research_result.json",result)
    lines=["# Track B: Market edge v1", "", "Historical market proxy only. Not executable ROI or an untouched holdout.", "",
           "## Prediction comparison", "", "| Model | 2021 Q4 NLL | 2022 NLL | 2022 Brier | Spearman | Top1 |", "|---|---:|---:|---:|---:|---:|"]
    for name,metrics in comparison.items():
        d,v=metrics["development"],metrics["validation"]
        lines.append(f"| {name} | {d['nll']:.6f} | {v['nll']:.6f} | {v['brier']:.6f} | {v['spearman']:.4f} | {v['top1_win_rate']:.2%} |")
    lines.extend(["",f"Candidate: {selected}; market edge: **{result['market_edge']}**; ROI category: **{result['roi_100_category']}**.",
                  f"State: {result['status']}. No 2023–2026 outcome evaluation; no Optuna or promotion.","","## Stop gates",""])
    lines.append("ROI category B, if present, denotes only point estimates somewhere in the diagnostic threshold grid, not primary-policy success.")
    lines.extend([f"- {k}: {v}" for k,v in gates.items()])
    lines.extend(["","## Policy (2022)","", "```json",json.dumps(bootstrap,ensure_ascii=False,indent=2),"```","",
                  "## Compute","",f"Completed CPU jobs: {counts['cpu']}; GPU jobs: {counts['gpu']} (two limited parity/size benchmarks).",
                  f"Median CPU/GPU fit seconds: {cpu:.4f}/{gpu:.4f}; fit speedup: {gain:.3f}x. Device: {chosen_device}.",
                  "This speedup does not describe data preparation, scheduling or full-campaign speed.","",
                  "## Interpretation","","Threshold and coverage tables are diagnostics, not new adopted policies. Wilson lower factors are grouped historical stresses, not certified individual probability bounds.",
                  "Track A remains frozen; candidates are separate research artifacts. A different future protocol is required for adoption."])
    (ROOT/"summary.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    audit=["# Research audit", "", f"Plan SHA256: `{freeze['plan_sha256']}`", f"Dataset SHA256: `{freeze['dataset_sha256']}`", "",
           "2021 H1 calibrates probability; Q3 meta fit → Q4 development; H2 final meta refit → 2022 validation. No 2023+ outcome reads by this campaign.",
           "Prior-year H04 artifacts provide OOT scores. H04 parameters were previously selected using these years: research reuse is disclosed, not treated as pristine holdout.",
           "Fixed Track A temperature is included solely as a contaminated reference; never a residual feature or selection gate.",
           "Models and standardization receive only fitting labels; inference drops labels/payout. Market offset is added explicitly to LightGBM raw predictions.",
           "All official payouts must be available and valid; unknown payouts do not become zero. Dead heats excluded by fixed cohort rule.",
           "GPU sample utilization is system-wide and cannot isolate this worker. Subsecond fits and simultaneous CPU work limit timing precision.",
           "Track A protocol/analysis hashes and active model pointer verified unchanged after all jobs. Collector was not restarted.",
           "Bootstrap resamples whole dates including no-bet days; selection and historical data reuse are not corrected by those confidence intervals.",
           "See provenance.json for exact artifact/model hashes and cohort exclusions, and the telemetry/events tables in job_ledger.sqlite for admission/pauses."]
    (ROOT/"audit.md").write_text("\n\n".join(audit)+"\n",encoding="utf-8")
    files=[p for p in ROOT.rglob("*") if p.is_file() and p.name not in {"artifact_manifest.json", "job_ledger.sqlite-wal", "job_ledger.sqlite-shm"}]
    write(ROOT/"artifact_manifest.json",{"created_at":datetime.now(timezone.utc).isoformat(),
          "files":{str(p.relative_to(ROOT)):sha(p) for p in sorted(files)},"track_a_changed":False,"active_model_changed":False})
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=["train","evaluate"])
    args=parser.parse_args()
    if args.action=="train": train()
    else: evaluate()
