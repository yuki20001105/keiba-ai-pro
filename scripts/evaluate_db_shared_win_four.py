"""Phase1-gated fixed46 four-candidate research, never an active-model update.

Shared PIT raw features are verified once and reused by isolated workers. Final
odds are hindsight proxies. Payout absence never changes fitting/calibration.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/research/db-shared-win-four-20261012.json"
OUTPUT = ROOT / "reports/generated/db-shared-win-four"
LOCK_PATH = OUTPUT / "preregistration-20261012-v1.json"
PYTHON = ROOT / "python-api/.venv/Scripts/python.exe"
CANDIDATES = ("expanding_binary", "rolling5_binary", "expanding_race_nll", "rolling5_race_nll")
SOURCES = (
    "scripts/evaluate_db_shared_win_four.py", "scripts/db_shared_win_four_worker.py",
    "scripts/run_db_shared_win_pipeline.py", "scripts/db_shared_win_direct_worker.py",
    "scripts/db_shared_win_direct_features.py", "scripts/db_shared_win_direct_diagnostics.py",
    "scripts/db_shared_blend46_betting.py", "scripts/benchmark_db_shared_full_worker.py",
    "scripts/evaluate_db_shared_venues.py", "python-api/training/db_shared_fit.py",
    "python-api/training/db_shared_features.py", "python-api/training/eligibility.py",
    "python-api/experiments/features.py", "python-api/experiments/search_features.py",
    "python-api/forward_adapters/feature_state.py", "config/research/db-shared-win-four-20261012.json",
    "python-api/scraping/odds.py",
    "python-api/scraping/constants.py",
)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def validate_protocol(protocol):
    if (protocol != read(CONFIG) or protocol.get("schema") != "db-shared-win-four-protocol-v1"
            or protocol["candidates"] != list(CANDIDATES)
            or protocol["fold_years"] != list(range(2016, 2027))
            or protocol["roi_rules"] != ["top1", "top1_ev105"]
            or protocol["maximum_parallel_fits"] != 3 or protocol["threads_per_fit"] != 2
            or protocol["rounds"] != 150 or protocol["policy_family_size"] != 9
            or any(protocol[key] is not False for key in ("optuna", "test_early_stopping",
                "publication", "activation", "forward_changes", "actual_purchase", "commit_push"))):
        raise ValueError("four-frozen-protocol-required")


def register_protocol():
    """Freeze every research source before the live race, never fit a model."""
    from evaluate_db_shared_venues import preservation_receipt
    protocol = read(CONFIG)
    validate_protocol(protocol)
    pins = {name: sha(ROOT / name) for name in SOURCES}
    if LOCK_PATH.exists():
        locked = read(LOCK_PATH)
        if locked.get("protocol_sha256") != sha(CONFIG) or locked.get("source_hashes") != pins:
            raise ValueError("four-preregistered-protocol-or-source-drift")
        return {"status": "already_registered", "path": str(LOCK_PATH), "sha256": sha(LOCK_PATH), "fits_started": 0}
    import run_db_shared_win_pipeline as pipeline
    plan = pipeline.read_plan(ROOT / protocol["phase1_plan"])
    registered = datetime.now(timezone.utc)
    if registered >= pipeline.instant(plan["plan"]["target"]["post_time"]):
        raise ValueError("four-preregistration-must-precede-target-outcome")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    save(LOCK_PATH, {"schema": "db-shared-win-four-preregistration-v1", "registered_at": registered.isoformat(),
        "protocol": protocol, "protocol_sha256": sha(CONFIG), "source_hashes": pins,
        "phase1_plan_sha256": plan["plan_sha256"], "preservation_before": preservation_receipt(),
        "fits_started": 0, "actual_purchase": False})
    return {"status": "registered", "path": str(LOCK_PATH), "sha256": sha(LOCK_PATH), "fits_started": 0}


def read_preregistration(protocol, pins):
    locked = read(LOCK_PATH)
    if (locked.get("schema") != "db-shared-win-four-preregistration-v1"
            or locked.get("protocol") != protocol or locked.get("protocol_sha256") != sha(CONFIG)
            or locked.get("source_hashes") != pins or locked.get("fits_started") != 0
            or locked.get("actual_purchase") is not False):
        raise ValueError("four-preregistered-protocol-or-source-drift")
    return locked


def phase1_gate(plan):
    """Recompute the sealed chain; a user-written true flag is insufficient."""
    import run_db_shared_win_pipeline as pipeline
    proof_path = Path(plan) / "proof.json"
    verified = pipeline.verify(Path(plan))
    if not proof_path.is_file() or not verified.get("phase1_pass"):
        raise ValueError("four-real-complete-phase1-required")
    proof = read(proof_path)
    if proof != verified:
        raise ValueError("four-phase1-proof-readback-mismatch")
    gate = pipeline.verify_gate_proof(proof_path)
    if gate["proof"] != proof or gate["proof_sha256"] != sha(proof_path):
        raise ValueError("four-phase1-sealed-gate-mismatch")
    return proof_path.resolve(), sha(proof_path), proof


def payout_attachment(frame, snapshot):
    """Nullable outcome-only payout sidecar; no missing payout or odds imputation."""
    import numpy as np
    import pandas as pd
    ids = sorted(frame.race_id.unique())
    rows = []
    connection = sqlite3.connect(Path(snapshot).as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        for start in range(0, len(ids), 500):
            batch = ids[start:start + 500]
            placeholders = ",".join("?" for _ in batch)
            rows.extend(connection.execute(
                "SELECT race_id,combinations,payout FROM return_tables_ultimate "
                f"WHERE bet_type='単勝' AND race_id IN ({placeholders})", batch).fetchall())
    finally:
        connection.close()
    returns = pd.DataFrame(rows, columns=["race_id", "combination", "payout"])
    valid = {}
    invalid = {}
    if not returns.empty:
        for race_id, group in returns.groupby("race_id", sort=False):
            if len(group) != 1:
                invalid[str(race_id)] = "duplicate_or_multiple_winner_return"
                continue
            item = group.iloc[0]
            token = str(item.combination)
            try:
                amount = float(item.payout)
            except (TypeError, ValueError):
                invalid[str(race_id)] = "invalid_return_amount"
                continue
            if (re.fullmatch(r"[1-9]\d?", token) is None or not 1 <= int(token) <= 18
                    or not np.isfinite(amount) or amount < 100 or amount % 10 != 0):
                invalid[str(race_id)] = "invalid_return_key_or_amount"
                continue
            winner = frame.loc[frame.race_id.eq(str(race_id)) & frame.finish.eq(1), "horse_number"]
            if len(winner) != 1 or int(winner.iloc[0]) != int(token):
                invalid[str(race_id)] = "winner_return_key_conflict"
                continue
            valid[str(race_id)] = amount
    result = frame.copy()
    result["race_payout_complete"] = result.race_id.isin(valid)
    result["tansho_payout"] = np.where(result.finish.eq(1), result.race_id.map(valid), np.nan)
    missing = sorted(set(ids) - set(valid))
    audit = {"input_races": len(ids), "confirmed_payout_races": len(valid),
             "missing_or_invalid_payout_races": len(missing),
             "excluded_reasons": {race_id: invalid.get(race_id, "return_unavailable") for race_id in missing},
             "snapshot_read_only": True, "odds_substitution": False,
             "fitting_rows_excluded_due_to_payout": 0}
    return result, audit


def prepare_data(protocol):
    import numpy as np
    import pandas as pd
    import benchmark_db_shared_full_worker as raw
    import db_shared_win_direct_worker as old_worker
    import db_shared_win_direct_features as market_features
    for directory in (ROOT / "python-api", ROOT / "keiba"):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    from training import db_shared_fit as fit
    cache = (ROOT / protocol["raw47_cache"]).resolve(strict=True)
    if not cache.is_relative_to(ROOT / "reports/generated") or sha(cache / "manifest.json") != protocol["raw47_manifest_sha256"]:
        raise ValueError("four-frozen-cache-boundary-or-hash")
    manifest = read(cache / "manifest.json")
    if fit.source_hashes() != manifest["source_hashes"]:
        raise ValueError("four-raw-cache-current-formula-source-mismatch")
    snapshot = ROOT / "keiba/data/model_retrain_snapshots" / (protocol["snapshot_sha256"] + ".db")
    if sha(snapshot) != protocol["snapshot_sha256"]:
        raise ValueError("four-snapshot-hash-mismatch")
    target, features, checked = raw.read_cache(cache, {
        "snapshot_sha256": protocol["snapshot_sha256"], "period": manifest["period"],
        "expected_source_hashes": manifest["source_hashes"]}, fit)
    columns, categorical = old_worker.feature_contract("market46")
    raw_columns = columns[4:]
    if len(target) != len(features) or len(raw_columns) != 46:
        raise ValueError("four-fixed46-raw-cache-order")
    frame = target.copy().reset_index(drop=True)
    for name in raw_columns:
        frame[name] = features[name].reset_index(drop=True)
    frame["expected_runner_count"] = frame.num_horses.astype(int)
    # Always remove a whole race when an input required for all four methods
    # is invalid. Return table availability is deliberately not in this mask.
    odds = pd.to_numeric(frame.odds, errors="coerce").to_numpy(float)
    bad = set(frame.loc[~np.isfinite(odds) | (odds < 1), "race_id"])
    winners = frame.finish.eq(1).groupby(frame.race_id, sort=False).sum()
    bad |= set(winners.loc[winners.ne(1)].index)
    frame = frame.loc[~frame.race_id.isin(bad)].copy()
    frame = frame.sort_values(["race_date", "race_id", "horse_number"], kind="mergesort").reset_index(drop=True)
    frame = market_features.add_market_features(frame)
    frame, payout_audit = payout_attachment(frame, snapshot)
    # Compatibility with the frozen policy schema; this is a log market
    # probability, never a physical speed score and never a model input.
    frame["raw_score"] = np.log(frame.normalized_market_probability.to_numpy(float))
    return frame, {"cache_manifest_sha256": sha(cache / "manifest.json"),
        "cache_rows": len(target), "cache_races": int(target.race_id.nunique()),
        "prediction_rows": len(frame), "prediction_races": int(frame.race_id.nunique()),
        "whole_prediction_excluded_races": sorted(bad), "raw46_cache_reused": True,
        "point_in_time_audit": manifest["point_in_time_audit"], "payout_audit": payout_audit,
        "date_from": frame.race_date.min(), "date_to": frame.race_date.max(),
        "all_four_identical_prediction_cohort": True, "historical_prestart_quotes_verified": False}


def run_subprocess(request, output):
    environment = os.environ.copy()
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        environment[name] = "2"
    completed = subprocess.run([str(PYTHON), str(ROOT / "scripts/db_shared_win_four_worker.py"),
        "--request", str(request), "--output", str(output)], cwd=ROOT, env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    with output.with_suffix(".stdout.log").open("x", encoding="utf-8") as stream:
        stream.write(completed.stdout)
    with output.with_suffix(".stderr.log").open("x", encoding="utf-8") as stream:
        stream.write(completed.stderr)
    if completed.returncode != 0:
        raise ValueError(f"four-worker-failed:{request.stem}")
    return read(output)


def attach_predictions(reference, output):
    """Identical race/runner/date order and outcomes across every method."""
    import numpy as np
    keys = ["race_id", "race_date", "horse_number"]
    order = ["race_date", "race_id", "horse_number"]
    reference = reference.sort_values(order, kind="mergesort").reset_index(drop=True)
    output = output.sort_values(order, kind="mergesort").reset_index(drop=True)
    if output.duplicated(keys).any() or not output[keys].equals(reference[keys]):
        raise ValueError("four-identical-full-prediction-cohort-required")
    for name in ("finish", "odds", "expected_runner_count", "venue", "surface"):
        if name not in output or not output[name].equals(reference[name]):
            raise ValueError("four-prediction-outcome-or-metadata-binding")
    probability = output.probability.to_numpy(float)
    if (not np.isfinite(probability).all() or (probability <= 0).any() or (probability >= 1).any()
            or not np.allclose(output.probability.groupby(output.race_id).sum(), 1., atol=1e-8, rtol=0.)):
        raise ValueError("four-race-probability-required")
    method = reference.copy()
    method["probability"] = probability
    method["raw_score"] = np.log(probability)
    return method


def validate_worker_result(result, request, worker):
    expected = {"schema": worker.SCHEMA, "status": "completed", "request_sha256": worker.canonical_hash(request),
        **{key: request[key] for key in ("job_id", "candidate_id", "fold_year", "frame_sha256",
            "protocol_sha256", "phase1_receipt_sha256", "feature_columns", "categorical_columns")},
        "source_hashes": request["expected_source_hashes"], "feature_count": 50}
    if any(result.get(key) != value for key, value in expected.items()):
        raise ValueError("four-worker-result-binding")
    if result.get("periods") != worker.fold_periods(request["fold_year"], request["candidate_id"]):
        raise ValueError("four-worker-result-period-binding")
    runtime = result.get("runtime", {})
    if runtime.get("device") != "cpu" or runtime.get("native_threads") != 2 or runtime.get("rounds") != 150:
        raise ValueError("four-worker-result-runtime-binding")
    if set(result.get("partitions", {})) != {"TRAIN", "CAL", "TEST"}:
        raise ValueError("four-worker-result-partitions-required")
    for split, receipt in result.get("partitions", {}).items():
        bounds = result["periods"][split]
        if not bounds[0] <= receipt["date_from"] <= receipt["date_to"] <= bounds[1]:
            raise ValueError("four-worker-result-partition-period-binding")


def evaluate_predictions(predictions, frame, protocol):
    import numpy as np
    import pandas as pd
    import db_shared_blend46_betting as betting
    import db_shared_win_direct_diagnostics as diagnostics
    reference = frame.loc[frame.race_date.ge("20160101")].copy().sort_values(
        ["race_date", "race_id", "horse_number"], kind="mergesort").reset_index(drop=True)
    reference["probability"] = reference.normalized_market_probability.to_numpy(float)
    methods = {"market_reference": reference}
    for candidate in CANDIDATES:
        methods[candidate] = attach_predictions(reference, predictions[candidate])
    probability, calibration, roi = {}, {}, {}
    for period, bounds in (("DEVELOPMENT_2016_2024", ("20160101", "20241231")),
                           ("SEEN_DIAGNOSTIC_2025_2026", ("20250101", "20261231")),
                           ("ALL_RETROSPECTIVE", ("20160101", "20261231"))):
        ref = reference.loc[reference.race_date.between(*bounds)].reset_index(drop=True)
        probability[period], calibration[period], policies = {}, {}, {}
        roi_ref = ref.loc[ref.race_payout_complete].reset_index(drop=True)
        policies["market_favorite"] = betting.select_bets(roi_ref, "top1")
        for name, method in methods.items():
            part = method.loc[method.race_date.between(*bounds)].reset_index(drop=True)
            probability[period][name] = diagnostics.analyze_probability(part, ref,
                repetitions=protocol["probability_bootstrap_repetitions"], seed=protocol["bootstrap_seed"])
            # A clearly named runner win-indicator error, not speed-score RMSE.
            probability[period][name]["rmse_win_indicator"] = float(np.sqrt(
                np.mean((part.probability.to_numpy() - part.finish.eq(1).to_numpy()) ** 2)))
            probability[period][name]["physical_speed_rmse"] = None
            calibration[period][name] = {}
            for rule in protocol["roi_rules"]:
                chosen = betting.select_bets(part, rule)
                calibration[period][name][rule] = diagnostics.selection_calibration(part, chosen,
                    repetitions=protocol["probability_bootstrap_repetitions"], seed=protocol["bootstrap_seed"])
                if name != "market_reference":
                    roi_part = part.loc[part.race_payout_complete].reset_index(drop=True)
                    policies[name + ":" + rule] = betting.select_bets(roi_part, rule)
        comparison = betting.bootstrap_policy_comparisons(roi_ref, policies, "market_favorite",
            repetitions=protocol["roi_bootstrap_repetitions"], seed=protocol["bootstrap_seed"],
            family_size=protocol["policy_family_size"])
        summaries = {name: {key: value for key, value in betting.summarize_policy(roi_ref, bets).items()
            if key != "daily_ledger"} for name, bets in policies.items()}
        roi[period] = {"summaries": summaries, "bootstrap": comparison,
            "prediction_races": int(ref.race_id.nunique()), "roi_races": int(roi_ref.race_id.nunique()),
            "unavailable_return_excluded_only_from_roi": int(ref.race_id.nunique() - roi_ref.race_id.nunique()),
            "policy_selection_performed": False, "historical_proxy_only": True}
    yearly = {}
    for year in protocol["fold_years"]:
        yearly[str(year)] = {}
        for name, method in methods.items():
            part = method.loc[method.race_date.str[:4].eq(str(year))].reset_index(drop=True)
            metrics = diagnostics._race_values(diagnostics._validate(part))
            yearly[str(year)][name] = {metric: float(np.mean(metrics[metric]))
                for metric in ("nll", "brier", "spearman", "top1")}
            yearly[str(year)][name].update(races=int(part.race_id.nunique()), runners=len(part))
    return {"prediction_precision": probability, "win_probability_calibration": calibration,
            "separate_fixed_rule_roi": roi, "yearly_precision": yearly,
            "market_reference_scope": protocol["reference_scope"],
            "independent_market_feature_edge_established": False,
            "independent_holdout": False, "new_model_activation": False}


def execute():
    started = time.perf_counter()
    protocol = read(CONFIG)
    validate_protocol(protocol)
    proof_path, proof_sha, proof = phase1_gate(ROOT / protocol["phase1_plan"])
    from evaluate_db_shared_venues import preservation_receipt
    before = preservation_receipt()
    pins = {name: sha(ROOT / name) for name in SOURCES}
    locked = read_preregistration(protocol, pins)
    if (locked["preservation_before"] != before or locked["phase1_plan_sha256"] != proof["plan_sha256"]
            or datetime.fromisoformat(locked["registered_at"]) >= datetime.fromisoformat(proof["post_time"].replace("Z", "+00:00"))):
        raise ValueError("four-preregistration-binding-or-preservation-drift")
    protocol_hash = sha(CONFIG)
    destination = OUTPUT / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8])
    destination.mkdir(parents=True, exist_ok=False)
    save(destination / "protocol.json", {"protocol": protocol, "protocol_sha256": protocol_hash,
        "source_hashes": pins, "phase1_proof_path": str(proof_path), "phase1_proof_sha256": proof_sha,
        "preregistration_sha256": sha(LOCK_PATH),
        "preservation_before": before})
    try:
        import pandas as pd
        import db_shared_win_four_worker as worker
        preparation_start = time.perf_counter()
        frame, audit = prepare_data(protocol)
        frame_path = destination / "shared50.parquet"
        with frame_path.open("xb") as stream:
            frame.to_parquet(stream, index=False, compression="zstd")
        preparation_seconds = time.perf_counter() - preparation_start
        frame_sha = sha(frame_path)
        columns, categorical = worker.feature_contract(CANDIDATES[0])
        jobs = []
        for year in protocol["fold_years"]:
            for candidate in CANDIDATES:
                job_id = f"{year}-{candidate}"
                request = {"schema": worker.SCHEMA, "job_id": job_id, "candidate_id": candidate,
                    "fold_year": year, "frame_path": str(frame_path.resolve()), "frame_sha256": frame_sha,
                    "protocol_sha256": protocol_hash, "expected_source_hashes": pins,
                    "feature_columns": columns, "categorical_columns": categorical, "device": "cpu", "n_threads": 2,
                    "phase1_receipt_path": str(proof_path), "phase1_receipt_sha256": proof_sha}
                request_path, output_path = destination / (job_id + "-request.json"), destination / (job_id + ".json")
                save(request_path, request)
                jobs.append((request_path, output_path))
        fit_start = time.perf_counter()
        results = []
        with ThreadPoolExecutor(max_workers=protocol["maximum_parallel_fits"]) as pool:
            futures = {pool.submit(run_subprocess, request, output): request for request, output in jobs}
            try:
                for future in as_completed(futures):
                    result = future.result()
                    validate_worker_result(result, read(futures[future]), worker)
                    results.append(result)
                    print(json.dumps({"completed": len(results), "total": len(jobs), "job_id": result["job_id"]}), flush=True)
            except Exception:
                for future in futures:
                    future.cancel()
                raise
        if {(item["candidate_id"], item["fold_year"]) for item in results} != {
                (candidate, year) for year in protocol["fold_years"] for candidate in CANDIDATES} or len(results) != 44:
            raise ValueError("four-exact44-worker-grid-required")
        fit_wall_seconds = time.perf_counter() - fit_start
        predictions = {}
        for candidate in CANDIDATES:
            parts = []
            for result in sorted(results, key=lambda value: value["job_id"]):
                if result["candidate_id"] != candidate:
                    continue
                receipt = result["artifacts"]["predictions"]
                path = Path(receipt["path"])
                if not path.resolve().is_relative_to(destination.resolve()) or sha(path) != receipt["sha256"]:
                    raise ValueError("four-prediction-artifact-binding")
                part = pd.read_parquet(path)
                if "split" in part:
                    part = part.loc[part.split.eq("TEST")]
                test_receipt = result["partitions"]["TEST"]
                bounds = result["periods"]["TEST"]
                if (len(part) != receipt["rows"] or len(part) != test_receipt["runner_count"]
                        or not part.candidate_id.eq(candidate).all() or not part.fold_year.eq(result["fold_year"]).all()
                        or not part.race_date.between(*bounds).all()
                        or int(part.race_id.nunique()) != test_receipt["race_count"]):
                    raise ValueError("four-test-artifact-candidate-fold-binding")
                parts.append(part)
            predictions[candidate] = pd.concat(parts, ignore_index=True)
        evaluation = evaluate_predictions(predictions, frame, protocol)
        if sha(frame_path) != frame_sha or any(sha(ROOT / name) != value for name, value in pins.items()):
            raise ValueError("four-input-or-source-drift")
        after = preservation_receipt()
        if before != after:
            raise ValueError("four-protected-state-changed")
        result = {"schema": "db-shared-win-four-results-v1", "status": "completed", "protocol_sha256": protocol_hash,
            "source_hashes": pins, "phase1_proof_sha256": proof_sha, "phase1_pass": True,
            "data_audit": audit, "worker_results": sorted(results, key=lambda value: value["job_id"]),
            "evaluation": evaluation, "preservation_before": before, "preservation_after": after,
            "runtime": {"shared_preparation_seconds": preparation_seconds, "parallel_fit_wall_seconds": fit_wall_seconds,
                "entire_elapsed_seconds": time.perf_counter() - started, "maximum_workers": 3, "threads_per_worker": 2,
                "same_work_serial_speedup_verified": False, "gpu_speedup_verified": False},
            "actual_purchase": False, "production_changed": False, "forward_collection_changed": False}
        save(destination / "results.json", result)
        save(destination / "artifact_manifest.json", {"schema": "db-shared-win-four-manifest-v1",
            "files": {str(path.relative_to(destination)): sha(path) for path in sorted(destination.rglob("*")) if path.is_file()}})
        print(json.dumps({"status": "completed", "directory": str(destination), "results_sha256": sha(destination / "results.json")}), flush=True)
        return result
    except Exception as error:
        save(destination / "failure.json", {"status": "failed", "error_type": type(error).__name__, "reason": str(error),
            "preservation_after": preservation_receipt(), "actual_purchase": False})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Requires an independently verified completed Phase1 chain")
    parser.add_argument("--check-protocol", action="store_true")
    parser.add_argument("--register-protocol", action="store_true", help="Freeze protocol and source pins before the target race; zero fits")
    args = parser.parse_args()
    if args.register_protocol:
        print(json.dumps(register_protocol(), ensure_ascii=False))
    elif args.check_protocol:
        protocol = read(CONFIG)
        validate_protocol(protocol)
        print(json.dumps({"protocol_valid": True, "protocol_sha256": sha(CONFIG), "fits_started": 0}))
    elif args.run:
        execute()
    else:
        parser.error("choose --check-protocol or --run")


if __name__ == "__main__":
    main()
