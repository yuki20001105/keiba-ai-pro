"""Finite, locally approved pace research with independent prediction checks."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import shutil
import time

import joblib
import numpy as np
import pandas as pd

from experiments import cli
from experiments.common import (REPO_ROOT, active_pointer_hash, file_sha256, json_sha256,
                                read_json, source_manifest, write_json)
from experiments.goal_loop import _event, _events, _other_experiments_settled
from experiments.orchestrator import driver_lock
from training import db_context_features as context
from training import db_context_fit as control
from training import pace_forecast as legacy
from training import pace_research as research

BASE = REPO_ROOT / "reports" / "generated" / "model-experiments"
OLD = REPO_ROOT / "reports" / "generated" / "pace-stack" / "20261114-v1"
BUDGET = {"maximum_steps": 10, "maximum_configurations": 24, "maximum_fits": 360,
          "seconds": 21600, "workers": 1, "native_threads": 2, "stagnation_steps": 3}
CRITERIA = {"primary": "equal-year mean of macro-race NLL", "minimum_nll_improvement": .001,
            "maximum_annual_nll_regression": .005, "maximum_annual_brier_regression": .002,
            "maximum_annual_top1_regression": .005, "bootstrap_repetitions": 1000,
            "minimum_bootstrap_dates": 20, "promotion_eligible": False}
TARGET_COLUMNS = ["race_id", "race_date", "horse_id", "horse_number", "num_horses",
                  "finish", "time_seconds", "distance", "surface", "venue", "field_condition",
                  "_ctx_history_corners"]


def freeze(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)


def reuse_immutable(path, value):
    if path.exists():
        if read_json(path) != value:
            raise ValueError("research-immutable-evidence-mismatch")
    else:
        freeze(path, value)


def spec(identifier, *, groups=None, objective="speed", head="none", mode="legacy",
         fields=False, interactions=False, time_feature=False):
    return {"id": identifier, "groups": groups or ["baseline46"], "objective": objective,
            "head": head, "mode": mode, "fields": fields, "interactions": interactions,
            "time_feature": time_feature, "params": {}}


def initial_stages():
    all_groups = list(context.FEATURE_GROUPS)
    controls = [spec("speed46"), spec("speed80", groups=all_groups),
                spec("legacy90", groups=all_groups, head="both")]
    objective_and_groups = [spec("win46", objective="win"), spec("win80", objective="win", groups=all_groups),
                           spec("win90", objective="win", groups=all_groups, head="both")]
    objective_and_groups += [spec("group-" + group, groups=["baseline46", group])
                             for group in all_groups if group != "baseline46"]
    hierarchy = [
        spec("hier-pace", head="pace", mode="hier", time_feature=True),
        spec("hier-both", head="both", mode="hier", time_feature=True),
        spec("condition-pace", head="pace", mode="condition", time_feature=True),
        spec("field-speed46", head="both", mode="hier_field", fields=True, time_feature=True),
        spec("field-win80", objective="win", groups=all_groups, head="both",
             mode="hier_field", fields=True, time_feature=True),
        spec("interact-win46", objective="win", head="both", mode="hier_field",
             fields=True, interactions=True, time_feature=True),
        spec("interact-win80", objective="win", groups=all_groups, head="both", mode="hier_field",
             fields=True, interactions=True, time_feature=True),
    ]
    ablations = [spec("legacy46-" + head, head=head) for head in ("pace", "position", "both")]
    return [controls, objective_and_groups, hierarchy, ablations]


def mean_nll(result):
    return float(np.mean([fold["metrics"]["nll"] for fold in result["folds"]]))


def select_winner(results):
    if not results or results[0]["id"] != "speed46":
        raise ValueError("development-baseline-required")
    for result in results:
        if sorted(fold["year"] for fold in result["folds"]) != [2022, 2023, 2024]:
            raise ValueError("development-years-only")
        if any(not all(np.isfinite(fold["metrics"][name]) for name in ("nll", "brier", "top1"))
               for fold in result["folds"]):
            raise ValueError("development-missing-finite-metrics")
    baseline = results[0]
    eligible = [baseline]
    for result in results[1:]:
        safe = True
        by_year = {fold["year"]: fold for fold in result["folds"]}
        for base in baseline["folds"]:
            fold = by_year[base["year"]]
            b, c = base["metrics"], fold["metrics"]
            safe &= (not fold["boundary"]
                     and c["nll"] <= b["nll"] + CRITERIA["maximum_annual_nll_regression"]
                     and c["brier"] <= b["brier"] + CRITERIA["maximum_annual_brier_regression"]
                     and c["top1"] >= b["top1"] - CRITERIA["maximum_annual_top1_regression"])
        if safe and mean_nll(result) <= mean_nll(baseline) - CRITERIA["minimum_nll_improvement"]:
            eligible.append(result)
    return min(eligible, key=lambda value: (mean_nll(value), value["id"]))


def paired_bootstrap(baseline, candidate):
    columns = ["race_id", "race_date"]
    if (baseline.duplicated("race_id").any() or candidate.duplicated("race_id").any()
            or not baseline[columns].sort_values(columns).reset_index(drop=True).equals(
                candidate[columns].sort_values(columns).reset_index(drop=True))):
        raise ValueError("paired-bootstrap-cohort-mismatch")
    paired = baseline.merge(candidate, on=columns, suffixes=("_base", "_candidate"), validate="one_to_one")
    paired["improvement"] = paired.nll_base - paired.nll_candidate
    grouped = paired.groupby("race_date").improvement.agg(["sum", "count"])
    rng = np.random.default_rng(42)
    draws = rng.integers(0, len(grouped), size=(CRITERIA["bootstrap_repetitions"], len(grouped)))
    sampled = grouped["sum"].to_numpy()[draws].sum(axis=1) / grouped["count"].to_numpy()[draws].sum(axis=1)
    return {"paired_dates": len(grouped), "races": len(paired),
            "nll_improvement": float(paired.improvement.mean()),
            "nll_improvement_ci95": np.quantile(sampled, [.025, .975]).tolist(),
            "sufficient_dates": len(grouped) >= CRITERIA["minimum_bootstrap_dates"],
            "selection_multiplicity_corrected": False, "unseen_future_evidence": False}


def guards():
    pointer = read_json(REPO_ROOT / "python-api" / "models" / ".active_model.json")
    artifact = REPO_ROOT / "python-api" / "models" / (pointer["model_id"] + ".joblib")
    paths = [artifact, REPO_ROOT / "config" / "model_acceptance_contract.v1.json",
             REPO_ROOT / "config" / "phase3n_staking_payout_policy.v1.json"]
    return {"active_pointer_sha256": active_pointer_hash(),
            "files": {str(path.relative_to(REPO_ROOT)): file_sha256(path) for path in paths}}


def own_sources():
    return {**research.source_hashes(),
            "scripts.run_pace_research": file_sha256(REPO_ROOT / "scripts" / "run_pace_research.py"),
            "scripts.train_pace_stack": file_sha256(REPO_ROOT / "scripts" / "train_pace_stack.py")}


def prepare(root):
    if root.parent != BASE.resolve() or root.exists():
        raise ValueError("fresh-isolated-campaign-root-required")
    _other_experiments_settled(root)
    receipt = read_json(OLD / "input-receipt.json")
    snapshot = OLD / "snapshot.sqlite"
    if file_sha256(snapshot) != receipt["snapshot_sha256"]:
        raise ValueError("immutable-snapshot-changed")
    entry = OLD / "inputs" / "context-v1" / receipt["cache_key_sha256"]
    pointer = read_json(entry / "current.json")
    if pointer["manifest_sha256"] != receipt["cache_manifest_sha256"]:
        raise ValueError("prior-input-receipt-mismatch")
    cache_manifest = read_json(entry / pointer["generation"] / "manifest.json")
    from training.db_context_cache import _read
    target, features, manifest, _ = _read(entry.parent, cache_manifest["key"], control)
    if manifest["key"]["snapshot_sha256"] != receipt["snapshot_sha256"]:
        raise ValueError("cache-snapshot-lineage-mismatch")
    target, corners = context._attach_history_corners(snapshot, target)
    from importlib.util import module_from_spec, spec_from_file_location
    module_spec = spec_from_file_location("pace_research_labels", REPO_ROOT / "scripts" / "train_pace_stack.py")
    runner = module_from_spec(module_spec)
    module_spec.loader.exec_module(runner)
    laps, lap_receipt = runner._labels(OLD / "recovered-laps.json", target)
    root.mkdir(parents=True)
    dataset = root / "dataset"
    dataset.mkdir()
    packed = pd.concat([target.loc[:, TARGET_COLUMNS], features.add_prefix("feature__")], axis=1)
    development = packed.race_date.le("20241231")
    packed.loc[development].to_parquet(dataset / "dataset.parquet", index=False)
    packed.loc[~development].to_parquet(dataset / "confirmation.parquet", index=False)
    shutil.copy2(OLD / "recovered-laps.json", dataset / "lap-observations.json")
    data_manifest = {"schema": research.SCHEMA, "development_years": [2022, 2023, 2024],
                     "confirmation_years": [2025, 2026], "confirmation_is_unseen": False,
                     "features": list(context.FEATURE_COLUMNS), "teacher_columns": TARGET_COLUMNS,
                     "snapshot": {"path": str(snapshot), "sha256": receipt["snapshot_sha256"]},
                     "files": {name: file_sha256(dataset / name) for name in
                               ("dataset.parquet", "confirmation.parquet", "lap-observations.json")},
                     "prior_cache_manifest_sha256": pointer["manifest_sha256"],
                     "feature_audit": manifest["feature_audit"], "corners": corners, "laps": lap_receipt}
    freeze(dataset / "dataset_manifest.json", data_manifest)
    contract = {"capacity": {"cpu": 1, "gpu": 0}, "reserve_ram_mb": 2048,
                "estimated_worker_memory_mb": 4096, "source_manifest": source_manifest(),
                "active_pointer_sha256": active_pointer_hash(),
                "dataset_manifest_sha256": file_sha256(dataset / "dataset_manifest.json"),
                "dataset_sha256": data_manifest["files"]["dataset.parquet"]}
    task = {"schema": research.SCHEMA, "created_at": datetime.now().isoformat(),
            "purpose": "Diagnose pace degradation and select a stable research model by chronological race NLL",
            "budget": BUDGET, "criteria": CRITERIA, "sources": own_sources(),
            "guards": guards(), "execution_contract": contract, "initial_stages": initial_stages(),
            "adaptive_stages": ["fixed 0.10/0.25 blend of best development win candidate with speed46",
                                "one regularized variant of the development winner: leaves15/L2=5"],
            "folds": [{"train_to": year - 2, "calibration": year - 1, "validation": year}
                      for year in (2022, 2023, 2024)],
            "fit_accounting": "Every LightGBM fit, temperature fit, speed normalization and head threshold fit is reserved",
            "selection_uses_2025_or_2026": False, "network_requests": 0,
            "production_changes": False, "known_reused_confirmation": True}
    freeze(root / "task-ledger.json", task)
    store = cli.store_for(root)
    store.create_experiment(root.name, contract)
    archive = root / "sources"
    source_paths = set(contract["source_manifest"])
    source_paths.update(str(path.relative_to(REPO_ROOT))
                        for path in (REPO_ROOT / "python-api" / "training").rglob("*.py"))
    source_paths.add(str(Path("python-api") / "forward_adapters" / "feature_state.py"))
    source_paths.update(str(Path(module.__file__).resolve().relative_to(REPO_ROOT))
                        for module in (research, legacy, context, control))
    for path in source_paths:
        source = REPO_ROOT / path
        destination = archive / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    for name in ("run_pace_research.py", "train_pace_stack.py"):
        destination = archive / "scripts" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / "scripts" / name, destination)
    dev_x = features.loc[development]
    diagnostics = {"development_only": True, "date_to": str(target.loc[development].race_date.max()),
                   "races": int(target.loc[development].race_id.nunique()),
                   "runners": int(development.sum()), "missing": dev_x.isna().mean().to_dict(),
                   "constant": [name for name in dev_x if dev_x[name].nunique(dropna=False) <= 1],
                   "pace_labels_by_year": laps.loc[laps.race_date.le("20241231")].race_date.str[:4].value_counts().sort_index().to_dict()}
    freeze(root / "diagnostics.json", diagnostics)
    _event(root, "prepared", {"task_sha256": file_sha256(root / "task-ledger.json")})
    return {"prepared": True, "root": str(root), "task_sha256": file_sha256(root / "task-ledger.json"),
            "budget": BUDGET}


def approve(root):
    freeze(root / "approval.json", {"task_sha256": file_sha256(root / "task-ledger.json"),
                                   "scope": "local-research-only", "approved_at": time.time(),
                                   "authorization": "user explicitly requested execution of the bounded research plan"})


def assert_approval(root):
    approval = read_json(root / "approval.json")
    if (approval["task_sha256"] != file_sha256(root / "task-ledger.json")
            or approval["scope"] != "local-research-only"):
        raise ValueError("research-approval-contract-mismatch")


def assert_task(root):
    assert_approval(root)
    task = read_json(root / "task-ledger.json")
    if (task["schema"] != research.SCHEMA or task["budget"] != BUDGET or task["criteria"] != CRITERIA
            or task["initial_stages"] != initial_stages() or task["sources"] != own_sources()
            or task["guards"] != guards()):
        raise ValueError("research-task-or-production-guard-changed")
    cli.verify_contract(root, task["execution_contract"])
    stored = cli.store_for(root).snapshot(root.name)
    if stored["contract"] != task["execution_contract"]:
        raise ValueError("research-queue-contract-mismatch")
    manifest = read_json(root / "dataset" / "dataset_manifest.json")
    for name, digest in manifest["files"].items():
        if file_sha256(root / "dataset" / name) != digest:
            raise ValueError("research-input-file-changed")
    _events(root)
    return task


def load_data(dataset, confirmation=False):
    packed = pd.read_parquet(dataset / "dataset.parquet")
    if confirmation:
        packed = pd.concat([packed, pd.read_parquet(dataset / "confirmation.parquet")], ignore_index=True)
    elif packed.race_date.gt("20241231").any():
        raise ValueError("development-future-labels-rejected")
    target = packed.loc[:, TARGET_COLUMNS].copy()
    target["_ctx_history_corners"] = target["_ctx_history_corners"].map(
        lambda value: tuple(value.tolist()) if isinstance(value, np.ndarray) else value)
    x = packed.loc[:, ["feature__" + name for name in context.FEATURE_COLUMNS]].copy()
    x.columns = context.FEATURE_COLUMNS
    target, x = control.prepare(target, x)
    observed = read_json(dataset / "lap-observations.json")
    laps = pd.DataFrame(observed["observations"])
    laps = laps.loc[laps.race_id.isin(target.race_id)].copy()
    return target, x, laps


def cached_heads(root, target, features, laps, mode, cutoff, budget):
    fingerprint = {"mode": mode, "cutoff": cutoff,
                   "dataset_manifest": file_sha256(root / "dataset" / "dataset_manifest.json"),
                   "sources": own_sources()}
    directory = root / "head-cache" / json_sha256(fingerprint)
    artifact = directory / "heads.joblib"
    if directory.exists():
        receipt = read_json(directory / "receipt.json")
        if receipt["identity"] != fingerprint or file_sha256(artifact) != receipt["artifact_sha256"]:
            raise ValueError("head-cache-integrity-failed")
        return joblib.load(artifact)
    train_mask = target.race_date.le(cutoff)
    train = target.loc[train_mask]
    if train.empty or str(train.race_date.max()) != cutoff:
        raise ValueError("head-training-cutoff-mismatch")
    teacher = laps.loc[laps.race_id.isin(train.race_id)]
    budget.reserve("pace-condition-thresholds")
    heads = research.head_api(mode, budget).fit_heads(
        train, research.head_inputs(train, features.loc[train_mask], mode), pace_labels=teacher)
    directory.mkdir(parents=True)
    joblib.dump(heads, artifact, compress=3)
    freeze(directory / "receipt.json", {"identity": fingerprint, "artifact_sha256": file_sha256(artifact),
                                       "training_races": int(train.race_id.nunique()),
                                       "pace_status": heads["pace"]["status"],
                                       "position_status": heads["position"]["status"]})
    return heads


def fold_forecasts(root, target, features, laps, train_to, mode, budget):
    train = target.race_date.le(f"{train_to}1231")
    output = pd.DataFrame(np.nan, index=target.index,
                          columns=list(legacy.FORECAST_COLUMNS) + list(research.EXTRA_FORECAST))
    output[["pace_model_available", "position_model_available"]] = 0.
    audit = []
    for year in sorted(target.loc[train].race_date.str[:4].unique()):
        predicted = train & target.race_date.str.startswith(year)
        previous = target.race_date.lt(str(target.loc[predicted].race_date.min()))
        if not previous.any():
            continue
        cutoff = str(target.loc[previous].race_date.max())
        heads = cached_heads(root, target, features, laps, mode, cutoff, budget)
        output.loc[predicted] = research.predict_head_features(
            heads, target.loc[predicted], features.loc[predicted], mode)
        audit.append({"prediction_date_from": str(target.loc[predicted].race_date.min()),
                      "training_date_to": cutoff})
    cutoff = str(target.loc[train].race_date.max())
    heads = cached_heads(root, target, features, laps, mode, cutoff, budget)
    future = ~train
    if future.any():
        output.loc[future] = research.predict_head_features(heads, target.loc[future], features.loc[future], mode)
        audit.append({"prediction_date_from": str(target.loc[future].race_date.min()), "training_date_to": cutoff})
    if any(record["training_date_to"] >= record["prediction_date_from"] for record in audit):
        raise ValueError("research-oof-future-leakage")
    return output, heads, audit


def feature_matrix(target, x, forecast, specification):
    return research.assemble(
        x, forecast, groups=specification["groups"], head=specification["head"],
        fields=research.field_features(target, x) if specification["fields"] else None,
        interactions=specification["interactions"], time_feature=specification["time_feature"])


def trimmed_fit(fitted):
    excluded = {"scores", "cal_scores", "probabilities", "race_records"}
    return {key: value for key, value in fitted.items() if key not in excluded}


def predict_bundle(bundle, target, x):
    specification = bundle["specification"]
    forecast = (research.predict_head_features(bundle["heads"], target, x, specification["mode"])
                if specification["head"] != "none" else None)
    matrix = feature_matrix(target, x, forecast, specification)
    fitted = bundle["fit"]
    scores = np.asarray(fitted["model"].predict(fitted["encoder"].transform(matrix), num_threads=2))
    if "blend_weight" in specification:
        auxiliary = bundle["auxiliary"]
        base_x = x.loc[:, context.FEATURE_GROUPS["baseline46"]]
        base_scores = auxiliary["model"].predict(auxiliary["encoder"].transform(base_x), num_threads=2)
        weight = specification["blend_weight"]
        scores = (1. - weight) * np.asarray(base_scores) / .25 + weight * scores
    probability = research.race_probabilities(scores, target.race_id, fitted["calibration"]["temperature"])
    return scores, probability


def run_trial(dataset, output, payload, progress):
    root = dataset.parent
    assert_task(root)
    if payload["num_threads"] != 2 or payload["device"] != "cpu":
        raise ValueError("research-cpu-budget-mismatch")
    phase = payload["phase"]
    if phase not in {"development", "confirmation"}:
        raise ValueError("research-phase-not-whitelisted")
    assert_payload(root, payload)
    if phase == "confirmation":
        if payload["selection_sha256"] != file_sha256(root / "selection.json"):
            raise ValueError("research-frozen-selection-mismatch")
    specification = payload["specification"]
    target, x, laps = load_data(dataset, confirmation=phase == "confirmation")
    timing = read_json(root / "run-budget.json")
    budget = research.FitBudget(BUDGET["maximum_fits"], timing["deadline"],
                                path=root / "fit-budget.json")
    output.mkdir(parents=True)
    years = (2026,) if phase == "confirmation" else (2022, 2023, 2024)
    folds = []
    for year in years:
        progress(f"{specification['id']}: fold {year}", int(5 + 80 * len(folds) / len(years)))
        train_to = 2024 if phase == "confirmation" else year - 2
        calibration_year = 2025 if phase == "confirmation" else year - 1
        train_mask = target.race_date.le(f"{train_to}1231")
        cal_mask = target.race_date.str.startswith(str(calibration_year))
        val_mask = target.race_date.str.startswith(str(year))
        cohort = train_mask | cal_mask | val_mask
        t, features = target.loc[cohort], x.loc[cohort]
        if specification["head"] != "none":
            forecast, heads, audit = fold_forecasts(root, t, features, laps, train_to,
                                                    specification["mode"], budget)
        else:
            forecast, heads, audit = None, None, []
        matrix = feature_matrix(t, features, forecast, specification)
        tr, ca, va = train_mask.loc[cohort], cal_mask.loc[cohort], val_mask.loc[cohort]
        fitted = research.fit_final(t.loc[tr], matrix.loc[tr], t.loc[ca], matrix.loc[ca],
                                    t.loc[va], matrix.loc[va], objective=specification["objective"],
                                    params=specification["params"], budget=budget)
        auxiliary = None
        if "blend_weight" in specification:
            base_matrix = features.loc[:, context.FEATURE_GROUPS["baseline46"]]
            auxiliary = research.fit_final(t.loc[tr], base_matrix.loc[tr], t.loc[ca], base_matrix.loc[ca],
                                           t.loc[va], base_matrix.loc[va], objective="speed", budget=budget)
            weight = specification["blend_weight"]
            fitted["cal_scores"] = (1. - weight) * auxiliary["cal_scores"] / .25 + weight * fitted["cal_scores"]
            fitted["scores"] = (1. - weight) * auxiliary["scores"] / .25 + weight * fitted["scores"]
            fitted["calibration"] = research.calibrate(t.loc[ca], fitted["cal_scores"], budget)
            fitted["probabilities"] = research.race_probabilities(
                fitted["scores"], t.loc[va].race_id, fitted["calibration"]["temperature"])
            fitted["race_records"] = research.race_records(t.loc[va], fitted["probabilities"])
            fitted["metrics"] = research.summary_metrics(fitted["race_records"])
        bundle = {"schema": research.SCHEMA, "specification": specification, "heads": heads,
                  "fit": trimmed_fit(fitted), "sources": own_sources(), "reference_only": True,
                  "production_eligible": False, "training_date_to": str(t.loc[tr].race_date.max())}
        if auxiliary is not None:
            bundle["auxiliary"] = trimmed_fit(auxiliary)
        directory = output / str(year)
        directory.mkdir()
        joblib.dump(bundle, directory / "research.joblib", compress=3)
        restored = joblib.load(directory / "research.joblib")
        scores, probability = predict_bundle(restored, t.loc[va], features.loc[va])
        if not (np.array_equal(scores, fitted["scores"])
                and np.array_equal(probability, fitted["probabilities"])):
            raise ValueError("research-serialized-prediction-parity-failed")
        predictions = t.loc[va, ["race_id", "race_date", "horse_number", "finish"]].copy()
        predictions["score"] = scores
        predictions["probability"] = probability
        predictions.to_parquet(directory / "predictions.parquet", index=False)
        fitted["race_records"].to_parquet(directory / "race-records.parquet", index=False)
        importance = pd.DataFrame({"feature": matrix.columns,
                                   "gain": fitted["model"].feature_importance("gain"),
                                   "split": fitted["model"].feature_importance("split")})
        importance.sort_values("gain", ascending=False).to_csv(directory / "importance.csv", index=False)
        head_metrics = None
        if heads is not None:
            heldout_laps = laps.loc[laps.race_id.isin(t.loc[va].race_id)]
            head_metrics = research.head_api(specification["mode"]).evaluate_heads(
                heads, t.loc[va], research.head_inputs(t.loc[va], features.loc[va], specification["mode"]),
                pace_labels=heldout_laps)
        record = {"year": year, "metrics": fitted["metrics"], "features": len(matrix.columns),
                  "boundary": fitted["calibration"]["boundary_reached"], "calibration": fitted["calibration"],
                  "oof_audit": audit, "head_metrics": head_metrics, "serialized_prediction_parity": True,
                  "forecast_availability": None if forecast is None else {
                      part: {name: float(forecast.loc[mask, name].mean())
                             for name in ("pace_model_available", "position_model_available")}
                      for part, mask in (("TRAIN", tr), ("CAL", ca), ("VALID", va))}}
        freeze(directory / "metrics.json", record)
        folds.append(record)
    assert_task(root)
    return {"id": specification["id"], "phase": phase, "specification": specification, "folds": folds,
            "physical_fits_reserved_total": len(budget.records), "reference_only": True,
            "production_eligible": False}


def payload(specification, phase="development", selection=None):
    return {"id": ("confirm-" if phase == "confirmation" else "dev-") + specification["id"],
            "phase": phase, "specification": specification, "selection_sha256": selection}


def assert_payload(root, proposed):
    specification = proposed["specification"]
    phase = proposed["phase"]
    if proposed["id"] != payload(specification, phase, proposed.get("selection_sha256"))["id"]:
        raise ValueError("research-payload-identity-mismatch")
    if phase == "confirmation":
        selection = read_json(root / "selection.json")
        allowed = initial_stages()[0] + [selection["specification"]]
        if specification not in allowed:
            raise ValueError("research-confirmation-not-frozen")
        return
    admitted = []
    for event in _events(root):
        if event["kind"] == "step_started":
            details = event["details"]
            path = root / "attempts" / f"{details['step']:02d}.json"
            if file_sha256(path) != details["attempt_sha256"]:
                raise ValueError("research-attempt-evidence-changed")
            admitted.extend(read_json(path)["specifications"])
    if specification not in admitted:
        raise ValueError("research-candidate-not-admitted")


def validate_reuse(root, trial):
    if trial["status"] != "completed":
        raise ValueError("research-trial-needs-investigation-no-retry")
    directory = root / "trials" / trial["trial_id"]
    manifest = read_json(directory / "artifact_manifest.json")
    for relative, digest in manifest.items():
        if file_sha256(directory / relative) != digest:
            raise ValueError("research-trial-artifact-changed")
    if read_json(directory / "result.json") != trial["result"]:
        raise ValueError("research-trial-result-ledger-mismatch")


def execute(root, specifications, timing, phase="development"):
    assert_task(root)
    if phase == "confirmation":
        selection = file_sha256(root / "selection.json")
    else:
        selection = None
    payloads = [payload(item, phase, selection) for item in specifications]
    snapshot = cli.store_for(root).snapshot(root.name)
    indexed = {trial["trial_id"]: trial for trial in snapshot["trials"]}
    for item in payloads:
        if item["id"] in indexed:
            trial = indexed[item["id"]]
            validate_reuse(root, trial)
            if any(trial["payload"].get(key) != value for key, value in item.items()):
                raise ValueError("research-reuse-payload-mismatch")
    result = cli.batch(root, payloads, worker_script=REPO_ROOT / "scripts" / "run_pace_research.py",
                       deadline_at=timing["deadline"], native_threads=2)
    return [result[item["id"]] for item in payloads]


def run(root):
    with driver_lock(BASE, root):
        task = assert_task(root)
        _other_experiments_settled(root)
        manifest = read_json(root / "dataset" / "dataset_manifest.json")
        if file_sha256(Path(manifest["snapshot"]["path"])) != manifest["snapshot"]["sha256"]:
            raise ValueError("research-snapshot-changed")
        if not (root / "run-budget.json").exists():
            started = time.time()
            freeze(root / "run-budget.json", {"started": started, "deadline": started + BUDGET["seconds"]})
        timing = read_json(root / "run-budget.json")
        if (root / "run-summary.json").exists():
            raise ValueError("completed-research-not-rerun")
        results, attempts, stagnation, best_score = [], [], 0, np.inf
        stages = initial_stages()
        stop = "plan_completed"
        for index in range(BUDGET["maximum_steps"]):
            if index < len(stages):
                specifications = stages[index]
            elif index == 4:
                win_candidates = [item for item in results if item["specification"]["objective"] == "win"]
                component = min(win_candidates, key=mean_nll)
                specifications = []
                for weight in (.10, .25):
                    proposal = deepcopy(component["specification"])
                    proposal.update(id=f"blend-{int(weight * 100)}", blend_weight=weight)
                    specifications.append(proposal)
            elif index == 5:
                proposal = deepcopy(select_winner(results)["specification"])
                proposal.update(id="regularized-winner", params={"num_leaves": 15, "lambda_l2": 5.})
                specifications = [proposal]
            else:
                break
            if len(attempts) + len(specifications) > BUDGET["maximum_configurations"]:
                stop = "configuration_budget"
                break
            reuse_immutable(root / "attempts" / f"{index:02d}.json", {"step": index, "specifications": specifications,
                                                                   "prior_results_sha256": json_sha256(results)})
            _event(root, "step_started", {"step": index, "candidates": [item["id"] for item in specifications],
                                          "attempt_sha256": file_sha256(root / "attempts" / f"{index:02d}.json")})
            evaluated = execute(root, specifications, timing)
            results.extend(evaluated)
            attempts.extend(item["id"] for item in specifications)
            winner = select_winner(results)
            score = mean_nll(winner)
            improved = score < best_score - CRITERIA["minimum_nll_improvement"]
            stagnation = 0 if improved else stagnation + 1
            best_score = min(best_score, score)
            decision = {"step": index, "best": winner["id"], "mean_nll": score, "improved": bool(improved),
                        "stagnation": stagnation, "results": evaluated,
                        "next": "stop" if stagnation >= BUDGET["stagnation_steps"] else "next_whitelisted_hypothesis"}
            reuse_immutable(root / "decisions" / f"{index:02d}.json", decision)
            _event(root, "step_evaluated", {"step": index, "best": winner["id"], "mean_nll": score})
            print(json.dumps({key: value for key, value in decision.items() if key != "results"}), flush=True)
            if stagnation >= BUDGET["stagnation_steps"]:
                stop = "stagnation"
                break
        winner = select_winner(results)
        selection = {"winner": winner["id"], "specification": winner["specification"],
                     "development_mean_nll": mean_nll(winner), "results_sha256": json_sha256(results),
                     "criteria": CRITERIA, "selected_at": time.time(), "uses_2025_2026": False}
        reuse_immutable(root / "development-results.json", results)
        if (root / "selection.json").exists():
            saved_selection = read_json(root / "selection.json")
            selection["selected_at"] = saved_selection["selected_at"]
        reuse_immutable(root / "selection.json", selection)
        _event(root, "selection_frozen", {"selection_sha256": file_sha256(root / "selection.json")})
        confirmations = [results[0]["specification"], results[1]["specification"], results[2]["specification"]]
        if winner["id"] not in {item["id"] for item in confirmations}:
            confirmations.append(winner["specification"])
        confirmation = execute(root, confirmations, timing, phase="confirmation")
        reuse_immutable(root / "confirmation-results.json", confirmation)
        summary = {"stop_reason": stop, "configurations": len(attempts), "steps": len(list((root / "decisions").glob("*.json"))),
                   "selected": winner["id"], "started": timing["started"], "finished": time.time(),
                   "fits_reserved": len(read_json(root / "fit-budget.json")["records"]),
                   "production_changed": False, "confirmation_is_unseen": False}
        freeze(root / "run-summary.json", summary)
        cli.store_for(root).finish_experiment(root.name)
        _event(root, "finished", summary)
        return summary


def verify(root):
    task = assert_task(root)
    summary = read_json(root / "run-summary.json")
    timing = read_json(root / "run-budget.json")
    fit_records = read_json(root / "fit-budget.json")
    if (summary["configurations"] > BUDGET["maximum_configurations"]
            or summary["steps"] > BUDGET["maximum_steps"] or len(fit_records["records"]) > BUDGET["maximum_fits"]
            or any(item["started_at"] >= timing["deadline"] for item in fit_records["records"])
            or summary["finished"] > timing["deadline"]):
        raise ValueError("research-budget-violation")
    state = cli.store_for(root).snapshot(root.name)
    if state["status"] != "completed":
        raise ValueError("research-experiment-not-settled")
    development = read_json(root / "development-results.json")
    selection = read_json(root / "selection.json")
    if (select_winner(development)["id"] != selection["winner"]
            or json_sha256(development) != selection["results_sha256"]):
        raise ValueError("research-selection-recomputation-failed")
    dev_target, dev_x, _ = load_data(root / "dataset")
    full_target, full_x, _ = load_data(root / "dataset", confirmation=True)
    verified, races = [], {}
    injection_cases = []
    for trial in state["trials"]:
        validate_reuse(root, trial)
        specification = trial["payload"]["specification"]
        confirmation = trial["payload"]["phase"] == "confirmation"
        target, x = (full_target, full_x) if confirmation else (dev_target, dev_x)
        for fold in trial["result"]["folds"]:
            year = fold["year"]
            subset = target.race_date.str.startswith(str(year))
            t, features = target.loc[subset], x.loc[subset]
            directory = root / "trials" / trial["trial_id"] / "artifacts" / str(year)
            saved = pd.read_parquet(directory / "predictions.parquet")
            if not saved[["race_id", "race_date", "horse_number", "finish"]].reset_index(drop=True).equals(
                    t[["race_id", "race_date", "horse_number", "finish"]].reset_index(drop=True)):
                raise ValueError("research-prediction-cohort-mismatch")
            independently = control.legacy.probability_metrics(t.reset_index(drop=True), saved.probability.to_numpy())
            expected = fold["metrics"]
            for key, foreign in (("nll", "nll"), ("brier", "brier"), ("ece", "selection_ece")):
                if not np.isclose(expected[key], independently[foreign], atol=1e-10, rtol=0):
                    raise ValueError("research-independent-probability-metric-mismatch")
            recomputed = research.race_records(t, saved.probability)
            saved_records = pd.read_parquet(directory / "race-records.parquet")
            pd.testing.assert_frame_equal(recomputed.reset_index(drop=True), saved_records.reset_index(drop=True))
            if not np.isclose(recomputed.top1.mean(), expected["top1"], atol=1e-12):
                raise ValueError("research-independent-top1-mismatch")
            bundle = joblib.load(directory / "research.joblib")
            if bundle["sources"] != own_sources() or bundle["production_eligible"] is not False:
                raise ValueError("research-bundle-source-or-scope-mismatch")
            if confirmation:
                scores, probability = predict_bundle(bundle, t, features)
                if not np.array_equal(scores, saved.score) or not np.array_equal(probability, saved.probability):
                    raise ValueError("research-confirmation-prediction-replay-failed")
                if specification["id"] == selection["winner"]:
                    for race_id in t.race_id.drop_duplicates().iloc[[0, len(t.race_id.unique()) // 2, -1]]:
                        case = t.loc[t.race_id.eq(race_id)].copy()
                        inputs = features.loc[case.index]
                        original, original_p = predict_bundle(bundle, case, inputs)
                        case["finish"] = 1
                        case["time_seconds"] = 1.
                        case["_ctx_history_corners"] = [(1,) for _ in range(len(case))]
                        changed, changed_p = predict_bundle(bundle, case, inputs)
                        if not np.array_equal(original, changed) or not np.array_equal(original_p, changed_p):
                            raise ValueError("research-current-outcome-injection-used")
                        injection_cases.append({"race_id": str(race_id), "ignored_current_outcomes": True})
            races[(trial["trial_id"], year)] = recomputed
            verified.append({"trial": trial["trial_id"], "year": year, "races": len(recomputed)})
    for receipt_path in (root / "head-cache").glob("*/receipt.json"):
        receipt = read_json(receipt_path)
        if file_sha256(receipt_path.parent / "heads.joblib") != receipt["artifact_sha256"]:
            raise ValueError("research-cached-head-file-mismatch")
    comparisons = {}
    for year in (2022, 2023, 2024, 2026):
        prefix = "confirm-" if year == 2026 else "dev-"
        baseline = races[("".join([prefix, "speed46"]), year)]
        candidate = races[(prefix + selection["winner"], year)]
        comparisons[str(year)] = paired_bootstrap(baseline, candidate)
    manifest = read_json(root / "dataset" / "dataset_manifest.json")
    if file_sha256(Path(manifest["snapshot"]["path"])) != manifest["snapshot"]["sha256"]:
        raise ValueError("research-snapshot-final-hash-mismatch")
    final = comparisons["2026"]
    confirmations = read_json(root / "confirmation-results.json")
    candidate = next(item for item in confirmations if item["id"] == selection["winner"])["folds"][0]
    baseline = confirmations[0]["folds"][0]
    improved = (final["sufficient_dates"] and final["nll_improvement_ci95"][0] > 0
                and candidate["metrics"]["brier"] <= baseline["metrics"]["brier"]
                and candidate["metrics"]["top1"] >= baseline["metrics"]["top1"] - .005
                and not candidate["boundary"])
    verification = {"verified": True, "model_improved": bool(improved), "selected": selection["winner"],
                    "comparisons": comparisons, "checked_folds": verified, "outcome_injection": injection_cases,
                    "production_unchanged": guards() == task["guards"], "known_retrospective_only": True,
                    "future_generalization_proven": False, "roi_measured": False, "production_eligible": False}
    write_json(root / "verification.json", verification)
    return verification


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "approve", "run", "worker", "verify", "status"])
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--device", default="cpu", choices=["cpu"])
    args = parser.parse_args()
    root = args.root.resolve()
    if args.action == "prepare":
        result = prepare(root)
    elif args.action == "approve":
        approve(root)
        result = {"approved": True}
    elif args.action == "run":
        result = run(root)
    elif args.action == "worker":
        return cli.worker(root, args.device, trial_runner=run_trial)
    elif args.action == "verify":
        result = verify(root)
    else:
        result = cli.store_for(root).snapshot(root.name)
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False), flush=True)
    return 0
