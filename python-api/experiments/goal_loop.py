"""Fixed-goal, evidence-driven offline model improvement with bounded autonomy."""
from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
import time

from experiments import cli
from experiments.common import (
    REPO_ROOT, active_pointer_hash, file_sha256, json_sha256,
    read_json, source_manifest, write_json,
)
from experiments.goal_evidence import compare_records, verify_trial
from experiments.orchestrator import driver_lock
from experiments.runtime import ExperimentError
from experiments.search import freeze_json


SCHEMA = "keiba-model-goal-loop-v1"
GOAL = (
    "Diagnose missingness, test bounded improvement hypotheses on the same "
    "point-in-time development cohort, and persist independently recomputed "
    "adopt/reject/inconclusive evidence without changing production."
)
CRITERIA = [
    "immutable_data_code_and_goal",
    "development_only_diagnosis",
    "verified_baseline_and_candidate",
    "bounded_auditable_attempts",
    "recomputed_comparison",
    "production_unchanged",
]
BASE = REPO_ROOT / "reports" / "generated" / "model-experiments"
WORKER = REPO_ROOT / "scripts" / "run_model_goal_loop.py"
DEFAULT_CONFIG = REPO_ROOT / "config" / "model_goal_loop.v1.json"
GUARDS = [
    REPO_ROOT / "config" / "model_acceptance_contract.v1.json",
    REPO_ROOT / "config" / "phase3n_staking_payout_policy.v1.json",
]
BASE_PARAMS = {"num_leaves": 31, "min_data_in_leaf": 30, "learning_rate": 0.05}
SEARCH_SPACE = {
    "num_leaves": [7, 15, 31, 63],
    "min_data_in_leaf": [10, 30, 80, 150],
    "learning_rate": [0.02, 0.1],
    "lambda_l2": [0.0, 10.0],
}
_INTEGER_LIMITS = {
    "train_start_year": (2016, 2099), "max_proposals": (1, 3),
    "max_trials_per_proposal": (1, 30), "max_total_trials": (2, 90),
    "max_seconds": (1, 21600), "max_no_progress": (1, 3),
    "bootstrap_samples": (1, 1000), "minimum_paired_dates": (2, 10000),
    "num_boost_round": (1, 1000), "estimated_worker_memory_mb": (256, 131072),
    "reserve_ram_mb": (256, 131072), "seed": (0, 2147483647),
}
_FLOAT_KEYS = {
    "minimum_improvement", "maximum_annual_degradation", "maximum_top1_degradation",
}


def strict_json(path: Path) -> dict:
    def pairs(values):
        result = {}
        for name, value in values:
            if name in result:
                raise ValueError("duplicate-json-field")
            result[name] = value
        return result
    def invalid_constant(value):
        raise ValueError(f"nonfinite-json-number:{value}")
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs,
                       parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError("goal-json-object-required")
    return value


def validate_config(config: dict) -> dict:
    expected = set(_INTEGER_LIMITS) | _FLOAT_KEYS | {
        "schema", "target", "allowed_targets", "development_years",
    }
    if set(config) != expected or config["schema"] != "keiba-model-goal-loop-config-v1":
        raise ValueError("invalid-goal-config-fields-or-schema")
    for name, (low, high) in _INTEGER_LIMITS.items():
        if type(config[name]) is not int or not low <= config[name] <= high:
            raise ValueError(f"invalid-goal-config:{name}")
    for name in _FLOAT_KEYS:
        value = config[name]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not 0 <= value <= 1):
            raise ValueError(f"invalid-goal-config:{name}")
    if config["minimum_improvement"] <= 0:
        raise ValueError("minimum-improvement-must-be-positive")
    targets = config["allowed_targets"]
    if (not isinstance(targets, list) or not targets
            or any(not isinstance(t, str) or t not in {"win", "speed_deviation", "rank"} for t in targets)
            or len(targets) != len(set(targets)) or config["target"] not in targets):
        raise ValueError("invalid-goal-targets")
    years = config["development_years"]
    if (not isinstance(years, list) or not 1 <= len(years) <= 3
            or any(type(y) is not int or not 2017 <= y <= 2100 for y in years)
            or years != sorted(set(years)) or config["train_start_year"] >= min(years)):
        raise ValueError("invalid-goal-development-years")
    return dict(config)


def _guard_hashes() -> dict:
    return {str(path.relative_to(REPO_ROOT)): file_sha256(path) for path in GUARDS}


def _other_experiments_settled(root: Path) -> None:
    for ledger in BASE.rglob("experiment.sqlite3"):
        if ledger.resolve() == (root / "experiment.sqlite3").resolve():
            continue
        with sqlite3.connect(ledger.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            if connection.execute(
                    "SELECT 1 FROM experiments WHERE status IN ('active','cancelling','blocked') LIMIT 1"
            ).fetchone():
                raise RuntimeError(f"another-experiment-requires-attention:{ledger.parent.name}")


def _event(root: Path, kind: str, details: dict) -> None:
    paths = sorted((root / "progress").glob("*.json"))
    previous = file_sha256(paths[-1]) if paths else None
    freeze_json(root / "progress" / f"{len(paths):06d}.json", {
        "sequence": len(paths), "previous_sha256": previous,
        "timestamp": time.time(), "kind": kind, "details": details,
    })


def _events(root: Path) -> list[dict]:
    events, previous = [], None
    for index, path in enumerate(sorted((root / "progress").glob("*.json"))):
        value = strict_json(path)
        if (path.name != f"{index:06d}.json" or value["sequence"] != index
                or value["previous_sha256"] != previous):
            raise ValueError("goal-progress-ledger-chain-invalid")
        events.append(value)
        previous = file_sha256(path)
    return events


def _assert_task(root: Path) -> dict:
    task = strict_json(root / "task-ledger.json")
    if (task["schema"] != SCHEMA or task["goal"] != GOAL or task["criteria"] != CRITERIA
            or task["search_space"] != SEARCH_SPACE):
        raise ValueError("goal-or-criteria-changed")
    validate_config(task["config"])
    if task["guard_hashes"] != _guard_hashes():
        raise ValueError("goal-production-policy-changed")
    cli.verify_contract(root, task["execution_contract"])
    snapshot = strict_json(root / "snapshot.json")
    if (snapshot != task["snapshot"]
            or file_sha256(Path(snapshot["path"])) != snapshot["sha256"]):
        raise ValueError("goal-snapshot-changed")
    if file_sha256(root / "diagnostics.json") != task["diagnostics_sha256"]:
        raise ValueError("goal-diagnostics-changed")
    if file_sha256(root / "proposals.json") != task["proposals_sha256"]:
        raise ValueError("goal-proposals-changed")
    _events(root)
    return task


def prepare(root: Path, source: Path, config: dict) -> dict:
    from experiments.goal_diagnostics import diagnose_dataset, propose_hypotheses
    config = validate_config(config)
    with driver_lock(BASE, root):
        _other_experiments_settled(root)
        if (root / "task-ledger.json").exists():
            task = _assert_task(root)
            if task["config"] != config or task["source_path"] != str(source.resolve()):
                raise ValueError("cannot-change-prepared-goal")
            return {"status": "awaiting_approval", "task_sha256": json_sha256(task),
                    "already_prepared": True}
        if (root / "experiment.sqlite3").exists() or (root / "contract.json").exists():
            raise ValueError("goal-requires-a-new-experiment-root")
        manifest = cli.prepare(root, source)
        for name, key in (("dataset.py", "dataset_module_sha256"),
                          ("features.py", "features_module_sha256")):
            if manifest["source_contract"].get(key) != file_sha256(Path(__file__).with_name(name)):
                raise ValueError("goal-dataset-feature-source-mismatch")
        snapshot = strict_json(root / "snapshot.json")
        if manifest["snapshot_sha256"] != snapshot["sha256"]:
            raise ValueError("goal-dataset-snapshot-mismatch")
        diagnostics = diagnose_dataset(root / "dataset", config["development_years"],
                                       config["train_start_year"])
        proposals = propose_hypotheses(diagnostics, manifest, config["max_proposals"])
        authorized = []
        for proposal in proposals:
            if proposal["kind"] == "target_comparison":
                alternatives = [target for target in config["allowed_targets"]
                                if target != config["target"]]
                if not alternatives:
                    continue
                proposal = {**proposal, "target": alternatives[0]}
            else:
                proposal = {**proposal, "target": config["target"]}
            authorized.append(proposal)
        proposals = authorized
        freeze_json(root / "diagnostics.json", diagnostics)
        freeze_json(root / "proposals.json", {"proposals": proposals})
        contract = {
            "schema": SCHEMA, "source_manifest": source_manifest(),
            "dataset_sha256": manifest["dataset_sha256"],
            "dataset_manifest_sha256": file_sha256(root / "dataset" / "dataset_manifest.json"),
            "active_pointer_sha256": active_pointer_hash(),
            "capacity": {"cpu": 1, "gpu": 0}, "reserve_ram_mb": config["reserve_ram_mb"],
            "estimated_worker_memory_mb": config["estimated_worker_memory_mb"],
            "no_promotion": True, "no_live_bets": True, "no_network": True,
        }
        freeze_json(root / "contract.json", contract)
        task = {
            "schema": SCHEMA, "goal": GOAL, "criteria": CRITERIA,
            "config": config, "search_space": SEARCH_SPACE,
            "source_path": str(source.resolve()), "snapshot": snapshot,
            "execution_contract": contract, "guard_hashes": _guard_hashes(),
            "diagnostics_sha256": file_sha256(root / "diagnostics.json"),
            "proposals_sha256": file_sha256(root / "proposals.json"),
            "evaluation_label": "development_known_exposed",
            "final_holdout_access": False, "promotion_eligible": False,
            "approval_scope": "offline-allowlisted-features-and-parameters-only",
            "agent_policy": "deterministic-local-proposals; no external LLM or raw-data export",
        }
        freeze_json(root / "task-ledger.json", task)
        _event(root, "prepared", {"task_sha256": json_sha256(task)})
        return {"status": "awaiting_approval", "task_sha256": json_sha256(task),
                "proposals": proposals, "goal": GOAL}


def approve(root: Path, actor: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,100}", actor):
        raise ValueError("bounded-approval-actor-required")
    with driver_lock(BASE, root):
        task = _assert_task(root)
        value = {
            "schema": "local-goal-approval-v1", "actor": actor,
            "task_sha256": json_sha256(task), "scope": task["approval_scope"],
            "production_approval": False, "acquisition_approved": False,
        }
        freeze_json(root / "approval.json", value)
        return {"status": "ready", **value}


def _approved_task(root: Path) -> dict:
    task = _assert_task(root)
    approval = strict_json(root / "approval.json")
    if (approval["task_sha256"] != json_sha256(task)
            or approval["scope"] != task["approval_scope"]
            or approval["production_approval"] is not False
            or approval["acquisition_approved"] is not False):
        raise ValueError("goal-approval-invalid-or-stale")
    return task


def _payload(task: dict, manifest: dict, identifier: str, target: str,
             features: list[str], params: dict, proposal_id: str) -> dict:
    from experiments.training import _features
    config = task["config"]
    if target not in config["allowed_targets"]:
        raise ValueError("goal-target-outside-approval")
    _features(manifest, {"candidate_id": "M03", "feature_columns": features})
    return {
        "id": identifier, "target": target, "phase": "development",
        "validation_years": config["development_years"],
        "train_start_year": config["train_start_year"], "feature_columns": features,
        "params": {"num_boost_round": config["num_boost_round"], **BASE_PARAMS, **params},
        "seed": config["seed"], "num_threads": 4, "device": "cpu",
        "bootstrap_samples": config["bootstrap_samples"], "proposal_id": proposal_id,
    }


def _completed_rows(root: Path) -> list[dict]:
    state = cli.store_for(root).snapshot(root.name)
    return [row for row in state["trials"] if row["status"] == "completed"]


def _execute(root: Path, task: dict, payload: dict, deadline: float) -> dict:
    _approved_task(root)
    state = cli.store_for(root).snapshot(root.name)
    rows = {row["trial_id"]: row for row in state["trials"]}
    if payload["id"] in rows:
        row = rows[payload["id"]]
        normalized = {k: v for k, v in row["payload"].items()
                      if k != "experiment_contract_sha256"}
        if normalized != payload:
            raise ValueError("goal-resume-payload-changed")
        if row["status"] in {"running", "unknown", "cancelling", "failed", "cancelled"}:
            raise RuntimeError("goal-worker-needs-investigation-before-resume")
    elif len(rows) >= task["config"]["max_total_trials"]:
        raise TimeoutError("goal-loop-trial-budget-exhausted")
    cli.batch(root, [payload], worker_script=WORKER, deadline_at=deadline)
    row = next(row for row in _completed_rows(root) if row["trial_id"] == payload["id"])
    evaluation, records = verify_trial(root, row, task)
    return {"row": row, "evaluation": evaluation, "records": records}


def _suggest(trial, fixed: dict) -> dict:
    return {
        "num_leaves": trial.suggest_categorical("num_leaves", SEARCH_SPACE["num_leaves"]),
        "min_data_in_leaf": trial.suggest_categorical(
            "min_data_in_leaf", SEARCH_SPACE["min_data_in_leaf"]),
        "learning_rate": trial.suggest_float("learning_rate", *SEARCH_SPACE["learning_rate"], log=True),
        "lambda_l2": trial.suggest_float("lambda_l2", *SEARCH_SPACE["lambda_l2"]),
        **fixed,
    }


def _trial_fingerprint(payload: dict) -> str:
    return json_sha256({key: payload[key] for key in (
        "target", "validation_years", "train_start_year", "feature_columns", "params", "seed",
    )})


def run(root: Path) -> dict:
    import optuna
    with driver_lock(BASE, root):
        _other_experiments_settled(root)
        task = _approved_task(root)
        config = task["config"]
        store = cli.store_for(root)
        try:
            state = store.snapshot(root.name)
        except ExperimentError as exc:
            if str(exc) != "Experiment does not exist":
                raise
            store.create_experiment(root.name, task["execution_contract"])
            state = store.snapshot(root.name)
        if state["contract"] != task["execution_contract"]:
            raise ValueError("goal-queue-contract-mismatch")
        if state["status"] == "completed":
            return verify(root)
        if state["status"] != "active":
            raise RuntimeError("goal-queue-needs-investigation")
        if any(r["status"] not in {"queued", "completed"} for r in state["trials"]):
            raise RuntimeError("goal-worker-needs-investigation-before-resume")
        budget_file = root / "run-budget.json"
        if not budget_file.exists():
            started = time.time()
            freeze_json(budget_file, {
                "task_sha256": json_sha256(task), "started_at": started,
                "deadline_at": started + config["max_seconds"],
            })
            _event(root, "started", {"started_at": started})
        budget = strict_json(budget_file)
        if (budget["task_sha256"] != json_sha256(task)
                or budget["deadline_at"] - budget["started_at"] != config["max_seconds"]):
            raise ValueError("goal-time-budget-changed")
        deadline = budget["deadline_at"]
        manifest = strict_json(root / "dataset" / "dataset_manifest.json")
        baseline_payload = _payload(task, manifest, "baseline", config["target"],
                                    manifest["feature_sets"]["M03"], {}, "baseline")
        try:
            baseline = _execute(root, task, baseline_payload, deadline)
            no_progress = 0
            stop_reason = "plan_completed"
            for proposal_index, proposal in enumerate(strict_json(root / "proposals.json")["proposals"]):
                if proposal["requires_approval"]:
                    freeze_json(root / "approval-requests" / f"{proposal_index:02d}.json", {
                        "proposal": proposal, "executed": False,
                        "reason": "code-or-acquisition-changes-require-separate-review",
                    })
                    continue
                study = optuna.create_study(
                    study_name=f"proposal-{proposal_index}", direction="maximize",
                    storage=f'sqlite:///{(root / "optuna.sqlite3").as_posix()}',
                    load_if_exists=True, sampler=optuna.samplers.TPESampler(seed=config["seed"]),
                )
                improved = False
                for index in range(config["max_trials_per_proposal"]):
                    identifier = f"p{proposal_index:02d}-t{index:02d}"
                    planned = root / "attempts" / f"{identifier}.json"
                    if planned.exists():
                        plan = strict_json(planned)
                        frozen_trial = study.get_trials()[plan["optuna_trial_number"]]
                        payload = plan["payload"]
                    else:
                        if len(store.snapshot(root.name)["trials"]) >= config["max_total_trials"]:
                            raise TimeoutError("goal-loop-trial-budget-exhausted")
                        if time.time() >= deadline:
                            raise TimeoutError("goal-loop-time-budget-exhausted")
                        trial = study.ask()
                        params = (proposal["params"] if index == 0 else
                                  _suggest(trial, proposal["params"]))
                        target = proposal["target"]
                        payload = _payload(
                            task, manifest, identifier, target, proposal["feature_columns"],
                            params, proposal["id"],
                        )
                        plan = {"proposal": proposal, "payload": payload,
                                "optuna_trial_number": trial.number}
                        freeze_json(planned, plan)
                        frozen_trial = study.get_trials()[trial.number]
                    previous = store.snapshot(root.name)["trials"]
                    duplicate = next((row for row in previous
                                      if row["trial_id"] != identifier
                                      and _trial_fingerprint(row["payload"]) == _trial_fingerprint(payload)),
                                     None)
                    if duplicate is not None:
                        if frozen_trial.state == optuna.trial.TrialState.RUNNING:
                            study.tell(plan["optuna_trial_number"], state=optuna.trial.TrialState.PRUNED)
                        _event(root, "duplicate_attempt_skipped", {
                            "trial_id": identifier, "same_as": duplicate["trial_id"],
                        })
                        continue
                    observed = _execute(root, task, payload, deadline)
                    score = observed["evaluation"]["overall"]["rank_correlation"]
                    if frozen_trial.state == optuna.trial.TrialState.RUNNING:
                        study.tell(plan["optuna_trial_number"], score)
                    elif (frozen_trial.state != optuna.trial.TrialState.COMPLETE
                          or frozen_trial.value != score):
                        raise ValueError("goal-optuna-evidence-mismatch")
                    comparison = compare_records(baseline["records"], observed["records"], config)
                    freeze_json(root / "decisions" / f"{identifier}.json", {
                        "trial_id": identifier, "result_sha256": json_sha256(observed["row"]["result"]),
                        "comparison": comparison,
                    })
                    improved = improved or comparison["verdict"] == "candidate"
                no_progress = 0 if improved else no_progress + 1
                if no_progress >= config["max_no_progress"]:
                    stop_reason = "stagnation"
                    break
            _event(root, "exploration_stopped", {"reason": stop_reason})
            report = verify(root)
            if report["verified"]:
                store.finish_experiment(root.name)
            _event(root, "verification", {"verified": report["verified"], "reason": stop_reason})
            return report
        except TimeoutError as exc:
            _event(root, "budget_exhausted", {"reason": str(exc)})
            report = {"schema": SCHEMA, "status": "budget_exhausted", "verified": False,
                      "reason": str(exc), "promotion_eligible": False}
            write_json(root / "verification.json", report)
            return report
        except (Exception, KeyboardInterrupt) as exc:
            _event(root, "blocked", {"reason": str(exc), "type": type(exc).__name__})
            raise


def verify(root: Path) -> dict:
    task = _approved_task(root)
    state = cli.store_for(root).snapshot(root.name)
    if state["contract"] != task["execution_contract"]:
        raise ValueError("goal-queue-contract-mismatch")
    rows = state["trials"]
    if any(row["status"] != "completed" for row in rows):
        raise ValueError("goal-incomplete-or-unknown-trials")
    verified = {}
    for row in rows:
        verified[row["trial_id"]] = verify_trial(root, row, task)
    if "baseline" not in verified:
        raise ValueError("goal-baseline-evidence-missing")
    comparisons = {
        key: compare_records(verified["baseline"][1], records, task["config"])
        for key, (_, records) in verified.items() if key != "baseline"
    }
    candidates = [key for key, comparison in comparisons.items()
                  if comparison["verdict"] == "candidate"]
    selected = max(candidates, key=lambda key: (
        comparisons[key]["rank_correlation_delta"], -len(next(
            row["payload"]["feature_columns"] for row in rows if row["trial_id"] == key)),
        key,
    )) if candidates else "baseline"
    config = task["config"]
    diagnostics = strict_json(root / "diagnostics.json")
    stopped = [e for e in _events(root) if e["kind"] == "exploration_stopped"]
    plans_valid = True
    for row in rows:
        if row["trial_id"] == "baseline":
            continue
        plan = strict_json(root / "attempts" / f'{row["trial_id"]}.json')
        expected = {k: v for k, v in row["payload"].items()
                    if k != "experiment_contract_sha256"}
        if plan["payload"] != expected:
            plans_valid = False
    checks = {
        "immutable_data_code_and_goal": True,
        "development_only_diagnosis": diagnostics["no_future_rows_read"] is True,
        "verified_baseline_and_candidate": len(verified) >= 2,
        "bounded_auditable_attempts": (
            len(rows) <= config["max_total_trials"]
            and plans_valid and bool(stopped)),
        "recomputed_comparison": bool(comparisons),
        "production_unchanged": (
            active_pointer_hash() == task["execution_contract"]["active_pointer_sha256"]
            and _guard_hashes() == task["guard_hashes"]),
    }
    report = {
        "schema": SCHEMA, "goal": GOAL, "task_sha256": json_sha256(task),
        "status": "completed" if all(checks.values()) else "blocked",
        "verified": all(checks.values()), "criteria": checks,
        "trial_count": len(rows), "selected_trial": selected,
        "stop_reason": stopped[-1]["details"]["reason"] if stopped else "exploration_incomplete",
        "model_improved": bool(candidates), "comparisons": comparisons,
        "evaluation_label": "development_known_exposed", "unseen_validation": False,
        "promotion_eligible": False, "production_approval": False,
        "acquisition_requests_performed": 0,
        "baseline_label": "same-condition-retrained-control; not active-production-model",
        "financial_evaluation": "retrospective-top1-flat100; not approved Phase3N evidence",
        "probability_acceptance": "not_evaluated; calibration and timestamped market evidence required",
        "approval_requests": [str(p.relative_to(root)) for p in sorted(
            (root / "approval-requests").glob("*.json"))],
    }
    write_json(root / "verification.json", report)
    _write_dashboard(root, report, diagnostics, verified)
    return report


def _write_dashboard(root: Path, report: dict, diagnostics: dict, results: dict) -> None:
    rows, annual_rows, curves = [], [], []
    for identifier, (evaluation, records) in results.items():
        metric = evaluation["overall"]
        verdict = report["comparisons"].get(identifier, {}).get("verdict", "control")
        rows.append(
            f"<tr><td>{html.escape(identifier)}</td><td>{metric['rank_correlation']:.5f}</td>"
            f"<td>{metric['top1_win_rate']:.3%}</td><td>{html.escape(verdict)}</td></tr>"
        )
        for year, values in evaluation["by_year"].items():
            annual_rows.append(
                f"<tr><td>{html.escape(identifier)}</td><td>{html.escape(year)}</td>"
                f"<td>{values['rank_correlation']:.5f}</td>"
                f"<td>{values['top1_win_rate']:.3%}</td></tr>"
            )
        if records["payout_verified"].eq(1).all():
            profit = (records.sort_values(["race_date", "race_id"])["win_return_yen"] - 100).cumsum()
            span = max(1.0, float(profit.max() - profit.min()))
            points = " ".join(
                f"{index * 600 / max(1, len(profit) - 1):.2f},"
                f"{140 - (float(value) - float(profit.min())) * 120 / span:.2f}"
                for index, value in enumerate(profit)
            )
            curves.append(
                f"<h3>{html.escape(identifier)}: retrospective profit (JPY)</h3>"
                f"<p>End: {float(profit.iloc[-1]):.0f} JPY; 100 JPY top1 per race, "
                "not the approved expected-value staking policy.</p>"
                f"<svg viewBox='0 0 620 160' role='img' aria-label='Retrospective cumulative profit'>"
                f"<polyline fill='none' stroke='navy' points='{points}'/></svg>"
            )
        else:
            curves.append(
                f"<p>{html.escape(identifier)}: profit curve not evaluable; "
                "some winning payouts are unverified.</p>"
            )
    missing = []
    for name, feature in diagnostics["features"].items():
        fraction = feature["missing_fraction"]
        missing.append(
            f"<tr><td>{html.escape(name)}</td><td><meter min='0' max='1' "
            f"value='{fraction}'></meter> {fraction:.1%}</td></tr>"
        )
    contributions = []
    selected_row = read_json(root / "trials" / report["selected_trial"] / "result.json")
    contribution_artifact = selected_row["artifacts"]["contributions"]
    from experiments.goal_evidence import checked_artifact
    contribution_data = read_json(checked_artifact(root, contribution_artifact))
    feature_effects = {}
    for row in contribution_data["rows"]:
        feature_effects.setdefault(row["feature"], []).append(row["mean_abs_contribution"])
    ranked = sorted(((name, sum(values) / len(values)) for name, values in feature_effects.items()),
                    key=lambda item: item[1], reverse=True)[:20]
    largest = max((value for _, value in ranked), default=1.0)
    for name, value in ranked:
        contributions.append(
            f"<tr><td>{html.escape(name)}</td><td><meter min='0' max='{max(largest, 1e-12)}' "
            f"value='{value}'></meter> {value:.5f}</td></tr>"
        )
    calibration = []
    if "probability_metrics" in results[report["selected_trial"]][0]:
        import pandas as pd
        predicted = pd.read_parquet(checked_artifact(root, selected_row["artifacts"]["predictions"]))
        for index in range(10):
            selected = predicted.loc[
                predicted.win_probability.ge(index / 10)
                & (predicted.win_probability.lt((index + 1) / 10)
                   if index < 9 else predicted.win_probability.le(1))
            ]
            if not selected.empty:
                calibration.append(
                    f"<tr><td>{index / 10:.1f}-{(index + 1) / 10:.1f}</td>"
                    f"<td>{selected.win_probability.mean():.4f}</td>"
                    f"<td>{selected.finish.eq(1).mean():.4f}</td><td>{len(selected)}</td></tr>"
                )
    document = (
        "<!doctype html><html lang='en'><meta charset='utf-8'>"
        "<title>KeibaAI goal-loop evidence</title><h1>KeibaAI goal-loop</h1>"
        f"<p>{html.escape(GOAL)}</p><p>Status: {report['status']}; "
        f"selected: {html.escape(report['selected_trial'])}. "
        "Development evidence only; no promotion or live bets.</p>"
        "<h2>Recomputed comparison</h2><table><tr><th>Trial</th><th>Spearman</th>"
        "<th>Top1 win rate</th><th>Verdict</th></tr>" + "".join(rows) + "</table>"
        "<h2>Annual development slices</h2><table><tr><th>Trial</th><th>Year</th>"
        "<th>Spearman</th><th>Top1</th></tr>" + "".join(annual_rows) + "</table>"
        "<h2>Missingness (selected cohort, not all source records)</h2><table>"
        + "".join(missing) + "</table>"
        "<h2>Selected model contributions</h2><p>Train-only TreeSHAP samples, diagnostic only; "
        "not causal effects or feature-deletion criteria.</p><table>"
        + "".join(contributions) + "</table>"
        "<h2>Probability reliability</h2><p>No separate calibration has been applied. "
        "Rank/velocity scores are not probabilities.</p><table><tr><th>Bin</th>"
        "<th>Mean probability</th><th>Win fraction</th><th>Rows</th></tr>"
        + "".join(calibration) + "</table>"
        "<h2>Retrospective profit</h2>" + "".join(curves)
        + "<h2>Unmet production requirements</h2><p>Independent approval, probability calibration, "
        "timestamped pre-bet odds and trusted out-of-time acceptance evidence are required.</p></html>"
    )
    (root / "dashboard.html").write_text(document, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "approve", "run", "verify", "status", "cancel", "worker"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=REPO_ROOT / "keiba" / "data" / "keiba_ultimate.db")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--actor")
    parser.add_argument("--device", choices=["cpu"], default="cpu")
    args = parser.parse_args()
    unresolved = args.root.absolute()
    if any(path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())
           for path in [unresolved, *unresolved.parents] if path.exists()):
        parser.error("symlink or junction campaign roots are not allowed")
    root = unresolved.resolve()
    if root.parent != BASE.resolve() or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}", root.name):
        parser.error("root must be a named direct child of reports\\generated\\model-experiments")
    try:
        if args.action == "prepare":
            result = prepare(root, args.source, strict_json(args.config))
        elif args.action == "approve":
            if not args.actor:
                parser.error("approve requires --actor")
            result = approve(root, args.actor)
        elif args.action == "worker":
            from experiments.goal_training import run_goal_trial
            return cli.worker(root, "cpu", trial_runner=run_goal_trial)
        elif args.action == "run":
            result = run(root)
        elif args.action == "verify":
            with driver_lock(BASE, root):
                result = verify(root)
        elif args.action == "cancel":
            cli.store_for(root).cancel(root.name)
            result = {"status": "cancellation_requested"}
        else:
            task = _assert_task(root)
            result = {"goal": task["goal"], "task_sha256": json_sha256(task), "events": _events(root)}
            if (root / "experiment.sqlite3").exists():
                result["queue"] = cli.store_for(root).snapshot(root.name)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if result.get("verified", True) else 2
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "type": type(exc).__name__}),
              file=sys.stderr)
        return 1
