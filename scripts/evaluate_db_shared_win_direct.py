"""Isolated, locked direct-win study; no production or forward-state writes.

Three fresh processes share one immutable complete-race input. TRAIN fits,
CAL calibrates, SELECT freezes policies, and TEST only diagnoses. Historical
final odds are a hindsight proxy, not verified executable pre-start quotes.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/research/db-shared-win-direct-20261012.json"
OUTPUT = ROOT / "reports/generated/db-shared-win-direct"
PYTHON = ROOT / "python-api/.venv/Scripts/python.exe"
SOURCES = [
    "scripts/evaluate_db_shared_win_direct.py", "scripts/db_shared_win_direct_worker.py",
    "scripts/db_shared_win_direct_features.py", "scripts/db_shared_win_direct_diagnostics.py",
    "scripts/evaluate_db_shared_blend46_roi.py", "scripts/db_shared_blend46_betting.py",
    "scripts/benchmark_db_shared_full_worker.py", "scripts/evaluate_db_shared_venues.py",
    "python-api/experiments/features.py", "python-api/experiments/search_features.py",
    "python-api/experiments/dataset.py", "python-api/training/eligibility.py",
    "python-api/experiments/training.py", "keiba/keiba_ai/speed_deviation.py",
    "python-api/training/db_shared_features.py", "python-api/training/db_shared_fit.py",
    "python-api/forward_adapters/feature_state.py", "config/research/db-shared-win-direct-20261012.json",
]


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


def object_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_protocol(protocol):
    import db_shared_win_direct_worker as worker
    import db_shared_blend46_betting as betting
    if (protocol.get("schema") != "db-shared-win-direct-protocol-v1"
            or protocol.get("candidates") != list(worker.CANDIDATES)
            or protocol.get("phases") != {key: list(value) for key, value in worker.PERIODS.items()}
            or protocol.get("feature_counts") != [len(worker.feature_contract(name)[0]) for name in worker.CANDIDATES]
            or protocol.get("rules") != list(betting.RULES)
            or protocol.get("main_policy_family_size") != 19
            or protocol.get("maximum_parallel_fits") != 3 or protocol.get("threads_per_fit") != 2
            or protocol.get("bootstrap_repetitions") != 50000
            or protocol.get("probability_bootstrap_repetitions") != 2000
            or protocol.get("bootstrap_seed") != worker.SEED
            or any(protocol.get(key) is not False for key in ("publication", "activation", "forward_changes", "actual_purchase", "commit_push"))):
        raise ValueError("win-direct-frozen-protocol-required")
    calibration = protocol.get("calibration", {})
    if (calibration.get("kind") != worker.CALIBRATION_SCHEMA
            or calibration.get("a_bounds") != list(worker.CALIBRATION_BOUNDS[0])
            or calibration.get("b_bounds") != list(worker.CALIBRATION_BOUNDS[1])
            or calibration.get("ridge") != worker.CALIBRATION_RIDGE
            or calibration.get("initial") != [1., 0.] or calibration.get("fit_scope") != "CAL only"):
        raise ValueError("win-direct-frozen-calibration-required")


def preflight():
    import evaluate_db_shared_blend46_roi as old
    protocol = read(CONFIG)
    validate_protocol(protocol)
    old_protocol, source, prior, snapshot, before = old.preflight()
    if any(protocol[key] != old_protocol[key] for key in ("prediction_run", "prediction_results_sha256", "snapshot_sha256")):
        raise ValueError("win-direct-old-source-binding-changed")
    cache = (ROOT / protocol["raw47_cache"]).resolve(strict=True)
    if not cache.is_relative_to(ROOT / "reports/generated") or cache.is_symlink():
        raise ValueError("win-direct-readonly-cache-boundary")
    if sha(cache / "manifest.json") != protocol["raw47_manifest_sha256"]:
        raise ValueError("win-direct-cache-manifest-hash-changed")
    cache_manifest = read(cache / "manifest.json")
    # Cache provenance is keyed by importable module name, not file path.
    from training import db_shared_fit
    if db_shared_fit.source_hashes() != cache_manifest["source_hashes"]:
        raise ValueError("win-direct-frozen-raw-feature-source-changed")
    pins = {name: sha(ROOT / name) for name in SOURCES}
    return protocol, source, prior, snapshot, cache, before, pins


def merge_feature_cohort(base, features):
    """One-to-one key/date/horse proof; never silently drop partial races."""
    import numpy as np
    import db_shared_win_direct_features as feature_api
    import db_shared_win_direct_worker as worker
    keys = ["race_id", "race_date", "horse_number", "horse_id"]
    if base.duplicated(keys[:3]).any() or features.duplicated(keys[:3]).any():
        raise ValueError("win-direct-duplicate-feature-identity")
    safe = worker.feature_contract("market46_extended")[0][len(worker.MARKET_COLUMNS):]
    if len(safe) != 88 or not set(safe) <= set(features):
        raise ValueError("win-direct-exact-feature-88-required")
    input_frame = base.drop(columns=["probability"]).merge(features[keys + safe], on=keys,
        how="left", validate="one_to_one", indicator=True)
    if len(input_frame) != len(base) or not input_frame._merge.eq("both").all():
        raise ValueError("win-direct-feature-cohort-key-date-horse-mismatch")
    input_frame = feature_api.add_market_features(input_frame.drop(columns="_merge"))
    input_frame = input_frame.sort_values(["race_date", "race_id", "horse_number"], kind="mergesort").reset_index(drop=True)
    worker.split_positions(input_frame)
    for name in safe:
        if name not in worker.feature_contract("market46")[1]:
            values = input_frame[name].to_numpy(float)
            if np.isinf(values).any():
                raise ValueError("win-direct-infinite-feature")
    return input_frame


def prepare_data(source, prior, snapshot, cache):
    import benchmark_db_shared_full_worker as raw
    import evaluate_db_shared_blend46_roi as old
    import db_shared_win_direct_features as feature_api
    for directory in (ROOT / "python-api", ROOT / "keiba"):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    from training import db_shared_fit as fit
    manifest = read(cache / "manifest.json")
    target, raw47, checked = raw.read_cache(cache, {"snapshot_sha256": manifest["snapshot_sha256"],
        "period": manifest["period"], "expected_source_hashes": manifest["source_hashes"]}, fit)
    features, feature_receipt = feature_api.build_training_features(target, raw47)
    methods, _, _, audit = old.load_data(source, prior, snapshot)
    frame = merge_feature_cohort(methods["blend50"], features)
    return frame, {"historical_cache_rows": len(target), "historical_cache_races": int(target.race_id.nunique()),
        "raw47_manifest_sha256": sha(cache / "manifest.json"), "feature_receipt": feature_receipt,
        "market_payout_audit": audit, "identical_candidate_cohort": True,
        "raw46_exact_cache_parity": True, "old_prediction_used_as_feature": False,
        "old_raw_speed_score_retained_only_as_unused_betting_schema_metadata": True}


def run_subprocess(request_path, output_path):
    environment = os.environ.copy()
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        environment[name] = "2"
    clock = time.perf_counter()
    completed = subprocess.run([str(PYTHON), str(ROOT / "scripts/db_shared_win_direct_worker.py"),
        "--request", str(request_path), "--output", str(output_path)], env=environment,
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    # Logs contain only local worker diagnostics, no network/cookies/credentials.
    with output_path.with_suffix(".log").open("x", encoding="utf-8") as stream:
        stream.write(completed.stdout + completed.stderr)
    if completed.returncode != 0:
        raise ValueError("win-direct-worker-failed-" + output_path.stem)
    result = read(output_path)
    result["subprocess_wall_seconds"] = time.perf_counter() - clock
    return result


def load_predictions(workers, frame, protocol_hash, pins):
    import pandas as pd
    import numpy as np
    import db_shared_win_direct_worker as worker
    split = {}
    common = None
    for result in workers:
        name = result["candidate_id"]
        if (result.get("status") != "completed" or name not in worker.CANDIDATES
                or result["protocol_sha256"] != protocol_hash or result["source_hashes"] != pins
                or result["frame_sha256"] != sha(frame)
                or result["runtime"]["device"] != "cpu" or result["runtime"]["native_threads"] != 2):
            raise ValueError("win-direct-worker-result-binding")
        if name in split:
            raise ValueError("win-direct-duplicate-worker-method")
        request = read(frame.parent / (name + "-request.json"))
        expected_columns, expected_categorical = worker.feature_contract(name)
        if (result["request_sha256"] != object_hash(request)
                or result["feature_columns"] != expected_columns
                or result["categorical_columns"] != expected_categorical
                or result["runtime"]["parameters"] != worker.PARAMETERS
                or result["runtime"]["rounds"] != worker.BOOST_ROUNDS):
            raise ValueError("win-direct-result-request-feature-runtime-binding")
        for artifact in result["artifacts"].values():
            artifact_path = Path(artifact["path"])
            if (not artifact_path.resolve().is_relative_to(frame.parent.resolve())
                    or artifact_path.is_symlink() or sha(artifact_path) != artifact["sha256"]):
                raise ValueError("win-direct-all-worker-artifacts-hash-binding")
        if result["artifacts"]["model"].get("schema") != worker.MODEL_SCHEMA:
            raise ValueError("win-direct-research-model-schema-required")
        path = Path(result["artifacts"]["predictions"]["path"])
        if not path.resolve().is_relative_to(frame.parent.resolve()) or sha(path) != result["artifacts"]["predictions"]["sha256"]:
            raise ValueError("win-direct-prediction-artifact-binding")
        predictions = pd.read_parquet(path)
        if len(predictions) != result["artifacts"]["predictions"]["rows"]:
            raise ValueError("win-direct-prediction-count-binding")
        split[name] = {phase: predictions.loc[predictions.split.eq(phase)].copy().reset_index(drop=True)
                       for phase in ("SELECT", "TEST")}
        identity = predictions[["race_id", "race_date", "horse_number", "finish", "odds", "tansho_payout"]]
        if common is not None and not identity.equals(common):
            raise ValueError("win-direct-identical-method-cohort-required")
        common = identity
        for phase, part in split[name].items():
            if not part.race_date.between(*worker.PERIODS[phase]).all():
                raise ValueError("win-direct-prediction-period-binding")
            worker.validate_frame(part)
            if not np.allclose(part.groupby("race_id").probability.sum(), 1., atol=1e-8, rtol=0):
                raise ValueError("win-direct-invalid-prediction-race-sums")
    if set(split) != set(worker.CANDIDATES):
        raise ValueError("win-direct-all-three-candidates-required")
    # Normalized reciprocal odds is a no-fit diagnostic and policy reference.
    split["market_reciprocal"] = {phase: split["market_only"][phase].assign(
        probability=split["market_only"][phase].market_probability) for phase in ("SELECT", "TEST")}
    return split


def compact(value):
    return {key: item for key, item in value.items() if key != "daily_ledger"}


def evaluate_predictions(split, protocol, run, progress):
    import db_shared_blend46_betting as betting
    import db_shared_win_direct_diagnostics as diagnostics
    from threadpoolctl import threadpool_limits
    candidates = protocol["candidates"]
    boot_options = {"repetitions": protocol["bootstrap_repetitions"], "seed": protocol["bootstrap_seed"], "family_size": 19}
    diagnostic_options = {"repetitions": protocol["probability_bootstrap_repetitions"], "seed": protocol["bootstrap_seed"]}
    select_policies = {name + ":" + rule: betting.select_bets(split[name]["SELECT"], rule)
        for name in candidates for rule in protocol["rules"]}
    select_policies["market_favorite"] = betting.select_bets(split["market_reciprocal"]["SELECT"], "top1")
    progress("select_policy_bootstrap", policies=len(select_policies))
    with threadpool_limits(limits=2):
        boot_select = betting.bootstrap_policy_comparisons(split["market_only"]["SELECT"], select_policies,
            protocol["reference"], **boot_options)
    selections = {"overall": betting.choose_policy_on_select(split["market_only"]["SELECT"], select_policies, boot_select)}
    for name in candidates:
        policies = {key: value for key, value in select_policies.items() if key.startswith(name + ":")}
        family_boot = {**boot_select, "candidates": [record for record in boot_select["candidates"] if record["policy_id"] in policies]}
        selections[name] = betting.choose_policy_on_select(split[name]["SELECT"], policies, family_boot)
        # The best eligible SELECT rule is a diagnostic when the profit gate fails.
        choice = selections[name]["highest_candidate"]
        selections[name]["diagnostic_rule"] = choice["policy_id"].split(":", 1)[1] if choice else "top1"
        selections[name]["diagnostic_rule_is_purchase_authority"] = False
    save(run / "frozen-selection.json", selections)
    save(run / "frozen-selection-hash.json", {"sha256": object_hash(selections)})
    progress("selection_frozen_test_diagnostics")
    test_policies = {name + ":" + rule: betting.select_bets(split[name]["TEST"], rule)
        for name in candidates for rule in protocol["rules"]}
    test_policies["market_favorite"] = betting.select_bets(split["market_reciprocal"]["TEST"], "top1")
    with threadpool_limits(limits=2):
        boot_test = betting.bootstrap_policy_comparisons(split["market_only"]["TEST"], test_policies,
            protocol["reference"], **boot_options)
    probability, selected, policy_tables, yearly = {}, {}, {}, {}
    for phase, boot, policies in (("SELECT", boot_select, select_policies), ("TEST", boot_test, test_policies)):
        probability[phase], selected[phase], yearly[phase] = {}, {}, {}
        for name in [*candidates, "market_reciprocal"]:
            part = split[name][phase]
            progress("probability_and_price_band_audit", phase=phase, method=name)
            reference = split["market_only"][phase] if name != "market_only" else None
            with threadpool_limits(limits=2):
                probability[phase][name] = diagnostics.analyze_probability(part, reference,
                    **diagnostic_options)
                if name in candidates:
                    temperature = part.assign(probability=part.temperature_probability)
                    probability[phase][name]["temperature_only_diagnostic"] = diagnostics.analyze_probability(temperature,
                        repetitions=protocol["probability_bootstrap_repetitions"], seed=protocol["bootstrap_seed"])
                    rule = selections[name]["diagnostic_rule"]
                    bets = policies[name + ":" + rule]
                    selected[phase][name] = {"frozen_rule": rule,
                        "probability_diagnostics": diagnostics.selection_calibration(part, bets, **diagnostic_options),
                        "policy": compact(betting.summarize_policy(part, bets)),
                        "payout_sensitivity": betting.payout_sensitivity(part, bets)}
                    top1 = policies[name + ":top1"]
                    selected[phase][name]["top1_probability_diagnostics"] = diagnostics.selection_calibration(part, top1,
                        **diagnostic_options)
            yearly[phase][name] = [{"year": int(year), **betting.probability_metrics(group.reset_index(drop=True))}
                for year, group in part.groupby(part.race_date.str[:4])]
        policy_tables[phase] = [{"policy_id": record["policy_id"], **compact(record["summary"]),
            "roi_ci95": record["roi_ci95"], "roi_ci_simultaneous": record["roi_ci_simultaneous"],
            "profit_difference_per_eligible_race_vs_market": record["profit_difference_per_eligible_race_vs_reference"],
            "paired_profit_difference_ci95_vs_market": record["paired_profit_difference_ci95"],
            "paired_profit_difference_ci_simultaneous_vs_market": record["paired_profit_difference_ci_simultaneous"]}
            for record in boot["candidates"]]
    save(run / "bootstrap-select.json", boot_select)
    save(run / "bootstrap-test.json", boot_test)
    return {"frozen_selections": selections, "probability_metrics": probability,
        "selected_ticket_calibration": selected, "policy_tables": policy_tables, "yearly_probability_metrics": yearly}


def execute():
    clock = time.perf_counter()
    protocol, source, prior, snapshot, cache, before, pins = preflight()
    import db_shared_win_direct_worker as worker
    from evaluate_db_shared_venues import preservation_receipt
    run = OUTPUT / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8])
    run.mkdir(parents=True, exist_ok=False)
    def progress(stage, **extra):
        print(json.dumps({"stage": stage, "elapsed_seconds": time.perf_counter() - clock, **extra}), flush=True)
    locked = {"protocol": protocol, "source_hashes": pins, "preservation_before": before,
        "raw47_manifest_sha256": sha(cache / "manifest.json"),
        "prediction_manifest_sha256": sha(source / "artifact-manifest.json")}
    protocol_hash = object_hash(locked)
    save(run / "protocol.json", locked)
    save(run / "protocol-hash.json", {"sha256": protocol_hash})
    try:
        progress("prepare_once_full_history_and_market_payout")
        started = time.perf_counter()
        frame, audit = prepare_data(source, prior, snapshot, cache)
        audit["partitions"] = {name: worker.validate_frame(frame.iloc[positions].reset_index(drop=True))
            for name, positions in worker.split_positions(frame).items()}
        frame_path = run / "common-input.parquet"
        frame.attrs = {}
        with frame_path.open("xb") as stream:
            frame.to_parquet(stream, index=False, compression="zstd")
        input_hash = sha(frame_path)
        audit.update(frame_sha256=input_hash, frame_bytes=frame_path.stat().st_size,
            requested_period=["2016-01", "2026-09"], actual_period=[frame.race_date.min(), frame.race_date.max()])
        save(run / "data-audit.json", audit)
        preparation_seconds = time.perf_counter() - started
        requests = {}
        for name in protocol["candidates"]:
            columns, categorical = worker.feature_contract(name)
            request = {"schema": worker.SCHEMA, "job_id": name, "candidate_id": name,
                "frame_path": str(frame_path.resolve()), "frame_sha256": input_hash,
                "protocol_sha256": protocol_hash, "expected_source_hashes": pins,
                "feature_columns": columns, "categorical_columns": categorical, "device": "cpu", "n_threads": 2}
            path = run / (name + "-request.json")
            save(path, request)
            requests[name] = path
        progress("parallel_direct_win_fit", cpu_processes=3, threads_each=2, runners=len(frame), races=int(frame.race_id.nunique()))
        started = time.perf_counter()
        workers = []
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(run_subprocess, path, run / (name + "-result.json")): name for name, path in requests.items()}
            for future in as_completed(futures):
                result = future.result()
                workers.append(result)
                progress("worker_finished", method=result["candidate_id"], seconds=result["elapsed_seconds"])
        parallel_seconds = time.perf_counter() - started
        workers.sort(key=lambda result: protocol["candidates"].index(result["candidate_id"]))
        split = load_predictions(workers, frame_path, protocol_hash, pins)
        evaluated = evaluate_predictions(split, protocol, run, progress)
        after = preservation_receipt()
        if before != after or sha(snapshot) != protocol["snapshot_sha256"]:
            raise ValueError("win-direct-protected-state-or-snapshot-mutated")
        if any(sha(ROOT / name) != value for name, value in pins.items()):
            raise ValueError("win-direct-source-mutated-during-study")
        if sha(cache / "manifest.json") != protocol["raw47_manifest_sha256"] or sha(source / "results.json") != protocol["prediction_results_sha256"]:
            raise ValueError("win-direct-old-research-mutated")
        result = {"schema": "db-shared-win-direct-result-v1", "status": "completed", "output": str(run.resolve()),
            "protocol_sha256": protocol_hash, "data_audit": audit, "worker_results": workers,
            **evaluated, "preparation_seconds": preparation_seconds, "parallel_worker_wall_seconds": parallel_seconds,
            "elapsed_seconds": time.perf_counter() - clock, "preservation_before": before, "preservation_after": after,
            "models_research_only": True, "models_published": False, "models_activated": False,
            "forward_changed": False, "actual_bets": 0, "test_outcomes_used_for_selection": False,
            "test_outcomes_used_for_fit": False, "executable_odds_verified": False, "independent_future_validation": False,
            "market_edge_scope": "retrospective evidence only; historical prices/periods previously inspected",
            "roi_target_guaranteed": False}
        save(run / "results.json", result)
        files = {path.relative_to(run).as_posix(): {"sha256": sha(path), "bytes": path.stat().st_size}
            for path in run.rglob("*") if path.is_file()}
        save(run / "artifact-manifest.json", {"schema": "db-shared-win-direct-manifest-v1", "protocol_sha256": protocol_hash, "files": files})
        progress("completed", output=str(run), seconds=result["elapsed_seconds"])
        return result
    except BaseException as error:
        save(run / "failure.json", {"error": type(error).__name__ + ": " + str(error),
            "elapsed_seconds": time.perf_counter() - clock, "preservation_after": preservation_receipt()})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.execute:
        execute()
    else:
        protocol, source, prior, snapshot, cache, before, pins = preflight()
        print(json.dumps({"status": "preflight_passed", "active_model": before["active_model_id"],
            "phases": protocol["phases"], "feature_counts": protocol["feature_counts"]}))
