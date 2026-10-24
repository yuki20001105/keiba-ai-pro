"""Synthetic-only verifier fixtures; never inspect real study performance."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd
import pytest

from bet_types.baseline import rank_baselines, topn_summary, rule_summary, ranking_gate, win_roi_bootstrap

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/verify_h04_bet_types.py"
spec = importlib.util.spec_from_file_location("verify_bet_types_under_test", SCRIPT)
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def synthetic():
    rows = []
    for i, date in enumerate(("20210501", "20210701", "20211001", "20220101", "20220701")):
        rid = date[:4] + f"05010{i + 1:03d}"
        for horse in range(1, 9):
            finish = ((horse + i) % 8) + 1
            rows.append({"race_id": rid, "race_date": date, "horse_number": horse, "finish": finish,
                "h04_score": -float(horse // 2), "odds": float(finish + 1), "expected_runner_count": 8,
                "tansho_payout": 300.0 if finish == 1 else np.nan})
    return pd.DataFrame(rows)


def test_independent_rank_reconstruction_matches_all_synthetic_outputs():
    frame = synthetic().sample(frac=1, random_state=19)
    native, independent = rank_baselines(frame), verify.independent_rows(frame)
    for left, right, keys in zip(native, independent,
            (["race_id", "model"], ["race_id", "model", "top_n"], ["race_id", "model", "rule", "horses"])):
        verify.compare_frames(left, right, keys)
    top, rules = verify._summaries(independent[1], independent[2])
    verify.compare_frames(topn_summary(native[1]), top, ["period", "model", "top_n"])
    verify.compare_frames(rule_summary(native[2]), rules, ["period", "model", "rule"])


def test_tied_predictions_use_horse_number_and_no_outcome_tiebreak():
    frame = synthetic()
    frame["h04_score"], frame["odds"] = 0., 2.
    _, top, tickets = verify.independent_rows(frame)
    assert tickets.loc[tickets.rule.eq("win_top1"), "horses"].eq("1").all()
    assert tickets.loc[tickets.rule.eq("wide_13"), "horses"].eq("1-3").all()
    assert tickets.loc[tickets.rule.eq("quinella_top3_box"), "horses"].isin(["1-2", "1-3", "2-3"]).all()
    assert top.exacta_pair_in_topn.equals(top.quinella_pair_in_topn)


def test_independent_bootstrap_gate_matches_synthetic_shared_draws():
    races, _, _ = verify.independent_rows(synthetic())
    plan = {"bootstrap": {"samples": 40, "seed": 42}, "minimum_races": 1, "minimum_runners": 8}
    saved = ranking_gate(races, minimum_races=1, minimum_runners=8, samples=40, seed=42)
    expected = verify.independent_gate(races, plan)
    verify.verify_gate(saved, expected)
    saved["primary_comparisons"][0]["point"] += .001
    with pytest.raises(ValueError, match="numeric output"):
        verify.verify_gate(saved, expected)


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    repo, root = tmp_path, tmp_path / "study"
    root.mkdir()
    worker = repo / "scripts/run_market_edge_bet_types.py"
    worker.parent.mkdir()
    worker.write_text("# frozen fixture worker", encoding="utf-8")
    frame = synthetic()
    quality = repo / "quality"
    quality.mkdir()
    frame.to_parquet(quality / "clean_runners.parquet", index=False)
    write(quality / "quality_summary.json", {"fixture": True})
    audits = {}
    inventory = root / "inventory"
    inventory.mkdir()
    pd.DataFrame({"bet_type": ["tansho", "fukusho", "wide", "umaren", "umatan", "sanrenpuku", "sanrentan"],
        "all_combination_odds_races": [5, 0, 0, 0, 0, 0, 0]}).to_csv(inventory / "bet_type_data_inventory.csv", index=False)
    eligibility = root / "eligibility-v2"
    eligibility.mkdir()
    frame[["race_id"]].drop_duplicates().assign(common_phase_a_eligible=True).to_csv(eligibility / "race_eligibility.csv", index=False)
    for key, folder in (("inventory", inventory), ("eligibility", eligibility)):
        manifest = {"files": {p.name: verify.sha256(p) for p in folder.iterdir()}, "code": {}}
        write(folder / "artifact_manifest.json", manifest)
        audits[key] = {"directory": folder.name, "manifest_sha256": verify.sha256(folder / "artifact_manifest.json"), "files": manifest["files"]}
    plan = {"schema": "h04-fixed-bet-types-preregistered-v1", "audits_completed_before_model_computation": True,
        "frozen_at": datetime.now(timezone.utc).isoformat(), "source_sha256": {"scripts/run_market_edge_bet_types.py": verify.sha256(worker)},
        "phase1_gate": {"quality_summary_path": str(quality / "quality_summary.json")}, "audits": audits,
        "source_cohort_sha256": verify.sha256(quality / "clean_runners.parquet"), "distribution_spec": {"fixture": True},
        "fixed_rules": {name: {"bet_type": kind, "zero_based_rank_positions": positions} for name, (kind, positions) in verify.RULES.items()},
        "primary_rank_contrasts": verify.PRIMARY, "topn": [1, 2, 3, 4, 5], "stake_per_ticket_yen": 100,
        "bootstrap": {"samples": 20, "seed": 42}, "minimum_races": 1, "minimum_runners": 8}
    write(root / "experiment_plan.json", plan)
    write(root / "ranking_distribution_spec.json", plan["distribution_spec"])
    plan_hash = verify.sha256(root / "experiment_plan.json")
    frame.to_parquet(root / "common_cohort.parquet", index=False)
    races, topn, tickets = rank_baselines(frame)
    races.to_parquet(root / "rank_race_metrics.parquet", index=False)
    topn.to_parquet(root / "topn_race_metrics.parquet", index=False)
    tickets.to_parquet(root / "fixed_tickets.parquet", index=False)
    topn_summary(topn).to_csv(root / "topn_accuracy.csv", index=False)
    rules = rule_summary(tickets)
    rules.to_csv(root / "fixed_rule_results.csv", index=False)
    rules.loc[rules.model.eq("Market")].to_csv(root / "market_baseline.csv", index=False)
    rules.assign(year=rules.period.map({"calibration": 2021, "development": 2021, "validation": 2022})).to_csv(root / "yearly_results.csv", index=False)
    rules[["period", "model", "rule", "bet_type", "roi_evaluable", "roi_percent", "top1_payout_concentration",
           "top3_payout_concentration", "max_payout_removed_roi", "top3_payout_removed_roi", "status"]].to_csv(root / "payout_concentration.csv", index=False)
    gate = ranking_gate(races, minimum_races=1, minimum_runners=8, samples=20, seed=42)
    assert not gate["calibrator_fitting_allowed"]
    write(root / "rank_gate.json", gate)
    write(root / "bootstrap_results.json", {"rank_gate": gate, "ROI_reference": win_roi_bootstrap(tickets, samples=20)})
    pd.DataFrame(gate["development_comparisons"] + gate["primary_comparisons"]).to_csv(root / "paired_rank_comparison.csv", index=False)
    pd.DataFrame({"bet_type": sorted(verify.BET_TYPES), "status": "NOT_EVALUATED", "bets": None, "ROI": None, "profit": None}).to_csv(root / "ev_results.csv", index=False)
    pd.DataFrame({"bet_type": sorted(verify.BET_TYPES), "status": "NOT_COMPUTED_REAL_DATA", "temperature_fitted": False}).to_csv(root / "combination_probability_audit.csv", index=False)
    result = {"status": "completed", "plan_sha256": plan_hash, "new_H04_fits": 0, "new_calibrator_fits": 0,
        "actual_purchases": 0, "post_2022_outcomes": False, "rank_gate": gate["gate"], "leads": gate["leads"],
        "calibration_next_allowed": False, "common_cohort_sha256": verify.sha256(root / "common_cohort.parquet"),
        "cohort": {"races": 5, "runners": 40}, "artifact_sha256": {p.name: verify.sha256(p) for p in root.iterdir() if p.is_file()}}
    write(root / "baseline-result.json", result)
    with sqlite3.connect(root / "job_ledger.sqlite") as db:
        db.executescript("CREATE TABLE meta(key TEXT,value TEXT); CREATE TABLE jobs(job_id TEXT,status TEXT,exit_code INTEGER,started_at REAL,finished_at REAL,result_path TEXT,result_sha256 TEXT,command_json TEXT);")
        db.execute("INSERT INTO meta VALUES('plan_sha256',?)", (plan_hash,))
        at = datetime.fromisoformat(plan["frozen_at"]).timestamp()
        command = ["python", str(worker), "baseline", "--expected-plan-sha256", plan_hash]
        db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)", ("rank-baseline", "completed", 0, at + 1, at + 2,
            str(root / "baseline-result.json"), verify.sha256(root / "baseline-result.json"), json.dumps(command)))
    monkeypatch.setattr(verify, "_quality_check", lambda p: None)
    monkeypatch.setattr(verify, "_protect", lambda r: {"healthy": True, "unchanged": True})
    return repo, root, plan_hash


def run(campaign):
    repo, root, plan_hash = campaign
    return verify.verify_outputs(root, plan_hash, repo_root=repo)


def reseal(root, name):
    result = verify.read(root / "baseline-result.json")
    result["artifact_sha256"][name] = verify.sha256(root / name)
    write(root / "baseline-result.json", result)
    with sqlite3.connect(root / "job_ledger.sqlite") as db:
        db.execute("UPDATE jobs SET result_sha256=?", (verify.sha256(root / "baseline-result.json"),))


def test_whole_verification_passes_without_artifact_changes(campaign):
    _, root, _ = campaign
    before = {p: verify.sha256(p) for p in root.rglob("*") if p.is_file()}
    result = run(campaign)
    assert result["verified"], result["issues"]
    assert result["races"] == 5 and result["runners"] == 40
    assert result["independently_checked_tickets"] == 80
    assert before == {p: verify.sha256(p) for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("mutation", ["wrong_hit", "wrong_horse", "extra_ticket", "wrong_topn", "missing_runner", "extra_race", "future_date", "nonwin_money", "EV", "temperature_file", "gate"])
def test_hash_bound_but_inconsistent_outputs_rejected(campaign, mutation):
    _, root, _ = campaign
    if mutation in {"wrong_hit", "wrong_horse", "extra_ticket", "nonwin_money"}:
        name = "fixed_tickets.parquet"
        frame = pd.read_parquet(root / name)
        if mutation == "wrong_hit": frame.loc[0, "hit"] = 1 - frame.loc[0, "hit"]
        elif mutation == "wrong_horse": frame.loc[0, "horses"] = "99"
        elif mutation == "extra_ticket": frame = pd.concat([frame, frame.iloc[:1]], ignore_index=True)
        else: frame.loc[frame.bet_type.eq("wide"), "payout_yen"] = 100
        frame.to_parquet(root / name, index=False)
    elif mutation == "wrong_topn":
        name = "topn_race_metrics.parquet"
        frame = pd.read_parquet(root / name); frame.loc[0, "top3_recall"] = .123
        frame.to_parquet(root / name, index=False)
    elif mutation in {"missing_runner", "extra_race", "future_date"}:
        name = "common_cohort.parquet"
        frame = pd.read_parquet(root / name)
        if mutation == "missing_runner": frame = frame.iloc[1:]
        elif mutation == "extra_race": frame.loc[0, "race_id"] = "202205019999"
        else: frame.loc[0, "race_date"] = "20230101"
        frame.to_parquet(root / name, index=False)
    elif mutation == "EV":
        name = "ev_results.csv"
        frame = pd.read_csv(root / name); frame.loc[0, "ROI"] = 120
        frame.to_csv(root / name, index=False)
    elif mutation == "temperature_file":
        name = "temperature.json"; write(root / name, {"temperature": 1.})
    else:
        name = "rank_gate.json"
        gate = verify.read(root / name); gate["calibrator_fitting_allowed"] = True
        write(root / name, gate)
    reseal(root, name)
    assert not run(campaign)["verified"]


@pytest.mark.parametrize("mutation", ["uncompleted", "extra_job", "wrong_plan", "wrong_command"])
def test_ledger_must_be_one_completed_plan_bound_job(campaign, mutation):
    _, root, _ = campaign
    with sqlite3.connect(root / "job_ledger.sqlite") as db:
        if mutation == "uncompleted": db.execute("UPDATE jobs SET status='running'")
        elif mutation == "extra_job": db.execute("INSERT INTO jobs(job_id) VALUES('temperature-fit')")
        elif mutation == "wrong_plan": db.execute("UPDATE meta SET value='bad'")
        else: db.execute("UPDATE jobs SET command_json='[]'")
    assert not run(campaign)["verified"]


def test_manifest_modified_without_freeze_update_rejected(campaign):
    _, root, _ = campaign
    (root / "eligibility-v2/race_eligibility.csv").write_text("changed", encoding="utf-8")
    assert not run(campaign)["verified"]


def test_independent_gate_win_only_does_not_allow_expansion():
    races, _, _ = verify.independent_rows(synthetic())
    for column in verify.PRIMARY: races[column] = 0
    races.loc[races.model.eq("H04"), "win_top1"] = 1
    expected = verify.independent_gate(races, {"bootstrap": {"samples": 20, "seed": 42}, "minimum_races": 1, "minimum_runners": 8})
    assert expected["leads"] == ["win_top1"] and expected["nonwin_leads"] == []
