"""Protected Phase-2 preparation/diagnostics/evaluation, with no model fitting."""
from pathlib import Path
import argparse
import gc
import json
import sys
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]
import pandas as pd
from market_quality.lineage import prepare_event_history, sha256
from market_segments.modeling import _quality_gate
from market_segments.data import build_segment_dataset, F8, SEGMENT_NAMES
from market_segments.diagnostics import case_analysis, divergence_analysis, extract_cases
from market_segments.evaluation import evaluate, race_losses, compare_day_bootstrap


def write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def job_receipt(directory, job_id):
    ledger = directory / "job_ledger.sqlite"
    with sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        planned = conn.execute("SELECT value FROM meta WHERE key='plan_sha256'").fetchone()
    if not planned or planned[0] != sha256(directory / "segment_plan.json") or not row or row["status"] != "completed" or row["exit_code"] != 0:
        raise ValueError("Missing completed plan-bound job:" + job_id)
    path = Path(row["result_path"])
    if not path.resolve().is_relative_to(directory) or sha256(path) != row["result_sha256"]:
        raise ValueError("Completed job result changed:" + job_id)
    return json.loads(path.read_text(encoding="utf-8")), dict(row)


def verify_data(directory, plan):
    result, _ = job_receipt(directory, "prepare")
    for name, digest in result["files"].items():
        path = (directory / "data" / name).resolve()
        if not path.is_relative_to(directory / "data") or sha256(path) != digest:
            raise ValueError("Prepared data artifact changed")
    contract = json.loads((directory / "data/data_contract.json").read_text(encoding="utf-8"))
    if (contract["binding"]["phase1_gate"] != plan["phase1_gate"]
            or contract["binding"]["clean_runners_sha256"] != plan["clean_cohort_sha256"]
            or contract["source_snapshot_sha256"] != plan["snapshot_sha256"]):
        raise ValueError("Prepared data quality/source binding mismatch")
    return contract


def verified_diagnostics(directory):
    result, _ = job_receipt(directory, "diagnostics")
    for name, digest in result["files"].items():
        target = (directory / "diagnostics" / name).resolve()
        if not target.is_relative_to(directory / "diagnostics") or sha256(target) != digest:
            raise ValueError("Frozen divergence diagnostic changed")
    return result


def prior_surface_mapping(history, clean):
    """Exactly the same eligible strict-prior event source, never scraped prev_*.

    All target runners on a date are emitted before updating any horse state.
    No-career-history is not proof of debut; absence stays unknown.
    """
    targets = {(str(r.race_id), int(r.horse_number)) for r in clean.itertuples(index=False)}
    states, output = {}, []
    ordered = history.sort_values(["race_date", "race_id", "horse_number"], kind="stable")
    for date, day in ordered.groupby("race_date", sort=False):
        for row in day[["race_id", "horse_number", "horse_id", "surface"]].itertuples(index=False):
            if (str(row.race_id), int(row.horse_number)) not in targets:
                continue
            previous = states.get(str(row.horse_id)) if row.horse_id else None
            if previous:
                source_id, source_date, surface = previous
                if not source_date < str(date):
                    raise ValueError("non-prior-surface-source")
                output.append({"race_id": str(row.race_id), "horse_number": int(row.horse_number),
                    "source_race_id": source_id, "source_race_date": source_date, "previous_surface": surface})
        seen_today = set()
        for row in day[["race_id", "horse_id", "surface"]].itertuples(index=False):
            if row.horse_id:
                horse = str(row.horse_id)
                # Unknown order of two same-day appearances is not a known previous surface.
                states[horse] = None if horse in seen_today else (str(row.race_id), str(date), str(row.surface))
                seen_today.add(horse)
    return pd.DataFrame(output, columns=["race_id", "horse_number", "source_race_id", "source_race_date", "previous_surface"])


def verify_plan(plan_path, expected):
    if sha256(plan_path) != expected:
        raise ValueError("Predeclared segment plan changed")
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    _quality_gate(plan["phase1_gate"])
    for name, digest in plan["code_sha256"].items():
        if sha256(ROOT / name) != digest:
            raise ValueError("Frozen Phase-2 source changed:" + name)
    return plan


def prepare(directory, plan):
    quality = Path(plan["phase1_gate"]["quality_summary_path"]).parent
    clean = pd.read_parquet(quality / "clean_runners.parquet", use_threads=False)
    snapshot = ROOT / f"reports/generated/model-experiments/20260919-jra-safe-v1/snapshots/{plan['snapshot_sha256']}.db"
    history, receipt = prepare_event_history(snapshot, lambda stage: print(stage, flush=True), expected_snapshot_sha256=plan["snapshot_sha256"])
    mapping = prior_surface_mapping(history, clean)
    del history
    gc.collect()
    contract = {"history_boundary": "source_race_date < target_race_date", "snapshot_sha256": plan["snapshot_sha256"],
        "eligibility": "exact original frozen eligibility + complete-runner target filter", "source": "2013-2022 canonical event history", "T5_receipt_verified": False}
    manifest = build_segment_dataset(quality / "clean_runners.parquet", ROOT / "reports/generated/model-experiments/20260919-feature-search-v1/dataset", directory / "data",
        quality_summary_path=plan["phase1_gate"]["quality_summary_path"], expected_quality_sha256=plan["phase1_gate"]["quality_summary_sha256"],
        class_labels=plan["class_labels"], prior_surface=mapping, prior_surface_provenance=contract,
        phase1_gate=plan["phase1_gate"])
    mapping.to_parquet(directory / "data/prior_surface_mapping.parquet", index=False)
    write(directory / "data/history_receipt.json", receipt)
    return {"status": "completed", "races": manifest["races"], "runners": manifest["runners"],
        "dataset_sha256": sha256(directory / "data/model_input.parquet"), "segments": manifest["segment_coverage"], "training_performed": False,
        "files": {p.name: sha256(p) for p in (directory / "data").iterdir() if p.is_file()}}


def diagnostics(directory, plan):
    verify_data(directory, plan)
    frame = pd.read_parquet(directory / "data/model_input.parquet", use_threads=False)
    selected = frame.loc[frame.phase.isin(["development", "validation"])].copy()
    output = directory / "diagnostics"
    output.mkdir(exist_ok=False)
    for phase in ["development", "validation"]:
        part = selected.loc[selected.phase.eq(phase)]
        divergence_analysis(part).to_csv(output / (phase + "-divergence.csv"), index=False)
        write(output / (phase + "-cases.json"), case_analysis(part, plan["case_comparison_features"]))
    cases = extract_cases(selected)
    columns = ["race_id", "race_date", "horse_number", "phase", "normalized_market_probability", "h04_score", "h04_probability",
        "market_rank_ordinal", "h04_rank_ordinal", "probability_divergence", "rank_divergence", "finish", "actual_rank_average", "case_A", "case_B", "case_C", "case_D", "case_tied"]
    cases[[c for c in columns if c in cases]].to_parquet(output / "runner_divergence_cases.parquet", index=False)
    return {"status": "completed", "training_performed": False, "diagnostics_before_training": True,
        "interpretation": "Outcome-conditioned associations only; not causal identification or feature selection",
        "files": {p.name: sha256(p) for p in output.iterdir()}}


def evaluation(directory, plan):
    verify_data(directory, plan)
    verified_diagnostics(directory)
    training_plan_path = directory / "training_plan.json"
    training_plan = json.loads(training_plan_path.read_text(encoding="utf-8"))
    if (training_plan["segment_plan_sha256"] != sha256(directory / "segment_plan.json")
            or training_plan["dataset_sha256"] != sha256(directory / "data/model_input.parquet")
            or training_plan["feature_spec_sha256"] != sha256(directory / "feature_spec.json")
            or training_plan["diagnostics_result_sha256"] != sha256(directory / "diagnostics-result.json")):
        raise ValueError("Fit/evaluation contract changed")
    data = pd.read_parquet(directory / "data/model_input.parquet", use_threads=False)
    base = data.loc[data.phase.isin(["development", "validation"])].reset_index(drop=True)
    models = {"A_Market": base.assign(probability=base.normalized_market_probability), "H04": base.assign(probability=base.h04_probability)}
    for group in ["B", "F"]:
        for model in ["logistic", "lightgbm"]:
            predictions = []
            for period in ["development", "validation"]:
                folder = directory / "jobs" / f"{group}-{model}-{period}"
                result, ledger_row = job_receipt(directory, f"{group}-{model}-{period}")
                provenance = result["provenance"]
                if (result["status"] != "completed" or result["group"] != group or result["model"] != model or result["fold"] != period
                        or provenance["plan_sha256"] != sha256(training_plan_path)
                        or provenance["feature_spec_sha256"] != training_plan["feature_spec_sha256"]
                        or provenance["dataset_sha256"] != training_plan["dataset_sha256"]
                        or Path(ledger_row["result_path"]).resolve() != folder / "result.json"):
                    raise ValueError("Predeclared fit did not complete:" + str(folder))
                for name, digest in result["artifact_sha256"].items():
                    target = (folder / name).resolve()
                    if not target.is_relative_to(folder) or sha256(target) != digest:
                        raise ValueError("Saved fitted artifact changed")
                predicted = pd.read_parquet(folder / "predictions.parquet", use_threads=False)
                keys = ["race_id", "horse_number", "race_date"]
                expected_keys = base.loc[base.phase.eq(period), keys].sort_values(keys).reset_index(drop=True)
                actual_keys = predicted[keys].sort_values(keys).reset_index(drop=True)
                if not expected_keys.equals(actual_keys):
                    raise ValueError("Prediction keys/dates not exact fixed evaluation cohort")
                predictions.append(predicted)
            combined = pd.concat(predictions, ignore_index=True)
            models[group + "_" + model] = base.merge(combined, how="left", on=["race_id", "horse_number", "race_date"], validate="one_to_one")
    flags = pd.read_parquet(directory / "data/race_segments.parquet", use_threads=False)
    metrics, ci, candidates, family = evaluate(models, flags, SEGMENT_NAMES,
        minimum_races=plan["minimum_races"], minimum_runners=plan["minimum_runners"], samples=plan["bootstrap_samples"], seed=plan["seed"])
    output = directory / "evaluation"
    output.mkdir(exist_ok=False)
    metrics.to_csv(output / "all_segment_metrics.csv", index=False)
    ci.to_csv(output / "paired_day_bootstrap.csv", index=False)
    candidates.to_csv(output / "segment_candidates.csv", index=False)
    write(output / "multiplicity.json", family)
    control_comparison = []
    for period, item in plan["frozen_market_calibration_control"].items():
        if sha256(item["path"]) != item["sha256"]:
            raise ValueError("Frozen old market-calibration control changed")
        control_pred = pd.read_parquet(item["path"], columns=["race_id", "horse_number", "race_date", "probability"], use_threads=False)
        keys = ["race_id", "horse_number", "race_date"]
        view = base.loc[base.phase.eq(period)]
        if not view[keys].sort_values(keys).reset_index(drop=True).equals(control_pred[keys].sort_values(keys).reset_index(drop=True)):
            raise ValueError("Old control cohort differs; do not silently filter")
        control = race_losses(view.merge(control_pred, on=keys, validate="one_to_one"))
        for name in ["B_logistic", "B_lightgbm", "F_logistic", "F_lightgbm"]:
            loss = race_losses(models[name].loc[models[name].phase.eq(period)])
            for metric in ["nll", "brier"]:
                receipt, _ = compare_day_bootstrap((loss[metric]-control[metric]).to_numpy(), loss.race_date.to_numpy(), samples=plan["bootstrap_samples"], seed=plan["seed"])
                control_comparison.append({"period": period, "model": name, "reference": "frozen_market_only_logistic_control", "metric": metric,
                    **receipt, "use": "prespecified global explanatory contrast; unadjusted descriptive CI, not independent confirmation or selection"})
    pd.DataFrame(control_comparison).to_csv(output / "calibration_control_comparison.csv", index=False)
    # Report all case rates and conditions; final prose may show at most five.
    cases = extract_cases(base)
    merged = cases.merge(flags[["race_id", *SEGMENT_NAMES]], on="race_id", validate="many_to_one")
    case_rows = []
    for segment in SEGMENT_NAMES:
        part = merged.loc[merged[segment].fillna(False) & merged.phase.eq("validation")]
        c, d = int(part.case_C.sum()), int(part.case_D.sum())
        case_rows.append({"segment": segment, "races": int(part.race_id.nunique()), "runners": len(part), "H04_correct_cases": c,
            "market_correct_cases": d, "ties": int(part.case_tied.sum()), "H04_share_of_C_D": c / (c + d) if c+d else None,
            "minimum_sample_pass": part.race_id.nunique() >= plan["minimum_races"] and len(part) >= plan["minimum_runners"],
            "interpretation": "descriptive rank-error cases, not incremental probability edge"})
    pd.DataFrame(case_rows).to_csv(output / "case_condition_comparison.csv", index=False)
    positive = candidates.loc[candidates.candidate.eq("PROBABILITY_IMPROVEMENT_LEAD")]
    conclusion = {"status": "completed", "segment_candidate": "PROBABILITY_IMPROVEMENT_LEAD_ONLY" if len(positive) else "INCONCLUSIVE",
        "market_edge": "INCONCLUSIVE", "independent_validation": False, "production_adoption": False,
        "new_model_fits": 8, "unavailable_groups": {g: "historical source-time availability unverified and sparse coverage; no proxy substituted" for g in ["C", "D", "E"]},
        "positive_cells": positive.to_dict("records"),
        "stop": "Fixed small comparison complete; no Optuna, EV search, post-2022 outcomes or further trials",
        "cohort": {"development_races": int(base.loc[base.phase.eq('development')].race_id.nunique()), "validation_races": int(base.loc[base.phase.eq('validation')].race_id.nunique())},
        "roi": "Only descriptive top1 and fixed EV>1.10; historical odds are not executable T-5 odds",
        "files": {p.name: sha256(p) for p in output.iterdir()}}
    write(output / "conclusion.json", conclusion)
    return conclusion


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--stage", choices=["prepare", "diagnostics", "evaluate"], required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    directory = args.plan.resolve().parent
    if not directory.is_relative_to(ROOT / "reports/generated/model-experiments") or not args.result.resolve().is_relative_to(directory):
        raise ValueError("new-research-only-output")
    plan = verify_plan(args.plan, args.expected_plan_sha256)
    result = {"prepare": prepare, "diagnostics": diagnostics, "evaluate": evaluation}[args.stage](directory, plan)
    verify_plan(args.plan, args.expected_plan_sha256)
    write(args.result, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
