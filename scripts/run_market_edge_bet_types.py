"""New fixed-H04 bet-type experiment with a rank-first early-stop gate."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import json
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba"), str(ROOT / "scripts")]
import pandas as pd
from market_quality.lineage import sha256
from market_segments.modeling import _quality_gate
from market_edge.compute import JobSpec, ProtectionLimits, run_jobs
from verify_market_segments_outputs import verify_files, _protected_state, require
from bet_types.baseline import PRIMARY, RULES, rank_baselines, topn_summary, rule_summary, ranking_gate, win_roi_bootstrap

OUTPUT = ROOT / "reports/generated/model-experiments/h04-bet-types-v1"
SEGMENTS = ROOT / "reports/generated/model-experiments/market-segments-v1"
QUALITY = ROOT / "reports/generated/data-quality/market-h04-clean-v2"
TRACK = ROOT / "reports/generated/forward-runtime/h04-live-20260920-v2"
BET_TYPES = ["win", "place", "wide", "quinella", "exacta", "trio", "trifecta"]
AUDIT_DIRS = {"inventory": "inventory", "eligibility": "eligibility-v2"}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, content):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(content, stream, ensure_ascii=False, indent=2, allow_nan=False)


def protect():
    value = _protected_state(ROOT)
    manifest = read(SEGMENTS / "artifact_manifest.json")
    value["old_segments_artifacts"] = verify_files(SEGMENTS, manifest["files"])
    value["old_segments_manifest_sha256"] = sha256(SEGMENTS / "artifact_manifest.json")
    verify_files(ROOT, read(SEGMENTS / "segment_plan.json")["code_sha256"])
    return value


def initialize():
    require(not (OUTPUT / "experiment_plan.json").exists(), "plan already frozen: never overwrite")
    phase1 = read(SEGMENTS / "segment_plan.json")["phase1_gate"]
    _quality_gate(phase1)
    audits = {}
    for folder, directory_name in AUDIT_DIRS.items():
        root = OUTPUT / directory_name
        manifest = read(root / "artifact_manifest.json")
        verify_files(root, manifest["files"])
        audits[folder] = {"directory": directory_name, "manifest_sha256": sha256(root / "artifact_manifest.json"), "files": manifest["files"]}
    odds = pd.read_csv(OUTPUT / "inventory/bet_type_data_inventory.csv")
    require(len(odds) == 7 and odds.bet_type.nunique() == 7, "all seven bet types must be inventoried")
    require(odds.loc[odds.bet_type.eq("tansho"), "all_combination_odds_races"].tolist() == [6109], "historical complete win odds required")
    require(odds.loc[odds.bet_type.ne("tansho"), "all_combination_odds_races"].eq(0).all(),
            "New nonwin odds found: preregister a separate eligible plan before performance computation")
    before = protect()
    definition = {
        "method": "Exact Plackett-Luce up to third place; no Monte Carlo", "score_orientation": "larger is better",
        "weights": "exp((H04_score-max_score)/temperature), evaluated in log space",
        "ordered_pair": "w_i/sum(w) * w_j/sum(w excluding i), i!=j",
        "ordered_triple": "ordered_pair(i,j) * w_k/sum(w excluding i,j), all distinct",
        "marginals": "sum exact disjoint ordered outcomes, never independent-win-probability products",
        "mass_checks": "win=1; top2=2; top3=3; unordered/ordered top2=1; wide=3; unordered/ordered top3=1",
        "place_rule": "JRA sales-opening >=8 => top3; 5..7 => top2. Sales-opening count not fabricated from starters. Common study uses >=8 actual starters as conservative lower bound; smaller races excluded explicitly",
        "dead_heats": "all tied finishes excluded before performance analysis; source data unchanged",
        "calibration": {"fit_window": "2021H1 only", "temperature_bounds": [.01, 100.], "objective": "mean ordered top3 negative log likelihood", "parameters": 1,
            "reject": "optimum within 1% of log-range boundary, all scores constant, or <300 races", "selection": "no evaluation-period recalibration",
            "execution_condition": "Only if predeclared rank-baseline gate passes; otherwise NOT_FITTED"},
        "market_comparison": "normalized inverse single-win odds as PL weights only if calibration stage reached; this is a model-derived approximation, NOT observed combination-market probabilities",
        "source": "https://hturner.github.io/PlackettLuce/articles/Overview.html"}
    sources = [*sorted((ROOT / "python-api/bet_types").glob("*.py")), Path(__file__),
               ROOT / "scripts/audit_bet_type_inventory.py", ROOT / "scripts/audit_bet_type_eligibility.py",
               ROOT / "python-api/market_segments/evaluation.py", ROOT / "python-api/market_edge/compute.py"]
    plan = {"schema": "h04-fixed-bet-types-preregistered-v1", "frozen_at": datetime.now(timezone.utc).isoformat(),
        "audits_completed_before_model_computation": True, "phase1_gate": phase1, "audits": audits,
        "protected_before": before, "source_cohort_sha256": sha256(QUALITY / "clean_runners.parquet"),
        "scope": "same audited2021/2022 complete no-tie >=8-runner races, independent of payouts/odds/profit", 
        "periods": {"calibration": "2021H1 reserved", "development": "2021H2", "validation": "2022 research-known"},
        "post_2022_outcomes": "never decode in this experiment",
        "baseline_models": ["fixed_H04_score", "ascending_single_win_odds_market_popularity"],
        "tie_break": "exact score/odds ties only, ascending horse_number; never outcomes",
        "topn": [1, 2, 3, 4, 5], "topn_metrics": ["winner_in_topn", "top2_recall", "top3_recall", "exacta_pair_in_topn", "quinella_pair_in_topn", "trifecta_horses_in_topn", "ordered_leading_pair_hit"],
        "topn_caveat": "exacta-pair-in-TopN means both actual first/second included (ordered BOX coverage), numerically same as quinella-pair coverage; NOT exact predicted leading order",
        "fixed_rules": {name: {"bet_type": kind, "zero_based_rank_positions": positions} for name, (kind, positions) in RULES.items()},
        "stake_per_ticket_yen": 100, "max_tickets": {"win": 1, "place": 1, "wide": 1, "quinella": 3},
        "rule_budget": "six isolated baseline rules; not a combined bankroll portfolio; no added combinations",
        "ROI_gate": "full-combination odds and audited payout required; win historical reference only, non-win payouts not an odds substitute; unavailable currencies stay null",
        "EV": {"run_before_rank_gate": False, "only_if_full_combination_odds": True,
            "win_thresholds": [1.10], "nonwin_thresholds": [], "reason": "no nonwin odds audited; no threshold copying from win; any newly available source requires a separately preregistered study"},
        "primary_rank_contrasts": PRIMARY, "primary_rule_metric": "paired race-level any-ticket hit, box ticket-hit rate also reported",
        "minimum_races": 300, "minimum_runners": 3000, "minimum_period": "both development and validation",
        "bootstrap": {"samples": 2000, "seed": 42, "unit": "paired whole race_date, same draws for all eight validation contrasts",
            "multiplicity": "centered standardized max-abs-Z simultaneous95% for all8 contrasts, no favorable filtering"},
        "early_rank_gate": "at least one non-win contrast in the8 primary metrics: H04-Market positive2021H2,positiveboth2022halves,simultaneous95lower>0,and bothperiod sample pass. Win-only lead cannot advance nonwin study",
        "if_rank_gate_fails": "stop before real-data temperature fitting, probability/EV research and PhaseB/C; probability code only synthetic-tested; preserve skipped artifacts with reasons",
        "Phase_A": ["win", "place", "wide", "quinella"], "Phase_B": ["exacta", "trio"], "Phase_C": ["trifecta"],
        "Phase_B_C": "not enabled merely by ROI>100; requires PhaseA reproducible improvement and adequate data; never automatic dedicated-model training",
        "distribution_spec": definition,
        "resources": "protected CPU2-thread job, two independent baseline model branches; audit work parallel; no GPU due bounded small computations",
        "forbidden": ["H04 training or target change", "existing99feature changes", "TrackA change", "Production change", "existingresults edit", "Optuna", "thresholdscan", "actualbet", "commit", "push"],
        "source_sha256": {p.relative_to(ROOT).as_posix(): sha256(p) for p in sources}}
    write(OUTPUT / "ranking_distribution_spec.json", definition)
    for source, targets in {"bet_type_data_inventory.csv": ["bet_type_data_inventory.csv", "bet_type_inventory.csv"],
                            "bet_type_coverage.csv": ["bet_type_coverage.csv", "odds_coverage.csv"],
                            "odds_source_inventory.json": ["odds_source_inventory.json"]}.items():
        for target in targets:
            require(not (OUTPUT / target).exists(), "inventory alias exists; no overwrite")
            with (OUTPUT / "inventory" / source).open("rb") as source_stream, (OUTPUT / target).open("xb") as target_stream:
                shutil.copyfileobj(source_stream, target_stream)
    write(OUTPUT / "experiment_plan.json", plan)
    print(json.dumps({"frozen": True, "plan_sha256": sha256(OUTPUT / "experiment_plan.json")}, indent=2))


def verify(expected):
    require(sha256(OUTPUT / "experiment_plan.json") == expected, "frozen plan changed")
    plan = read(OUTPUT / "experiment_plan.json")
    _quality_gate(plan["phase1_gate"])
    verify_files(ROOT, plan["source_sha256"])
    require(read(OUTPUT / "ranking_distribution_spec.json") == plan["distribution_spec"], "distribution spec changed")
    for name, audit in plan["audits"].items():
        folder = OUTPUT / audit["directory"]
        require(sha256(folder / "artifact_manifest.json") == audit["manifest_sha256"], "audit manifest changed")
        verify_files(folder, audit["files"])
    require(sha256(QUALITY / "clean_runners.parquet") == plan["source_cohort_sha256"], "source changed")
    return plan


def baseline(expected):
    plan = verify(expected)
    outputs = ["baseline-start.json", "baseline-result.json", "common_cohort.parquet", "rank_race_metrics.parquet",
               "fixed_tickets.parquet", "topn_race_metrics.parquet", "topn_accuracy.csv", "fixed_rule_results.csv",
               "yearly_results.csv", "market_baseline.csv", "paired_rank_comparison.csv", "bootstrap_results.json",
               "payout_concentration.csv", "rank_gate.json", "ev_results.csv", "combination_probability_audit.csv"]
    require(not any((OUTPUT / name).exists() for name in outputs), "existing attempt must not be overwritten")
    write(OUTPUT / "baseline-start.json", {"started_at": datetime.now(timezone.utc).isoformat(), "plan_sha256": expected})
    before = protect()
    race_audit = pd.read_csv(OUTPUT / plan["audits"]["eligibility"]["directory"] / "race_eligibility.csv", dtype={"race_id": str})
    require(race_audit.common_phase_a_eligible.isin([True, False]).all(), "bad eligibility boolean")
    require(not race_audit.race_id.duplicated().any() and race_audit.race_id.notna().all(), "unique eligibility race identifiers required")
    ids = set(race_audit.loc[race_audit.common_phase_a_eligible, "race_id"])
    frame = pd.read_parquet(QUALITY / "clean_runners.parquet", use_threads=False)
    frame = frame.loc[frame.race_id.astype(str).isin(ids)].copy()
    require(frame.race_id.nunique() == len(ids), "cohort key mismatch")
    frame = frame.sort_values(["race_date", "race_id", "horse_number"], kind="stable").reset_index(drop=True)
    frame.to_parquet(OUTPUT / "common_cohort.parquet", index=False)
    # Fixed rank rules are calculated before fitting any probability conversion.
    races, topn, tickets = rank_baselines(frame)
    settings = plan["bootstrap"]
    gate = ranking_gate(races, minimum_races=plan["minimum_races"], minimum_runners=plan["minimum_runners"], samples=settings["samples"], seed=settings["seed"])
    summary = topn_summary(topn)
    rules = rule_summary(tickets)
    roi = win_roi_bootstrap(tickets, samples=settings["samples"], seed=settings["seed"])
    races.to_parquet(OUTPUT / "rank_race_metrics.parquet", index=False)
    tickets.to_parquet(OUTPUT / "fixed_tickets.parquet", index=False)
    topn.to_parquet(OUTPUT / "topn_race_metrics.parquet", index=False)
    summary.to_csv(OUTPUT / "topn_accuracy.csv", index=False)
    rules.to_csv(OUTPUT / "fixed_rule_results.csv", index=False)
    rules.assign(year=rules.period.map({"calibration": 2021, "development": 2021, "validation": 2022})).to_csv(OUTPUT / "yearly_results.csv", index=False)
    rules.loc[rules.model.eq("Market")].to_csv(OUTPUT / "market_baseline.csv", index=False)
    pd.DataFrame(gate["development_comparisons"] + gate["primary_comparisons"]).to_csv(OUTPUT / "paired_rank_comparison.csv", index=False)
    write(OUTPUT / "bootstrap_results.json", {"rank_gate": gate, "ROI_reference": roi})
    rules[["period", "model", "rule", "bet_type", "roi_evaluable", "roi_percent", "top1_payout_concentration", "top3_payout_concentration", "max_payout_removed_roi", "top3_payout_removed_roi", "status"]].to_csv(OUTPUT / "payout_concentration.csv", index=False)
    write(OUTPUT / "rank_gate.json", gate)
    if not gate["calibrator_fitting_allowed"]:
        pd.DataFrame([{"bet_type": kind, "status": "NOT_EVALUATED", "reason": "rank_gate_failed" if kind == "win" else "no_all_combination_odds; rank_gate_failed" if kind in plan["Phase_A"] else "phase_A_gate_failed", "bets": None, "ROI": None, "profit": None} for kind in BET_TYPES]).to_csv(OUTPUT / "ev_results.csv", index=False)
        pd.DataFrame([{"bet_type": kind, "status": "NOT_COMPUTED_REAL_DATA", "reason": "early_rank_gate_failed", "mathematical_method": "exact_PL_synthetic_tests_only", "temperature_fitted": False} for kind in BET_TYPES]).to_csv(OUTPUT / "combination_probability_audit.csv", index=False)
    after = protect()
    verify(expected)
    write(OUTPUT / "baseline-result.json", {"status": "completed", "rank_gate": gate["gate"], "leads": gate["leads"],
        "calibration_next_allowed": gate["calibrator_fitting_allowed"], "cohort": {"races": len(ids), "runners": len(frame)},
        "plan_sha256": expected, "common_cohort_sha256": sha256(OUTPUT / "common_cohort.parquet"),
        "new_H04_fits": 0, "new_calibrator_fits": 0, "actual_purchases": 0, "post_2022_outcomes": False,
        "protected_before": before, "protected_after": after,
        "artifact_sha256": {p.name: sha256(p) for p in OUTPUT.iterdir() if p.is_file() and p.suffix in [".csv", ".json", ".parquet"]}})
    print(json.dumps({"gate": gate["gate"], "leads": gate["leads"], "races": len(ids), "runners": len(frame)}, indent=2))


def launch():
    path = OUTPUT / "experiment_plan.json"
    expected = sha256(path)
    verify(expected)
    job = JobSpec("rank-baseline", "fixed-H04-no-fit", "cpu", str(QUALITY / "clean_runners.parquet"), "2021-2022",
        [str(ROOT / "python-api/.venv/Scripts/python.exe"), str(Path(__file__)), "baseline", "--expected-plan-sha256", expected],
        str(ROOT), str(OUTPUT / "baseline-result.json"), cpu_threads=2, memory_mb=1400, gpu_memory_mb=0)
    run_jobs(OUTPUT, [job], TRACK, path, expected, limits=ProtectionLimits(gpu_capacity=0), max_wait_seconds=300)
    result = read(OUTPUT / "baseline-result.json")
    print(json.dumps({k: result[k] for k in ["status", "rank_gate", "leads", "cohort"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["initialize", "launch", "baseline"])
    parser.add_argument("--expected-plan-sha256")
    args = parser.parse_args()
    if args.stage == "initialize": initialize()
    elif args.stage == "launch": launch()
    else: baseline(args.expected_plan_sha256)
