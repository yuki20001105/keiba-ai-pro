"""Format-only recovery of a failed evaluation; frozen models/rules stay intact.

Old control predictions contain only race_id/horse_number/probability. Date is
joined from the SHA-bound complete cohort, never inferred from row position.
All primary evaluation tables must reproduce the failed attempt byte-for-byte.
"""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba"), str(ROOT / "scripts")]
import pandas as pd
from market_quality.lineage import sha256
from market_edge.compute import JobSpec, ProtectionLimits, run_jobs
from run_market_edge_segments_analysis import verify_plan, verify_data, verified_diagnostics, job_receipt, write
from market_segments.data import SEGMENT_NAMES
from market_segments.diagnostics import extract_cases
from market_segments.evaluation import evaluate, race_losses, compare_day_bootstrap

DIRECTORY = ROOT / "reports/generated/model-experiments/market-segments-v1"
PRIMARY_TABLES = ["all_segment_metrics.csv", "paired_day_bootstrap.csv", "segment_candidates.csv", "multiplicity.json"]
ORIGINAL_PLAN = "957006d104504c3fb4a68491556b8844d403efedd6e18a93bf5ed2c795a2a441"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def score_frames(plan):
    verify_data(DIRECTORY, plan)
    verified_diagnostics(DIRECTORY)
    training = read(DIRECTORY / "training_plan.json")
    for key, path in [("segment_plan_sha256", DIRECTORY / "segment_plan.json"),
                      ("dataset_sha256", DIRECTORY / "data/model_input.parquet"),
                      ("feature_spec_sha256", DIRECTORY / "feature_spec.json"),
                      ("diagnostics_result_sha256", DIRECTORY / "diagnostics-result.json")]:
        if training[key] != sha256(path):
            raise ValueError("Original fit contract changed")
    data = pd.read_parquet(DIRECTORY / "data/model_input.parquet", use_threads=False)
    base = data.loc[data.phase.isin(["development", "validation"])].reset_index(drop=True)
    frames = {"A_Market": base.assign(probability=base.normalized_market_probability), "H04": base.assign(probability=base.h04_probability)}
    for group in ["B", "F"]:
        for model in ["logistic", "lightgbm"]:
            predictions = []
            for period in ["development", "validation"]:
                name = f"{group}-{model}-{period}"
                folder = DIRECTORY / "jobs" / name
                result, ledger = job_receipt(DIRECTORY, name)
                provenance = result["provenance"]
                if (result["status"] != "completed" or result["group"] != group or result["model"] != model or result["fold"] != period
                        or provenance["plan_sha256"] != sha256(DIRECTORY / "training_plan.json")
                        or provenance["feature_spec_sha256"] != training["feature_spec_sha256"]
                        or provenance["dataset_sha256"] != training["dataset_sha256"]
                        or Path(ledger["result_path"]).resolve() != folder / "result.json"):
                    raise ValueError("Original completed fit receipt differs")
                for relative, digest in result["artifact_sha256"].items():
                    path = (folder / relative).resolve()
                    if not path.is_relative_to(folder) or sha256(path) != digest:
                        raise ValueError("Saved fitted artifact changed")
                predicted = pd.read_parquet(folder / "predictions.parquet", use_threads=False)
                keys = ["race_id", "horse_number", "race_date"]
                if not base.loc[base.phase.eq(period), keys].sort_values(keys).reset_index(drop=True).equals(predicted[keys].sort_values(keys).reset_index(drop=True)):
                    raise ValueError("Prediction keys are not the exact original cohort")
                predictions.append(predicted)
            frames[group + "_" + model] = base.merge(pd.concat(predictions, ignore_index=True), on=["race_id", "horse_number", "race_date"], validate="one_to_one")
    return base, frames


def fixed_control_with_dates(view, path, expected_sha):
    if sha256(path) != expected_sha:
        raise ValueError("Historical calibration control changed")
    control = pd.read_parquet(path, use_threads=False)
    keys = ["race_id", "horse_number"]
    if set(control) != {*keys, "probability"}:
        raise ValueError("Unexpected historical control schema; no generic fallback")
    if not view[keys].sort_values(keys).reset_index(drop=True).equals(control[keys].sort_values(keys).reset_index(drop=True)):
        raise ValueError("Historical control runner set mismatch")
    return view.merge(control, on=keys, validate="one_to_one")


def execute(expected):
    recovery_path = DIRECTORY / "evaluation_recovery_plan.json"
    recovery = read(recovery_path)
    if sha256(recovery_path) != expected or sha256(Path(__file__)) != recovery["worker_sha256"] or recovery["original_plan_sha256"] != ORIGINAL_PLAN:
        raise ValueError("Recovery contract changed")
    plan = verify_plan(DIRECTORY / "segment_plan.json", ORIGINAL_PLAN)
    for name, digest in recovery["original_partial_tables"].items():
        if sha256(DIRECTORY / "evaluation" / name) != digest:
            raise ValueError("Failed-attempt evidence changed")
    base, models = score_frames(plan)
    flags = pd.read_parquet(DIRECTORY / "data/race_segments.parquet", use_threads=False)
    metrics, ci, candidates, family = evaluate(models, flags, SEGMENT_NAMES,
        minimum_races=plan["minimum_races"], minimum_runners=plan["minimum_runners"], samples=plan["bootstrap_samples"], seed=plan["seed"])
    out = DIRECTORY / "evaluation-recovery-v1"
    out.mkdir(exist_ok=False)
    metrics.to_csv(out / PRIMARY_TABLES[0], index=False)
    ci.to_csv(out / PRIMARY_TABLES[1], index=False)
    candidates.to_csv(out / PRIMARY_TABLES[2], index=False)
    write(out / PRIMARY_TABLES[3], family)
    same_primary = {name: sha256(out / name) == recovery["original_partial_tables"][name] for name in PRIMARY_TABLES}
    if not all(same_primary.values()):
        raise ValueError("Primary results changed during format-only recovery")
    contrast = []
    for period, item in plan["frozen_market_calibration_control"].items():
        control = race_losses(fixed_control_with_dates(base.loc[base.phase.eq(period)], item["path"], item["sha256"]))
        for name in ["B_logistic", "B_lightgbm", "F_logistic", "F_lightgbm"]:
            loss = race_losses(models[name].loc[models[name].phase.eq(period)])
            for metric in ["nll", "brier"]:
                receipt, _ = compare_day_bootstrap((loss[metric]-control[metric]).to_numpy(), loss.race_date.to_numpy(), samples=plan["bootstrap_samples"], seed=plan["seed"])
                contrast.append({"period": period, "model": name, "reference": "frozen_market_only_logistic_control", "metric": metric,
                    **receipt, "use": "prespecified global explanatory contrast; unadjusted descriptive CI, not independent confirmation or selection"})
    pd.DataFrame(contrast).to_csv(out / "calibration_control_comparison.csv", index=False)
    cases = extract_cases(base).merge(flags[["race_id", *SEGMENT_NAMES]], on="race_id", validate="many_to_one")
    case_rows = []
    for segment in SEGMENT_NAMES:
        part = cases.loc[cases[segment].fillna(False) & cases.phase.eq("validation")]
        c, d = int(part.case_C.sum()), int(part.case_D.sum())
        case_rows.append({"segment": segment, "races": int(part.race_id.nunique()), "runners": len(part),
            "H04_correct_cases": c, "market_correct_cases": d, "ties": int(part.case_tied.sum()),
            "H04_share_of_C_D": c / (c+d) if c+d else None,
            "minimum_sample_pass": part.race_id.nunique() >= plan["minimum_races"] and len(part) >= plan["minimum_runners"],
            "interpretation": "descriptive rank-error cases, not incremental probability edge"})
    pd.DataFrame(case_rows).to_csv(out / "case_condition_comparison.csv", index=False)
    positive = candidates.loc[candidates.candidate.eq("PROBABILITY_IMPROVEMENT_LEAD")]
    conclusion = {"status": "completed", "segment_candidate": "PROBABILITY_IMPROVEMENT_LEAD_ONLY" if len(positive) else "INCONCLUSIVE",
        "market_edge": "INCONCLUSIVE", "independent_validation": False, "production_adoption": False,
        "new_model_fits": 8, "recovery_new_fits": 0, "unavailable_groups": {g: "historical source-time availability unverified and sparse coverage; no proxy substituted" for g in ["C", "D", "E"]},
        "positive_cells": positive.to_dict("records"), "primary_evaluation_byte_identical": same_primary,
        "recovery_plan_sha256": expected, "original_plan_sha256": ORIGINAL_PLAN,
        "stop": "Fixed small comparison complete; no Optuna, EV search, post-2022 outcomes or further trials",
        "cohort": {"development_races": int(base.loc[base.phase.eq('development')].race_id.nunique()), "validation_races": int(base.loc[base.phase.eq('validation')].race_id.nunique())},
        "roi": "Only descriptive top1 and fixed EV>1.10; historical odds are not executable T-5 odds",
        "files": {p.name: sha256(p) for p in out.iterdir()}}
    write(out / "conclusion.json", conclusion)
    verify_plan(DIRECTORY / "segment_plan.json", ORIGINAL_PLAN)
    if sha256(recovery_path) != expected or sha256(Path(__file__)) != recovery["worker_sha256"]:
        raise ValueError("Recovery source/plan changed")
    write(DIRECTORY / "evaluate-recovery-result.json", {**conclusion, "conclusion_sha256": sha256(out / "conclusion.json"), "output_directory": str(out)})
    print(json.dumps(conclusion, ensure_ascii=False, indent=2))


def launch():
    verify_plan(DIRECTORY / "segment_plan.json", ORIGINAL_PLAN)
    recovery = {"schema": "market-segments-evaluation-format-recovery-v1", "original_plan_sha256": ORIGINAL_PLAN,
        "worker_sha256": sha256(Path(__file__)), "reason": "Historical control file has no race_date column; attach dates by exact race_id/horse_number keys from frozen cohort",
        "changes": "File-schema adapter only. Same data, feature groups, model artifacts, primary statistics, thresholds, bootstrap, multiplicity and selection. No fitting.",
        "original_partial_tables": {name: sha256(DIRECTORY / "evaluation" / name) for name in PRIMARY_TABLES},
        "preserve_failed_attempt": True, "output": str(DIRECTORY / "evaluation-recovery-v1")}
    path = DIRECTORY / "evaluation_recovery_plan.json"
    write(path, recovery)
    result = DIRECTORY / "evaluate-recovery-result.json"
    job = JobSpec("evaluate-recovery", "format-only-evaluation-no-fit", "cpu", str(DIRECTORY / "data/model_input.parquet"), "2021-2022",
        [str(ROOT / "python-api/.venv/Scripts/python.exe"), str(Path(__file__)), "--execute", "--expected-recovery-sha256", sha256(path)],
        str(ROOT), str(result), cpu_threads=2, memory_mb=1800, gpu_memory_mb=0)
    run_jobs(DIRECTORY, [job], ROOT / "reports/generated/forward-runtime/h04-live-20260920-v2", DIRECTORY / "segment_plan.json", ORIGINAL_PLAN,
        limits=ProtectionLimits(gpu_capacity=0), max_wait_seconds=300)
    print(json.dumps(read(result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--expected-recovery-sha256")
    args = parser.parse_args()
    execute(args.expected_recovery_sha256) if args.execute else launch()
