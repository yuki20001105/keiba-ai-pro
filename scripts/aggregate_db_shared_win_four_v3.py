"""User-authorized evaluation-only repair of the saved 44-model v2 study.

No fitting, subprocess, feature regeneration, acquisition, activation or purchase
API is called. The only numerical-code change is the empty selected-key boolean
reduction compatibility fix. Historical final odds remain retrospective proxies.
"""
from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
import uuid

import evaluate_db_shared_win_four_v2 as v2
import db_shared_win_four_worker as worker
from evaluate_db_shared_venues import preservation_receipt


ROOT = v2.ROOT
CONFIG = v2.CONFIG
CANDIDATES = v2.CANDIDATES
SAVED_STUDY = ROOT / "reports/generated/db-shared-win-four-v2/20261012T075916874189Z-92f0f837"
OUTPUT = ROOT / "reports/generated/db-shared-win-four-v3"
AMENDMENT_PATH = OUTPUT / "technical-amendment-20261012-v3.json"
AMENDMENT_MANIFEST_PATH = OUTPUT / "technical-amendment-20261012-v3-manifest.json"
AGGREGATION_SOURCES = ("scripts/aggregate_db_shared_win_four_v3.py", "scripts/db_shared_win_four_diagnostics_v3.py")
REVIEW_PINS = {
    "reports/generated/db-shared-win-four-review/v2-failed44-integrity-20261012.json":
        "93c5c4e73fccbe49451e548a929faea68d98eb8fcc205da2a135866293b05cc8",
    "reports/generated/db-shared-win-four-review/v2-empty-selection-full-readonly-diagnosis-20261012.json":
        "8df140d4ff48e3eb5976792f7ff036d8fc0b70e2f4bbb39c5b96dcc37247c97d",
}
SAVED_STUDY_FINGERPRINT = "dd7ba3d310129b687c105e701164fd2f1be4daa9bc3d444ea459d67509010b76"
SAVED_STUDY_FILE_COUNT = 311
sha = v2.sha
read = v2.read
save = v2.save
attach_predictions = v2.attach_predictions


def object_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def study_inventory():
    files = {path.relative_to(SAVED_STUDY).as_posix(): sha(path)
             for path in sorted(SAVED_STUDY.rglob("*")) if path.is_file()}
    return {"files_sha256": files, "file_count": len(files), "fingerprint_sha256": object_hash(files)}


def verify_semantic_copy():
    """Check the one compatibility node and the copied evaluator before use."""
    original = ast.parse((ROOT / "scripts/db_shared_win_direct_diagnostics.py").read_text(encoding="utf-8"))
    fixed = ast.parse((ROOT / "scripts/db_shared_win_four_diagnostics_v3.py").read_text(encoding="utf-8"))
    selection = next(node for node in fixed.body if isinstance(node, ast.FunctionDef) and node.name == "selection_calibration")
    replacements = 0
    for node in ast.walk(selection):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "all"
                and isinstance(node.func.value, ast.Call) and isinstance(node.func.value.func, ast.Attribute)
                and node.func.value.func.attr == "to_numpy"):
            conversion = node.func.value
            if (conversion.args or len(conversion.keywords) != 1 or conversion.keywords[0].arg != "dtype"
                    or not isinstance(conversion.keywords[0].value, ast.Name) or conversion.keywords[0].value.id != "bool"
                    or not isinstance(conversion.func.value, ast.Call)):
                raise ValueError("four-v3-only-empty-key-boolean-reduction-permitted")
            node.func.value = conversion.func.value
            replacements += 1
    dump = lambda value: ast.dump(value, include_attributes=False)
    if replacements != 1 or dump(original) != dump(fixed):
        raise ValueError("four-v3-diagnostic-math-or-other-guards-changed")
    old_driver = ast.parse(v2.V2_DRIVER.read_text(encoding="utf-8"))
    current = ast.parse((ROOT / AGGREGATION_SOURCES[0]).read_text(encoding="utf-8"))
    get_eval = lambda tree: next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "evaluate_predictions")
    expected, actual = get_eval(old_driver), get_eval(current)
    imports = [node for node in ast.walk(actual) if isinstance(node, ast.Import)
               and any(item.name == "db_shared_win_four_diagnostics_v3" for item in node.names)]
    if len(imports) != 1 or len(imports[0].names) != 1:
        raise ValueError("four-v3-evaluator-import-shape-changed")
    imports[0].names[0].name = "db_shared_win_direct_diagnostics"
    if dump(expected) != dump(actual):
        raise ValueError("four-v3-evaluation-formula-period-rules-or-bootstrap-changed")
    forbidden = {"fit", "fit_model", "fit_candidate", "run_subprocess", "prepare_data", "subprocess"}
    for node in ast.walk(current):
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
            if (name in forbidden or (name == "execute" and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "v2")):
                raise ValueError("four-v3-fit-preprocess-or-subprocess-call-forbidden")
    return {"only_selected_race_id_boolean_array_reduction_changed": True,
            "diagnostics_ast_sha256_after_unwrap": hashlib.sha256(dump(original).encode()).hexdigest(),
            "copied_evaluator_ast_sha256_after_import_unwrap": hashlib.sha256(dump(expected).encode()).hexdigest(),
            "evaluation_math_thresholds_periods_rules_bootstrap_unchanged": True,
            "fitter_subprocess_preprocessing_calls": 0}


def validate_saved_study():
    """Recompute every original source/receipt and all 132 saved artifacts."""
    protocol = read(CONFIG)
    v2.validate_protocol(protocol)
    recorded = read(SAVED_STUDY / "protocol.json")
    failure = read(SAVED_STUDY / "failure.json")
    if (recorded.get("protocol") != protocol or recorded.get("protocol_sha256") != sha(CONFIG)
            or failure.get("status") != "failed" or failure.get("error_type") != "TypeError"
            or failure.get("reason") != "Cannot perform reduction 'all' with string dtype"
            or (SAVED_STUDY / "results.json").exists() or (SAVED_STUDY / "artifact_manifest.json").exists()):
        raise ValueError("four-v3-exact-failed44-study-required")
    pins = recorded["source_hashes"]
    if set(pins) != set(v2.SOURCES) or any(sha(ROOT / name) != value for name, value in pins.items()):
        raise ValueError("four-v3-original19-source-drift")
    for name, value in REVIEW_PINS.items():
        if sha(ROOT / name) != value:
            raise ValueError("four-v3-independent-review-receipt-drift")
    proof_path, proof_sha, proof = v2.phase1_gate(ROOT / protocol["phase1_plan"])
    before = preservation_receipt()
    if before != recorded["preservation_before"] or before != failure["preservation_after"]:
        raise ValueError("four-v3-protected-state-drift")
    v2.read_amendment(protocol, pins, proof, before)
    if (sha(v2.AMENDMENT_PATH) != recorded["technical_amendment_sha256"]
            or sha(v2.AMENDMENT_MANIFEST_PATH) != recorded["technical_amendment_manifest_sha256"]
            or recorded["phase1_proof_sha256"] != proof_sha):
        raise ValueError("four-v3-original-amendment-or-phase1-binding-drift")
    inventory = study_inventory()
    if inventory["file_count"] != SAVED_STUDY_FILE_COUNT or inventory["fingerprint_sha256"] != SAVED_STUDY_FINGERPRINT:
        raise ValueError("four-v3-original311-file-fingerprint-drift")
    frame_path = SAVED_STUDY / "shared50.parquet"
    frame_sha = sha(frame_path)
    results, requests, artifacts = [], [], 0
    for year in protocol["fold_years"]:
        for candidate in CANDIDATES:
            job_id = f"{year}-{candidate}"
            request = read(SAVED_STUDY / (job_id + "-request.json"))
            result = read(SAVED_STUDY / (job_id + ".json"))
            v2.validate_worker_result(result, request, worker)
            if request["expected_source_hashes"] != pins or request["frame_sha256"] != frame_sha:
                raise ValueError("four-v3-worker-original-source-frame-binding")
            for receipt in result["artifacts"].values():
                path = Path(receipt["path"]).resolve()
                if not path.is_relative_to(SAVED_STUDY.resolve()) or sha(path) != receipt["sha256"]:
                    raise ValueError("four-v3-saved-worker-artifact-drift")
                artifacts += 1
            results.append(result)
            requests.append(request)
    if len(results) != 44 or artifacts != 132:
        raise ValueError("four-v3-exact44-workers-132-artifacts-required")
    return {"protocol": protocol, "protocol_sha256": sha(CONFIG), "source_hashes": pins,
            "worker_results": sorted(results, key=lambda item: item["job_id"]), "inventory": inventory,
            "shared_frame_sha256": frame_sha, "phase1_proof_path": str(proof_path),
            "phase1_proof_sha256": proof_sha, "phase1_pass": True, "preservation_before": before,
            "v2_amendment_sha256": recorded["technical_amendment_sha256"],
            "v2_amendment_manifest_sha256": recorded["technical_amendment_manifest_sha256"],
            "original_preregistration_sha256": sha(v2.LOCK_PATH), "validated_worker_artifacts": artifacts}


def verify_saved_data(frame, metadata):
    """Audit saved inputs and actual payouts without regenerating features."""
    import numpy as np
    import pandas as pd
    protocol = metadata["protocol"]
    worker.validate_frame(frame, require_results=True, require_payout=False)
    cache = ROOT / protocol["raw47_cache"]
    if sha(cache / "manifest.json") != protocol["raw47_manifest_sha256"]:
        raise ValueError("four-v3-original-raw47-manifest-drift")
    manifest = read(cache / "manifest.json")
    for receipt in manifest["files"].values():
        if sha(cache / receipt["path"]) != receipt["sha256"]:
            raise ValueError("four-v3-original-raw47-cache-drift")
    target = pd.read_parquet(cache / manifest["files"]["target"]["path"])
    features = pd.read_parquet(cache / manifest["files"]["features"]["path"])
    if len(target) != 586247 or target.race_id.nunique() != 41616 or len(features) != len(target):
        raise ValueError("four-v3-original-cache-cardinality-drift")
    keys = ["race_id", "race_date", "horse_number"]
    if target.duplicated(keys).any() or frame.duplicated(keys).any():
        raise ValueError("four-v3-input-identity-duplicate")
    index = pd.MultiIndex.from_frame(target[keys]).get_indexer(pd.MultiIndex.from_frame(frame[keys]))
    if (index < 0).any():
        raise ValueError("four-v3-input-outside-original-cache")
    pd.testing.assert_frame_equal(frame[target.columns].reset_index(drop=True), target.iloc[index].reset_index(drop=True))
    feature_columns = worker.feature_contract(CANDIDATES[0])[0][4:]
    pd.testing.assert_frame_equal(frame[feature_columns].reset_index(drop=True), features.iloc[index][feature_columns].reset_index(drop=True))
    ids = set(frame.race_id)
    original = target.assign(_bad=~np.isfinite(target.odds.to_numpy(float)) | target.odds.lt(1).to_numpy())
    bad = set(original.loc[original._bad, "race_id"])
    counts = target.finish.eq(1).groupby(target.race_id, sort=False).sum()
    bad |= set(counts.loc[counts.ne(1)].index)
    if ids != set(target.race_id) - bad or len(frame) != len(target.loc[~target.race_id.isin(bad)]):
        raise ValueError("four-v3-original-common-cohort-changed")
    snapshot = ROOT / "keiba/data/model_retrain_snapshots" / (protocol["snapshot_sha256"] + ".db")
    if sha(snapshot) != protocol["snapshot_sha256"]:
        raise ValueError("four-v3-immutable-snapshot-drift")
    rows = defaultdict(list)
    connection = sqlite3.connect(snapshot.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        for race_id, combination, payout in connection.execute(
                "SELECT race_id,combinations,payout FROM return_tables_ultimate WHERE bet_type='単勝'"):
            if race_id in ids:
                rows[race_id].append((combination, payout))
    finally:
        connection.close()
    winners = frame.loc[frame.finish.eq(1)].set_index("race_id").horse_number.to_dict()
    valid, invalid = {}, {}
    for race_id, entries in rows.items():
        if len(entries) != 1:
            invalid[race_id] = "duplicate_or_multiple_winner_return"
            continue
        token, payout = str(entries[0][0]), entries[0][1]
        try:
            amount = float(payout)
        except (TypeError, ValueError):
            invalid[race_id] = "invalid_return_amount"
            continue
        if (re.fullmatch(r"[1-9]\d?", token) is None or not 1 <= int(token) <= 18
                or not math.isfinite(amount) or amount < 100 or amount % 10 != 0):
            invalid[race_id] = "invalid_return_key_or_amount"
        elif race_id not in winners or int(winners[race_id]) != int(token):
            invalid[race_id] = "winner_return_key_conflict"
        else:
            valid[race_id] = amount
    expected_complete = frame.race_id.isin(valid)
    expected_payout = np.where(frame.finish.eq(1), frame.race_id.map(valid), np.nan)
    if (not frame.race_payout_complete.equals(expected_complete)
            or not np.array_equal(frame.tansho_payout.to_numpy(float), expected_payout, equal_nan=True)):
        raise ValueError("four-v3-saved-payout-not-literal-immutable-return-table")
    missing = sorted(ids - set(valid))
    return {"data_audit_scope": "saved-v2-frame/raw47-cache/readonly-immutable-actual-return-verification",
            "cache_rows": len(target), "cache_races": int(target.race_id.nunique()),
            "prediction_rows": len(frame), "prediction_races": int(frame.race_id.nunique()),
            "whole_prediction_excluded_races": sorted(bad), "raw46_cache_reused": True,
            "point_in_time_audit": manifest["point_in_time_audit"],
            "payout_audit": {"input_races": len(ids), "confirmed_payout_races": len(valid),
                "missing_or_invalid_payout_races": len(missing),
                "excluded_reasons": {race_id: invalid.get(race_id, "return_unavailable") for race_id in missing},
                "snapshot_read_only": True, "odds_substitution": False, "fitting_rows_excluded_due_to_payout": 0},
            "date_from": frame.race_date.min(), "date_to": frame.race_date.max(),
            "all_four_identical_prediction_cohort": True, "historical_prestart_quotes_verified": False,
            "new_feature_generation": False, "cohort_changed": False}


def load_saved_predictions(metadata):
    import pandas as pd
    frame = pd.read_parquet(SAVED_STUDY / "shared50.parquet")
    audit = verify_saved_data(frame, metadata)
    predictions = {}
    for candidate in CANDIDATES:
        parts = []
        for result in metadata["worker_results"]:
            if result["candidate_id"] != candidate:
                continue
            receipt = result["artifacts"]["predictions"]
            part = pd.read_parquet(receipt["path"])
            if "split" in part:
                part = part.loc[part.split.eq("TEST")]
            bounds, test = result["periods"]["TEST"], result["partitions"]["TEST"]
            if (len(part) != receipt["rows"] or len(part) != test["runner_count"]
                    or not part.candidate_id.eq(candidate).all() or not part.fold_year.eq(result["fold_year"]).all()
                    or not part.race_date.between(*bounds).all() or part.race_id.nunique() != test["race_count"]):
                raise ValueError("four-v3-original-test-candidate-fold-binding")
            parts.append(part)
        predictions[candidate] = pd.concat(parts, ignore_index=True)
        v2.attach_predictions(frame.loc[frame.race_date.ge("20160101")], predictions[candidate])
    return frame, predictions, audit


def manifest_for(amendment):
    return {"schema": "db-shared-win-four-aggregation-amendment-manifest-v3",
            "amendment_sha256": sha(AMENDMENT_PATH), "amendment_bytes": AMENDMENT_PATH.stat().st_size,
            "saved_study_fingerprint_sha256": amendment["saved_study_fingerprint_sha256"],
            "aggregation_source_hashes": amendment["aggregation_source_hashes"]}


def read_amendment(metadata):
    if not AMENDMENT_PATH.is_file() or not AMENDMENT_MANIFEST_PATH.is_file():
        raise ValueError("four-v3-sealed-amendment-required-before-aggregation")
    receipt = read(AMENDMENT_PATH)
    expected = {"schema": "db-shared-win-four-aggregation-amendment-v3", "human_authorized": True,
        "post_outcome_technical_amendment": True, "new_preregistration_claim": False,
        "new_fits_started": 0, "saved_v2_artifacts_reused": True, "old_v1_models_adopted": False,
        "saved_study_path": str(SAVED_STUDY.resolve()), "saved_study_fingerprint_sha256": SAVED_STUDY_FINGERPRINT,
        "original_source_hashes": metadata["source_hashes"],
        "aggregation_source_hashes": {name: sha(ROOT / name) for name in AGGREGATION_SOURCES},
        "protocol_sha256": metadata["protocol_sha256"], "phase1_proof_sha256": metadata["phase1_proof_sha256"],
        "v2_amendment_sha256": metadata["v2_amendment_sha256"], "original_preregistration_sha256": metadata["original_preregistration_sha256"],
        "review_receipt_hashes": REVIEW_PINS, "semantic_copy_proof": verify_semantic_copy(),
        "preservation_before": metadata["preservation_before"]}
    if any(receipt.get(key) != value for key, value in expected.items()) or read(AMENDMENT_MANIFEST_PATH) != manifest_for(receipt):
        raise ValueError("four-v3-amendment-source-provenance-drift")
    return receipt


def register_amendment():
    metadata = validate_saved_study()
    if AMENDMENT_PATH.exists() or AMENDMENT_MANIFEST_PATH.exists():
        read_amendment(metadata)
        return {"status": "already_registered_amendment", "path": str(AMENDMENT_PATH), "new_fits_started": 0}
    if OUTPUT.exists() and any(OUTPUT.glob("*/results.json")):
        raise ValueError("four-v3-evaluation-before-amendment-forbidden")
    receipt = {"schema": "db-shared-win-four-aggregation-amendment-v3", "registered_at": datetime.now(timezone.utc).isoformat(),
        "human_authorized": True, "authorization_scope": "Empty-data type repair and saved44 aggregation only; no retraining",
        "post_outcome_technical_amendment": True, "new_preregistration_claim": False,
        "new_fits_started": 0, "saved_v2_artifacts_reused": True, "old_v1_models_adopted": False,
        "saved_study_path": str(SAVED_STUDY.resolve()), "saved_study_fingerprint_sha256": SAVED_STUDY_FINGERPRINT,
        "original_source_hashes": metadata["source_hashes"],
        "aggregation_source_hashes": {name: sha(ROOT / name) for name in AGGREGATION_SOURCES},
        "protocol_sha256": metadata["protocol_sha256"], "phase1_proof_sha256": metadata["phase1_proof_sha256"],
        "v2_amendment_sha256": metadata["v2_amendment_sha256"], "original_preregistration_sha256": metadata["original_preregistration_sha256"],
        "review_receipt_hashes": REVIEW_PINS, "semantic_copy_proof": verify_semantic_copy(),
        "preservation_before": metadata["preservation_before"]}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    save(AMENDMENT_PATH, receipt)
    save(AMENDMENT_MANIFEST_PATH, manifest_for(receipt))
    read_amendment(metadata)
    return {"status": "registered_amendment", "path": str(AMENDMENT_PATH), "sha256": sha(AMENDMENT_PATH), "new_fits_started": 0}


def evaluate_predictions(predictions, frame, protocol):
    import numpy as np
    import pandas as pd
    import db_shared_blend46_betting as betting
    import db_shared_win_four_diagnostics_v3 as diagnostics
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


def aggregate():
    started = time.perf_counter()
    metadata = validate_saved_study()
    amendment = read_amendment(metadata)
    destination = OUTPUT / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8])
    destination.mkdir(parents=True, exist_ok=False)
    save(destination / "protocol.json", {"protocol": metadata["protocol"], "protocol_sha256": metadata["protocol_sha256"],
        "source_hashes": metadata["source_hashes"], "aggregation_source_hashes": amendment["aggregation_source_hashes"],
        "technical_amendment_sha256": sha(AMENDMENT_PATH), "worker_study_path": str(SAVED_STUDY.resolve()),
        "saved_study_fingerprint_sha256": SAVED_STUDY_FINGERPRINT, "new_fits_started": 0})
    try:
        clock = time.perf_counter()
        frame, predictions, data_audit = load_saved_predictions(metadata)
        verification_seconds = time.perf_counter() - clock
        clock = time.perf_counter()
        evaluation = evaluate_predictions(predictions, frame, metadata["protocol"])
        evaluation_seconds = time.perf_counter() - clock
        if study_inventory() != metadata["inventory"] or preservation_receipt() != metadata["preservation_before"]:
            raise ValueError("four-v3-original-artifact-or-protected-state-changed")
        read_amendment(metadata)
        result = {"schema": "db-shared-win-four-aggregation-results-v3", "status": "completed",
            "protocol_sha256": metadata["protocol_sha256"], "source_hashes": metadata["source_hashes"],
            "aggregation_source_hashes": amendment["aggregation_source_hashes"],
            "phase1_proof_sha256": metadata["phase1_proof_sha256"], "phase1_pass": True,
            "worker_study_path": str(SAVED_STUDY.resolve()), "worker_results": metadata["worker_results"],
            "data_audit": data_audit, "evaluation": evaluation, "technical_amendment_sha256": sha(AMENDMENT_PATH),
            "preservation_before": metadata["preservation_before"], "preservation_after": preservation_receipt(),
            "runtime": {"mode": "saved44-evaluation-only", "new_fits_started": 0, "saved_v2_artifacts_reused": True,
                "old_v1_models_adopted": False, "validated_saved_worker_count": 44,
                "input_and_payout_verification_seconds": verification_seconds, "evaluation_seconds": evaluation_seconds,
                "entire_elapsed_seconds": time.perf_counter() - started, "fit_wall_seconds_this_run": 0,
                "same_work_serial_speedup_verified": False, "gpu_speedup_verified": False},
            "actual_purchase": False, "production_changed": False, "forward_collection_changed": False,
            "post_outcome_technical_amendment": True, "new_preregistration_claim": False}
        save(destination / "results.json", result)
        save(destination / "artifact_manifest.json", {"schema": "db-shared-win-four-aggregation-manifest-v3",
            "files": {str(path.relative_to(destination)): sha(path) for path in sorted(destination.rglob("*")) if path.is_file()}})
        print(json.dumps({"status": "completed", "directory": str(destination), "results_sha256": sha(destination / "results.json"),
                          "new_fits_started": 0}, ensure_ascii=False))
        return result
    except Exception as error:
        save(destination / "failure.json", {"status": "failed", "error_type": type(error).__name__, "reason": str(error),
            "preservation_after": preservation_receipt(), "new_fits_started": 0, "saved_v2_artifacts_reused": True})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--register-amendment", action="store_true")
    parser.add_argument("--run", action="store_true", help="Aggregate the saved 44 models; never retrain")
    args = parser.parse_args()
    if args.register_amendment:
        print(json.dumps(register_amendment(), ensure_ascii=False))
    elif args.run:
        aggregate()
    else:
        parser.error("choose --register-amendment or --run")


if __name__ == "__main__":
    main()
