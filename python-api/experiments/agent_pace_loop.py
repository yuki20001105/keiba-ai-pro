"""Hash-bound AI proposals; isolated ten-cycle win-NLL research, never promotion.

The engine does not impersonate an AI: a separate agent supplies each proposal
after reading the preceding development-only context and reflection evidence.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
import time
from types import FunctionType, SimpleNamespace

import joblib
import numpy as np
import pandas as pd

from experiments import cli, pace_research_loop as v2
from experiments.common import REPO_ROOT, active_pointer_hash, file_sha256, json_sha256, source_manifest
from experiments.goal_loop import _event, _events, _other_experiments_settled
from experiments.orchestrator import driver_lock
from training import db_context_features as context_features
from training import db_context_fit as control
from training import pace_research as learning

BASE = v2.BASE
DEFAULT_ROOT = BASE / "pace-agent-20261114-v3"
REFERENCE = BASE / "pace-research-20261114-v2"
WORKER = REPO_ROOT / "scripts" / "run_agent_pace_research.py"
SCHEMA = "agent-pace-research-v3"
BUDGET = {"maximum_cycles": 10, "candidates_per_cycle": 3, "maximum_configurations": 30,
          "maximum_fits": 240, "seconds": 21600, "workers": 1, "native_threads": 2}
CRITERIA = {"primary": "equal-year mean race NLL", "minimum_nll_improvement": .001,
            "maximum_annual_nll_regression": .005, "maximum_annual_brier_regression": .001,
            "maximum_annual_top1_regression": .005, "parsimony_band": .001,
            "temperature_boundary_allowed": False, "production_eligible": False}
DEFAULT_PARAMS = {"num_boost_round": 200, "num_leaves": 31, "min_data_in_leaf": 30, "lambda_l2": 1.}
PARAM_SPACE = {"num_boost_round": [120, 200, 300, 450, 600], "num_leaves": [7, 15, 31, 63],
               "min_data_in_leaf": [30, 60, 120, 240], "lambda_l2": [1., 5., 20., 50.]}
SPEC_KEYS = {"id", "groups", "objective", "head", "mode", "fields", "interactions", "time_feature", "params"}
PROPOSAL_KEYS = {"cycle", "context_sha256", "hypothesis", "reasoning", "expected_outcome", "candidates"}
freeze = v2.freeze
reuse_immutable = v2.reuse_immutable
load_data = v2.load_data
predict_bundle = v2.predict_bundle
mean_nll = v2.mean_nll
paired_bootstrap = v2.paired_bootstrap


def strict_json(path):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate-json-key")
            value[key] = item
        return value

    def invalid(value):
        raise ValueError("nonfinite-json-number:" + value)

    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=pairs, parse_constant=invalid)


def validate_spec(value):
    if not isinstance(value, dict) or set(value) != SPEC_KEYS:
        raise ValueError("specification-exact-schema-required")
    value = deepcopy(value)
    if not isinstance(value["id"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value["id"]):
        raise ValueError("safe-candidate-id-required")
    groups = value["groups"]
    if (not isinstance(groups, list) or not all(isinstance(g, str) for g in groups)
            or "baseline46" not in groups or len(set(groups)) != len(groups)
            or not set(groups).issubset(context_features.FEATURE_GROUPS)):
        raise ValueError("named-feature-groups-with-mandatory-baseline46-required")
    if value["objective"] != "win" or value["head"] not in ("none", "pace", "position", "both"):
        raise ValueError("win-objective-and-safe-head-required")
    if value["mode"] not in ("legacy", "hier", "hier_field", "condition"):
        raise ValueError("unknown-head-mode")
    for key in ("fields", "interactions", "time_feature"):
        if type(value[key]) is not bool:
            raise ValueError("strict-boolean-required")
    if value["interactions"] and value["head"] != "both":
        raise ValueError("interactions-require-both-heads")
    if value["time_feature"] and value["head"] not in ("pace", "both"):
        raise ValueError("time-feature-requires-pace-head")
    params = value["params"]
    if not isinstance(params, dict) or not set(params).issubset(PARAM_SPACE):
        raise ValueError("finite-parameter-whitelist-required")
    for key, item in params.items():
        if key == "lambda_l2":
            valid = type(item) in (float, int) and math.isfinite(item)
        else:
            valid = type(item) is int
        if not valid or item not in PARAM_SPACE[key]:
            raise ValueError("finite-parameter-value-required")
    value["params"] = {**DEFAULT_PARAMS, **params}
    value["params"]["lambda_l2"] = float(value["params"]["lambda_l2"])
    value["groups"] = [g for g in context_features.FEATURE_GROUPS if g in groups]
    if value["head"] == "none":
        value["mode"] = "legacy"
    return value


def canonical_key(value, *, historical=False):
    if historical:
        # Completed v2 speed/blend trials are also recorded, without admitting
        # those objectives or blend knobs into the new legal proposal space.
        original = deepcopy(value)
        original["id"] = "historical"
        objective = original["objective"]
        blend = original.pop("blend_weight", None)
        original["objective"] = "win"
        normalized = validate_spec(original)
        normalized["objective"] = objective
        if blend is not None:
            normalized["blend_weight"] = blend
    else:
        normalized = validate_spec(value)
    normalized.pop("id")
    return json_sha256(normalized)


def select_winner(results):
    by_id = {r["id"]: r for r in results}
    if "win80" not in by_id or len(by_id) != len(results):
        raise ValueError("unique-development-win80-baseline-required")
    for result in results:
        if sorted(f["year"] for f in result["folds"]) != [2022, 2023, 2024]:
            raise ValueError("development-years-only")
        for fold in result["folds"]:
            if (not all(type(fold["metrics"][k]) in (int, float)
                        and np.isfinite(fold["metrics"][k]) for k in ("nll", "brier", "top1", "ece"))
                    or type(fold["features"]) is not int or fold["features"] <= 0
                    or type(fold["boundary"]) is not bool):
                raise ValueError("finite-development-metrics-required")
    baseline = by_id["win80"]
    base_folds = {f["year"]: f for f in baseline["folds"]}
    eligible = []
    for candidate in results:
        safe = True
        for f in candidate["folds"]:
            b, c = base_folds[f["year"]]["metrics"], f["metrics"]
            safe &= (not f["boundary"] and c["nll"] - b["nll"] <= .005 + 1e-12
                     and c["brier"] - b["brier"] <= .001 + 1e-12
                     and b["top1"] - c["top1"] <= .005 + 1e-12)
        if (candidate["id"] != "win80" and safe
                and mean_nll(baseline) - mean_nll(candidate) >= .001 - 1e-12):
            eligible.append(candidate)
    numerical = min(eligible or [baseline], key=lambda r: (mean_nll(r), r["id"]))
    tie_set = [r for r in eligible + [baseline] if mean_nll(r) <= mean_nll(numerical) + .001 + 1e-12]
    winner = min(tie_set, key=lambda r: (max(f["features"] for f in r["folds"]),
                                       np.mean([f["metrics"]["brier"] for f in r["folds"]]),
                                       mean_nll(r), r["id"]))
    return {"winner": winner["id"], "specification": winner["specification"],
            "development_mean_nll": mean_nll(winner), "numerical_minimum": numerical["id"],
            "numerical_minimum_mean_nll": mean_nll(numerical),
            "eligible": sorted(r["id"] for r in eligible),
            "parsimony_tie_set": sorted(r["id"] for r in tie_set)}


def own_sources():
    training = {str(p.relative_to(REPO_ROOT)): file_sha256(p)
                for p in (REPO_ROOT / "python-api" / "training").rglob("*.py")}
    return {**v2.own_sources(), **source_manifest(), **training,
            "experiments.agent_pace_loop": file_sha256(Path(__file__)),
            "scripts.run_agent_pace_research": file_sha256(WORKER)}


def source_path(name):
    if name.endswith(".py"):
        return Path(name)
    if name.startswith("scripts."):
        return Path("scripts") / (name.split(".", 1)[1] + ".py")
    prefix = "keiba" if name.startswith("keiba_ai.") else "python-api"
    return Path(prefix).joinpath(*name.split(".")).with_suffix(".py")


def guards():
    result = v2.guards()
    database = REPO_ROOT / "keiba" / "data" / "keiba_ultimate.db"
    result["live_database"] = {str(p): file_sha256(p) for p in
                               (database, Path(str(database) + "-wal")) if p.exists()}
    return result


def queue_state(root):
    return cli.store_for(root).snapshot(root.name)


def readonly_state(root):
    with sqlite3.connect((root / "experiment.sqlite3").resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        state = dict(db.execute("SELECT * FROM experiments WHERE experiment_id=?", (root.name,)).fetchone())
        state["contract"] = json.loads(state.pop("contract_json"))
        state["trials"] = []
        for row in db.execute("SELECT * FROM experiment_trials WHERE experiment_id=? ORDER BY created_at,rowid",
                              (root.name,)):
            trial = dict(row)
            for name in ("payload", "result"):
                trial[name] = json.loads(trial.pop(name + "_json"))
                if json_sha256(trial[name]) != trial[name + "_sha256"]:
                    raise ValueError("reference-queue-json-hash-mismatch")
            state["trials"].append(trial)
    if json_sha256(state["contract"]) != state["contract_sha256"]:
        raise ValueError("reference-queue-contract-hash-mismatch")
    return state


def checked_manifest(root, manifest):
    for relative, digest in manifest.items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()) or path.is_symlink() or file_sha256(path) != digest:
            raise ValueError("immutable-file-evidence-changed:" + str(relative))


def trial_evidence(root, trial):
    directory = root / "trials" / trial["trial_id"]
    manifest = strict_json(directory / "artifact_manifest.json")
    checked_manifest(directory, manifest)
    v2.validate_reuse(root, trial)
    actual = {str(p.relative_to(directory)) for p in directory.rglob("*")
              if p.is_file() and p.name not in ("progress.json", "artifact_manifest.json")}
    if set(manifest) != actual:
        raise ValueError("trial-artifact-manifest-incomplete")
    result = trial["result"]
    if (result["experiment_contract_sha256"] != queue_contract_hash(root)
            or result["specification"] != trial["payload"]["specification"]):
        raise ValueError("trial-result-contract-or-specification-mismatch")
    return {"manifest_sha256": file_sha256(directory / "artifact_manifest.json"),
            "result_sha256": file_sha256(directory / "result.json")}


def queue_contract_hash(root):
    # Read-only even for frozen references; ExperimentStore.snapshot expires
    # leases and is deliberately never used on the reference.
    with sqlite3.connect((root / "experiment.sqlite3").resolve().as_uri() + "?mode=ro", uri=True) as db:
        return db.execute("SELECT contract_sha256 FROM experiments WHERE experiment_id=?", (root.name,)).fetchone()[0]


def audit_predictions(root, trial, target, features, expected_sources, *, replay=True):
    """Recompute probability metrics against the original complete cohort."""
    trial_evidence(root, trial)
    result = trial["result"]
    phase = trial["payload"]["phase"]
    years = [2026] if phase == "confirmation" else [2022, 2023, 2024]
    if [f["year"] for f in result["folds"]] != years:
        raise ValueError("trial-chronological-folds-required")
    records = {}
    for fold in result["folds"]:
        year = fold["year"]
        mask = target.race_date.str.startswith(str(year))
        t, x = target.loc[mask], features.loc[mask]
        directory = root / "trials" / trial["trial_id"] / "artifacts" / str(year)
        if strict_json(directory / "metrics.json") != fold:
            raise ValueError("fold-metrics-ledger-mismatch")
        prediction = pd.read_parquet(directory / "predictions.parquet")
        keys = ["race_id", "race_date", "horse_number", "finish"]
        if not prediction[keys].reset_index(drop=True).equals(t[keys].reset_index(drop=True)):
            raise ValueError("prediction-original-cohort-mismatch")
        independent = control.legacy.probability_metrics(t.reset_index(drop=True), prediction.probability.to_numpy())
        for name, foreign in (("nll", "nll"), ("brier", "brier"), ("ece", "selection_ece")):
            if not np.isclose(fold["metrics"][name], independent[foreign], atol=1e-10, rtol=0):
                raise ValueError("independent-probability-metric-mismatch")
        recomputed = learning.race_records(t, prediction.probability.to_numpy())
        for name, number in learning.summary_metrics(recomputed).items():
            if not np.isclose(number, fold["metrics"][name], atol=1e-10, rtol=0):
                raise ValueError("independent-race-metric-mismatch")
        pd.testing.assert_frame_equal(recomputed.reset_index(drop=True),
                                      pd.read_parquet(directory / "race-records.parquet").reset_index(drop=True))
        bundle = joblib.load(directory / "research.joblib")
        train_to = "20241231" if phase == "confirmation" else f"{year - 2}1231"
        cal_year = 2025 if phase == "confirmation" else year - 1
        if (bundle["sources"] != expected_sources or bundle["production_eligible"] is not False
                or bundle["specification"] != result["specification"]
                or bundle["schema"] != learning.SCHEMA or bundle["training_date_to"] > train_to
                or bundle["fit"]["calibration"] != fold["calibration"]
                or not fold["calibration"]["date_from"].startswith(str(cal_year))
                or not fold["calibration"]["date_to"].startswith(str(cal_year))
                or fold["calibration"]["fit_uses_test"] is not False
                or fold["boundary"] != fold["calibration"]["boundary_reached"]
                or len(bundle["fit"]["encoder"].feature_columns) != fold["features"]):
            raise ValueError("bundle-training-source-date-calibration-audit-failed")
        model = bundle["fit"]["model"]
        params = validate_spec(bundle["specification"])["params"]
        if (bundle["fit"]["objective"] != "win" or model.params["num_threads"] != 2
                or any(model.params[k] != params[k] for k in ("num_leaves", "min_data_in_leaf", "lambda_l2"))
                or not 1 <= model.current_iteration() <= params["num_boost_round"]):
            raise ValueError("bundle-learning-parameters-or-cpu2-mismatch")
        for item in fold["oof_audit"]:
            if not item["training_date_to"] < item["prediction_date_from"]:
                raise ValueError("annual-oof-future-leakage")
        expected_audit = []
        if bundle["heads"] is not None:
            heads = bundle["heads"]
            if heads["source_hashes"] != learning.source_hashes():
                raise ValueError("head-training-source-mismatch")
            if not heads["training_date_to"] <= train_to:
                raise ValueError("head-training-date-leakage")
            training = target.loc[target.race_date.le(train_to)]
            for training_year in sorted(training.race_date.str[:4].unique()):
                predicted_from = str(training.loc[training.race_date.str.startswith(training_year)].race_date.min())
                previous = training.loc[training.race_date.lt(predicted_from)]
                if not previous.empty:
                    expected_audit.append({"prediction_date_from": predicted_from,
                                           "training_date_to": str(previous.race_date.max())})
            expected_audit.append({"prediction_date_from": str(target.loc[target.race_date.gt(train_to)].race_date.min()),
                                   "training_date_to": str(training.race_date.max())})
            if heads["training_date_to"] != str(training.race_date.max()):
                raise ValueError("final-head-training-cutoff-mismatch")
        if fold["oof_audit"] != expected_audit:
            raise ValueError("annual-oof-coverage-or-cutoff-mismatch")
        if replay:
            scores, probabilities = predict_bundle(bundle, t, x)
            if not (np.array_equal(scores, prediction.score) and np.array_equal(probabilities, prediction.probability)):
                raise ValueError("serialized-prediction-replay-failed")
            for race in t.race_id.drop_duplicates().iloc[[0, -1]]:
                sample = t.loc[t.race_id.eq(race)].copy()
                before = predict_bundle(bundle, sample, x.loc[sample.index])
                sample["finish"], sample["time_seconds"] = 1, .01
                sample["_ctx_history_corners"] = [(1,) for _ in range(len(sample))]
                after = predict_bundle(bundle, sample, x.loc[sample.index])
                if not all(np.array_equal(a, b) for a, b in zip(before, after)):
                    raise ValueError("current-outcome-injection-used")
        records[year] = recomputed
    return records


def development_view(result):
    folds = []
    for fold in result["folds"]:
        if fold["year"] not in (2022, 2023, 2024):
            raise ValueError("development-context-future-rejected")
        folds.append({key: deepcopy(fold[key]) for key in ("year", "metrics", "features", "boundary")})
    return {"id": result["id"], "specification": result["specification"], "folds": folds}


def verify_reference(reference):
    """Verify old evidence without v2.assert_task's now-inapplicable full source guard."""
    task = strict_json(reference / "task-ledger.json")
    task_hash = file_sha256(reference / "task-ledger.json")
    approval = strict_json(reference / "approval.json")
    events = _events(reference)
    if (task["schema"] != learning.SCHEMA or approval["task_sha256"] != task_hash
            or approval["scope"] != "local-research-only"
            or not any(e["kind"] == "prepared" and e["details"]["task_sha256"] == task_hash for e in events)
            or strict_json(reference / "verification.json")["verified"] is not True
            or task["sources"] != v2.own_sources()):
        raise ValueError("reference-task-approval-verification-or-training-sources-invalid")
    # Archived original bytes prove the old contract; additions to today's
    # orchestration tree need not and must not rewrite this historical contract.
    checked_manifest(reference / "sources", task["execution_contract"]["source_manifest"])
    helper_key = "python-api/experiments/pace_research_loop.py"
    if file_sha256(Path(v2.__file__)) != task["execution_contract"]["source_manifest"][helper_key]:
        raise ValueError("frozen-v2-training-orchestration-changed")
    for module, digest in task["sources"].items():
        path = source_path(module)
        if (file_sha256(REPO_ROOT / path) != digest
                or file_sha256(reference / "sources" / path) != digest):
            raise ValueError("reference-relevant-training-source-changed")
    state = readonly_state(reference)
    if state["status"] != "completed" or state["contract"] != task["execution_contract"]:
        raise ValueError("reference-not-completed-or-contract-changed")
    manifest = strict_json(reference / "dataset" / "dataset_manifest.json")
    if (file_sha256(reference / "dataset" / "dataset_manifest.json")
            != task["execution_contract"]["dataset_manifest_sha256"]):
        raise ValueError("reference-dataset-manifest-changed")
    checked_manifest(reference / "dataset", manifest["files"])
    if file_sha256(Path(manifest["snapshot"]["path"])) != manifest["snapshot"]["sha256"]:
        raise ValueError("reference-snapshot-changed")
    target, x, _ = load_data(reference / "dataset")
    reused, historical = [], []
    saved_dev = strict_json(reference / "development-results.json")
    for trial in state["trials"]:
        trial_evidence(reference, trial)
        if trial["payload"]["phase"] == "development":
            if trial["result"] not in saved_dev:
                raise ValueError("reference-development-result-mismatch")
            historical.append(development_view(trial["result"]))
            if trial["result"]["id"] in ("win80", "field-win80"):
                audit_predictions(reference, trial, target, x, task["sources"])
                reused.append(trial["result"])
    if {r["id"] for r in reused} != {"win80", "field-win80"}:
        raise ValueError("reference-strong-win-controls-required")
    controls = {s["id"]: s for stage in v2.initial_stages() for s in stage
                if s["id"] in ("win80", "field-win80")}
    if any(r["specification"] != controls[r["id"]] for r in reused):
        raise ValueError("reference-strong-win-control-specification-mismatch")
    heads = verify_heads(reference, task["sources"])
    files = {str(p.relative_to(reference)): file_sha256(p) for p in reference.rglob("*") if p.is_file()}
    return {"root": str(reference), "task_sha256": task_hash, "files": files,
            "training_sources": task["sources"], "reused_development": reused,
            "historical_development": historical, "reused_heads": heads,
            "full_current_source_manifest_not_applicable": True}


def verify_heads(root, expected_sources):
    receipts = []
    for path in sorted((root / "head-cache").glob("*/receipt.json")):
        receipt = strict_json(path)
        identity = receipt["identity"]
        if (identity["sources"] != expected_sources
                or identity["dataset_manifest"] != file_sha256(root / "dataset" / "dataset_manifest.json")
                or identity["cutoff"] > "20241231"
                or path.parent.name != json_sha256(identity)
                or file_sha256(path.parent / "heads.joblib") != receipt["artifact_sha256"]):
            raise ValueError("head-cache-source-data-artifact-mismatch")
        heads = joblib.load(path.parent / "heads.joblib")
        if heads["source_hashes"] != learning.source_hashes() or heads["training_date_to"] != identity["cutoff"]:
            raise ValueError("head-cache-source-date-mismatch")
        receipts.append({"directory": path.parent.name, "identity": identity,
                         "receipt_sha256": file_sha256(path), "artifact_sha256": receipt["artifact_sha256"],
                         "new_fits": 0})
    return receipts


def prepare(root, reference=REFERENCE):
    root, reference = Path(root).resolve(), Path(reference).resolve()
    with driver_lock(BASE, root):
        if root.parent != BASE.resolve() or root.exists() or root == reference:
            raise ValueError("fresh-isolated-campaign-root-required")
        _other_experiments_settled(root)
        receipt = verify_reference(reference)
        root.mkdir()
        shutil.copytree(reference / "dataset", root / "dataset")
        shutil.copytree(reference / "head-cache", root / "head-cache")
        shutil.copy2(reference / "diagnostics.json", root / "diagnostics.json")
        freeze(root / "reference-receipt.json", receipt)
        manifest = strict_json(root / "dataset" / "dataset_manifest.json")
        checked_manifest(root / "dataset", manifest["files"])
        contract = {"capacity": {"cpu": 1, "gpu": 0}, "reserve_ram_mb": 2048,
                    "estimated_worker_memory_mb": 4096, "source_manifest": source_manifest(),
                    "active_pointer_sha256": active_pointer_hash(),
                    "dataset_manifest_sha256": file_sha256(root / "dataset" / "dataset_manifest.json"),
                    "dataset_sha256": manifest["files"]["dataset.parquet"]}
        task = {"schema": SCHEMA, "learning_schema": learning.SCHEMA, "budget": BUDGET, "criteria": CRITERIA,
                "sources": own_sources(), "guards": guards(), "execution_contract": contract,
                "reference_receipt_sha256": file_sha256(root / "reference-receipt.json"),
                "reference_development": receipt["reused_development"],
                "historical_development": receipt["historical_development"],
                "historical_specifications": [r["specification"] for r in receipt["historical_development"]],
                "diagnostics_sha256": file_sha256(root / "diagnostics.json"),
                "reused_heads": receipt["reused_heads"], "snapshot": manifest["snapshot"],
                "folds": [{"train_to": y - 2, "calibration": y - 1, "validation": y} for y in (2022, 2023, 2024)],
                "confirmation": {"train_to": 2024, "calibration": 2025, "validation": 2026,
                                 "known_retrospective_only": True, "selection_uses_confirmation": False},
                "fit_accounting": "All new LightGBM, temperature, normalization and threshold fits; reused heads excluded",
                "agent_authorship": "External agent writes fresh context-bound hypothesis/reasoning/expected_outcome and three specs",
                "network_requests": 0, "production_eligible": False}
        freeze(root / "task-ledger.json", task)
        for name, digest in task["sources"].items():
            path = source_path(name)
            destination = root / "sources" / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.copy2(REPO_ROOT / path, destination)
            if file_sha256(destination) != digest:
                raise ValueError("source-changed-during-prepare")
        cli.store_for(root).create_experiment(root.name, contract)
        _event(root, "prepared", {"task_sha256": file_sha256(root / "task-ledger.json")})
        return {"prepared": True, "root": str(root), "task_sha256": file_sha256(root / "task-ledger.json")}


def approve(root):
    task_sha = file_sha256(root / "task-ledger.json")
    freeze(root / "approval.json", {"task_sha256": task_sha, "scope": "local-research-only",
                                   "authorization": "Explicit user authorization for exactly ten AI-directed research cycles; no production changes",
                                   "approved_at": time.time()})
    return {"approved": True, "task_sha256": task_sha}


def assert_task(root):
    task = strict_json(root / "task-ledger.json")
    approval = strict_json(root / "approval.json")
    events = _events(root)
    if (task["schema"] != SCHEMA or task["budget"] != BUDGET or task["criteria"] != CRITERIA
            or task["sources"] != own_sources() or task["guards"] != guards()
            or approval["task_sha256"] != file_sha256(root / "task-ledger.json")
            or approval["scope"] != "local-research-only"
            or not any(e["kind"] == "prepared" and e["details"]["task_sha256"] == approval["task_sha256"] for e in events)):
        raise ValueError("agent-task-approval-source-or-production-guard-changed")
    checked_manifest(root / "sources", {str(source_path(name)): digest for name, digest in task["sources"].items()})
    cli.verify_contract(root, task["execution_contract"])
    state = queue_state(root)
    if (state["contract"] != task["execution_contract"] or state["cancel_requested"]
            or state["status"] not in ("active", "completed")
            or any(t["status"] in ("failed", "unknown", "cancelled", "cancelling") for t in state["trials"])):
        raise ValueError("queue-contract-or-state-needs-investigation")
    if file_sha256(root / "reference-receipt.json") != task["reference_receipt_sha256"]:
        raise ValueError("reference-receipt-changed")
    receipt = strict_json(root / "reference-receipt.json")
    checked_manifest(Path(receipt["root"]), receipt["files"])
    manifest = strict_json(root / "dataset" / "dataset_manifest.json")
    checked_manifest(root / "dataset", manifest["files"])
    if (manifest["snapshot"] != task["snapshot"]
            or file_sha256(Path(task["snapshot"]["path"])) != task["snapshot"]["sha256"]
            or file_sha256(root / "diagnostics.json") != task["diagnostics_sha256"]):
        raise ValueError("snapshot-or-diagnostics-changed")
    for item in task["reused_heads"]:
        directory = root / "head-cache" / item["directory"]
        if (file_sha256(directory / "receipt.json") != item["receipt_sha256"]
                or file_sha256(directory / "heads.joblib") != item["artifact_sha256"]):
            raise ValueError("reused-head-evidence-changed")
    if (root / "run-budget.json").exists() or any(e["kind"] == "budget_started" for e in events):
        assert_budget(root)
    return task


def start_budget(root):
    events = _events(root)
    if any(e["kind"] == "budget_started" for e in events):
        return {k: v for k, v in assert_budget(root).items() if k != "fit_records"}
    if (root / "run-budget.json").exists() or (root / "fit-budget.json").exists():
        raise ValueError("unbound-original-budget-needs-investigation")
    started = time.time()
    freeze(root / "run-budget.json", {"started": started, "deadline": started + BUDGET["seconds"]})
    freeze(root / "fit-budget.json", {"maximum": BUDGET["maximum_fits"],
                                     "deadline": started + BUDGET["seconds"], "records": []})
    _event(root, "budget_started", {"run_budget_sha256": file_sha256(root / "run-budget.json"),
                                   "fit_budget_sha256": file_sha256(root / "fit-budget.json")})
    return strict_json(root / "run-budget.json")


def assert_budget(root, *, live=False):
    starts = [e for e in _events(root) if e["kind"] == "budget_started"]
    if len(starts) != 1:
        raise ValueError("original-budget-event-required")
    timing = strict_json(root / "run-budget.json")
    fits = strict_json(root / "fit-budget.json")
    updated = [e for e in _events(root) if e["kind"] == "fit_reserved"]
    pinned = updated[-1]["details"]["fit_budget_sha256"] if updated else starts[0]["details"]["fit_budget_sha256"]
    if (set(timing) != {"started", "deadline"}
            or any(type(timing[k]) not in (int, float) or not math.isfinite(timing[k]) for k in timing)
            or timing["deadline"] != timing["started"] + BUDGET["seconds"]
            or file_sha256(root / "run-budget.json") != starts[0]["details"]["run_budget_sha256"]
            or fits["maximum"] != BUDGET["maximum_fits"] or fits["deadline"] != timing["deadline"]
            or file_sha256(root / "fit-budget.json") != pinned
            or len(fits["records"]) > BUDGET["maximum_fits"]
            or len(updated) != len(fits["records"])
            or any(r["number"] != i + 1 or not timing["started"] <= r["started_at"] < timing["deadline"]
                   for i, r in enumerate(fits["records"]))):
        raise ValueError("original-run-or-fit-budget-identity-changed")
    if live and time.time() >= timing["deadline"]:
        raise TimeoutError("agent-decision-or-learning-time-budget-exhausted")
    return {**timing, "fit_records": fits["records"]}


class AgentFitBudget(learning.FitBudget):
    def __post_init__(self):
        if self.path is None:
            raise ValueError("persistent-agent-fit-budget-required")
        timing = assert_budget(self.path.parent, live=True)
        if self.maximum != BUDGET["maximum_fits"] or self.deadline != timing["deadline"]:
            raise ValueError("fit-budget-contract-changed")
        super().__post_init__()

    def reserve(self, name):
        assert_budget(self.path.parent, live=True)
        super().reserve(name)
        _event(self.path.parent, "fit_reserved", {
            "number": len(self.records), "fit_budget_sha256": file_sha256(self.path)})


def evidence(root, directory, kind):
    events = [e for e in _events(root) if e["kind"] == kind]
    paths = sorted((root / directory).glob("*.json"))
    if len(paths) != len(events):
        raise ValueError("immutable-evidence-event-count-mismatch:" + directory)
    documents = []
    for n, (path, event) in enumerate(zip(paths, events), 1):
        if (path.name != f"{n:02d}.json" or event["details"]["cycle"] != n
                or file_sha256(path) != event["details"]["sha256"]):
            raise ValueError("immutable-evidence-hash-or-sequence-changed:" + directory)
        documents.append(strict_json(path))
    return documents


def admissions(root):
    admitted = evidence(root, "admissions", "proposal_admitted")
    for item in admitted:
        path = root / "admitted-proposals" / f"{item['cycle']:02d}.json"
        if (file_sha256(path) != item["proposal_source_sha256"]
                or strict_json(path) != item["proposal"]):
            raise ValueError("immutable-proposal-copy-evidence-changed")
    return admitted


def decisions(root):
    return evidence(root, "decisions", "cycle_evaluated")


def _settled(root, *, allow_running=False):
    state = queue_state(root)
    allowed = {"completed"} | ({"running"} if allow_running else set())
    if any(t["status"] not in allowed for t in state["trials"]):
        raise ValueError("failed-unknown-or-unfinished-worker-no-retry")
    return state


def _all_development(task, completed):
    return list(task["reference_development"]) + [r for d in completed for r in d["results"]]


def head_cache_evidence(root):
    return {str(p.relative_to(root)): file_sha256(p) for p in (root / "head-cache").rglob("*") if p.is_file()}


def context(root, cycle=None):
    with driver_lock(BASE, root):
        task = assert_task(root)
        _other_experiments_settled(root)
        _settled(root)
        start_budget(root)
        timing = assert_budget(root, live=True)
        completed, admitted = decisions(root), admissions(root)
        n = len(completed) + 1
        if n > 10 or (cycle is not None and (type(cycle) is not int or cycle != n)):
            raise ValueError("next-cycle-sequence-only-no-cycle-11-or-pending-cycle-skip")
        if len(admitted) not in (len(completed), n):
            raise ValueError("pending-admitted-cycle-sequence-invalid")
        contexts = evidence(root, "agent-context", "context_created")
        path = root / "agent-context" / f"{n:02d}.json"
        if len(contexts) == n:
            return {"path": str(path), "sha256": file_sha256(path), "next_cycle": n}
        if len(contexts) != n - 1:
            raise ValueError("context-sequence-invalid")
        prior = task.get("historical_development", task["reference_development"])
        payload = {"schema": SCHEMA, "cycle": n, "development_years": [2022, 2023, 2024],
                   "metrics_are_equal_year_weighted": True, "criteria": CRITERIA,
                   "task_sha256": file_sha256(root / "task-ledger.json"),
                   "previous_context_sha256": file_sha256(root / "agent-context" / f"{n-1:02d}.json") if n > 1 else None,
                   "proposal_schema": {"cycle": n, "context_sha256": "<SHA of this file>",
                                       "hypothesis": "<agent text>", "reasoning": "<agent reflection>",
                                       "expected_outcome": "<testable development expectation>", "candidates": "<exactly three full specifications>"},
                   "specification_keys": sorted(SPEC_KEYS), "groups": list(context_features.FEATURE_GROUPS),
                   "mandatory_groups": ["baseline46"], "objective": ["win"], "head": ["none", "pace", "position", "both"],
                   "mode": ["legacy", "hier", "hier_field", "condition"], "params": PARAM_SPACE,
                   "defaults": DEFAULT_PARAMS, "booleans": ["fields", "interactions", "time_feature"],
                   "admissibility": ["interactions requires head=both", "time_feature requires pace or both",
                                     "fields=True with head=none is legal", "no-head mode canonicalizes to legacy",
                                     "No arbitrary columns, blend, source rewrites, network, or production changes"],
                   "tried": [development_view(r) | {"canonical_sha256": canonical_key(r["specification"], historical=True)}
                             for r in list(prior) + [r for d in completed for r in d["results"]]],
                   "prior_cycles": [{"cycle": d["cycle"], "hypothesis": d["hypothesis"],
                                     "reasoning": d["reasoning"], "expected_outcome": d["expected_outcome"],
                                     "results": [development_view(r) for r in d["results"]],
                                     "interim_selection": d["interim_selection"]} for d in completed],
                   "interim_selection": select_winner(_all_development(task, completed)),
                   "remaining": {"cycles": 10 - len(completed), "specifications": 30 - 3 * len(completed),
                                 "fits": 240 - len(timing["fit_records"]), "deadline": timing["deadline"]},
                   "requires_external_ai_proposal": True, "roi_measured": False, "production_eligible": False}
        freeze(path, payload)
        _event(root, "context_created", {"cycle": n, "sha256": file_sha256(path)})
        return {"path": str(path), "sha256": file_sha256(path), "next_cycle": n}


def admit(root, proposal):
    with driver_lock(BASE, root):
        task = assert_task(root)
        _other_experiments_settled(root)
        _settled(root)
        assert_budget(root, live=True)
        completed, admitted = decisions(root), admissions(root)
        n = len(completed) + 1
        if n > 10 or len(admitted) != len(completed):
            raise ValueError("proposal-already-admitted-or-cycle-sequence-exhausted")
        contexts = evidence(root, "agent-context", "context_created")
        if len(contexts) != n:
            raise ValueError("next-context-required")
        path = root / "agent-context" / f"{n:02d}.json"
        raw = strict_json(proposal)
        if (not isinstance(raw, dict) or set(raw) != PROPOSAL_KEYS
                or type(raw["cycle"]) is not int or raw["cycle"] != n
                or raw["context_sha256"] != file_sha256(path)):
            raise ValueError("proposal-schema-sequence-or-stale-context")
        for key in ("hypothesis", "reasoning", "expected_outcome"):
            if not isinstance(raw[key], str) or not raw[key].strip() or len(raw[key]) > 20000:
                raise ValueError("agent-reasoning-text-required")
        if not isinstance(raw["candidates"], list) or len(raw["candidates"]) != 3:
            raise ValueError("exactly-three-distinct-new-specifications-required")
        candidates = [validate_spec(s) for s in raw["candidates"]]
        tried = list(task["historical_specifications"]) + [s for a in admitted for s in a["candidates"]]
        keys = {canonical_key(s, historical=True) for s in tried}
        ids = {s["id"] for s in tried}
        for s in candidates:
            key = canonical_key(s)
            if key in keys or s["id"] in ids:
                raise ValueError("duplicate-canonical-specification-or-id")
            keys.add(key)
            ids.add(s["id"])
        admission = {"cycle": n, "proposal": raw, "proposal_source_sha256": file_sha256(proposal),
                     "proposal_source_path": str(Path(proposal).resolve()), "context_sha256": file_sha256(path),
                     "candidates": candidates, "canonical_sha256": [canonical_key(s) for s in candidates]}
        copied = root / "admitted-proposals" / f"{n:02d}.json"
        copied.parent.mkdir(exist_ok=True)
        with copied.open("xb") as stream:
            stream.write(Path(proposal).read_bytes())
        if file_sha256(copied) != admission["proposal_source_sha256"] or strict_json(copied) != raw:
            raise ValueError("proposal-changed-during-admission")
        frozen = root / "admissions" / f"{n:02d}.json"
        freeze(frozen, admission)
        _event(root, "proposal_admitted", {"cycle": n, "sha256": file_sha256(frozen)})
        return {"admitted": True, "cycle": n, "path": str(frozen), "sha256": file_sha256(frozen)}


def worker_api():
    """Clone function globals, never monkeypatch the frozen v2 module.

    The original cached_heads deliberately retains its *old* source fingerprint
    and the byte-identical copied dataset manifest. New final bundles instead
    carry this orchestration's full source guard.
    """
    namespace = dict(v2.__dict__)
    for name, value in v2.__dict__.items():
        if isinstance(value, FunctionType) and value.__module__ == v2.__name__:
            clone = FunctionType(value.__code__, namespace, name, value.__defaults__, value.__closure__)
            clone.__kwdefaults__ = value.__kwdefaults__
            namespace[name] = clone
    namespace.update(assert_task=assert_task, assert_payload=assert_payload, own_sources=own_sources,
                     BUDGET=BUDGET, cached_heads=v2.cached_heads,
                     research=SimpleNamespace(**{**learning.__dict__, "FitBudget": AgentFitBudget}))
    return SimpleNamespace(**namespace)


def assert_payload(root, proposed):
    phase = proposed.get("phase")
    specification = proposed.get("specification")
    if (phase not in ("development", "confirmation")
            or validate_spec(specification) != specification
            or proposed["id"] != v2.payload(specification, phase)["id"]
            or proposed.get("device") != "cpu" or type(proposed.get("num_threads")) is not int
            or proposed["num_threads"] != 2):
        raise ValueError("agent-worker-payload-or-cpu2-contract-invalid")
    if phase == "development":
        admitted = admissions(root)
        if specification not in [s for a in admitted for s in a["candidates"]]:
            raise ValueError("agent-candidate-not-admitted")
    else:
        if len(decisions(root)) != 10 or len(admissions(root)) != 10:
            raise ValueError("confirmation-requires-ten-completed-cycles")
        selection = strict_json(root / "selection.json")
        pinned = [e for e in _events(root) if e["kind"] == "selection_frozen"]
        allowed = [validate_spec(s) for s in selection["confirmation_specifications"]]
        if (len(pinned) != 1 or file_sha256(root / "selection.json") != pinned[0]["details"]["sha256"]
                or proposed.get("selection_sha256") != file_sha256(root / "selection.json")
                or specification not in allowed):
            raise ValueError("confirmation-selection-not-frozen")


def run_trial(dataset, output, payload, progress):
    assert_budget(dataset.parent, live=True)
    result = worker_api().run_trial(dataset, output, payload, progress)
    assert_budget(dataset.parent, live=True)
    return result


def completed_evidence(root, specifications, phase="development"):
    state = _settled(root)
    indexed = {t["trial_id"]: t for t in state["trials"]}
    receipts = {}
    for s in specifications:
        identifier = v2.payload(s, phase)["id"]
        if identifier not in indexed or indexed[identifier]["payload"]["specification"] != s:
            raise ValueError("all-three-real-completed-evaluations-required")
        receipts[identifier] = trial_evidence(root, indexed[identifier])
    return receipts


def execute(root, specifications, timing, phase="development"):
    assert_task(root)
    current = assert_budget(root, live=True)
    if timing["deadline"] != current["deadline"]:
        raise ValueError("execution-deadline-changed")
    state = _settled(root)
    indexed = {t["trial_id"]: t for t in state["trials"]}
    selection = file_sha256(root / "selection.json") if phase == "confirmation" else None
    payloads = [v2.payload(s, phase, selection) for s in specifications]
    for p in payloads:
        assert_payload(root, {**p, "device": "cpu", "num_threads": 2})
        if p["id"] in indexed:
            trial = indexed[p["id"]]
            trial_evidence(root, trial)
            if any(trial["payload"].get(k) != v for k, v in p.items()):
                raise ValueError("completed-trial-reuse-contract-mismatch")
    results = cli.batch(root, payloads, worker_script=WORKER, deadline_at=current["deadline"], native_threads=2)
    assert_budget(root, live=True)
    completed_evidence(root, specifications, phase)
    return [results[p["id"]] for p in payloads]


def cycle(root, cycle):
    with driver_lock(BASE, root):
        task = assert_task(root)
        _other_experiments_settled(root)
        assert_budget(root, live=True)
        completed, admitted = decisions(root), admissions(root)
        if type(cycle) is not int or not 1 <= cycle <= 10 or cycle != len(completed) + 1:
            raise ValueError("next-cycle-sequence-only-no-cycle-11-or-rerun")
        if len(admitted) != cycle:
            raise ValueError("exact-next-cycle-admission-required")
        admission = admitted[-1]
        evaluated = execute(root, admission["candidates"], strict_json(root / "run-budget.json"))
        if (len(evaluated) != 3 or [r["specification"] for r in evaluated] != admission["candidates"]
                or any([f["year"] for f in r["folds"]] != [2022, 2023, 2024] for r in evaluated)):
            raise ValueError("all-three-development-evaluations-required")
        decision = {"cycle": cycle, "context_sha256": admission["context_sha256"],
                    "admission_sha256": file_sha256(root / "admissions" / f"{cycle:02d}.json"),
                    **{k: admission["proposal"][k] for k in ("hypothesis", "reasoning", "expected_outcome")},
                    "results": evaluated, "trial_evidence": completed_evidence(root, admission["candidates"]),
                    "head_cache_evidence": head_cache_evidence(root),
                    "interim_selection": select_winner(_all_development(task, completed) + evaluated),
                    "next": "finalize" if cycle == 10 else "agent_reflection_and_new_proposal"}
        assert_budget(root, live=True)
        path = root / "decisions" / f"{cycle:02d}.json"
        freeze(path, decision)
        _event(root, "cycle_evaluated", {"cycle": cycle, "sha256": file_sha256(path)})
        return decision


def finalize(root):
    with driver_lock(BASE, root):
        task = assert_task(root)
        _other_experiments_settled(root)
        completed = decisions(root)
        if len(completed) != 10 or len(admissions(root)) != 10:
            raise ValueError("exactly-ten-successfully-evaluated-cycles-required")
        timing = assert_budget(root, live=True)
        _settled(root)
        development = _all_development(task, completed)
        selected = select_winner(development)
        baseline = next(r for r in task["reference_development"] if r["id"] == "win80")
        confirmations = [validate_spec(baseline["specification"])]
        if selected["winner"] != "win80":
            confirmations.append(validate_spec(selected["specification"]))
        selection = {**selected, "specification": validate_spec(selected["specification"]),
                     "confirmation_specifications": confirmations, "criteria": CRITERIA,
                     "results_sha256": json_sha256(development), "uses_2025_2026": False,
                     "known_retrospective_only": True}
        reuse_immutable(root / "development-results.json", development)
        reuse_immutable(root / "selection.json", selection)
        pinned = [e for e in _events(root) if e["kind"] == "selection_frozen"]
        if not pinned:
            _event(root, "selection_frozen", {"sha256": file_sha256(root / "selection.json"),
                                             "development_sha256": file_sha256(root / "development-results.json")})
        elif len(pinned) != 1 or pinned[0]["details"]["sha256"] != file_sha256(root / "selection.json"):
            raise ValueError("frozen-selection-evidence-changed")
        if (root / "run-summary.json").exists():
            summary = strict_json(root / "run-summary.json")
            _assert_finished(root, summary)
            return summary
        confirmation = execute(root, confirmations, timing, phase="confirmation")
        reuse_immutable(root / "confirmation-results.json", confirmation)
        assert_budget(root, live=True)
        selected_path = root / "trials" / ("confirm-" + selected["winner"]) / "artifacts" / "2026" / "research.joblib"
        summary = {"cycles": 10, "new_specifications": 30, "new_development_folds": 90,
                   "stop_reason": "ten_cycles_completed", "selected": selected["winner"],
                   "numerical_minimum": selected["numerical_minimum"], "started": timing["started"],
                   "finished": time.time(), "fit_reservations": len(assert_budget(root)["fit_records"]),
                   "selected_research_bundle": str(selected_path),
                   "selected_research_bundle_sha256": file_sha256(selected_path),
                   "confirmation_evidence": completed_evidence(root, confirmations, "confirmation"),
                   "head_cache_evidence": head_cache_evidence(root),
                   "known_retrospective_only": True, "roi_measured": False, "production_eligible": False}
        freeze(root / "run-summary.json", summary)
        cli.store_for(root).finish_experiment(root.name)
        _event(root, "finished", {"sha256": file_sha256(root / "run-summary.json"),
                                 "confirmation_sha256": file_sha256(root / "confirmation-results.json")})
        return summary


def _assert_finished(root, summary):
    events = [e for e in _events(root) if e["kind"] == "finished"]
    if (len(events) != 1 or events[0]["details"]["sha256"] != file_sha256(root / "run-summary.json")
            or events[0]["details"]["confirmation_sha256"] != file_sha256(root / "confirmation-results.json")
            or file_sha256(Path(summary["selected_research_bundle"])) != summary["selected_research_bundle_sha256"]):
        raise ValueError("finished-summary-or-confirmation-evidence-changed")


def verify(root):
    """Offline independent verifier; never fits or changes the reference."""
    with driver_lock(BASE, root):
        task = assert_task(root)
        completed, admitted = decisions(root), admissions(root)
        contexts = evidence(root, "agent-context", "context_created")
        if len(completed) != 10 or len(admitted) != 10 or len(contexts) != 10:
            raise ValueError("incomplete-exactly-ten-cycles-required")
        state = _settled(root)
        if state["status"] != "completed":
            raise ValueError("experiment-not-completed-zero-unfinished-workers-required")
        summary = strict_json(root / "run-summary.json")
        _assert_finished(root, summary)
        timing = assert_budget(root)
        if (summary["cycles"] != 10 or summary["new_specifications"] != 30
                or summary["new_development_folds"] != 90 or summary["finished"] > timing["deadline"]
                or summary["started"] != timing["started"]
                or summary["fit_reservations"] != len(timing["fit_records"])
                or any(not timing["started"] <= t["started_at"] <= t["finished_at"] <= timing["deadline"]
                       for t in state["trials"])):
            raise ValueError("frozen-budget-or-completion-count-violation")
        if head_cache_evidence(root) != summary["head_cache_evidence"]:
            raise ValueError("final-head-cache-evidence-changed")
        indexed = {t["trial_id"]: t for t in state["trials"]}
        dev_trials = [t for t in state["trials"] if t["payload"]["phase"] == "development"]
        if len(dev_trials) != 30:
            raise ValueError("exactly-thirty-new-development-trials-required")
        development, seen_ids = list(task["reference_development"]), set()
        tried = list(task["historical_specifications"])
        seen_keys = {canonical_key(s, historical=True) for s in tried}
        prior = task["historical_development"]
        for n, (ctx, admission, decision) in enumerate(zip(contexts, admitted, completed), 1):
            context_hash = file_sha256(root / "agent-context" / f"{n:02d}.json")
            proposal = admission["proposal"]
            expected_tried = [development_view(r) | {"canonical_sha256": canonical_key(r["specification"], historical=True)}
                              for r in list(prior) + [r for d in completed[:n-1] for r in d["results"]]]
            if (ctx["cycle"] != n or ctx["development_years"] != [2022, 2023, 2024]
                    or ctx["criteria"] != CRITERIA or ctx["params"] != PARAM_SPACE
                    or ctx["defaults"] != DEFAULT_PARAMS
                    or ctx["task_sha256"] != file_sha256(root / "task-ledger.json")
                    or ctx["tried"] != expected_tried
                    or ctx["remaining"]["deadline"] != timing["deadline"]
                    or ctx["interim_selection"] != select_winner(development)
                    or ctx["previous_context_sha256"] != (
                        file_sha256(root / "agent-context" / f"{n-1:02d}.json") if n > 1 else None)
                    or set(proposal) != PROPOSAL_KEYS or proposal["cycle"] != n
                    or proposal["context_sha256"] != context_hash or admission["context_sha256"] != context_hash
                    or decision["context_sha256"] != context_hash or decision["cycle"] != n
                    or decision["admission_sha256"] != file_sha256(root / "admissions" / f"{n:02d}.json")
                    or len(admission["candidates"]) != 3 or len(decision["results"]) != 3):
                raise ValueError("context-proposal-decision-chain-recomputation-failed")
            if [validate_spec(s) for s in proposal["candidates"]] != admission["candidates"]:
                raise ValueError("admitted-normalized-proposal-mismatch")
            if admission["canonical_sha256"] != [canonical_key(s) for s in admission["candidates"]]:
                raise ValueError("admitted-canonical-evidence-mismatch")
            for name in ("hypothesis", "reasoning", "expected_outcome"):
                if not isinstance(proposal[name], str) or not proposal[name].strip() or decision[name] != proposal[name]:
                    raise ValueError("agent-reasoning-or-reflection-evidence-missing")
            for s, result in zip(admission["candidates"], decision["results"]):
                key = canonical_key(s)
                identifier = v2.payload(s)["id"]
                if (key in seen_keys or s["id"] in seen_ids
                        or s["id"] in {v["id"] for v in task["historical_specifications"]}
                        or result["specification"] != s or result["id"] != s["id"]
                        or identifier not in indexed or indexed[identifier]["result"] != result
                        or indexed[identifier]["payload"]["specification"] != s):
                    raise ValueError("duplicate-or-inconsistent-new-development-trial")
                seen_keys.add(key)
                seen_ids.add(s["id"])
                assert_payload(root, indexed[identifier]["payload"])
            if completed_evidence(root, admission["candidates"]) != decision["trial_evidence"]:
                raise ValueError("decision-pinned-trial-artifact-evidence-changed")
            checked_manifest(root, decision["head_cache_evidence"])
            development.extend(decision["results"])
            if decision["interim_selection"] != select_winner(development):
                raise ValueError("interim-selection-recomputation-failed")
        saved = strict_json(root / "development-results.json")
        selection = strict_json(root / "selection.json")
        computed = select_winner(development)
        if (saved != development or selection["results_sha256"] != json_sha256(development)
                or any(selection[k] != v for k, v in computed.items() if k != "specification")
                or selection["specification"] != validate_spec(computed["specification"])
                or selection["criteria"] != CRITERIA or selection["uses_2025_2026"] is not False
                or summary["selected"] != computed["winner"]):
            raise ValueError("frozen-selection-recomputation-failed")
        selection_events = [e for e in _events(root) if e["kind"] == "selection_frozen"]
        if (len(selection_events) != 1
                or selection_events[0]["details"]["sha256"] != file_sha256(root / "selection.json")
                or selection_events[0]["details"]["development_sha256"] != file_sha256(root / "development-results.json")):
            raise ValueError("frozen-selection-hash-evidence-changed")
        selected_event = selection_events[0]
        decision_events = [e for e in _events(root) if e["kind"] == "cycle_evaluated"]
        if selected_event["sequence"] <= decision_events[-1]["sequence"]:
            raise ValueError("selection-must-follow-all-ten-cycles")
        baseline = next(r for r in task["reference_development"] if r["id"] == "win80")
        required = [validate_spec(baseline["specification"])]
        if computed["winner"] != "win80":
            required.append(validate_spec(computed["specification"]))
        if selection["confirmation_specifications"] != required:
            raise ValueError("confirmation-must-compare-primary-win80-and-selected-only")
        expected_bundle = root / "trials" / ("confirm-" + computed["winner"]) / "artifacts" / "2026" / "research.joblib"
        if Path(summary["selected_research_bundle"]) != expected_bundle:
            raise ValueError("selected-research-bundle-must-stay-outside-production")
        confirmations = [t for t in state["trials"] if t["payload"]["phase"] == "confirmation"]
        if ({t["trial_id"] for t in confirmations} != {"confirm-" + s["id"] for s in required}
                or len(state["trials"]) != 30 + len(required)
                or any(t["started_at"] < selected_event["timestamp"] for t in confirmations)
                or strict_json(root / "confirmation-results.json") != [
                    indexed["confirm-" + s["id"]]["result"] for s in required]
                or summary["confirmation_evidence"] != completed_evidence(root, required, "confirmation")):
            raise ValueError("confirmation-result-set-or-frozen-selection-order-invalid")
        for trial in confirmations:
            assert_payload(root, trial["payload"])
        # Only after fully recomputing the frozen development choice do we open
        # confirmation cohorts. All saved predictions (not just final ones) replay.
        dev_target, dev_x, _ = load_data(root / "dataset")
        races, checked = {}, []
        receipt = strict_json(root / "reference-receipt.json")
        reference = Path(receipt["root"])
        ref_state = readonly_state(reference)
        for result in task["reference_development"]:
            trial = next(t for t in ref_state["trials"] if t["trial_id"] == "dev-" + result["id"])
            if trial["result"] != result:
                raise ValueError("reference-reused-result-changed")
            for year, record in audit_predictions(reference, trial, dev_target, dev_x, receipt["training_sources"]).items():
                races[(trial["trial_id"], year)] = record
                checked.append({"trial": trial["trial_id"], "year": year, "reused": True})
        full_target, full_x, _ = load_data(root / "dataset", confirmation=True)
        for trial in state["trials"]:
            confirmation = trial["payload"]["phase"] == "confirmation"
            target, x = (full_target, full_x) if confirmation else (dev_target, dev_x)
            for year, record in audit_predictions(root, trial, target, x, task["sources"]).items():
                races[(trial["trial_id"], year)] = record
                checked.append({"trial": trial["trial_id"], "year": year, "reused": False})
        head_receipts = verify_heads(root, receipt["training_sources"])
        reused_directories = {item["directory"] for item in task["reused_heads"]}
        new_heads = [item for item in head_receipts if item["directory"] not in reused_directories]
        expected_head_fits = 0
        for item in new_heads:
            heads = joblib.load(root / "head-cache" / item["directory"] / "heads.joblib")
            expected_head_fits += 2 * (heads["pace"]["status"] == "trained") + (heads["position"]["status"] == "trained")
        counts = {}
        for item in timing["fit_records"]:
            counts[item["name"]] = counts.get(item["name"], 0) + 1
        expected_counts = {"lightgbm": 90 + len(required) + expected_head_fits,
                           "temperature-calibration": 90 + len(required)}
        if new_heads:
            expected_counts["pace-condition-thresholds"] = len(new_heads)
        if counts != expected_counts:
            raise ValueError("comprehensive-physical-fit-accounting-mismatch")
        comparisons = {str(year): paired_bootstrap(races[("dev-win80", year)],
                                                   races[("dev-" + computed["winner"], year)])
                       for year in (2022, 2023, 2024)}
        final = paired_bootstrap(races[("confirm-win80", 2026)], races[("confirm-" + computed["winner"], 2026)])
        comparisons["2026"] = final
        base_final = indexed["confirm-win80"]["result"]["folds"][0]
        chosen_final = indexed["confirm-" + computed["winner"]]["result"]["folds"][0]
        improved = (computed["winner"] != "win80" and final["sufficient_dates"]
                    and final["nll_improvement_ci95"][0] > 0
                    and chosen_final["metrics"]["brier"] <= base_final["metrics"]["brier"]
                    and chosen_final["metrics"]["top1"] >= base_final["metrics"]["top1"] - .005
                    and not chosen_final["boundary"])
        # Assert source, production, original inputs and snapshot once more at
        # the end; verification itself cannot paper over concurrent changes.
        assert_task(root)
        verification = {"verified": True, "model_improved": bool(improved), "selected": computed["winner"],
                        "numerical_minimum": computed["numerical_minimum"], "comparisons_vs_win80": comparisons,
                        "checked_folds": checked, "cycles": 10, "new_specifications": 30,
                        "new_development_folds": 90, "fit_reservations": len(timing["fit_records"]),
                        "all_predictions_replayed": True, "current_outcome_injection_ignored": True,
                        "source_sha256": json_sha256(task["sources"]),
                        "final_snapshot_sha256": file_sha256(Path(task["snapshot"]["path"])),
                        "selected_research_bundle": summary["selected_research_bundle"],
                        "selected_research_bundle_sha256": summary["selected_research_bundle_sha256"],
                        "zero_unfinished_workers": True, "production_unchanged": True,
                        "known_retrospective_only": True, "future_generalization_proven": False,
                        "roi_measured": False, "production_eligible": False}
        reuse_immutable(root / "verification.json", verification)
        return verification


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "approve", "context", "admit", "cycle", "finalize",
                                         "verify", "worker", "status"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--reference", type=Path, default=REFERENCE)
    parser.add_argument("--proposal", type=Path)
    parser.add_argument("--cycle", type=int)
    parser.add_argument("--device", choices=("cpu",), default="cpu")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.action == "prepare":
        result = prepare(root, args.reference)
    elif args.action == "approve":
        result = approve(root)
    elif args.action == "context":
        result = context(root, args.cycle)
    elif args.action == "admit":
        if args.proposal is None:
            parser.error("--proposal is required for admit")
        result = admit(root, args.proposal)
    elif args.action == "cycle":
        if args.cycle is None:
            parser.error("--cycle is required for cycle")
        result = cycle(root, args.cycle)
    elif args.action == "finalize":
        result = finalize(root)
    elif args.action == "verify":
        result = verify(root)
    elif args.action == "worker":
        return cli.worker(root, args.device, trial_runner=run_trial)
    else:
        result = queue_state(root)
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False), flush=True)
    return 0
