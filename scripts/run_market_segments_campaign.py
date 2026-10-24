"""New, bounded, preregistered Phase-2 campaign; collector protection reused."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]
from market_quality.lineage import sha256
from market_segments.data import segment_definitions, F8
from market_segments.diagnostics import diagnostic_definition
from market_segments.modeling import _quality_gate
from market_edge.modeling import FEATURES, MARKET_FEATURES, PARAMS
from market_edge.compute import JobSpec, ProtectionLimits, run_jobs

OUTPUT = ROOT / "reports/generated/model-experiments/market-segments-v1"
QUALITY = ROOT / "reports/generated/data-quality/market-h04-clean-v2"
TRACK = ROOT / "reports/generated/forward-runtime/h04-live-20260920-v2"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def write_or_match(path, value):
    if Path(path).exists():
        if read(path) != value:
            raise ValueError("Existing frozen contract differs; no overwrite:" + str(path))
        return
    write(path, value)


def initialize():
    if OUTPUT.exists():
        from run_market_edge_segments_analysis import verify_plan
        verify_plan(OUTPUT / "segment_plan.json", sha256(OUTPUT / "segment_plan.json"))
        return
    release = ROOT / "reports/generated/model-experiments/market-quality-audit-v1/quality-release.json"
    gate = {"quality_summary_path": str(QUALITY / "quality_summary.json"), "quality_summary_sha256": sha256(QUALITY / "quality_summary.json"),
        "quality_manifest_path": str(QUALITY / "artifact_manifest.json"), "quality_manifest_sha256": sha256(QUALITY / "artifact_manifest.json"),
        "quality_release_path": str(release), "quality_release_sha256": sha256(release)}
    _quality_gate(gate)
    classes = {"newcomer": ["新馬"], "maiden": ["未勝利"]}
    paths = [*sorted((ROOT / "python-api/market_segments").glob("*.py")),
             ROOT / "scripts/run_market_edge_segments_analysis.py", ROOT / "scripts/run_market_edge_segments_worker.py", Path(__file__)]
    old = ROOT / "reports/generated/model-experiments/market-edge-v1"
    old_manifest = read(old / "artifact_manifest.json")["files"]
    for name, digest in read(old / "research_freeze.json")["source_sha256"].items():
        if sha256(ROOT / name) != digest:
            raise ValueError("Protected original source changed:" + name)
        paths.append(ROOT / name)
    paths.extend(sorted((ROOT / "python-api/experiments").glob("*.py")))
    paths.extend(sorted((ROOT / "python-api/prospective").glob("*.py")))
    paths.extend([ROOT / "python-api/training/eligibility.py", ROOT / "python-api/market_quality/lineage.py"])
    paths = sorted(set(paths))
    control = {}
    for period, folder in [("development", "market-control-dev"), ("validation", "market-control-val")]:
        target = old / "jobs" / folder / "predictions.parquet"
        digest = sha256(target)
        relative = str(target.relative_to(old))
        if old_manifest.get(relative) != digest:
            raise ValueError("Old control not bound by original manifest")
        control[period] = {"path": str(target), "sha256": digest}
    plan = {"schema": "market-segments-preregistered-v1", "frozen_at": datetime.now(timezone.utc).isoformat(),
        "phase1_gate": gate, "snapshot_sha256": read(QUALITY / "audit_plan.json")["snapshot_sha256"],
        "clean_cohort_sha256": read(QUALITY / "quality_summary.json")["clean_runners_sha256"],
        "class_labels": classes, "segment_definition": segment_definitions(classes), "diagnostic_definition": diagnostic_definition(),
        "case_comparison_features": [*F8, "safe_age", "safe_distance", "safe_horse_surface_starts", "safe_distance_change", "field_size"],
        "minimum_races": 300, "minimum_runners": 3000, "minimum_sample_period": "Both development and2022validation for candidates; lower-sample cells still reported as underpowered",
        "bootstrap_samples": 2000, "seed": 42, "resampling": "paired whole race_date including no-member days; same validation draws across all model/segment/metric cells",
        "multiplicity": "Centered standardized day-bootstrap maximum absolute Z, simultaneous95% band across all eligible validation models including H04,18segments+ALL,NLL+Brier; no filtering by effect direction",
        "candidate": "Both NLL/Brier validation family CI upper<0; development and validation point improvements; both halves of2022 improved; sample pass in both periods. Exploratory probability-improvement lead only; conditional calibration and payout concentration still must be reviewed, never production",
        "models": {"A": "raw normalized inverse historical odds; no fitting", "H04": "fixed OOT scores + H1-2021 frozen calibrator; no fitting",
                   "B": {"features": FEATURES, "models": ["logistic", "lightgbm"]},
                   "C": "unavailable: historical Training source-time evidence absent; no substitute",
                   "D": "unavailable: historical SpeedFigures source-time evidence absent; no substitute",
                   "E": "unavailable: both source groups fail availability gate",
                   "F": {"features": MARKET_FEATURES + F8, "models": ["logistic", "lightgbm"]}},
        "frozen_market_calibration_control": control,
        "secondary_contrast": "Global B/F minus existing fixed market-only Logistic, paired day95%CI, descriptive only; separates market recalibration from added-feature value; no new fit or candidate selection",
        "splits": {"seed_calibration": "2021H1 fixed original", "development_fit": "2021Q3", "development_evaluation": "2021Q4", "validation_fit": "2021H2", "validation_evaluation": "2022 research-known"},
        "fit_budget": 8, "lightgbm": {**PARAMS, "rounds": 150, "deterministic": True, "force_col_wise": True},
        "logistic": {"l2": .01, "solver": "L-BFGS-B", "maxiter": 500, "ftol": 1e-12, "gtol": 1e-8},
        "missing": "F extras: train-only median plus fixed8missing flags, no dropping incomplete runners; baseline fields may not be missing",
        "ROI": "Reference only:100yen top1 eachrace and fixed maxEV>1.10; no threshold search or optimization",
        "early_stop": "After the fixed8fits, if no stable additional edge, stop; no Optuna, expanded features, or extra model trials",
        "known_period": "2021/2022 already used in H04 research, not independent; no2023+outcomes read in this campaign",
        "compute": "CPU2threads one protected job at a time; GPU not used for ~20k fit rows because prior same-size actual benchmark was slower; independent engineering/audits delegated in parallel",
        "protected": "Track A, H04/99features/temp/rule/protocol/active model/Production/old market-edge-v1 untouched",
        "forbidden": ["actual betting", "commit", "push", "automatic promotion", "2022-driven retuning", "post2022 outcomes"],
        "code_sha256": {str(p.relative_to(ROOT)): sha256(p) for p in paths}}
    OUTPUT.mkdir(parents=True, exist_ok=False)
    write(OUTPUT / "segment_plan.json", plan)
    print("Frozen segment plan", sha256(OUTPUT / "segment_plan.json"), flush=True)


def run_analysis(stage):
    plan = OUTPUT / "segment_plan.json"
    digest = sha256(plan)
    result = OUTPUT / (stage + "-result.json")
    job = JobSpec(stage, "no-fit-segment-" + stage, "cpu", str(QUALITY / "clean_runners.parquet"), "2021-2022",
        [str(ROOT / "python-api/.venv/Scripts/python.exe"), str(ROOT / "scripts/run_market_edge_segments_analysis.py"),
         "--plan", str(plan), "--expected-plan-sha256", digest, "--stage", stage, "--result", str(result)],
        str(ROOT), str(result), cpu_threads=2, memory_mb=5500 if stage == "prepare" else 1800, gpu_memory_mb=0)
    run_jobs(OUTPUT, [job], TRACK, plan, digest, limits=ProtectionLimits(gpu_capacity=0), max_wait_seconds=300)
    print(json.dumps(read(result), ensure_ascii=False, indent=2))


def train():
    from run_market_edge_segments_analysis import verify_plan, verify_data, verified_diagnostics
    prereg = verify_plan(OUTPUT / "segment_plan.json", sha256(OUTPUT / "segment_plan.json"))
    _quality_gate(prereg["phase1_gate"])
    verify_data(OUTPUT, prereg)
    diagnostics = verified_diagnostics(OUTPUT)
    if diagnostics.get("diagnostics_before_training") is not True:
        raise ValueError("Divergence/cause diagnostics must be completed first")
    data = OUTPUT / "data/model_input.parquet"
    groups = {"A": {"status": "available", "columns": []}, "B": {"status": "available", "columns": FEATURES},
        "F": {"status": "available", "columns": MARKET_FEATURES + F8, "asof_verified": True}}
    for g in ["C", "D", "E"]:
        groups[g] = {"status": "unavailable", "reason": prereg["models"][g]}
    spec = {"schema": "market-segment-fixed-feature-spec-v1", "dataset_sha256": sha256(data), "groups": groups,
        "feature_evidence": {c: {"asof_verified": True, "source_policy": "strict prior event-history/source race field; exact Phase1 reconstruction; not historicalT5receipt",
            "lineage_path": str(QUALITY / "lineage.json"), "lineage_sha256": sha256(QUALITY / "lineage.json")} for c in F8}}
    write_or_match(OUTPUT / "feature_spec.json", spec)
    training_plan = {**prereg, "segment_plan_sha256": sha256(OUTPUT / "segment_plan.json"),
        "feature_spec_sha256": sha256(OUTPUT / "feature_spec.json"), "dataset_sha256": sha256(data),
        "diagnostics_result_sha256": sha256(OUTPUT / "diagnostics-result.json")}
    path = OUTPUT / "training_plan.json"
    write_or_match(path, training_plan)
    jobs = []
    for group in ["B", "F"]:
        for model in ["logistic", "lightgbm"]:
            for fold in ["development", "validation"]:
                job_id = f"{group}-{model}-{fold}"
                out = OUTPUT / "jobs" / job_id
                jobs.append(JobSpec(job_id, group + "-" + model, "cpu", str(data), fold,
                    [str(ROOT / "python-api/.venv/Scripts/python.exe"), str(ROOT / "scripts/run_market_edge_segments_worker.py"),
                     "--data", str(data), "--feature-spec", str(OUTPUT / "feature_spec.json"), "--plan", str(path),
                     "--expected-plan-sha256", sha256(path), "--expected-feature-spec-sha256", sha256(OUTPUT / "feature_spec.json"),
                     "--group", group, "--model", model, "--fold", fold, "--output", str(out)],
                    str(ROOT), str(out / "result.json"), cpu_threads=2, memory_mb=1200, gpu_memory_mb=0))
    admission_plan = OUTPUT / "segment_plan.json"
    rows = run_jobs(OUTPUT, jobs, TRACK, admission_plan, sha256(admission_plan), limits=ProtectionLimits(gpu_capacity=0), max_wait_seconds=300)
    print(json.dumps([{k: row[k] for k in ["job_id", "status", "ram_mb", "paused_seconds"]} for row in rows], indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["prepare", "diagnostics", "train", "evaluate"])
    args = parser.parse_args()
    if args.stage == "prepare":
        initialize()
    if args.stage == "train":
        train()
    else:
        run_analysis(args.stage)


if __name__ == "__main__":
    main()
