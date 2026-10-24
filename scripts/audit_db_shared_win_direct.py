"""External, read-only audit of a completed direct-win experiment.

The sealed study is never modified. No models/calibrators are refitted, no
policy is retuned, no network call or database write is made, and no serving
authority is granted. A new exclusive JSON receipt is written outside the run.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
for _directory in (ROOT / "scripts", ROOT / "python-api", ROOT / "keiba"):
    if str(_directory) not in sys.path:
        sys.path.insert(0, str(_directory))

import db_shared_win_direct_worker as worker
import db_shared_win_direct_diagnostics as diagnostics

OLD_ROI_RUN = "reports/generated/db-shared-blend46-roi/20261012T024802090432Z-1f096e1f"
OLD_ROI_RESULT_SHA = "88e0f6137cf1854b1a147cd9382afeedbae2b0fc0b6ff71d73fcad463665f023"
OLD_ROI_MANIFEST_SHA = "589b10bca5efbe6115e45b863ff7147a5dc572d3271d898489deb3e2f441ef87"
KEYS = ["race_id", "horse_number"]
IDENTITY = ["race_id", "race_date", "horse_number", "finish", "odds", "tansho_payout",
            "expected_runner_count", "venue", "surface"]
RULES = ("top1", "top1_odds3_20", "top1_ev105", "max_ev105", "max_ev115", "top3_ev105")


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def object_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def bounded(base, relative):
    if (not isinstance(relative, str) or not relative or relative.startswith("/") or "\\" in relative
            or Path(relative).is_absolute() or ".." in Path(relative).parts
            or Path(relative).as_posix() != relative):
        raise ValueError("win-audit-relative-path-required")
    candidate = Path(base) / relative
    if any(item.is_symlink() for item in (candidate, *candidate.parents)):
        raise ValueError("win-audit-symlink-forbidden")
    candidate = candidate.resolve(strict=True)
    if not candidate.is_relative_to(Path(base).resolve(strict=True)) or not candidate.is_file():
        raise ValueError("win-audit-file-boundary")
    return candidate


def verify_manifest(run, expected_hash=None, *, exact_files=False, legacy_windows_paths=False):
    manifest_path = bounded(run, "artifact-manifest.json")
    if expected_hash is not None and sha(manifest_path) != expected_hash:
        raise ValueError("win-audit-manifest-hash-mismatch")
    manifest = read(manifest_path)
    if not isinstance(manifest.get("files"), dict) or not manifest["files"]:
        raise ValueError("win-audit-manifest-files-required")
    for relative, receipt in manifest["files"].items():
        path = bounded(run, relative.replace("\\", "/") if legacy_windows_paths else relative)
        if (not isinstance(receipt, dict) or path.stat().st_size != receipt.get("bytes")
                or sha(path) != receipt.get("sha256")):
            raise ValueError("win-audit-manifest-file-changed")
    if exact_files:
        observed = {path.relative_to(run).as_posix() for path in Path(run).rglob("*") if path.is_file()}
        if observed != set(manifest["files"]) | {"artifact-manifest.json"}:
            raise ValueError("win-audit-sealed-run-extra-or-missing-file")
    return manifest


def verified_artifact(run, receipt):
    path = Path(receipt["path"])
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("win-audit-symlink-forbidden")
    path = path.resolve(strict=True)
    if not path.is_relative_to(Path(run).resolve(strict=True)) or not path.is_file() or sha(path) != receipt["sha256"]:
        raise ValueError("win-audit-worker-artifact-changed")
    return path


def verify_request(request, result, protocol_hash, pins, frame_path):
    candidate = result.get("candidate_id")
    columns, categorical = worker.feature_contract(candidate)
    if (set(request) != worker.REQUEST_KEYS or request.get("schema") != worker.SCHEMA
            or request.get("candidate_id") != candidate or request.get("job_id") != candidate
            or result.get("schema") != worker.SCHEMA or result.get("status") != "completed"
            or result.get("job_id") != candidate or result.get("request_sha256") != object_hash(request)
            or result.get("source_hashes") != pins or request.get("expected_source_hashes") != pins
            or request.get("protocol_sha256") != protocol_hash or result.get("protocol_sha256") != protocol_hash
            or Path(request.get("frame_path", "")).resolve() != Path(frame_path).resolve()
            or request.get("frame_sha256") != sha(frame_path) or result.get("frame_sha256") != sha(frame_path)
            or request.get("feature_columns") != columns or result.get("feature_columns") != columns
            or request.get("categorical_columns") != categorical or result.get("categorical_columns") != categorical
            or result.get("feature_count") != len(columns) or result.get("periods") != {name: list(bounds) for name, bounds in worker.PERIODS.items()}
            or request.get("device") != "cpu" or request.get("n_threads") != 2
            or result.get("runtime", {}).get("parameters") != worker.PARAMETERS
            or result.get("runtime", {}).get("rounds") != worker.BOOST_ROUNDS):
        raise ValueError("win-audit-worker-request-binding")


def prediction_match(actual, expected, outputs):
    if not set(IDENTITY) <= set(actual) or len(actual) != len(expected):
        raise ValueError("win-audit-prediction-cohort-count")
    try:
        pd.testing.assert_frame_equal(actual[IDENTITY].reset_index(drop=True), expected[IDENTITY].reset_index(drop=True),
                                      check_dtype=False, check_exact=True)
    except AssertionError as error:
        raise ValueError("win-audit-prediction-cohort-values") from error
    errors = {}
    for name, values in outputs.items():
        if name not in actual or not np.array_equal(actual[name].to_numpy(float), np.asarray(values, float)):
            raise ValueError("win-audit-saved-model-replay-difference:" + name)
        errors[name] = 0.
    return errors


def policy_keys(frame, rule):
    """Independent fixed decision recipe; projects away every outcome field."""
    if rule not in RULES:
        raise ValueError("win-audit-fixed-rule-required")
    value = frame[KEYS + ["odds", "probability"]].copy()
    value = value.sort_values(["race_id", "probability", "horse_number"], ascending=[True, False, True], kind="mergesort")
    value["rank"] = value.groupby("race_id", sort=False).cumcount() + 1
    value["ev"] = value.probability * value.odds
    if rule == "top1":
        chosen = value.loc[value["rank"].eq(1)]
    elif rule == "top1_odds3_20":
        chosen = value.loc[value["rank"].eq(1) & value.odds.ge(3.) & value.odds.le(20.)]
    elif rule == "top1_ev105":
        chosen = value.loc[value["rank"].eq(1) & value.ev.ge(1.05)]
    elif rule in ("max_ev105", "max_ev115"):
        minimum = 1.05 if rule == "max_ev105" else 1.15
        eligible = value.loc[value.odds.le(30.) & value.probability.ge(.02) & value.ev.ge(minimum)]
        chosen = eligible.sort_values(["race_id", "ev", "horse_number"], ascending=[True, False, True],
                                     kind="mergesort").drop_duplicates("race_id")
    else:
        chosen = value.loc[value["rank"].le(3) & value.ev.ge(1.05) & value.odds.le(30.)]
    result = chosen[KEYS].sort_values(KEYS, kind="mergesort").reset_index(drop=True)
    result["stake"] = 100.
    return result


def independent_settlement(frame, tickets):
    """Saved actual payouts only. Quoted odds never settle a ticket."""
    if tickets.duplicated(KEYS).any() or not tickets.stake.eq(100.).all():
        raise ValueError("win-audit-unique-100-yen-tickets-required")
    if tickets.groupby("race_id").size().gt(3).any():
        raise ValueError("win-audit-maximum-three-tickets-required")
    result = tickets[KEYS + ["stake"]].merge(frame[KEYS + ["race_date", "finish", "tansho_payout"]],
        on=KEYS, how="left", validate="one_to_one", indicator=True)
    if not result._merge.eq("both").all():
        raise ValueError("win-audit-ticket-outside-cohort")
    result["hit"] = result.finish.eq(1)
    winner_payout = result.loc[result.hit, "tansho_payout"].to_numpy(float)
    if not np.isfinite(winner_payout).all() or (winner_payout < 100).any():
        raise ValueError("win-audit-actual-winner-payout-required")
    result["payout"] = np.where(result.hit, result.tansho_payout, 0.)
    result["profit"] = result.payout - result.stake
    return result.drop(columns="_merge")


def settlement_summary(frame, settlement):
    dates = pd.Index(sorted(frame.race_date.unique()), name="race_date")
    ledger = pd.DataFrame(index=dates)
    ledger["eligible_races"] = frame.groupby("race_date").race_id.nunique().reindex(dates)
    for name in ("stake", "payout", "profit"):
        ledger[name] = settlement.groupby("race_date")[name].sum().reindex(dates, fill_value=0.)
    ledger["bets"] = settlement.groupby("race_date").size().reindex(dates, fill_value=0)
    ledger["hits"] = settlement.groupby("race_date").hit.sum().reindex(dates, fill_value=0)
    ledger["bet_races"] = settlement.groupby("race_date").race_id.nunique().reindex(dates, fill_value=0)
    ledger = ledger.reset_index()
    ledger["year"] = ledger.race_date.str[:4].astype(int)
    stake, payout = float(settlement.stake.sum()), float(settlement.payout.sum())
    profit = payout - stake
    cumulative = ledger.profit.cumsum().to_numpy(float)
    peaks = np.maximum.accumulate(np.r_[0., cumulative])[1:]
    yearly = []
    for year, part in ledger.groupby("year", sort=True):
        expense, reward = float(part.stake.sum()), float(part.payout.sum())
        yearly.append({"year": int(year), "bets": int(part.bets.sum()), "dates_with_bets": int(part.bets.gt(0).sum()),
                       "stake": expense, "payout": reward, "roi_percent": 100. * reward / expense if expense else None})
    return {"bets": int(len(settlement)), "hits": int(settlement.hit.sum()), "stake": stake, "payout": payout,
        "profit": profit, "roi_percent": 100. * payout / stake if stake else None,
        "bet_races": int(settlement.race_id.nunique()), "race_coverage": settlement.race_id.nunique() / frame.race_id.nunique(),
        "dates_with_bets": int(settlement.race_date.nunique()), "date_coverage": settlement.race_date.nunique() / len(dates),
        "profit_per_eligible_race": profit / frame.race_id.nunique(),
        "max_drawdown_jpy": float(np.max(peaks - cumulative)), "yearly": yearly, "daily_ledger": ledger.to_dict("records")}


def compare_summary(actual, recorded):
    for name, expected in actual.items():
        if name not in recorded:
            raise ValueError("win-audit-settlement-summary-field-missing:" + name)
        value = recorded[name]
        if isinstance(expected, float):
            if not isinstance(value, (int, float)) or not math.isclose(value, expected, rel_tol=0., abs_tol=1e-10):
                raise ValueError("win-audit-settlement-summary-mismatch:" + name)
        elif value != expected:
            raise ValueError("win-audit-settlement-summary-mismatch:" + name)


def verify_winner_payouts(snapshot, frame):
    """Independent read-only SQL fetch, matching the complete winner key set."""
    ids = sorted(frame.race_id.unique())
    records = []
    connection = sqlite3.connect(Path(snapshot).resolve(strict=True).as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        for start in range(0, len(ids), 500):
            batch = ids[start:start + 500]
            placeholders = ",".join("?" for _ in batch)
            rows = connection.execute(f"SELECT race_id,combinations,payout FROM return_tables_ultimate "
                f"WHERE bet_type='単勝' AND race_id IN ({placeholders})", batch)
            for race_id, combination, payout in rows:
                if not re.fullmatch(r"[1-9]\d?", str(combination)):
                    raise ValueError("win-audit-payout-horse-key-invalid")
                records.append({"race_id": str(race_id), "horse_number": int(combination), "tansho_payout": float(payout)})
    finally:
        connection.close()
    saved = pd.DataFrame(records).sort_values(KEYS).reset_index(drop=True)
    expected = frame.loc[frame.finish.eq(1), KEYS + ["tansho_payout"]].sort_values(KEYS).reset_index(drop=True)
    try:
        pd.testing.assert_frame_equal(saved, expected, check_dtype=False, check_exact=True)
    except AssertionError as error:
        raise ValueError("win-audit-independent-payout-key-value-mismatch") from error
    return {"winner_keys": len(saved), "snapshot_opened_read_only": True, "source": "return_tables_ultimate actual 単勝 payout",
            "odds_substituted_for_settlement": False, "exact_payout_join": True}


def verify_selection(frozen, bootstrap_records, prefix=None):
    records = [row for row in bootstrap_records if prefix is None or row["policy_id"].startswith(prefix + ":")]
    eligible = []
    for row in records:
        summary = row["summary"]
        by_year = {part["year"]: part for part in summary["yearly"]}
        interval = row["roi_ci_simultaneous"]
        if (summary["bets"] >= 200 and summary["dates_with_bets"] >= 20
                and all(by_year.get(year, {}).get("bets", 0) >= 30 for year in (2023, 2024))
                and interval is not None and len(interval) == 2 and np.isfinite(interval).all()):
            eligible.append({"policy_id": row["policy_id"], "lower_roi_bound": float(interval[0]),
                             "roi_percent": summary["roi_percent"], "bets": summary["bets"]})
    eligible.sort(key=lambda row: (-row["lower_roi_bound"], row["policy_id"]))
    best = eligible[0] if eligible else None
    selected = best["policy_id"] if best and best["lower_roi_bound"] > 100. else None
    if (frozen.get("highest_candidate") != best or frozen.get("eligible_candidates") != eligible
            or frozen.get("selected_policy_id") != selected or frozen.get("test_outcomes_consumed") is not False):
        raise ValueError("win-audit-select-frozen-choice-mismatch")
    if prefix is not None:
        expected_rule = best["policy_id"].split(":", 1)[1] if best else "top1"
        if frozen.get("diagnostic_rule") != expected_rule or frozen.get("diagnostic_rule_is_purchase_authority") is not False:
            raise ValueError("win-audit-select-diagnostic-rule-mismatch")
    return {"highest_candidate": best, "selected_policy_id": selected,
            "uses_select_only": True, "rule_retuned": False}


def publish_audit(output, receipt, run):
    path = Path(output)
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("win-audit-output-symlink-forbidden")
    resolved = path.resolve()
    if (not resolved.is_relative_to(ROOT / "reports/generated") or resolved.is_relative_to(Path(run).resolve())
            or path.suffix != ".json" or not path.parent.is_dir()):
        raise ValueError("win-audit-output-must-be-new-json-outside-study")
    with path.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2, allow_nan=False)


def audit_run(run, output):
    import joblib
    from threadpoolctl import threadpool_limits
    from evaluate_db_shared_venues import preservation_receipt
    clock = time.perf_counter()
    run = Path(run).resolve(strict=True)
    if not run.is_relative_to(ROOT / "reports/generated/db-shared-win-direct"):
        raise ValueError("win-audit-direct-study-boundary")
    manifest_before = sha(run / "artifact-manifest.json")
    manifest = verify_manifest(run, exact_files=True)
    result, locked = read(run / "results.json"), read(run / "protocol.json")
    protocol_hash = read(run / "protocol-hash.json")["sha256"]
    if (result.get("schema") != "db-shared-win-direct-result-v1" or result.get("status") != "completed"
            or object_hash(locked) != protocol_hash or result.get("protocol_sha256") != protocol_hash
            or manifest.get("protocol_sha256") != protocol_hash):
        raise ValueError("win-audit-completed-locked-run-required")
    protocol, pins = locked["protocol"], locked["source_hashes"]
    for relative, expected in pins.items():
        if sha(bounded(ROOT, relative)) != expected:
            raise ValueError("win-audit-source-pin-mutated")
    if read(ROOT / "config/research/db-shared-win-direct-20261012.json") != protocol:
        raise ValueError("win-audit-original-config-changed")
    before = preservation_receipt()
    if before != result["preservation_before"] or before != result["preservation_after"] or before != locked["preservation_before"]:
        raise ValueError("win-audit-protected-state-not-preserved")
    if any(result.get(name) is not False for name in ("models_published", "models_activated", "forward_changed",
            "test_outcomes_used_for_selection", "test_outcomes_used_for_fit", "executable_odds_verified", "independent_future_validation")) or result.get("actual_bets") != 0:
        raise ValueError("win-audit-research-only-contract")
    prior = ROOT / protocol["prediction_run"]
    verify_manifest(prior, locked["prediction_manifest_sha256"], legacy_windows_paths=True)
    if sha(prior / "results.json") != protocol["prediction_results_sha256"]:
        raise ValueError("win-audit-old-prediction-result-changed")
    for relative, expected in read(prior / "protocol.json")["source_manifest"].items():
        if sha(bounded(ROOT, relative)) != expected:
            raise ValueError("win-audit-old-prediction-source-changed")
    old_roi = ROOT / OLD_ROI_RUN
    verify_manifest(old_roi, OLD_ROI_MANIFEST_SHA, legacy_windows_paths=True)
    if sha(old_roi / "results.json") != OLD_ROI_RESULT_SHA:
        raise ValueError("win-audit-old-roi-result-changed")
    cache = ROOT / protocol["raw47_cache"]
    if sha(cache / "manifest.json") != protocol["raw47_manifest_sha256"]:
        raise ValueError("win-audit-old-cache-manifest-changed")
    raw_manifest = read(cache / "manifest.json")
    for receipt in raw_manifest["files"].values():
        if sha(bounded(cache, receipt["path"])) != receipt["sha256"]:
            raise ValueError("win-audit-old-cache-file-changed")
    for module_name, expected in raw_manifest["source_hashes"].items():
        if sha(importlib.import_module(module_name).__file__) != expected:
            raise ValueError("win-audit-old-cache-formula-changed")
    snapshot = ROOT / "keiba/data/model_retrain_snapshots" / (protocol["snapshot_sha256"] + ".db")
    if sha(snapshot) != protocol["snapshot_sha256"] or any(Path(str(snapshot) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("win-audit-snapshot-changed-or-uncheckpointed")
    frame_path = bounded(run, "common-input.parquet")
    if sha(frame_path) != result["data_audit"]["frame_sha256"]:
        raise ValueError("win-audit-common-input-changed")
    frame = pd.read_parquet(frame_path)
    parts = {phase: frame.iloc[positions].reset_index(drop=True) for phase, positions in worker.split_positions(frame).items()}
    train_labels_sha = hashlib.sha256(parts["TRAIN"].finish.eq(1).to_numpy(float).tobytes()).hexdigest()
    market = worker.market_values(parts["TRAIN"].drop(columns=["finish", "tansho_payout"]))
    train_offset_sha = hashlib.sha256((np.log(market) - np.log1p(-market)).tobytes()).hexdigest()
    replay, split = [], {}
    if [row["candidate_id"] for row in result["worker_results"]] != list(worker.CANDIDATES):
        raise ValueError("win-audit-three-fixed-workers-required")
    with threadpool_limits(limits=2):
        for row in result["worker_results"]:
            name = row["candidate_id"]
            request = read(bounded(run, name + "-request.json"))
            verify_request(request, row, protocol_hash, pins, frame_path)
            model_path = verified_artifact(run, row["artifacts"]["model"])
            prediction_path = verified_artifact(run, row["artifacts"]["predictions"])
            cal_path = verified_artifact(run, row["artifacts"]["calibration_predictions"])
            bundle = joblib.load(model_path)
            worker.validate_bundle(bundle)
            if (bundle["source_hashes"] != pins or bundle["protocol_sha256"] != protocol_hash
                    or bundle["candidate_id"] != name or bundle["frame_sha256"] != sha(frame_path)
                    or bundle["train_labels_sha256"] != train_labels_sha or row["train_labels_sha256"] != train_labels_sha
                    or bundle["train_market_offset_sha256"] != train_offset_sha or row["train_market_offset_sha256"] != train_offset_sha
                    or hashlib.sha256(bundle["model_text"].encode()).hexdigest() != row["model_text_sha256"]):
                raise ValueError("win-audit-model-source-label-offset-binding")
            encoded = bundle["encoder"].transform(parts["TRAIN"].loc[:, bundle["feature_columns"]])
            if worker.frame_hash(encoded) != row["encoded_train_sha256"]:
                raise ValueError("win-audit-encoded-train-input-mismatch")
            saved, cal_saved = pd.read_parquet(prediction_path), pd.read_parquet(cal_path)
            if len(saved) != row["artifacts"]["predictions"]["rows"] or len(cal_saved) != row["artifacts"]["calibration_predictions"]["rows"]:
                raise ValueError("win-audit-worker-prediction-row-count")
            split[name], phase_receipts = {}, {}
            for phase in ("CAL", "SELECT", "TEST"):
                part = parts[phase]
                if worker.frame_hash(part.loc[:, row["feature_columns"]]) != row["partition_input_hashes"][phase]:
                    raise ValueError("win-audit-partition-input-hash")
                actual = cal_saved if phase == "CAL" else saved.loc[saved.split.eq(phase)].reset_index(drop=True)
                outputs = worker.predict_components(bundle, part.drop(columns=["finish", "tansho_payout"]))
                phase_receipts[phase] = {"rows": len(part), "max_absolute_replay_errors": prediction_match(actual, part, outputs),
                                          "outcome_columns_passed_to_inference": False}
                split[name][phase] = actual
            replay.append({"candidate": name, "model_sha256": sha(model_path), "phases": phase_receipts,
                           "train_labels_and_offset_shared_exactly": True, "encoded_training_input_verified": True})
    split["market_reciprocal"] = {phase: split["market_only"][phase].assign(probability=split["market_only"][phase].market_probability)
                                  for phase in ("CAL", "SELECT", "TEST")}
    payout_receipt = verify_winner_payouts(snapshot, frame)
    frozen = read(bounded(run, "frozen-selection.json"))
    frozen_hash = read(bounded(run, "frozen-selection-hash.json"))["sha256"]
    if frozen != result["frozen_selections"] or object_hash(frozen) != frozen_hash:
        raise ValueError("win-audit-frozen-selection-hash")
    boots = {phase: read(bounded(run, "bootstrap-" + phase.lower() + ".json")) for phase in ("SELECT", "TEST")}
    policies, settlements, selected, probability_checks = [], {}, {}, {}
    for phase in ("SELECT", "TEST"):
        records = result["policy_tables"][phase]
        ids = {name + ":" + rule for name in worker.CANDIDATES for rule in RULES} | {"market_favorite"}
        if len(records) != 19 or {row["policy_id"] for row in records} != ids:
            raise ValueError("win-audit-exact-nineteen-policies-required")
        boot_records = {row["policy_id"]: row["summary"] for row in boots[phase]["candidates"]}
        settlements[phase] = {}
        for row in records:
            name, rule = ("market_reciprocal", "top1") if row["policy_id"] == "market_favorite" else row["policy_id"].split(":", 1)
            tickets = policy_keys(split[name][phase], rule)
            summary = settlement_summary(split[name][phase], independent_settlement(split[name][phase], tickets))
            compare_summary({key: value for key, value in summary.items() if key != "daily_ledger"}, row)
            compare_summary(summary, boot_records[row["policy_id"]])
            settlements[phase][row["policy_id"]] = tickets
            policies.append({"phase": phase, "policy_id": row["policy_id"], "bets": summary["bets"],
                             "stake": summary["stake"], "payout": summary["payout"], "roi_percent": summary["roi_percent"],
                             "independent_key_selection_and_actual_payout_equal": True, "daily_ledger_equal": True})
    selection_checks = {name: verify_selection(frozen[name], boots["SELECT"]["candidates"], None if name == "overall" else name)
                        for name in ("overall", *worker.CANDIDATES)}
    with threadpool_limits(limits=2):
        for phase in ("SELECT", "TEST"):
            selected[phase], probability_checks[phase] = {}, {}
            for name in worker.CANDIDATES:
                part = split[name][phase]
                reference = None if name == "market_only" else split["market_only"][phase]
                fresh = diagnostics.analyze_probability(part, reference, repetitions=protocol["probability_bootstrap_repetitions"],
                                                       seed=protocol["bootstrap_seed"])
                expected = {key: value for key, value in result["probability_metrics"][phase][name].items()
                            if key != "temperature_only_diagnostic"}
                if fresh != expected:
                    raise ValueError("win-audit-probability-diagnostics-mismatch")
                probability_checks[phase][name] = {"full_fresh_diagnostics_equal": True, "nll": fresh["nll"], "brier": fresh["brier"]}
                rule = frozen[name]["diagnostic_rule"]
                fresh_selected = diagnostics.selection_calibration(part, settlements[phase][name + ":" + rule],
                    repetitions=protocol["probability_bootstrap_repetitions"], seed=protocol["bootstrap_seed"])
                if fresh_selected != result["selected_ticket_calibration"][phase][name]["probability_diagnostics"]:
                    raise ValueError("win-audit-selected-calibration-mismatch")
                selected[phase][name] = {"rule": rule, "full_fresh_diagnostics_equal": True, **fresh_selected["overall"]}
    after = preservation_receipt()
    if after != before:
        raise ValueError("win-audit-protected-state-changed-during-audit")
    verify_manifest(run, manifest_before, exact_files=True)
    if any(sha(bounded(ROOT, relative)) != expected for relative, expected in pins.items()):
        raise ValueError("win-audit-source-mutated-during-audit")
    receipt = {"schema": "db-shared-win-direct-external-audit-v1", "status": "passed", "source_run": str(run),
        "source_results_sha256": sha(run / "results.json"), "source_manifest_sha256": sha(run / "artifact-manifest.json"),
        "audit_source_sha256": sha(__file__), "protocol_sha256": protocol_hash, "source_pins_verified": len(pins),
        "old_prediction_and_roi_artifacts_preserved": True, "raw_cache_files_and_formula_preserved": True,
        "snapshot_sha256_verified": protocol["snapshot_sha256"], "winner_payout_audit": payout_receipt,
        "model_replay": replay, "independent_policy_settlement": policies, "selection_checks": selection_checks,
        "selected_ticket_calibration": selected, "probability_checks": probability_checks,
        "preservation_before": before, "preservation_after": after, "models_refitted": False,
        "policies_retuned": False, "study_artifacts_written": False, "network_calls": 0, "db_writes": 0,
        "production_changed": False, "forward_changed": False, "actual_purchase": False,
        "elapsed_seconds": time.perf_counter() - clock}
    publish_audit(output, receipt, run)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    result = audit_run(arguments.run, arguments.output)
    print(json.dumps({"status": result["status"], "elapsed_seconds": result["elapsed_seconds"]}))
