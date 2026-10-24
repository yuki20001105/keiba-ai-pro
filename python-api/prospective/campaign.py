"""Fixed offline probability/variant campaign; no production model publication."""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

import psutil

from experiments.common import REPO_ROOT, active_pointer_hash, file_sha256, json_sha256, read_json, source_manifest, write_json
from experiments.cli import store_for, verify_contract, worker, _OwnedChild, _reap_owned_children
from experiments.orchestrator import driver_lock
from experiments.resources import capability_probe, resource_snapshot
from experiments.search import freeze_json
from experiments.runtime import ExperimentError

BASE = REPO_ROOT / "reports/generated/model-experiments"
WORKER = REPO_ROOT / "scripts/run_forward_validation.py"
PARENT = BASE / "20260919-feature-search-v1"
VARIANTS = ["reference", "history_shrink", "history_damped", "surface_split", "ensemble_speed", "ensemble_lambdarank"]
PLAN = {
    "schema": "probability-forward-plan-v1", "fixed_rank_candidate": "H04", "seed": 42,
    "variant_development_years": [2021, 2022], "variant_candidates": VARIANTS,
    "variant_selection": "maximum race-mean Spearman; ties within 1e-6 prefer fixed candidate order",
    "variant_selection_uses_roi": False, "variant_replaces_calibration_base": False,
    "calibration_fit_years": [2021], "calibration_selection_years": [2022], "calibration_refit_years": [2021, 2022],
    "calibration_families": ["uniform", "softmax_t1", "global_temperature", "surface_temperature"],
    "calibration_temperature_bounds": [.01, 100.], "calibration_surface_min_races": 100,
    "calibration_selection": "race-mean NLL then race-mean Brier then fixed family order",
    "known_reused_confirmation_years": [2023, 2024, 2025], "known_reused_retrospective_years": [2026],
    "confirmation_is_unseen": False, "post_confirmation_retuning": False,
    "paper_only": True, "no_automatic_promotion": True, "max_trial_jobs": 9,
    "prospective": {"date_from": "20260920", "date_to": "20261218", "calendar_days": 90,
                    "minimum_complete_races": 1000, "minimum_race_days": 30, "cutoff_minutes_before_off": 5,
                    "capture_window_seconds": 30, "maximum_observation_age_seconds": 120,
                    "model_calibrator_policy_frozen_during_period": True, "unobserved_future_results_not_fabricated": True},
}


def prospective_manifest() -> dict:
    paths = list((REPO_ROOT / "python-api/prospective").rglob("*.py"))
    paths += [WORKER]
    capture_script = REPO_ROOT / "scripts/capture_forward_snapshot.py"
    if capture_script.exists(): paths.append(capture_script)
    return {p.relative_to(REPO_ROOT).as_posix(): file_sha256(p) for p in sorted(paths)}


def check_contract(root: Path, *, data: bool = True) -> dict:
    contract = read_json(root / "contract.json")
    verify_contract(root, contract, verify_data=data)
    if contract["prospective_source_manifest"] != prospective_manifest():
        raise RuntimeError("prospective source changed after freeze")
    if contract["plan"] != PLAN or read_json(root / "plan.json") != PLAN:
        raise RuntimeError("frozen prospective plan changed")
    for name, digest in contract["parent_artifacts"].items():
        if file_sha256(Path(name)) != digest:
            raise RuntimeError("frozen parent artifact changed")
    return contract


def _references(parent: Path) -> dict:
    return {key: [{"path": str(parent / "trials" / f"{role}-{band}" / "result.json"),
                   "sha256": file_sha256(parent / "trials" / f"{role}-{band}" / "result.json")}
                  for band in ("search", "confirmation", "retrospective")]
            for key, role in (("h04", "winner"), ("speed", "regression"), ("lambdarank", "baseline"))}


def prepare(root: Path, parent: Path = PARENT) -> dict:
    if root == parent or root.is_relative_to(parent) or parent.is_relative_to(root):
        raise ValueError("new experiment must be separate from completed parent")
    if (root / "contract.json").exists():
        contract = check_contract(root)
        for name, digest in {**contract["source_manifest"], **contract["prospective_source_manifest"]}.items():
            dest = root / "code-snapshot" / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists(): shutil.copyfile(REPO_ROOT / name, dest)
            if file_sha256(dest) != digest: raise RuntimeError("source archive mismatch")
        store = store_for(root)
        try: state = store.snapshot(root.name)
        except ExperimentError as exc:
            if str(exc) != "Experiment does not exist": raise
            store.create_experiment(root.name, contract)
        else:
            if state["contract"] != contract: raise RuntimeError("existing ledger contract mismatch")
        return {"status": "already_prepared", "root": str(root)}
    with sqlite3.connect(f"file:{(parent / 'experiment.sqlite3').as_posix()}?mode=ro", uri=True) as db:
        row = db.execute("select status from experiments where experiment_id=?", (parent.name,)).fetchone()
    if row is None or row[0] != "completed": raise ValueError("parent not completed")
    verify_contract(parent, read_json(parent / "contract.json"))
    root.mkdir(parents=True, exist_ok=True)
    freeze_json(root / "plan.json", PLAN)
    target = root / "dataset"
    target.mkdir(exist_ok=True)
    for name in ("dataset.parquet", "dataset_manifest.json"):
        source, dest = parent / "dataset" / name, target / name
        if dest.exists():
            if file_sha256(source) != file_sha256(dest): raise RuntimeError("existing copied dataset changed")
        else:
            shutil.copyfile(source, dest)
    manifest = read_json(target / "dataset_manifest.json")
    resources, capability = resource_snapshot(), capability_probe()
    # No supported GPU implementation is installed on this host. Merely having
    # an NVIDIA device is not evidence that these LightGBM learners use it.
    reserve, estimate = 3072, 3200
    if resources["available_ram_mb"] < reserve + estimate:
        raise RuntimeError("insufficient memory to safely start validation")
    # Capacity is a ceiling, not a promise of simultaneous admission. Current
    # memory is checked again before each launch, so a transient preparation-time
    # dip does not permanently serialize a campaign that can later run two fits.
    capacity = 2
    parent_artifacts = {str(parent / "selection.json"): file_sha256(parent / "selection.json"),
                        str(parent / "dataset/dataset.parquet"): manifest["dataset_sha256"],
                        str(parent / "dataset/dataset_manifest.json"): file_sha256(parent / "dataset/dataset_manifest.json")}
    for refs in _references(parent).values():
        for ref in refs:
            path = Path(ref["path"])
            parent_artifacts[str(path)] = ref["sha256"]
            result = read_json(path)
            for fold in result["folds"]:
                parent_artifacts[fold["artifact"]] = fold["sha256"]
            for artifact in result["artifacts"].values():
                parent_artifacts[artifact["path"]] = artifact["sha256"]
    contract = {"schema": "isolated-probability-forward-campaign-v1", "plan": PLAN,
                "frozen_at": datetime.now(timezone.utc).isoformat(), "parent_root": str(parent),
                "parent_artifacts": parent_artifacts, "source_manifest": source_manifest(),
                "prospective_source_manifest": prospective_manifest(), "active_pointer_sha256": active_pointer_hash(),
                "dataset_sha256": manifest["dataset_sha256"], "dataset_manifest_sha256": file_sha256(target / "dataset_manifest.json"),
                "capacity": {"cpu": capacity, "gpu": 0}, "estimated_worker_memory_mb": estimate, "reserve_ram_mb": reserve,
                "capability": capability, "gpu_used": False,
                "gpu_reason": "installed LightGBM build has no GPU learner; no other GPU ML library installed",
                "no_live_bets": True, "no_promotion": True}
    freeze_json(root / "contract.json", contract)
    for name, digest in {**contract["source_manifest"], **contract["prospective_source_manifest"]}.items():
        dest = root / "code-snapshot" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists(): shutil.copyfile(REPO_ROOT / name, dest)
        if file_sha256(dest) != digest: raise RuntimeError("source archive mismatch")
    store_for(root).create_experiment(root.name, contract)
    check_contract(root)
    return {"status": "prepared", "rows": manifest["row_count"], "races": manifest["race_count"],
            "cpu_workers": capacity, "gpu_verified": capability["gpu_verified"], "gpu_used": False}


def load_h04_predictions(parent: Path, dataset: Path):
    import pandas as pd
    parts, model_hashes, artifacts = [], {}, []
    for ref in _references(parent)["h04"]:
        result = read_json(Path(ref["path"]))
        artifact = result["artifacts"]["predictions"]
        if file_sha256(Path(artifact["path"])) != artifact["sha256"]:
            raise RuntimeError("prediction artifact hash mismatch")
        part = pd.read_parquet(artifact["path"])
        train_to = {fold["year"]: fold["contract"]["train_period"][1] for fold in result["folds"]}
        part["fold_train_date_to"] = part["fold_year"].map(train_to)
        if part["fold_train_date_to"].isna().any() or not (part["fold_train_date_to"] < part["race_date"]).all():
            raise RuntimeError("non-OOF source predictions")
        model_hashes.update({str(fold["year"]): fold["sha256"] for fold in result["folds"]})
        parts.append(part); artifacts.append(artifact)
    combined = pd.concat(parts, ignore_index=True)
    history = pd.read_parquet(dataset / "dataset.parquet", columns=["race_id", "horse_number", "safe_prior_starts"],
                              filters=[("race_date", ">=", "20210101")])
    count = len(combined)
    combined = combined.merge(history, on=["race_id", "horse_number"], how="left", validate="one_to_one")
    if len(combined) != count or combined["safe_prior_starts"].isna().any():
        raise RuntimeError("incomplete history join")
    return combined, {"model_id": "H04", "prediction_artifacts": artifacts,
                      "model_artifact_sha256_by_year": model_hashes,
                      "selection_sha256": file_sha256(parent / "selection.json")}


def run_trial(dataset: Path, output: Path, trial: dict, progress=None) -> dict:
    root = dataset.parent
    contract = check_contract(root)
    if trial["kind"] == "calibration":
        from prospective.calibration import run_calibration_experiment
        frame, provenance = load_h04_predictions(Path(contract["parent_root"]), dataset)
        if progress: progress("calibrate fixed H04 with chronological split", 10)
        result = run_calibration_experiment(frame, output, model_provenance=provenance)
        result = {"status": "completed", "trial_id": trial["id"], "kind": "calibration", "report": result}
    else:
        from prospective.variants import run_variant_trial
        result = run_variant_trial(dataset, output, trial, progress)
    check_contract(root, data=False)
    return result


def _verified_results(root: Path, ids: set[str]) -> dict:
    results = {}
    for row in store_for(root).snapshot(root.name)["trials"]:
        if row["trial_id"] not in ids or row["status"] != "completed": continue
        folder = root / "trials" / row["trial_id"]
        if read_json(folder / "result.json") != row["result"]: raise RuntimeError("ledger result mismatch")
        for name, digest in read_json(folder / "artifact_manifest.json").items():
            path = (folder / name).resolve()
            if not path.is_relative_to(folder.resolve()) or file_sha256(path) != digest:
                raise RuntimeError("completed artifact changed")
        results[row["trial_id"]] = row["result"]
    return results


def execute_stage(root: Path, name: str, payloads: list[dict]) -> dict:
    """Own only spawned child handles; reuse proven lease/termination mechanisms."""
    contract, store = check_contract(root), store_for(root)
    frozen = {"name": name, "payloads": payloads}
    (root / "stages").mkdir(exist_ok=True)
    freeze_json(root / "stages" / f"{name}.json", frozen)
    wanted = {p["id"] for p in payloads}
    if len(wanted) != len(payloads): raise ValueError("duplicate trial IDs")
    existing = {r["trial_id"]: r for r in store.snapshot(root.name)["trials"]}
    if len(set(existing) | wanted) > PLAN["max_trial_jobs"]: raise RuntimeError("trial cap exceeded")
    for payload in payloads:
        normalized = {**payload, "device": "cpu", "num_threads": 4, "seed": 42,
                      "experiment_contract_sha256": store.snapshot(root.name)["contract_sha256"]}
        if payload["id"] in existing:
            if existing[payload["id"]]["payload"] != normalized: raise RuntimeError("resumed payload changed")
        else:
            store.enqueue(root.name, payload["id"], normalized, "cpu", memory_mb=contract["estimated_worker_memory_mb"])
    _verified_results(root, wanted)
    children, all_children = [], []
    logs = root / "logs"; logs.mkdir(exist_ok=True)
    last_message, starved_since = 0., None
    try:
        while True:
            snapshot = store.snapshot(root.name)
            rows = [r for r in snapshot["trials"] if r["trial_id"] in wanted]
            if snapshot["status"] != "active" or any(r["status"] not in ("queued", "running", "completed") for r in rows):
                raise RuntimeError("campaign failed/stopped/unknown; inspect before resuming")
            if len(rows) != len(wanted): raise RuntimeError("ledger incomplete")
            if all(r["status"] == "completed" for r in rows): break
            for child in list(children):
                status = child.process.poll()
                if status is not None:
                    child.log.close(); children.remove(child)
                    if status: raise RuntimeError(f"trial worker exited {status}")
            resources = resource_snapshot()
            occupied = max(len(children), sum(r["status"] == "running" for r in rows))
            queued = sum(r["status"] == "queued" for r in rows)
            slots = min(queued, contract["capacity"]["cpu"] - occupied)
            for _ in range(slots):
                if resources["available_ram_mb"] < contract["reserve_ram_mb"] + contract["estimated_worker_memory_mb"]: break
                log = (logs / f"cpu-{time.time_ns()}.log").open("wb")
                env = dict(os.environ, PYTHONUTF8="1", OMP_NUM_THREADS="4", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
                try:
                    process = subprocess.Popen([sys.executable, str(WORKER), "worker", "--root", str(root)],
                        cwd=REPO_ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
                except BaseException:
                    log.close(); raise
                child = _OwnedChild(process, log, "cpu", None)
                with contextlib.suppress(psutil.Error): child.created_at = psutil.Process(process.pid).create_time()
                children.append(child); all_children.append(child)
                resources["available_ram_mb"] -= contract["estimated_worker_memory_mb"]
            starved = queued and not children and not occupied
            if starved:
                if starved_since is None: starved_since = time.monotonic()
                if time.monotonic() - starved_since > 300: raise RuntimeError("RAM admission timeout; work not started")
            else: starved_since = None
            if time.monotonic() - last_message > 15:
                print(json.dumps({"stage": name, "completed": sum(r["status"] == "completed" for r in rows), "total": len(rows),
                    "running": [{k: r.get(k) for k in ("trial_id", "progress_stage", "progress_pct")} for r in rows if r["status"] == "running"]}, ensure_ascii=False), flush=True)
                last_message = time.monotonic()
            time.sleep(2)
    except BaseException:
        with contextlib.suppress(Exception): store.cancel(root.name)
        _reap_owned_children(store, root, all_children, grace_seconds=5)
        raise
    else:
        remaining = _reap_owned_children(store, root, all_children, grace_seconds=30)
        if remaining: raise RuntimeError(f"owned workers still alive: {remaining}")
    return _verified_results(root, wanted)


def select_variant(results: dict) -> str:
    if set(results) != {f"dev-{v}" for v in VARIANTS}: raise ValueError("predeclared candidates incomplete")
    for value in results.values():
        if value["validation_years"] != [2021, 2022]: raise ValueError("cannot select on later period")
        score = value["evaluation"]["overall"]["rank_correlation"]
        if score is None or not (-1 <= score <= 1): raise ValueError("invalid selection metric")
    highest = max(r["evaluation"]["overall"]["rank_correlation"] for r in results.values())
    return next(v for v in VARIANTS if results[f"dev-{v}"]["evaluation"]["overall"]["rank_correlation"] >= highest - 1e-6)


def run(root: Path) -> dict:
    started = time.perf_counter()
    with driver_lock(BASE, root):
        # Read-only inspection: never open an application DB or requeue an
        # unknown worker from a different campaign.
        for path in BASE.glob("*/experiment.sqlite3"):
            if path.parent.resolve() == root: continue
            with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as db:
                if db.execute("select count(*) from experiments where status in ('active','cancelling','blocked')").fetchone()[0]:
                    raise RuntimeError("another experiment requires completion/investigation first")
        contract = check_contract(root)
        if store_for(root).snapshot(root.name)["status"] == "completed":
            _verified_results(root, {r["trial_id"] for r in store_for(root).snapshot(root.name)["trials"]})
            return read_json(root / "summary.json")
        parent = Path(contract["parent_root"])
        spec = read_json(parent / "selection.json")["winner_spec"]
        common = {"kind": "variant", "base_spec": spec, "base_spec_sha256": json_sha256(spec),
                  "dataset_sha256": contract["dataset_sha256"], "manifest_sha256": contract["dataset_manifest_sha256"],
                  "reference_results": _references(parent), "bootstrap_samples": 0}
        dev = [{**common, "id": f"dev-{v}", "variant": v, "validation_years": [2021, 2022]} for v in VARIANTS]
        initial = execute_stage(root, "01-development-and-independent-calibration", dev + [{"id": "calibration", "kind": "calibration"}])
        chosen = select_variant({key: value for key, value in initial.items() if key.startswith("dev-")})
        selection = {"selected_variant": chosen, "period": [2021, 2022], "calibration_base_unchanged": "H04",
                     "roi_used": False, "confirmation_used": False,
                     "result_hashes": {key: file_sha256(root / "trials" / key / "result.json") for key in initial if key.startswith("dev-")}}
        freeze_json(root / "variant-selection.json", selection)
        followup = [{**common, "id": f"selected-{band}", "variant": chosen, "validation_years": years, "bootstrap_samples": 300}
                    for band, years in (("confirmation", [2023, 2024, 2025]), ("retrospective", [2026]))]
        finals = execute_stage(root, "02-frozen-variant-confirmation", followup)
        from prospective.policy import evaluate_policy
        import pandas as pd
        calibration_dir = root / "trials/calibration/artifacts"
        probabilities = pd.read_parquet(calibration_dir / "diagnostic-probabilities.parquet")
        policy_dir = root / "paper-policy"; policy_dir.mkdir(exist_ok=True)
        policy_results = {}
        for band, years in (("confirmation", [2023, 2024, 2025]), ("retrospective", [2026])):
            part = probabilities.loc[probabilities["race_date"].astype(str).str[:4].astype(int).isin(years)]
            for label, multiplier in (("stress", .9), ("no_stress_reference", 1.)):
                report, decisions = evaluate_policy(part, odds_multiplier=multiplier)
                key = f"{band}-{label}"
                freeze_json(policy_dir / f"{key}.json", report)
                path = policy_dir / f"{key}-decisions.parquet"
                if not path.exists(): decisions.to_parquet(path, index=False)
                else:
                    pd.testing.assert_frame_equal(pd.read_parquet(path), decisions)
                policy_results[key] = report
        check_contract(root)
        summary = {"status": "offline_completed_forward_pending", "selected_variant": chosen,
                   "development": {v: initial[f"dev-{v}"] for v in VARIANTS}, "selected_confirmation": finals,
                   "calibration_report": read_json(calibration_dir / "calibration-report.json"), "paper_policy": policy_results,
                   "timing": {"wall_seconds": time.perf_counter() - started, "cpu_workers": contract["capacity"]["cpu"], "gpu_used": False},
                   "forward": {"status": "awaiting_new_timestamped_observations", "observed_races": 0,
                               "automatic_collection_started": False, "protocol": PLAN["prospective"]},
                   "active_model_unchanged": active_pointer_hash() == contract["active_pointer_sha256"], "real_purchases": 0,
                   "original_parent_unchanged": True}
        if (root / "summary.json").exists():
            # Preserve measured elapsed time from a completed offline report if
            # the process stopped between report write and ledger finalization.
            # All other contents must still match the recomputed frozen result.
            summary["timing"] = read_json(root / "summary.json")["timing"]
        freeze_json(root / "summary.json", summary)
        store_for(root).finish_experiment(root.name)
        return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run", "worker", "status", "cancel"])
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(); root = args.root.resolve()
    if root == BASE.resolve() or not root.is_relative_to(BASE.resolve()): parser.error("root must be an experiment subdirectory")
    if args.action == "prepare": result = prepare(root)
    elif args.action == "worker":
        check_contract(root)
        return worker(root, "cpu", trial_runner=run_trial)
    elif args.action == "run":
        summary = run(root)
        result = {k: summary[k] for k in ("status", "selected_variant", "timing", "forward", "active_model_unchanged")}
    elif args.action == "cancel":
        store_for(root).cancel(root.name); result = {"cancel_requested": True}
    else:
        state = store_for(root).snapshot(root.name)
        result = {"status": state["status"], "counts": {s: sum(r["status"] == s for r in state["trials"]) for s in sorted({r["status"] for r in state["trials"]})},
                  "running": [{k: r.get(k) for k in ("trial_id", "progress_stage", "progress_pct")} for r in state["trials"] if r["status"] == "running"]}
    print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
    return 0
